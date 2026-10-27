"""Script interactivo para vincular WhatsApp a SmartFridge mediante Evolution API.
Crea la instancia, configura el webhook hacia FastAPI y abre el código QR para escanear.
"""
import base64
import json
import sys
import tempfile
import time
import webbrowser
from pathlib import Path

import httpx

# Configuración
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config

EVOLUTION_URL = config.EVOLUTION_API_URL.rstrip("/")
API_KEY = config.EVOLUTION_API_KEY
INSTANCE_NAME = config.EVOLUTION_INSTANCE_NAME

HEADERS = {
    "apikey": API_KEY,
    "Content-Type": "application/json"
}

# La URL a la que Evolution API enviará los webhooks (host.docker.internal apunta a tu PC desde Docker)
WEBHOOK_URL = "http://host.docker.internal:8000/api/v1/whatsapp/webhook"


def check_evolution_health() -> bool:
    """Verifica si Evolution API está corriendo en Docker."""
    try:
        r = httpx.get(f"{EVOLUTION_URL}/", timeout=5)
        return r.status_code < 500
    except Exception:
        return False


def get_connection_state() -> str:
    """Obtiene el estado de conexión de la instancia."""
    try:
        r = httpx.get(f"{EVOLUTION_URL}/instance/connectionState/{INSTANCE_NAME}", headers=HEADERS, timeout=5)
        if r.status_code == 200:
            data = r.json()
            return data.get("instance", {}).get("state", "disconnected")
    except Exception:
        pass
    return "disconnected"


def create_or_configure_instance():
    """Crea la instancia en Evolution API con el webhook listo."""
    print(f"[1/3] Configurando instancia '{INSTANCE_NAME}' en Evolution API...")

    # 1. Intentar crear
    payload = {
        "instanceName": INSTANCE_NAME,
        "token": API_KEY,
        "qrcode": True,
        "integration": "WHATSAPP-BAILEYS",
        "webhook": {
            "url": WEBHOOK_URL,
            "byEvents": False,
            "base64": True,
            "events": [
                "MESSAGES_UPSERT"
            ]
        }
    }
    r = httpx.post(f"{EVOLUTION_URL}/instance/create", headers=HEADERS, json=payload, timeout=15)

    # Si ya existía, asegurarse de que el webhook esté configurado
    if r.status_code in (400, 403, 409):
        print(f"      Instancia '{INSTANCE_NAME}' ya existe. Actualizando webhook...")
        webhook_payload = {
            "enabled": True,
            "url": WEBHOOK_URL,
            "byEvents": False,
            "base64": True,
            "events": ["MESSAGES_UPSERT"]
        }
        httpx.post(f"{EVOLUTION_URL}/webhook/set/{INSTANCE_NAME}", headers=HEADERS, json=webhook_payload, timeout=10)
    else:
        print("      Instancia creada correctamente con webhook activo.")


def fetch_qr_code() -> str | None:
    """Obtiene el código QR en base64 de Evolution API."""
    try:
        r = httpx.get(f"{EVOLUTION_URL}/instance/connect/{INSTANCE_NAME}", headers=HEADERS, timeout=10)
        if r.status_code == 200:
            data = r.json()
            # Puede venir en data['base64'] o data['qrcode']['base64']
            return data.get("base64") or data.get("qrcode", {}).get("base64")
    except Exception as e:
        print(f"Error obteniendo QR: {e}")
    return None


def show_qr_in_browser(b64_img: str):
    """Genera una página HTML temporal y abre el navegador para escanear el QR."""
    if not b64_img.startswith("data:image"):
        b64_img = f"data:image/png;base64,{b64_img}"

    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Vincular WhatsApp - SmartFridge</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #0f172a;
            color: #f8fafc;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
            margin: 0;
        }}
        .card {{
            background: #1e293b;
            padding: 2.5rem;
            border-radius: 1rem;
            box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.5);
            text-align: center;
            max-width: 420px;
        }}
        h2 {{ margin-top: 0; color: #38bdf8; }}
        img {{
            border-radius: 0.5rem;
            background: white;
            padding: 0.75rem;
            margin: 1.5rem 0;
            width: 280px;
            height: 280px;
        }}
        ol {{ text-align: left; line-height: 1.6; color: #cbd5e1; font-size: 0.95rem; }}
        .badge {{
            background: #22c55e;
            color: black;
            font-weight: bold;
            padding: 0.25rem 0.75rem;
            border-radius: 9999px;
            font-size: 0.8rem;
        }}
    </style>
</head>
<body>
    <div class="card">
        <h2>🧾 SmartFridge + WhatsApp</h2>
        <span class="badge">Escanear con tu celular</span>
        <br>
        <img src="{b64_img}" alt="WhatsApp QR Code">
        <ol>
            <li>Abre <b>WhatsApp</b> en tu celular.</li>
            <li>Toca en <b>Ajustes</b> o menú de 3 puntos.</li>
            <li>Selecciona <b>Dispositivos vinculados</b>.</li>
            <li>Toca <b>Vincular un dispositivo</b> y apunta la cámara a este código QR.</li>
        </ol>
    </div>
</body>
</html>
"""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".html")
    tmp.write(html.encode("utf-8"))
    tmp.close()
    webbrowser.open(f"file://{tmp.name}")


def main():
    print("=======================================================")
    print("  Conector de WhatsApp para SmartFridge (Evolution API)")
    print("=======================================================\n")

    # 1. Comprobar que Docker esté arriba
    if not check_evolution_health():
        print("❌ Error: No se puede conectar con Evolution API en http://localhost:8080.")
        print("   Por favor verifica que Docker Desktop esté encendido y ejecuta:")
        print("   > docker compose up -d\n")
        return

    # 2. Comprobar si ya está conectado
    state = get_connection_state()
    if state == "open":
        print(f"🎉 ¡Tu WhatsApp ya está conectado y activo en la instancia '{INSTANCE_NAME}'!")
        print(f"   Webhook de recepción: {WEBHOOK_URL}")
        print("\nYa puedes enviar fotos de tickets por WhatsApp a este número.")
        return

    # 3. Crear / actualizar instancia
    create_or_configure_instance()

    # 4. Obtener código QR
    print("[2/3] Solicitando código QR de vinculación...")
    qr_b64 = fetch_qr_code()
    if not qr_b64:
        # Reintentar en 2 segundos
        time.sleep(2)
        qr_b64 = fetch_qr_code()

    if not qr_b64:
        print("⚠️ No se pudo obtener el QR de inmediato. Puedes ver la consola en:")
        print(f"   http://localhost:8080/manager (Apikey: {API_KEY})\n")
        return

    # 5. Abrir en el navegador
    print("[3/3] Abriendo el código QR en tu navegador para que lo escanees...")
    show_qr_in_browser(qr_b64)

    # 6. Esperar a que el usuario escanee
    print("\n⏳ Esperando a que escanees el código QR desde tu celular...")
    for _ in range(30):
        time.sleep(2)
        state = get_connection_state()
        if state == "open":
            print("\n" + "="*55)
            print("🎉 ¡WHATSAPP VINCULADO CON ÉXITO!")
            print("   Los tickets que envíes por foto ahora llegarán directamente a:")
            print("   tickets/whatsapp/pending/")
            print("="*55 + "\n")
            return
        elif state == "connecting":
            print("   Conectando...")

    print("\n⏳ Si aún no lo has escaneado, el QR expira en unos segundos.")
    print("   Puedes volver a ejecutar este script en cualquier momento: python connect_whatsapp.py")


if __name__ == "__main__":
    main()
