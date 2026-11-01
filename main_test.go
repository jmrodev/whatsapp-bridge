package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/types/events"
)

// TestLoggedOutEventDoesNotDeleteDB verifies the reconnection design:
// a LoggedOut event must NOT nuke the SQLite store.
// Note: only the main store file is asserted. The -wal/-shm WAL artifacts are
// managed by any live bridge process holding the DB open, so they are not
// reliable to assert in a shared environment.
func TestLoggedOutEventDoesNotDeleteDB(t *testing.T) {
	os.MkdirAll("data", 0755)
	os.WriteFile("data/examplestore.db", []byte("dummy"), 0644)

	// Avoid side effects from the scheduling goroutine.
	clientMu.Lock()
	client = nil
	clientMu.Unlock()

	eventHandler(&events.LoggedOut{})

	if _, err := os.Stat("data/examplestore.db"); os.IsNotExist(err) {
		t.Errorf("Expected data/examplestore.db to be preserved (no DB nuke)")
	}
}

// TestReconnectCapsAtThree verifies the auto-reconnect attempt cap:
// after 3 attempts the bridge enters awaiting_admin and stays there.
func TestReconnectCapsAtThree(t *testing.T) {
	reconnectMu.Lock()
	reconnectState = "idle"
	reconnectAttempts = 0
	sessionExpiredSince = time.Time{}
	reconnectMu.Unlock()

	if got := attemptReconnect(); got != "reconnecting" {
		t.Errorf("attempt 1: expected reconnecting, got %s", got)
	}
	if got := attemptReconnect(); got != "reconnecting" {
		t.Errorf("attempt 2: expected reconnecting, got %s", got)
	}
	if got := attemptReconnect(); got != "awaiting_admin" {
		t.Errorf("attempt 3: expected awaiting_admin, got %s", got)
	}
	if got := attemptReconnect(); got != "awaiting_admin" {
		t.Errorf("attempt 4: expected awaiting_admin to persist, got %s", got)
	}
}

// TestHealthEndpointReturnsAuthenticatedFalseWhenUnauthenticated verifies
// /api/health always returns 200 and reports authenticated=false when logged out.
func TestHealthEndpointReturnsAuthenticatedFalseWhenUnauthenticated(t *testing.T) {
	clientMu.Lock()
	client = nil
	clientMu.Unlock()
	isClientAuthenticated = func(c *whatsmeow.Client) bool { return c != nil }

	req := httptest.NewRequest(http.MethodGet, "/api/health", nil)
	w := httptest.NewRecorder()
	handleHealth(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}
	var body map[string]interface{}
	if err := json.NewDecoder(w.Body).Decode(&body); err != nil {
		t.Fatalf("failed to decode body: %v", err)
	}
	if body["authenticated"] != false {
		t.Errorf("expected authenticated=false, got %v", body["authenticated"])
	}
}

// TestSendReturns401WhenNotLoggedIn verifies /api/send returns 401
// when the bridge is not authenticated.
func TestSendReturns401WhenNotLoggedIn(t *testing.T) {
	clientMu.Lock()
	client = &whatsmeow.Client{}
	clientMu.Unlock()
	isClientAuthenticated = func(c *whatsmeow.Client) bool { return false }

	body := strings.NewReader(`{"recipient":"1234567890","message":"hi"}`)
	req := httptest.NewRequest(http.MethodPost, "/api/send", body)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	handleSend(w, req)

	if w.Code != http.StatusUnauthorized {
		t.Fatalf("expected 401, got %d", w.Code)
	}
}
