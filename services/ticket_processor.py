"""Servicio de procesamiento de tickets para SmartFridge.

Reparto de responsabilidades:

* La IA de visión (LM Studio) solo LEE el ticket: tienda, fecha, total y
  productos (nombre, cantidad, unidad y una categoría sugerida).
* Este código DECIDE: normaliza los datos, corrige categorías, calcula la
  vida útil y completa lo que falte con valores razonables.

Un dato faltante o raro NO tumba el ticket. Solo se rechaza el ticket si la
IA no devuelve ningún producto utilizable; en ese caso no se registra nada.
"""

import base64
import io
import json
import logging
import re
import secrets
import shutil
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import openai
from openai import OpenAI
from PIL import Image
from pydantic import (
    BaseModel,
    ValidationError,
    field_validator,
    model_validator,
)

import config
import storage

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
PENDING_DIR = ROOT / config.WHATSAPP_PENDING_DIR
PROCESSED_DIR = ROOT / config.WHATSAPP_PROCESSED_DIR

IMG_EXT = {".png", ".jpg", ".jpeg", ".webp"}


# ============================================================
# REGLAS DE NEGOCIO (editar aquí, no en el prompt)
# ============================================================

# Vida útil estimada por categoría, en días. Ajusta a tu criterio.
SHELF_LIFE_DAYS = {
    "lacteos": 7,
    "quesos": 21,
    "embutidos": 10,
    "carnes": 3,
    "huevo": 21,
    "frutas": 5,
    "verduras": 5,
    "bebidas": 30,
    "otros": 30,
}

CATEGORIES = tuple(SHELF_LIFE_DAYS)

UNIT_ALIASES = {
    "pz": "pz", "pza": "pz", "pzas": "pz", "pieza": "pz", "piezas": "pz",
    "unidad": "pz", "unidades": "pz", "u": "pz", "und": "pz",
    "kg": "kg", "kgs": "kg", "kilo": "kg", "kilos": "kg",
    "kilogramo": "kg", "kilogramos": "kg",
    "l": "l", "lt": "l", "lts": "l", "ltr": "l",
    "litro": "l", "litros": "l",
}

# Palabras clave -> categoría. Si el nombre del producto contiene alguna,
# se usa esa categoría (es más confiable que la sugerencia de un modelo
# pequeño). Amplía esta tabla cuando veas productos mal clasificados.
CATEGORY_KEYWORDS = {
    "lacteos": {"leche", "yogur", "yogurt", "crema", "mantequilla"},
    "quesos": {"queso", "panela", "oaxaca", "manchego", "cotija"},
    "embutidos": {"jamon", "salchicha", "chorizo", "tocino", "salami"},
    "carnes": {"pollo", "carne", "bistec", "res", "cerdo", "pescado",
               "camaron", "molida", "pechuga"},
    "huevo": {"huevo"},
    "frutas": {"manzana", "platano", "naranja", "limon", "uva", "fresa",
               "mango", "papaya", "sandia", "melon", "pera", "piña"},
    "verduras": {"jitomate", "tomate", "cebolla", "zanahoria", "lechuga",
                 "chile", "aguacate", "calabacita", "brocoli", "espinaca",
                 "pepino", "cilantro"},
    "bebidas": {"refresco", "jugo", "agua", "cerveza", "cola"},
}

# Si la fecha leída es futura o más vieja que esto, se considera mala lectura.
MAX_TICKET_AGE_DAYS = 60

_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y")


class TicketReadError(Exception):
    """La IA no pudo leer el ticket (conexión, respuesta inválida, etc.)."""


# ============================================================
# PETICIONES A LA API DE SMARTFRIDGE
# ============================================================

def _api_request(method: str, endpoint: str, **kwargs) -> Any:
    """Envía peticiones a la API REST de SmartFridge."""
    url = f"{config.API_BASE_URL.rstrip('/')}{endpoint}"

    with httpx.Client(timeout=30) as client:
        response = client.request(method, url, **kwargs)

        if response.status_code >= 400:
            raise ValueError(
                f"Error SmartFridge API "
                f"({response.status_code}): {response.text}"
            )

        if response.status_code == 204 or not response.content:
            return None

        return response.json()


def get_or_create_default_user_and_fridge(
    user_name: str = "Alan Díaz",
    email: str = "alan.diaz@smartfridge.mx",
    phone: str = "+52 664 123 4567",
    fridge_name: str = "Refrigerador Principal",
) -> tuple[str, str]:
    """Obtiene o crea el usuario y su refrigerador activo."""

    users = _api_request("GET", "/users", params={"email": email})

    if users:
        user_id = users[0]["id"]
    else:
        created_user = _api_request(
            "POST",
            "/users",
            json={
                "name": user_name,
                "email": email,
                "phone": phone,
                "password": secrets.token_urlsafe(32),
            },
        )
        user_id = created_user["id"]

    fridges = _api_request(
        "GET",
        "/refrigerators",
        params={"user_id": user_id},
    )

    fridge_id = None

    for fridge in fridges:
        if fridge["name"].lower() == fridge_name.lower():
            fridge_id = fridge["id"]
            break

    if not fridge_id:
        created_fridge = _api_request(
            "POST",
            "/refrigerators",
            json={
                "user_id": user_id,
                "name": fridge_name,
                "brand": "Samsung",
                "capacity_liters": 500,
                "esp32_device_id": f"XIAOZHI-{str(user_id)[:6].upper()}",
            },
        )
        fridge_id = created_fridge["id"]

    return str(user_id), str(fridge_id)


# ============================================================
# NORMALIZACIÓN (aquí vive la tolerancia a tickets distintos)
# ============================================================

def _strip_accents(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def _resolve_category(name: str, suggested: Any) -> str:
    """Palabras clave primero; si no hay, la sugerencia del modelo."""
    tokens = re.findall(r"[a-zñ]+", _strip_accents(name))

    for category, words in CATEGORY_KEYWORDS.items():
        words = {_strip_accents(w) for w in words}
        if any(t in words or t.rstrip("s") in words for t in tokens):
            return category

    return suggested if suggested in CATEGORIES else "otros"


class ParsedItem(BaseModel):
    """Un producto ya normalizado. Los valores raros se corrigen, no fallan."""

    name: str
    category: str = "otros"
    quantity: float = 1.0
    unit: str = "pz"

    @field_validator("name", mode="before")
    @classmethod
    def _clean_name(cls, value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("producto sin nombre")
        return " ".join(value.split())

    @field_validator("quantity", mode="before")
    @classmethod
    def _clean_quantity(cls, value):
        try:
            qty = float(str(value).replace(",", "."))
        except (TypeError, ValueError):
            return 1.0
        return qty if qty > 0 else 1.0

    @field_validator("unit", mode="before")
    @classmethod
    def _clean_unit(cls, value):
        return UNIT_ALIASES.get(str(value).strip().lower().rstrip("."), "pz")

    @model_validator(mode="after")
    def _fix_category(self):
        self.category = _resolve_category(self.name, self.category)
        return self

    @property
    def expiration_days(self) -> int:
        return SHELF_LIFE_DAYS[self.category]


@dataclass
class ParsedTicket:
    store_name: str
    purchase_date: date
    total: float | None
    raw_text: str
    items: list[ParsedItem]
    skipped_items: int = 0
    date_estimated: bool = False
    warnings: list[str] = field(default_factory=list)


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None

    text = value.strip()[:10]

    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue

    return None


def _date_from_filename(path: Path) -> date | None:
    """Los archivos de WhatsApp traen la fecha: ticket_wa_20261020_..."""
    match = re.search(r"\d{8}", path.stem)

    if not match:
        return None

    try:
        return datetime.strptime(match.group(), "%Y%m%d").date()
    except ValueError:
        return None


def _clean_total(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None

    try:
        total = float(str(value).replace("$", "").replace(",", "").strip())
    except ValueError:
        return None

    return total if total >= 0 else None


def _build_ticket(data: dict, image_path: Path) -> ParsedTicket:
    """Convierte el JSON del modelo en un ticket normalizado."""
    warnings: list[str] = []

    items: list[ParsedItem] = []
    skipped = 0
    raw_items = data.get("items")

    for raw_item in raw_items if isinstance(raw_items, list) else []:
        try:
            items.append(ParsedItem.model_validate(raw_item))
        except ValidationError as error:
            skipped += 1
            logger.warning(
                "[Vision AI] Producto omitido %r: %s",
                raw_item,
                error.errors()[0]["msg"],
            )

    # Fecha: la del ticket si es creíble; si no, la de recepción del archivo.
    today = date.today()
    parsed_date = _parse_date(data.get("purchase_date"))
    date_estimated = False

    if parsed_date and 0 <= (today - parsed_date).days <= MAX_TICKET_AGE_DAYS:
        purchase_date = parsed_date
    else:
        purchase_date = _date_from_filename(image_path) or today
        date_estimated = True
        warnings.append(
            f"Fecha ilegible o poco creíble ({data.get('purchase_date')!r}); "
            f"se usó {purchase_date.isoformat()}."
        )

    store = data.get("store_name")
    store = store.strip() if isinstance(store, str) else ""

    raw_text = data.get("raw_text")
    raw_text = raw_text if isinstance(raw_text, str) else ""

    total = _clean_total(data.get("total"))

    if total is None:
        warnings.append("No se identificó el total de la compra.")

    for warning in warnings:
        logger.warning("[Vision AI] %s", warning)

    return ParsedTicket(
        store_name=store or "Tienda no identificada",
        purchase_date=purchase_date,
        total=total,
        raw_text=raw_text,
        items=items,
        skipped_items=skipped,
        date_estimated=date_estimated,
        warnings=warnings,
    )


# ============================================================
# CONVERSIÓN DE IMAGEN
# ============================================================

def _image_to_base64_data_url(path: Path, max_side: int = 1600) -> str:
    """Convierte una imagen local a una data URL JPEG."""

    with Image.open(path) as original:
        img = original.convert("RGB")
        img.thumbnail((max_side, max_side))

        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=85)

    encoded = base64.b64encode(buffer.getvalue()).decode("utf-8")

    return f"data:image/jpeg;base64,{encoded}"


# ============================================================
# LECTURA DEL TICKET CON LM STUDIO
# ============================================================

# Prompt corto: la IA solo extrae. Vida útil, unidades y categorías
# se resuelven en código (ver sección de normalización).
VISION_PROMPT = """
Lee este ticket de compra y devuelve SOLO un objeto JSON con:
store_name, purchase_date (YYYY-MM-DD), total, raw_text e items.
Cada item tiene: name, category, quantity, unit.

Reglas:
1. No inventes datos. Texto ilegible = "". Total no visible = null.
2. Las fechas en México suelen venir como DD/MM/AAAA.
3. El total es el monto final pagado, no el subtotal.
4. Incluye solo alimentos y bebidas. Ignora limpieza, higiene, ropa, etc.
5. quantity es lo comprado. Sin multiplicador explícito, es 1.
6. unit: "pz", "kg" o "l". Si no se indica, "pz".
7. category: lacteos, quesos, embutidos, carnes, huevo, frutas, verduras,
   bebidas u otros. Si dudas, "otros".
8. Si no identificas ningún alimento, items = [].
"""

# Esquema para "structured output" de LM Studio: el servidor obliga al
# modelo a respetar esta forma, así que casi nunca llegará JSON roto.
TICKET_SCHEMA = {
    "type": "object",
    "properties": {
        "store_name": {"type": "string"},
        "purchase_date": {"type": "string"},
        "total": {"type": ["number", "null"]},
        "raw_text": {"type": "string"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "category": {"type": "string", "enum": list(CATEGORIES)},
                    "quantity": {"type": "number"},
                    "unit": {"type": "string", "enum": ["pz", "kg", "l"]},
                },
                "required": ["name", "category", "quantity", "unit"],
            },
        },
    },
    "required": ["store_name", "purchase_date", "total", "raw_text", "items"],
}


def _check_vision_config() -> None:
    for name in ("VISION_API_KEY", "VISION_BASE_URL", "VISION_MODEL"):
        value = getattr(config, name, None)

        if not value or not str(value).strip():
            raise RuntimeError(
                f"No se configuró {name}. Revisa tu archivo .env."
            )


def _extract_json(text: str) -> dict:
    """Extrae el objeto JSON aunque venga con markdown o texto alrededor."""
    start, end = text.find("{"), text.rfind("}")

    if start == -1 or end <= start:
        raise ValueError("La respuesta no contiene un objeto JSON.")

    return json.loads(text[start:end + 1])


def _call_vision_model(client: OpenAI, messages: list) -> str:
    kwargs: dict[str, Any] = {
        "model": config.VISION_MODEL,
        "messages": messages,
        "temperature": 0.1,
    }

    if getattr(config, "VISION_USE_JSON_SCHEMA", True):
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "ticket",
                "strict": True,
                "schema": TICKET_SCHEMA,
            },
        }

    try:
        response = client.chat.completions.create(**kwargs)
    except openai.BadRequestError:
        # Algunos modelos/versiones no aceptan json_schema: reintenta sin él.
        if "response_format" not in kwargs:
            raise

        logger.warning(
            "[Vision AI] El servidor rechazó json_schema; "
            "reintentando sin salida estructurada."
        )
        kwargs.pop("response_format")
        response = client.chat.completions.create(**kwargs)

    return response.choices[0].message.content or ""


def _analyze_ticket_with_vision(image_path: Path) -> ParsedTicket:
    """Lee un ticket con el modelo de LM Studio y lo normaliza."""

    _check_vision_config()

    if not image_path.exists():
        raise FileNotFoundError(
            f"No existe la imagen del ticket: {image_path}"
        )

    client = OpenAI(
        base_url=config.VISION_BASE_URL,
        api_key=config.VISION_API_KEY,
        timeout=120.0,
        max_retries=0,
    )

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": VISION_PROMPT},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": _image_to_base64_data_url(image_path),
                    },
                },
            ],
        }
    ]

    logger.info(
        "[Vision AI] Analizando %s con modelo %s en %s",
        image_path.name,
        config.VISION_MODEL,
        config.VISION_BASE_URL,
    )

    data = None

    try:
        for attempt in (1, 2):
            raw = _call_vision_model(client, messages)

            try:
                data = _extract_json(raw)
                break
            except ValueError as error:  # incluye JSONDecodeError
                logger.warning(
                    "[Vision AI] Respuesta no válida (intento %d/2): %s",
                    attempt,
                    error,
                )
    except openai.APIConnectionError as error:
        raise TicketReadError(
            "No pude conectar con LM Studio. Verifica que el servidor "
            "esté activo y que el modelo de visión esté cargado."
        ) from error
    except openai.APIStatusError as error:
        raise TicketReadError(
            f"LM Studio respondió con error {error.status_code}."
        ) from error

    if data is None:
        raise TicketReadError(
            "El modelo no devolvió un JSON válido después de 2 intentos."
        )

    ticket = _build_ticket(data, image_path)

    logger.info(
        "[Vision AI] Lectura terminada: %s, %d productos (%d omitidos).",
        ticket.store_name,
        len(ticket.items),
        ticket.skipped_items,
    )

    return ticket


# ============================================================
# SUBIDA DEL TICKET A MINIO
# ============================================================

def _upload_ticket_to_minio(ticket_path: Path) -> str:
    """Sube la imagen del ticket al bucket de compras."""

    url = storage.upload_bytes(
        config.MINIO_BUCKET_TICKETS,
        ticket_path.read_bytes(),
        ticket_path.name,
        prefix=date.today().isoformat(),
    )

    logger.info("[MinIO] Ticket subido: %s", url)

    return url


# ============================================================
# PROCESAMIENTO DE UN TICKET
# ============================================================

def _food_status(days_left: int) -> str:
    if days_left < 0:
        return "expired"
    if days_left <= 3:
        return "expiring_soon"
    return "fresh"


def _spoken_summary(ticket: ParsedTicket, registered: list[str]) -> str:
    parts = [
        f"Procesé tu ticket de {ticket.store_name}.",
        f"Guardé {len(registered)} alimentos en tu refrigerador: "
        f"{', '.join(registered)}.",
    ]

    if ticket.total is not None:
        parts.append(
            f"El total de la compra fue de ${ticket.total:.2f} pesos."
        )

    if ticket.date_estimated:
        parts.append(
            "No pude leer bien la fecha del ticket, así que usé "
            f"el {ticket.purchase_date.isoformat()}."
        )

    if ticket.skipped_items:
        parts.append(
            f"No pude leer {ticket.skipped_items} producto(s) del ticket."
        )

    return " ".join(parts)


def process_single_ticket_file(ticket_path: Path) -> dict:
    """
    Procesa un ticket.

    1. Lee y normaliza el ticket con LM Studio.
    2. Exige al menos un alimento utilizable (lo demás se completa).
    3. Obtiene el usuario y el refrigerador.
    4. Sube la imagen a MinIO.
    5. Registra la compra y sus alimentos.
    6. Mueve el archivo a processed/.
    7. Devuelve un resumen para Xiaozhi.
    """

    if not ticket_path.exists():
        raise FileNotFoundError(f"No existe el archivo: {ticket_path}")

    # Leer primero para no crear usuarios o compras si la IA falla.
    ticket = _analyze_ticket_with_vision(ticket_path)

    if not ticket.items:
        raise ValueError(
            f"No se detectaron alimentos en '{ticket_path.name}'. "
            "Verifica que el ticket sea legible."
        )

    user_id, fridge_id = get_or_create_default_user_and_fridge()
    ticket_url = _upload_ticket_to_minio(ticket_path)
    purchase_iso = ticket.purchase_date.isoformat()

    ticket_record = _api_request(
        "POST",
        "/purchase_tickets",
        json={
            "user_id": user_id,
            "refrigerator_id": fridge_id,
            "store_name": ticket.store_name,
            "purchase_date": purchase_iso,
            "ticket_image_url": ticket_url,
            "raw_text_ocr": ticket.raw_text,
            "processed_by_ai": False,
        },
    )

    ticket_id = ticket_record["id"]

    registered_foods: list[str] = []
    food_errors: list[str] = []

    for item in ticket.items:
        expiration = ticket.purchase_date + timedelta(
            days=item.expiration_days
        )
        days_left = (expiration - date.today()).days

        try:
            _api_request(
                "POST",
                "/foods",
                json={
                    "refrigerator_id": fridge_id,
                    "name": item.name,
                    "category": item.category,
                    "quantity": item.quantity,
                    "unit": item.unit,
                    "purchase_date": purchase_iso,
                    "expiration_date": expiration.isoformat(),
                    "status": _food_status(days_left),
                },
            )

            registered_foods.append(item.name)

            logger.info(
                "[Food] Registrado: %s (%s, %s %s, vence en %d días)",
                item.name,
                item.category,
                item.quantity,
                item.unit,
                item.expiration_days,
            )

        except Exception as error:
            food_errors.append(item.name)
            logger.error(
                "[Food] No se pudo registrar %s: %s", item.name, error
            )

    # No marcar la compra como procesada si falló algún alimento.
    if food_errors:
        raise RuntimeError(
            f"El ticket {ticket_id} se creó, pero hubo errores "
            "al registrar estos alimentos: "
            + ", ".join(food_errors)
            + ". Revisa la base de datos antes de volver a procesarlo "
            "para evitar duplicados."
        )

    _api_request(
        "PATCH",
        f"/purchase_tickets/{ticket_id}",
        json={"processed_by_ai": True},
    )

    # Mover imagen (y su metadata, si existe) a processed/.
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    metadata_file = ticket_path.with_suffix(".json")

    shutil.move(str(ticket_path), str(PROCESSED_DIR / ticket_path.name))

    if metadata_file.exists():
        shutil.move(
            str(metadata_file),
            str(PROCESSED_DIR / metadata_file.name),
        )

    spoken_summary = _spoken_summary(ticket, registered_foods)

    logger.info("[OK] %s", spoken_summary)

    return {
        "success": True,
        "ticket_id": ticket_id,
        "ticket_url": ticket_url,
        "store": ticket.store_name,
        "total": ticket.total,
        "items": registered_foods,
        "warnings": ticket.warnings,
        "spoken_summary": spoken_summary,
    }


# ============================================================
# COLA DE TICKETS PENDIENTES
# ============================================================

def list_pending_tickets() -> list[Path]:
    """Obtiene las imágenes pendientes de procesar."""

    if not PENDING_DIR.exists():
        return []

    return [
        path
        for path in sorted(PENDING_DIR.glob("*"))
        if path.is_file() and path.suffix.lower() in IMG_EXT
    ]


def process_all_pending_tickets(limit: int = 5) -> str:
    """
    Procesa los tickets pendientes y devuelve un resumen
    hablado para Xiaozhi.
    """

    if limit <= 0:
        return "El límite de tickets debe ser mayor que cero."

    pending = list_pending_tickets()

    if not pending:
        return "No tienes ningún ticket pendiente recibido por WhatsApp."

    summaries = []

    for ticket_path in pending[:limit]:
        try:
            result = process_single_ticket_file(ticket_path)
            summaries.append(result["spoken_summary"])

        except Exception as error:
            logger.error(
                "Error procesando %s: %s", ticket_path.name, error
            )

            summaries.append(
                f"No pude procesar el ticket {ticket_path.name}: {error}"
            )

    if len(summaries) == 1:
        return summaries[0]

    return (
        f"Intenté procesar {len(summaries)} tickets. "
        + " ".join(summaries)
    )