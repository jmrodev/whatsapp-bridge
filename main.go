package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"sync"
	"syscall"
	"time"

	_ "github.com/mattn/go-sqlite3"
	"github.com/skip2/go-qrcode"
	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/store/sqlstore"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	waLog "go.mau.fi/whatsmeow/util/log"
	"google.golang.org/protobuf/proto"
)

const dbPath = "file:data/examplestore.db?_foreign_keys=on"

var (
	client    *whatsmeow.Client
	lastQR    string
	clientMu  sync.Mutex
	clientLog waLog.Logger
	dbLog     waLog.Logger

	// Reconnection lifecycle state (guarded by reconnectMu).
	reconnectMu         sync.Mutex
	reconnectState      string // "idle" | "reconnecting" | "awaiting_admin"
	reconnectAttempts   int
	sessionExpiredSince time.Time
)

// isClientAuthenticated reports whether the bridge holds a live WhatsApp session.
// Declared as a var (instead of a plain func) so tests can inject a fake client
// without constructing a real whatsmeow device.
var isClientAuthenticated = func(c *whatsmeow.Client) bool {
	return c != nil && c.IsLoggedIn()
}

func eventHandler(evt interface{}) {
	switch v := evt.(type) {
	case *events.Message:
		// Ignorar mensajes de grupos, historias/estados y canales de noticias
		if v.Info.IsGroup || v.Info.Chat.Server == "g.us" || v.Info.Chat.Server == "broadcast" || v.Info.Chat.Server == "newsletter" || v.Info.Chat.User == "status" || strings.HasPrefix(v.Info.Chat.User, "120363") {
			return
		}

		msgText := v.Message.GetConversation()
		if msgText == "" {
			msgText = v.Message.GetExtendedTextMessage().GetText()
		}

		if msgText != "" {
			realPhoneNumber := v.Info.Chat.User
			if v.Info.IsFromMe && v.Info.RecipientAlt.User != "" {
				realPhoneNumber = v.Info.RecipientAlt.User
			} else if !v.Info.IsFromMe && v.Info.SenderAlt.User != "" {
				realPhoneNumber = v.Info.SenderAlt.User
			}

			payload := map[string]interface{}{
				"sender":   realPhoneNumber,
				"message":  msgText,
				"isFromMe": v.Info.IsFromMe,
			}
			jsonData, _ := json.Marshal(payload)

			webhookUrl := os.Getenv("WHATSAPP_WEBHOOK_URL")
			if webhookUrl == "" {
				webhookUrl = "http://server:5000/api/whatsapp/webhook"
			}
			webhookReq, _ := http.NewRequest("POST", webhookUrl, bytes.NewBuffer(jsonData))
			webhookReq.Header.Set("Content-Type", "application/json")
			if secret := os.Getenv("WHATSAPP_BRIDGE_SECRET"); secret != "" {
				webhookReq.Header.Set("X-Bridge-Secret", secret)
			}
			webhookClient := &http.Client{Timeout: 5 * time.Second}
			resp, err := webhookClient.Do(webhookReq)
			if err == nil {
				resp.Body.Close()
			}
		}
	case *events.LoggedOut, *events.Disconnected:
		// Session lost, but the SQLite store is preserved (NO DB nuke).
		// Re-enter QR pairing via the guarded reconnect lifecycle.
		clientMu.Lock()
		if client != nil {
			client.Disconnect()
			lastQR = ""
		}
		clientMu.Unlock()
		scheduleReconnect()
	case *events.Connected:
		// Successful (re)connection resets the reconnect lifecycle.
		reconnectMu.Lock()
		reconnectState = "idle"
		reconnectAttempts = 0
		sessionExpiredSince = time.Time{}
		reconnectMu.Unlock()
	}
}

type SendRequest struct {
	Recipient string `json:"recipient"`
	Message   string `json:"message"`
}

func handleSend(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	// Read the raw body for logging BEFORE decoding
	bodyBytes, _ := io.ReadAll(r.Body)
	r.Body = io.NopCloser(bytes.NewBuffer(bodyBytes)) // Restore for Decode

	var req SendRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		fmt.Printf("[Send ERROR] Failed to decode request body: %v\n", err)
		fmt.Printf("[Send ERROR] Raw body received: %s\n", string(bodyBytes))
		http.Error(w, fmt.Sprintf("Invalid request body: %v", err), http.StatusBadRequest)
		return
	}
	fmt.Printf("[Send DEBUG] Decoded request — recipient: %q, message length: %d chars\n", req.Recipient, len(req.Message))

	clientMu.Lock()
	c := client
	clientMu.Unlock()

	if c == nil {
		http.Error(w, "WhatsApp bridge not available", http.StatusServiceUnavailable)
		return
	}
	if !isClientAuthenticated(c) {
		http.Error(w, "not authenticated", http.StatusUnauthorized)
		return
	}
	if !c.IsConnected() {
		http.Error(w, "bridge unavailable", http.StatusServiceUnavailable)
		return
	}

	var recipientJID types.JID
	rawPhone := strings.TrimPrefix(req.Recipient, "+")
	var cleanPhone string
	if strings.HasPrefix(rawPhone, "549") {
		cleanPhone = "54" + rawPhone[3:]
	} else {
		cleanPhone = rawPhone
	}

	numbersToCheck := []string{
		"+" + req.Recipient,
		req.Recipient,
		"+" + cleanPhone,
		cleanPhone,
		"+" + rawPhone,
		rawPhone,
	}

	onWA, errOnWA := c.IsOnWhatsApp(context.Background(), numbersToCheck)
	fmt.Printf("[IsOnWhatsApp] Query: %v | Err: %v | Results: %+v\n", numbersToCheck, errOnWA, onWA)

	found := false
	if errOnWA == nil {
		for _, item := range onWA {
			if item.IsIn {
				recipientJID = item.JID
				found = true
				fmt.Printf("[Send] SUCCESS Resolved JID: %s for input: %s\n", recipientJID.String(), req.Recipient)
				break
			}
		}
	}

	if !found {
		parsed, _ := types.ParseJID(cleanPhone + "@s.whatsapp.net")
		recipientJID = parsed
		fmt.Printf("[Send] Fallback JID: %s for input: %s\n", recipientJID.String(), req.Recipient)
	}

	_, err := c.SendMessage(context.Background(), recipientJID, &waE2E.Message{
		Conversation: proto.String(req.Message),
	})

	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(map[string]string{"status": "sent"})
}

func handleStatus(w http.ResponseWriter, r *http.Request) {
	clientMu.Lock()
	c := client
	qr := lastQR
	reconnectMu.Lock()
	state := reconnectState
	since := sessionExpiredSince
	reconnectMu.Unlock()
	clientMu.Unlock()

	status := "disconnected"
	if c != nil && c.Store != nil {
		if isClientAuthenticated(c) {
			status = "connected"
		} else if c.Store.ID != nil {
			status = "session_expired"
		}
	}
	if state == "awaiting_admin" {
		status = "awaiting_admin"
	}

	resp := map[string]interface{}{
		"status":  status,
		"qr_code": qr,
	}
	if (status == "session_expired" || status == "disconnected") && !since.IsZero() {
		resp["session_expired_since"] = since.Format(time.RFC3339)
	}

	json.NewEncoder(w).Encode(resp)
}

// handleHealth is a liveness probe for the Docker healthcheck.
// It always returns 200 and reports whether the bridge holds a session.
func handleHealth(w http.ResponseWriter, r *http.Request) {
	clientMu.Lock()
	c := client
	clientMu.Unlock()

	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(map[string]interface{}{
		"authenticated": isClientAuthenticated(c),
	})
}

// attemptReconnect performs one guarded reconnect attempt, updating the
// lifecycle state. Returns the resulting state. Exposed (and side-effect free
// apart from state mutation) so it can be unit tested without timers.
func attemptReconnect() string {
	reconnectMu.Lock()
	defer reconnectMu.Unlock()

	if reconnectState == "awaiting_admin" {
		return reconnectState
	}

	reconnectState = "reconnecting"
	if sessionExpiredSince.IsZero() {
		sessionExpiredSince = time.Now()
	}

	reconnectAttempts++
	if reconnectAttempts >= 3 {
		reconnectState = "awaiting_admin"
	}
	return reconnectState
}

// scheduleReconnect waits out the antiflicker delay, then performs one
// reconnect attempt. After 3 failures it stops in awaiting_admin.
func scheduleReconnect() {
	reconnectMu.Lock()
	if reconnectState == "awaiting_admin" {
		reconnectMu.Unlock()
		return
	}
	reconnectMu.Unlock()

	go func() {
		time.Sleep(5 * time.Second) // antiflicker guard
		state := attemptReconnect()
		if state != "awaiting_admin" {
			connectClient()
		}
	}()
}

func handleLogout(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	clientMu.Lock()
	if client != nil {
		client.Logout(context.Background())
		client.Disconnect()
		lastQR = ""
	}
	clientMu.Unlock()

	// Clean up database files on explicit logout
	os.Remove("data/examplestore.db")
	os.Remove("data/examplestore.db-wal")
	os.Remove("data/examplestore.db-shm")

	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(map[string]string{"status": "logged_out"})
}

// buildClient creates a fresh whatsmeow client from the DB store.
func buildClient() (*whatsmeow.Client, error) {
	container, err := sqlstore.New(context.Background(), "sqlite3", dbPath, dbLog)
	if err != nil {
		return nil, fmt.Errorf("failed to open store: %w", err)
	}
	deviceStore, err := container.GetFirstDevice(context.Background())
	if err != nil {
		return nil, fmt.Errorf("failed to get device: %w", err)
	}
	c := whatsmeow.NewClient(deviceStore, clientLog)
	c.AddEventHandler(eventHandler)
	return c, nil
}

// connectClient starts the pairing or session-resume flow.
// It must be called while clientMu is NOT held — it locks internally.
func connectClient() {
	clientMu.Lock()
	c := client
	clientMu.Unlock()

	if c == nil {
		return
	}

	if c.Store.ID == nil {
		fmt.Println("No session found. Starting pairing process...")
		qrChan, _ := c.GetQRChannel(context.Background())
		err := c.Connect()
		if err != nil {
			fmt.Printf("Failed to connect: %v\n", err)
			return
		}
		go func() {
			for evt := range qrChan {
				clientMu.Lock()
				switch evt.Event {
				case "code":
					lastQR = evt.Code
					clientMu.Unlock()
					fmt.Println("\n--- NEW QR CODE RECEIVED ---")
					fmt.Printf("Code: %s\n", evt.Code)
					q, _ := qrcode.New(evt.Code, qrcode.Medium)
					fmt.Println(q.ToSmallString(false))
				case "success":
					lastQR = ""
					clientMu.Unlock()
					fmt.Println("Successfully paired!")
				case "timeout":
					lastQR = ""
					clientMu.Unlock()
					fmt.Println("QR timeout.")
				default:
					clientMu.Unlock()
				}
			}
		}()
	} else {
		fmt.Println("Existing session found. Connecting...")
		err := c.Connect()
		if err != nil {
			fmt.Printf("Failed to connect with existing session: %v\n", err)
		} else {
			fmt.Println("WhatsApp connection initiated!")
		}
	}
}

// handleRefresh tears down the current client and builds a brand-new one
// so whatsmeow opens a fresh QR channel. This works even after a QR timeout.
func handleRefresh(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	clientMu.Lock()
	// Tear down old client completely
	if client != nil {
		client.Disconnect()
	}
	lastQR = ""

	os.Remove("data/examplestore.db")
	os.Remove("data/examplestore.db-wal")
	os.Remove("data/examplestore.db-shm")

	newClient, err := buildClient()
	if err != nil {
		clientMu.Unlock()
		fmt.Printf("[Refresh] Failed to rebuild client: %v\n", err)
		http.Error(w, "Failed to rebuild client", http.StatusInternalServerError)
		return
	}
	client = newClient
	clientMu.Unlock()

	fmt.Println("[Refresh] Client rebuilt. Starting new pairing...")
	connectClient()

	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(map[string]string{"status": "refreshing"})
}

func main() {
	dbLog = waLog.Stdout("Database", "INFO", true)
	clientLog = waLog.Stdout("Client", "INFO", true)

	initialClient, err := buildClient()
	if err != nil {
		panic(err)
	}

	clientMu.Lock()
	client = initialClient
	clientMu.Unlock()

	connectClient()

	http.HandleFunc("/api/send", handleSend)
	http.HandleFunc("/api/status", handleStatus)
	http.HandleFunc("/api/health", handleHealth)
	http.HandleFunc("/api/logout", handleLogout)
	http.HandleFunc("/api/refresh", handleRefresh)

	go func() {
		fmt.Println("Bridge listening on :8090")
		http.ListenAndServe(":8090", nil)
	}()

	c := make(chan os.Signal, 1)
	signal.Notify(c, os.Interrupt, syscall.SIGTERM)
	<-c

	clientMu.Lock()
	if client != nil {
		client.Disconnect()
	}
	clientMu.Unlock()
}
