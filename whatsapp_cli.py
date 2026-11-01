#!/usr/bin/env python3
"""
WhatsApp CLI — Herramienta de terminal para WhatsApp usando el Bridge autónomo (whatsmeow / Go).
Permite:
  - Iniciar / detener el servicio bridge local en background
  - Consultar estado de sesión (logueado, desconectado, reconectando)
  - Escanear código QR de vinculación directamente en la terminal (ASCII)
  - Enviar mensajes directos a números telefónicos
  - Cerrar sesión (logout) y forzar reconexión (refresh)
"""
import os
import sys
import json
import time
import subprocess
import urllib.request
import urllib.error
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parent
BRIDGE_BIN = BRIDGE_DIR / "whatsapp-bridge-server"
BRIDGE_PORT = int(os.environ.get("WHATSAPP_PORT", "8090"))
BRIDGE_URL = f"http://localhost:{BRIDGE_PORT}"

def _req(method, endpoint, payload=None):
    url = f"{BRIDGE_URL}{endpoint}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            content = resp.read().decode("utf-8")
            return json.loads(content) if content else {}
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", errors="ignore")
        return {"error": err_msg or str(e), "status_code": e.code}
    except urllib.error.URLError:
        return None
    except Exception as e:
        return {"error": str(e)}

def ensure_binary():
    """Compila el binario de Go si aún no existe."""
    if not BRIDGE_BIN.exists():
        print("🔨 Compilando whatsapp-bridge binario nativo...")
        res = subprocess.run(["go", "build", "-o", str(BRIDGE_BIN), "main.go"], cwd=str(BRIDGE_DIR), capture_output=True, text=True)
        if res.returncode != 0:
            print(f"❌ Error al compilar: {res.stderr}")
            sys.exit(1)
        print("✅ Binario compilado exitosamente.")

def start_server():
    """Inicia el servidor bridge en segundo plano."""
    ensure_binary()
    status = _req("GET", "/api/health")
    if status is not None:
        print(f"🟢 El servicio WhatsApp Bridge ya está corriendo en {BRIDGE_URL}")
        return

    print("🚀 Levantando WhatsApp Bridge en background...")
    log_path = BRIDGE_DIR / "bridge.log"
    with open(log_path, "a") as log_file:
        proc = subprocess.Popen(
            [str(BRIDGE_BIN)],
            cwd=str(BRIDGE_DIR),
            stdout=log_file,
            stderr=log_file,
            start_new_session=True,
            close_fds=True
        )

    # Esperar hasta 5 segundos a que el puerto responda
    for _ in range(10):
        time.sleep(0.5)
        st = _req("GET", "/api/health")
        if st is not None:
            print(f"✅ Bridge iniciado con éxito en {BRIDGE_URL} (PID: {proc.pid})")
            return

    print("⚠️ El bridge se inició pero aún no responde en el puerto. Verificá con 'whatsapp status'.")

def get_status():
    """Consulta el estado del bridge y sesión."""
    st = _req("GET", "/api/status")
    if not st:
        print("🔴 El servidor WhatsApp Bridge está APAGADO. Iniciálo con: 'whatsapp start'")
        return
    status_str = st.get('status', '')
    is_connected = status_str == 'connected'
    qr_code = st.get('qr_code') or st.get('qr')

    print("\n📱 Estado de WhatsApp:")
    print(f"   Estado         : {'🟢 Conectado' if is_connected else f'🟡 {status_str}'}")
    print(f"   Logueado       : {'✅ Sí' if is_connected else '❌ No (Requiere escaneo QR)'}")
    if not is_connected and qr_code:
        print("\n📷 Escaneá este código QR en WhatsApp (Dispositivos vinculados):\n")
        render_qr(qr_code)
    return st

def render_qr(qr_string):
    """Muestra el código QR compacto en la consola y guarda una imagen limpia."""
    try:
        import qrcode
        qr = qrcode.QRCode(border=1)
        qr.add_data(qr_string)
        qr.make()

        # Guardar automáticamente como imagen PNG para poder abrirla con visor o celular si se prefiere
        qr_img_path = BRIDGE_DIR / "whatsapp_qr.png"
        img = qr.make_image(fill_color="black", back_color="white")
        img.save(qr_img_path)

        # Renderizar en terminal con bloques compactos (1 línea por cada 2 filas)
        modcount = qr.modules_count
        border = 1
        lines = []
        for r in range(-border, modcount + border, 2):
            row_chars = []
            for c in range(-border, modcount + border):
                def get_val(row, col):
                    if row < 0 or row >= modcount or col < 0 or col >= modcount:
                        return False
                    return bool(qr.modules[row][col])

                top = get_val(r, c)
                bottom = get_val(r + 1, c)

                # Bloques ANSI para terminal con fondo oscuro (invertido natural)
                if top and bottom:
                    row_chars.append(" ")
                elif top and not bottom:
                    row_chars.append("▄")
                elif not top and bottom:
                    row_chars.append("▀")
                else:
                    row_chars.append("█")
            lines.append("".join(row_chars))

        print("\n".join(lines))
        print(f"\n🖼️  QR guardado también como imagen en: {qr_img_path}")
        print("💡 Para abrir en pantalla completa: xdg-open " + str(qr_img_path))
    except Exception as e:
        print(f"QR Raw Data: {qr_string}")
        print(f"(Error rendering QR: {e})")

def send_message(phone, text):
    """Envía un mensaje a un número telefónico (ej: 5492494123456)."""
    clean_phone = "".join(filter(str.isdigit, str(phone)))
    st = _req("POST", "/api/send", {"recipient": clean_phone, "message": text})
    if st is None:
        print("❌ Error: El servicio WhatsApp Bridge no está corriendo. Ejecutá 'whatsapp start'.")
        return
    if st.get("status") == "sent" or st.get("success"):
        print(f"✅ Mensaje enviado a +{clean_phone}: '{text}'")
    else:
        print(f"❌ Error al enviar mensaje: {st.get('error', st)}")

def logout():
    """Cierra la sesión de WhatsApp vinculada."""
    st = _req("POST", "/api/logout")
    if st and st.get("success"):
        print("👋 Sesión de WhatsApp cerrada con éxito.")
    else:
        print("❌ Error al cerrar sesión.")

def refresh():
    """Fuerza la reconexión con los servidores de WhatsApp."""
    st = _req("POST", "/api/refresh")
    if st and st.get("success"):
        print("🔄 Reconexión de WhatsApp solicitada.")
    else:
        print("❌ Error al solicitar reconexión.")

USAGE = """
Uso: whatsapp <comando> [args...]

Gestión del Servicio:
  start                                 Inicia el servidor bridge en segundo plano
  status                                Muestra si está conectado o imprime el QR de vinculación
  refresh                               Fuerza una reconexión con los servidores de WhatsApp
  logout                                Cierra la sesión activa en el dispositivo

Mensajería:
  send       <telefono> "<mensaje>"     Envía un mensaje de WhatsApp (ej: whatsapp send 5492494123456 "Hola")
"""

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(USAGE)
        sys.exit(0)
    cmd = sys.argv[1].lower()
    if cmd == "start":
        start_server()
    elif cmd == "status":
        get_status()
    elif cmd == "refresh":
        refresh()
    elif cmd == "logout":
        logout()
    elif cmd == "send" and len(sys.argv) >= 4:
        send_message(sys.argv[2], sys.argv[3])
    else:
        print(USAGE)
