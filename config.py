"""Configuración compartida (lee el archivo .env)."""
import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./smartfridge.db")
API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "")
MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"
MINIO_BUCKET_TICKETS = os.getenv("MINIO_BUCKET_TICKETS", "purchase-tickets")
MINIO_BUCKET_FOODS = os.getenv("MINIO_BUCKET_FOODS", "food-images")

# ---- Vision AI (configurable: LM Studio local u OpenRouter cloud) ----
VISION_BASE_URL = os.getenv("VISION_BASE_URL", "https://openrouter.ai/api/v1")
VISION_API_KEY = os.getenv("VISION_API_KEY", os.getenv("OPENROUTER_API_KEY", ""))
VISION_MODEL = os.getenv(
    "VISION_MODEL",
    os.getenv("OPENROUTER_MODEL", "google/gemini-2.5-flash"),
)

# ---- WhatsApp & Evolution API ----
WHATSAPP_PENDING_DIR = os.getenv("WHATSAPP_PENDING_DIR", "tickets/whatsapp/pending")
WHATSAPP_PROCESSED_DIR = os.getenv("WHATSAPP_PROCESSED_DIR", "tickets/whatsapp/processed")
EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL", "http://localhost:8080")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "")
EVOLUTION_INSTANCE_NAME = os.getenv("EVOLUTION_INSTANCE_NAME", "SmartFridge")
DEFAULT_USER_EMAIL = os.getenv("DEFAULT_USER_EMAIL", "alan.diaz@smartfridge.mx")

# ---- Xiaozhi Cloud MCP Endpoint (WebSocket) ----
XIAOZHI_MCP_URL = os.getenv("XIAOZHI_MCP_URL", "")
