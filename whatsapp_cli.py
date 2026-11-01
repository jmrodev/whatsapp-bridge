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
import re
import json
import time
import math
import struct
import subprocess
import urllib.request
import urllib.error
import unicodedata
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
    """Muestra el código QR compacto y lo abre en ventana si el entorno lo permite."""
    qr_img_path = BRIDGE_DIR / "whatsapp_qr.png"

    # 1. Generar PNG limpio de alta calidad con qrencode o PIL
    try:
        import qrcode
        qr = qrcode.QRCode(border=2)
        qr.add_data(qr_string)
        qr.make()
        img = qr.make_image(fill_color="black", back_color="white")
        img.save(qr_img_path)
    except Exception:
        subprocess.run(["qrencode", "-o", str(qr_img_path), "-s", "8", "-m", "2", qr_string], capture_output=True)

    # 2. Renderizado en terminal mediante qrencode UTF8 (máxima legibilidad y tamaño ajustado)
    res = subprocess.run(["qrencode", "-t", "UTF8", "-m", "1", qr_string], capture_output=True, text=True)
    if res.returncode == 0 and res.stdout:
        print(res.stdout)
    else:
        # Fallback manual con bloques unicode
        try:
            import qrcode
            qr = qrcode.QRCode(border=1)
            qr.add_data(qr_string)
            qr.make()
            modcount = qr.modules_count
            lines = []
            for r in range(-1, modcount + 1, 2):
                row = []
                for c in range(-1, modcount + 1):
                    def v(y, x):
                        return 0 <= y < modcount and 0 <= x < modcount and qr.modules[y][x]
                    top, bot = v(r, c), v(r + 1, c)
                    row.append(" " if top and bot else ("▄" if top else ("▀" if bot else "█")))
                lines.append("".join(row))
            print("\n".join(lines))
        except Exception:
            print(f"QR Raw Data: {qr_string}")

    print(f"\n🖼️  Imagen generada: {qr_img_path}")
    print("💡 Si preferís verlo en ventana flotante, ejecutá: whatsapp show")

def show_qr():
    """Abre la imagen del QR en una ventana gráfica flotante."""
    st = _req("GET", "/api/status")
    if not st or not st.get("qr_code"):
        print("❌ No hay un código QR activo en este momento. Verificá con 'whatsapp status'.")
        return
    qr_img_path = BRIDGE_DIR / "whatsapp_qr.png"
    if not qr_img_path.exists():
        get_status()
    # Intentar abrir con el visor preferido del sistema (nsxiv, xdg-open, etc.)
    viewer = None
    for cmd in ["nsxiv", "xdg-open", "display"]:
        if subprocess.run(["which", cmd], capture_output=True).returncode == 0:
            viewer = cmd
            break
    if viewer:
        print(f"🚀 Abriendo QR en ventana ({viewer})...")
        subprocess.Popen([viewer, str(qr_img_path)], start_new_session=True, close_fds=True)
    else:
        print(f"🖼️ Podés ver la imagen en: {qr_img_path}")

def list_contacts(query="", limit=20):
    """Busca o lista contactos sincronizados de WhatsApp desde la base de datos local SQLite."""
    params = []
    if query:
        params.append(f"q={urllib.parse.quote(query)}")
    if limit:
        params.append(f"limit={limit}")
    qs = "?" + "&".join(params) if params else ""
    st = _req("GET", f"/api/contacts{qs}")
    if not st or "contacts" not in st:
        print("❌ Error al consultar contactos del bridge.")
        return

    contacts = st.get("contacts", [])
    if not contacts:
        print(f"📭 No se encontraron contactos{' con el término ' + query if query else ''}.")
        return

    print(f"\n👥 Contactos encontrados ({len(contacts)}):")
    print("─" * 70)
    for c in contacts:
        name = c.get("fullName") or c.get("pushName") or c.get("businessName") or "Sin nombre"
        phone = c.get("phone", "")
        extra = []
        if c.get("pushName") and c.get("pushName") != name:
            extra.append(f"Push: {c.get('pushName')}")
        if c.get("businessName") and c.get("businessName") != name:
            extra.append(f"Empresa: {c.get('businessName')}")
        extra_str = f" [{', '.join(extra)}]" if extra else ""
        print(f"• {name:<28} | +{phone:<16}{extra_str}")
    print("─" * 70)

def list_messages(sender="", limit=10):
    """Busca o lista contactos sincronizados de WhatsApp desde la base de datos local SQLite."""
    params = []
    if query:
        params.append(f"q={urllib.parse.quote(query)}")
    if limit:
        params.append(f"limit={limit}")
    qs = "?" + "&".join(params) if params else ""
    st = _req("GET", f"/api/contacts{qs}")
    if not st or "contacts" not in st:
        print("❌ Error al consultar contactos del bridge.")
        return

    contacts = st.get("contacts", [])
    if not contacts:
        print(f"📭 No se encontraron contactos{' con el término ' + query if query else ''}.")
        return

    print(f"\n👥 Contactos encontrados ({len(contacts)}):")
    print("─" * 70)
    for c in contacts:
        name = c.get("fullName") or c.get("pushName") or c.get("businessName") or "Sin nombre"
        phone = c.get("phone", "")
        extra = []
        if c.get("pushName") and c.get("pushName") != name:
            extra.append(f"Push: {c.get('pushName')}")
        if c.get("businessName") and c.get("businessName") != name:
            extra.append(f"Empresa: {c.get('businessName')}")
        extra_str = f" [{', '.join(extra)}]" if extra else ""
        print(f"• {name:<28} | +{phone:<16}{extra_str}")
    print("─" * 70)
    """Muestra los mensajes recientes recibidos en la terminal."""
    params = []
    if sender:
        clean = "".join(filter(str.isdigit, str(sender)))
        params.append(f"sender={clean}")
    if limit:
        params.append(f"limit={limit}")
    query = "?" + "&".join(params) if params else ""
    st = _req("GET", f"/api/messages{query}")
    if not st or "messages" not in st:
        print("❌ Error al consultar mensajes del bridge.")
        return

    msgs = st.get("messages", [])
    if not msgs:
        print("📭 No hay mensajes nuevos registrados en el bridge.")
        return

    print(f"\n📩 Últimos {len(msgs)} mensajes recibidos:")
    print("─" * 60)
    for m in reversed(msgs):
        sender_phone = m.get("sender", "Desconocido")
        push_name = f" ({m.get('pushName')})" if m.get("pushName") else ""
        from_me = " [Enviado por mí]" if m.get("isFromMe") else ""
        ts = m.get("timestamp", "")
        if "T" in ts:
            ts = ts.split("T")[1][:8]
        print(f"[{ts}] +{sender_phone}{push_name}{from_me}:")
        print(f"   {m.get('message')}")
        print("─" * 60)
    st = _req("GET", "/api/status")
    if not st or not st.get("qr_code"):
        print("❌ No hay un código QR activo en este momento. Verificá con 'whatsapp status'.")
        return
    qr_img_path = BRIDGE_DIR / "whatsapp_qr.png"
    if not qr_img_path.exists():
        get_status()
    # Intentar abrir con el visor preferido del sistema (nsxiv, xdg-open, etc.)
    viewer = None
    for cmd in ["nsxiv", "xdg-open", "display"]:
        if subprocess.run(["which", cmd], capture_output=True).returncode == 0:
            viewer = cmd
            break
    if viewer:
        print(f"🚀 Abriendo QR en ventana ({viewer})...")
        subprocess.Popen([viewer, str(qr_img_path)], start_new_session=True, close_fds=True)
    else:
        print(f"🖼️ Podés ver la imagen en: {qr_img_path}")

def sanitize_text(text: str) -> str:
    """
    Limpia secuencias de escape ANSI (colores de terminal) y glifos privados
    de fuentes (NerdFonts / eza / bat) para que el mensaje llegue como texto plano
    limpio y legible a WhatsApp sin caracteres corruptos ni [34m.
    """
    if not text:
        return ""
    # 1. Eliminar secuencias de escape ANSI (\x1b[34m, \x1b[0m, etc.)
    ansi_regex = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    text = ansi_regex.sub('', text)

    # 2. Filtrar caracteres de NerdFont (Unicode Private Use Area)
    cleaned_chars = []
    for ch in text:
        cp = ord(ch)
        if (0xE000 <= cp <= 0xF8FF) or (0xF0000 <= cp <= 0xFFFFD) or unicodedata.category(ch) == 'Co':
            continue
        cleaned_chars.append(ch)
    return "".join(cleaned_chars)

def send_message(phone, text):
    """Envía un mensaje a un número telefónico (ej: 5492494123456)."""
    clean_phone = "".join(filter(str.isdigit, str(phone)))
    clean_text = sanitize_text(text)
    st = _req("POST", "/api/send", {"recipient": clean_phone, "message": clean_text})
    if st is None:
        print("❌ Error: El servicio WhatsApp Bridge no está corriendo. Ejecutá 'whatsapp start'.")
        return
    if st.get("status") == "sent" or st.get("success"):
        print(f"✅ Mensaje enviado a +{clean_phone}: '{clean_text[:60]}...'")
    else:
        print(f"❌ Error al enviar mensaje: {st.get('error', st)}")

def calculate_waveform_and_duration(audio_path):
    """Calcula 64 puntos de waveform y la duración en segundos del audio para WhatsApp PTT."""
    duration = 5
    waveform_hex = ""
    try:
        # 1. Duración con ffprobe
        probe_cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)]
        res = subprocess.run(probe_cmd, capture_output=True, text=True)
        if res.returncode == 0 and res.stdout.strip():
            duration = max(1, int(round(float(res.stdout.strip()))))

        # 2. Decodificar PCM a 8kHz mono para calcular 64 barras de onda
        pcm_cmd = ["ffmpeg", "-i", str(audio_path), "-f", "s16le", "-ac", "1", "-ar", "8000", "-"]
        proc = subprocess.Popen(pcm_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        raw_pcm, _ = proc.communicate()

        if raw_pcm and len(raw_pcm) >= 128:
            samples = [struct.unpack("<h", raw_pcm[i:i+2])[0] for i in range(0, len(raw_pcm), 2)]
            chunk_size = max(1, len(samples) // 64)
            wf = []
            for i in range(64):
                chunk = samples[i * chunk_size : (i + 1) * chunk_size]
                if not chunk:
                    wf.append(5)
                    continue
                rms = math.sqrt(sum(s**2 for s in chunk) / len(chunk))
                # Escalar a 0-100 para WhatsApp
                val = min(100, max(5, int((rms / 32768.0) * 250)))
                wf.append(val)
            waveform_hex = bytes(wf).hex()
    except Exception:
        pass
    return duration, waveform_hex

def send_file(phone, file_path, caption="", media_type="auto"):
    """Envía cualquier archivo (imagen, video, audio, documento PDF/ZIP/etc.) a un número."""
    p = Path(file_path).expanduser().resolve()
    if not p.exists() or not p.is_file():
        print(f"❌ Error: El archivo '{file_path}' no existe.")
        return

    clean_phone = "".join(filter(str.isdigit, str(phone)))
    clean_caption = sanitize_text(caption)

    # Construir multipart/form-data
    boundary = "----WhatsAppBoundary" + str(int(time.time() * 1000))
    body = bytearray()

    def add_field(name, val):
        body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{val}\r\n".encode("utf-8"))

    add_field("recipient", clean_phone)
    if clean_caption:
        add_field("caption", clean_caption)
    if media_type:
        add_field("type", media_type)

    if media_type == "voice" or p.suffix.lower() == ".ogg":
        duration, wf_hex = calculate_waveform_and_duration(p)
        if wf_hex:
            add_field("waveform", wf_hex)
        add_field("seconds", str(duration))

    # Archivo
    filename = p.name
    with open(p, "rb") as f:
        file_bytes = f.read()

    body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\nContent-Type: application/octet-stream\r\n\r\n".encode("utf-8"))
    body.extend(file_bytes)
    body.extend(f"\r\n--{boundary}--\r\n".encode("utf-8"))

    req = urllib.request.Request(
        f"{BRIDGE_URL}/api/send-media",
        data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            content = json.loads(resp.read().decode("utf-8"))
            if content.get("status") == "sent":
                print(f"✅ Archivo enviado exitosamente a +{clean_phone}: '{filename}' ({content.get('type')})")
            else:
                print(f"❌ Error al enviar archivo: {content}")
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="ignore")
        print(f"❌ Error al enviar archivo: {err or e}")
    except Exception as e:
        print(f"❌ Error al conectar con bridge: {e}")

def take_screenshot(target_file):
    """Toma una captura de pantalla en vivo usando grim o spectacle o import."""
    for cmd in [["grim", str(target_file)], ["spectacle", "-b", "-n", "-o", str(target_file)], ["import", "-window", "root", str(target_file)]]:
        if subprocess.run(["which", cmd[0]], capture_output=True).returncode == 0:
            res = subprocess.run(cmd, capture_output=True)
            if res.returncode == 0 and target_file.exists():
                return True
    return False

def take_webcam_photo(target_file):
    """Toma una foto en vivo desde la webcam usando ffmpeg."""
    cmd = ["ffmpeg", "-f", "v4l2", "-i", "/dev/video0", "-vframes", "1", "-update", "1", "-y", str(target_file)]
    res = subprocess.run(cmd, capture_output=True)
    return res.returncode == 0 and target_file.exists()

def record_voice_note(target_file, seconds=5):
    """
    Graba una nota de voz en vivo desde el micrófono usando el perfil estricto
    de timestamps y Opus requerido por los parsers de WhatsApp móvil:
      -af asetpts=N/SR/TB : Normaliza la tabla de PTS (Presentation Timestamps)
      -ar 48000           : Frecuencia nativa de Opus
      -ac 1               : Mono estricto
      -application voip   : Perfil VoIP de baja latencia
      -f ogg              : Contenedor Ogg nativo
    """
    cmd = [
        "ffmpeg", "-f", "pulse", "-i", "default",
        "-t", str(seconds),
        "-vn",
        "-af", "asetpts=N/SR/TB",
        "-ac", "1",
        "-ar", "48000",
        "-c:a", "libopus",
        "-b:a", "64k",
        "-application", "voip",
        "-f", "ogg",
        "-v", "error",
        "-y", str(target_file)
    ]
    res = subprocess.run(cmd, capture_output=True)
    if res.returncode == 0 and target_file.exists() and target_file.stat().st_size > 1000:
        return True

    # Fallback con alsa si pulse no responde
    cmd_alsa = [
        "ffmpeg", "-f", "alsa", "-i", "default",
        "-t", str(seconds),
        "-vn",
        "-af", "asetpts=N/SR/TB",
        "-ac", "1",
        "-ar", "48000",
        "-c:a", "libopus",
        "-b:a", "64k",
        "-application", "voip",
        "-f", "ogg",
        "-v", "error",
        "-y", str(target_file)
    ]
    res = subprocess.run(cmd_alsa, capture_output=True)
    return res.returncode == 0 and target_file.exists()

def refresh():
    """Fuerza la reconexión con los servidores de WhatsApp."""
    st = _req("POST", "/api/refresh")
    if st and (st.get("success") or st.get("status") == "refreshing"):
        print("🔄 Reconexión de WhatsApp solicitada. Generando nuevo QR...")
    else:
        print(f"❌ Error al solicitar reconexión: {st}")

def logout():
    """Cierra la sesión de WhatsApp vinculada."""
    st = _req("POST", "/api/logout")
    if st and (st.get("success") or st.get("status") == "logged_out"):
        print("👋 Sesión de WhatsApp cerrada con éxito.")
    else:
        print(f"❌ Error al cerrar sesión: {st}")

USAGE = """
Uso: whatsapp <comando> [args...]

Gestión del Servicio:
  start                                 Inicia el servidor bridge en segundo plano
  status                                Muestra si está conectado o imprime el QR de vinculación
  show                                  Abre el código QR en una ventana flotante (nsxiv/visor de imágenes)
  refresh                               Fuerza una reconexión con los servidores de WhatsApp
  logout                                Cierra la sesión activa en el dispositivo

Mensajería & Archivos:
  inbox       [telefono] [limite]       📥 Lee los mensajes recibidos (persistidos en SQLite local)
  contacts    [busqueda] [limite]       👥 Lista o busca en la agenda de contactos sincronizada
  send        <telefono> ["<mensaje>"]  Envía un mensaje de texto plano o pipe
                                          cat reporte.txt | whatsapp send 5492494123456
  send-file   <telefono> <ruta> [epígrafe]
                                        Envía cualquier archivo (PDF, ZIP, imagen, video, audio)
                                          whatsapp send-file 5492494123456 reporte.pdf "Reporte mensual"
  shot        <telefono> [epígrafe]     📸 Captura en vivo de tu pantalla y la envía al instante
                                          whatsapp shot 5492494123456 "Captura de mi pantalla"
  cam         <telefono> [epígrafe]     📷 Toma una foto en vivo con la webcam y la envía
                                          whatsapp cam 5492494123456 "Foto desde la webcam"
  voice       <telefono> [segundos]     🎙️ Graba y envía una nota de voz en vivo (PTT / onda azul)
                                          whatsapp voice 5492494123456 5
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
    elif cmd == "show":
        show_qr()
    elif cmd == "refresh":
        refresh()
    elif cmd == "logout":
        logout()
    elif cmd == "inbox":
        sender = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].isdigit() else ""
        limit = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else (int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3].isdigit() else 10)
        list_messages(sender=sender, limit=limit)
    elif cmd == "contacts":
        q = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].isdigit() else ""
        limit = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else (int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3].isdigit() else 20)
        list_contacts(query=q, limit=limit)
    elif cmd == "send-file" and len(sys.argv) >= 4:
        cap = " ".join(sys.argv[4:]) if len(sys.argv) > 4 else ""
        send_file(sys.argv[2], sys.argv[3], caption=cap)
    elif cmd == "shot" and len(sys.argv) >= 3:
        cap = " ".join(sys.argv[3:]) if len(sys.argv) > 3 else "📸 Captura de pantalla"
        tmp_shot = Path(f"/tmp/screenshot_{int(time.time())}.png")
        print("📸 Tomando captura de pantalla en vivo...")
        if take_screenshot(tmp_shot):
            send_file(sys.argv[2], str(tmp_shot), caption=cap, media_type="image")
            tmp_shot.unlink(missing_ok=True)
        else:
            print("❌ No se pudo tomar la captura. Verificá que grim/spectacle esté instalado.")
    elif cmd == "cam" and len(sys.argv) >= 3:
        cap = " ".join(sys.argv[3:]) if len(sys.argv) > 3 else "📷 Foto en vivo desde webcam"
        tmp_cam = Path(f"/tmp/webcam_{int(time.time())}.jpg")
        print("📷 Tomando foto en vivo con la webcam...")
        if take_webcam_photo(tmp_cam):
            send_file(sys.argv[2], str(tmp_cam), caption=cap, media_type="image")
            tmp_cam.unlink(missing_ok=True)
        else:
            print("❌ No se pudo capturar foto de la webcam (/dev/video0).")
    elif cmd == "voice" and len(sys.argv) >= 3:
        dur = int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3].isdigit() else 5
        tmp_audio = Path(f"/tmp/voice_{int(time.time())}.ogg")
        print(f"🎙️ Grabando nota de voz durante {dur} segundos... ¡Hablá ahora!")
        if record_voice_note(tmp_audio, seconds=dur):
            print("📤 Enviando nota de voz...")
            send_file(sys.argv[2], str(tmp_audio), caption="", media_type="voice")
            tmp_audio.unlink(missing_ok=True)
        else:
            print("❌ Error al grabar audio del micrófono.")
    elif cmd == "send":
        if len(sys.argv) == 3 or (len(sys.argv) >= 4 and sys.argv[3] == "-"):
            # Modo pipe / stdin: verificar si hay datos en sys.stdin
            if not sys.stdin.isatty():
                content = sys.stdin.read().strip()
                if not content:
                    print("❌ Error: La entrada por pipe está vacía.")
                    sys.exit(1)
                send_message(sys.argv[2], content)
            else:
                print("❌ Error: No se especificó mensaje ni se pasaron datos por pipe.")
                print("   Uso: echo 'Hola' | whatsapp send <telefono>")
                print("   O:   whatsapp send <telefono> 'Hola'")
                sys.exit(1)
        elif len(sys.argv) >= 4:
            send_message(sys.argv[2], " ".join(sys.argv[3:]))
        else:
            print(USAGE)
    else:
        print(USAGE)
