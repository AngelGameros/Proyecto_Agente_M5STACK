"""Rutas de integración con WhatsApp (Evolution API y simulación)."""
import base64
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

import config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/whatsapp", tags=["whatsapp"])

ROOT = Path(__file__).resolve().parents[1]
PENDING_DIR = ROOT / config.WHATSAPP_PENDING_DIR
PROCESSED_DIR = ROOT / config.WHATSAPP_PROCESSED_DIR

PENDING_DIR.mkdir(parents=True, exist_ok=True)
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)


class WebhookResponse(BaseModel):
    status: str
    message: str
    saved_file: Optional[str] = None


async def _download_evolution_media(instance: str, message_item: dict) -> bytes | None:
    """Descarga medios desde Evolution API si no vienen en base64 en el webhook."""
    url = f"{config.EVOLUTION_API_URL}/chat/getBase64FromMediaMessage/{instance}"
    headers = {"apikey": config.EVOLUTION_API_KEY, "Content-Type": "application/json"}
    payload = {"message": message_item, "convertToMp4": False}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                b64 = data.get("base64")
                if b64:
                    if "," in b64:
                        b64 = b64.split(",", 1)[1]
                    return base64.b64decode(b64)
            else:
                logger.warning(f"Error descargando media ({resp.status_code}): {resp.text}")
    except Exception as e:
        logger.error(f"Excepción al descargar media de Evolution API: {e}")
    return None


async def _send_whatsapp_reply(instance: str, number: str, text: str):
    """Envía un mensaje de texto de respuesta por WhatsApp usando Evolution API."""
    url = f"{config.EVOLUTION_API_URL}/message/sendText/{instance}"
    headers = {"apikey": config.EVOLUTION_API_KEY, "Content-Type": "application/json"}
    payload = {"number": number, "text": text}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(url, headers=headers, json=payload)
    except Exception as e:
        logger.warning(f"No se pudo enviar confirmación por WhatsApp ({e})")


@router.post("/webhook", response_model=WebhookResponse)
async def whatsapp_webhook(payload: dict[str, Any]):
    """Webhook receptor de eventos de Evolution API (messages.upsert).
    Detecta fotos de tickets, las extrae y las guarda en tickets/whatsapp/pending/."""
    event = payload.get("event", "").lower()
    instance = payload.get("instance", config.EVOLUTION_INSTANCE_NAME)
    data = payload.get("data", {})

    print(f"\n[WHATSAPP WEBHOOK] Evento recibido: '{event}'")

    if "messages.upsert" not in event and "message" not in event and event != "messages-upsert":
        return WebhookResponse(status="ignored", message=f"Evento '{event}' no relevante")

    # Puede venir como dict único o en lista
    msg_item = data[0] if isinstance(data, list) and data else data
    key = msg_item.get("key", {})
    from_me = key.get("fromMe", False)
    remote_jid = key.get("remoteJid", "")
    push_name = msg_item.get("pushName", "Usuario")
    message = msg_item.get("message", {})

    # Desenvolver mensajes efímeros o de vista única si vienen encapsulados
    if "ephemeralMessage" in message:
        message = message["ephemeralMessage"].get("message", message)
    if "viewOnceMessage" in message:
        message = message["viewOnceMessage"].get("message", message)
    if "viewOnceMessageV2" in message:
        message = message["viewOnceMessageV2"].get("message", message)

    # Detectar si es foto (imageMessage) o documento de imagen (documentMessage)
    image_msg = message.get("imageMessage")
    if not image_msg and "documentMessage" in message:
        doc = message["documentMessage"]
        mimetype = doc.get("mimetype", "")
        filename = doc.get("fileName", "").lower()
        if mimetype.startswith("image/") or filename.endswith((".png", ".jpg", ".jpeg", ".webp")):
            image_msg = doc

    # Si NO es imagen, ignorar
    if not image_msg:
        print(f"   ℹ️ El mensaje no contiene imagen (texto u otro tipo). Ignorando.")
        return WebhookResponse(status="ignored", message="El mensaje no contiene una imagen")

    print(f"   📸 ¡FOTO DETECTADA! De: {push_name} ({remote_jid}) [fromMe={from_me}]")

    raw_bytes = None
    # 1. ¿Viene base64 en el payload de Evolution API?
    b64 = (
        image_msg.get("base64")
        or message.get("base64")
        or msg_item.get("base64")
        or data.get("base64")
    )
    if b64:
        if "," in b64:
            b64 = b64.split(",", 1)[1]
        try:
            raw_bytes = base64.b64decode(b64)
        except Exception as e:
            logger.error(f"Error decodificando base64: {e}")
            raw_bytes = None

    # 2. Si no venía base64 directo, descargarlo desde Evolution API
    if not raw_bytes:
        print("   ⏳ Descargando imagen desde Evolution API...")
        raw_bytes = await _download_evolution_media(instance, msg_item)

    if not raw_bytes:
        print("   ❌ Error: No se pudo obtener los bytes de la imagen.")
        return WebhookResponse(status="error", message="No se pudo obtener el archivo de imagen")

    # Guardar imagen en tickets/whatsapp/pending/
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    msg_id = key.get("id", "img")[:8]
    filename = f"ticket_wa_{ts}_{msg_id}.jpg"
    target_path = PENDING_DIR / filename
    target_path.write_bytes(raw_bytes)

    # Guardar metadatos complementarios
    meta_path = PENDING_DIR / f"ticket_wa_{ts}_{msg_id}.json"
    meta = {
        "remote_jid": remote_jid,
        "push_name": push_name,
        "caption": image_msg.get("caption", ""),
        "received_at": datetime.utcnow().isoformat(),
        "filename": filename,
        "from_me": from_me
    }
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"   ✅ [OK] Foto de ticket guardada con éxito en: {target_path}")

    # Enviar respuesta automática solo si no fue enviado por uno mismo para evitar bucles
    clean_number = remote_jid.split("@")[0] if "@" in remote_jid else remote_jid
    if clean_number and not from_me:
        await _send_whatsapp_reply(
            instance,
            clean_number,
            f"🧾 ¡Ticket recibido, {push_name}!\n\n"
            "Ha sido guardado como pendiente. Puedes pedirle a Xiaozhi que lo procese diciendo:\n"
            "🗣️ 'Xiaozhi, revisa los tickets pendientes'",
        )

    return WebhookResponse(
        status="success",
        message="Ticket recibido y guardado como pendiente",
        saved_file=str(target_path.relative_to(ROOT)).replace("\\", "/"),
    )


@router.post("/simulate-upload", response_model=WebhookResponse)
async def simulate_upload(file: UploadFile = File(...)):
    """Sube una foto de ticket directamente a la cola de pendientes de WhatsApp
    sin necesidad de enviar un mensaje real (útil para pruebas)."""
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in (file.filename or "") else "jpg"
    filename = f"ticket_wa_{ts}_sim.{ext}"
    target_path = PENDING_DIR / filename

    content = await file.read()
    target_path.write_bytes(content)

    meta_path = PENDING_DIR / f"ticket_wa_{ts}_sim.json"
    meta = {
        "remote_jid": "simulated@whatsapp",
        "push_name": "Usuario Simulado",
        "caption": "Ticket simulado cargado manualmente",
        "received_at": datetime.utcnow().isoformat(),
        "filename": filename,
    }
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    return WebhookResponse(
        status="success",
        message="Ticket simulado guardado en pendientes",
        saved_file=str(target_path.relative_to(ROOT)).replace("\\", "/"),
    )


@router.get("/pending")
def list_pending():
    """Lista las imágenes de tickets actualmente pendientes en tickets/whatsapp/pending/."""
    valid_ext = {".png", ".jpg", ".jpeg", ".webp"}
    files = []
    for p in sorted(PENDING_DIR.glob("*")):
        if p.suffix.lower() in valid_ext:
            meta_p = p.with_suffix(".json")
            meta = {}
            if meta_p.exists():
                try:
                    meta = json.loads(meta_p.read_text(encoding="utf-8"))
                except Exception:
                    pass
            files.append({
                "path": str(p.relative_to(ROOT)).replace("\\", "/"),
                "filename": p.name,
                "size_kb": round(p.stat().st_size / 1024, 1),
                "sender": meta.get("push_name", "Desconocido"),
                "received_at": meta.get("received_at", ""),
            })
    return {"total": len(files), "tickets": files}


@router.post("/process-pending")
def process_pending(limit: int = 5):
    """Ejecuta el procesamiento de los tickets pendientes de WhatsApp
    (mismo motor que invoca Xiaozhi vía MCP)."""
    from services.ticket_processor import process_all_pending_tickets
    result_text = process_all_pending_tickets(limit=limit)
    return {"status": "ok", "summary": result_text}
