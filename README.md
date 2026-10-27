# SmartFridge — Xiaozhi, tickets y WhatsApp

SmartFridge recibe fotos de tickets por WhatsApp, las analiza con un modelo de visión y registra los alimentos en el refrigerador. Las imágenes se almacenan en **MinIO** y el asistente **Xiaozhi** puede consultar o gestionar el inventario mediante herramientas MCP.

Flujo: **WhatsApp → Evolution API → webhook FastAPI → cola local de tickets → visión y normalización → API SmartFridge/SQLite**. Xiaozhi se conecta al servidor MCP local o a Xiaozhi Cloud para procesar tickets y consultar/modificar el refrigerador.

---

## Puesta en marcha

### 1. Entorno virtual y dependencias
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Edita `.env` con tus propias claves y contraseñas aleatorias antes de arrancar Docker o los servicios. El archivo `.env`, la base SQLite local, el entorno virtual y las imágenes/metadatos de WhatsApp están excluidos de Git.

### 2. Infraestructura con Docker (MinIO + Evolution API + Postgres + Redis)
Con Docker Desktop encendido:
```powershell
docker compose up -d
```
* **MinIO Console**: [http://localhost:9001](http://localhost:9001) (`minioadmin` / `minioadmin123`)
* **Evolution API**: [http://localhost:8080](http://localhost:8080) (`apikey: smartfridge123`)

Vincula WhatsApp y configura el webhook ejecutando:
```powershell
python connect_whatsapp.py
```
Después, escanea el código QR con WhatsApp.

---

### 3. Iniciar API REST SmartFridge
```powershell
uvicorn api.main:app --reload
```
La documentación interactiva está en [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

Endpoints de WhatsApp en `/api/v1/whatsapp/`:
- `POST /webhook`: recibe fotos de Evolution API.
- `POST /simulate-upload`: agrega una foto de prueba a la cola.
- `GET /pending`: lista tickets pendientes.
- `POST /process-pending`: procesa la cola manualmente.

---

### 4. Iniciar Servidor MCP para Xiaozhi
```powershell
python integrations/xiaozhi/mcp/server.py
```
* Servidor activo en: `http://127.0.0.1:3001/mcp` (Streamable HTTP).
* Herramientas disponibles para Xiaozhi:
  1. `consultar_tickets_pendientes`: Xiaozhi revisa si tienes compras pendientes de registrar.
  2. `procesar_tickets_pendientes`: Xiaozhi analiza los tickets de WhatsApp y llena el refrigerador.
  3. `consultar_refrigerador`: Xiaozhi te dice qué comida tienes y qué está por caducar.
  4. `consumir_alimento`: Marca alimentos como consumidos por voz.

Para conectar directamente con Xiaozhi Cloud, define `XIAOZHI_MCP_URL` en `.env` y ejecuta el mismo servidor. También puede iniciarse en modo local con `--local`.

---

### 5. Probar el flujo del MCP de Xiaozhi
Con el servidor `server.py` y `uvicorn` encendidos:
```powershell
python integrations/xiaozhi/mcp/tests/test_mcp.py
```

---

## Estructura del Proyecto

| Carpeta / Archivo | Función |
|---|---|
| `api/` | FastAPI + SQLAlchemy (datos SmartFridge y webhook WhatsApp) |
| `integrations/xiaozhi/mcp/` | Servidor MCP HTTP (puerto 3001) con herramientas para Xiaozhi |
| `services/ticket_processor.py`| Motor central de visión, normalización, MinIO y registro en refri |
| `tickets/whatsapp/pending/` | Fotos de tickets recibidas por WhatsApp en espera |
| `tickets/whatsapp/processed/`| Fotos de tickets ya digitalizadas y registradas |
| `storage.py` | Cliente MinIO (buckets `purchase-tickets`, `food-images`) |
| `docker-compose.yml` | MinIO + Evolution API (WhatsApp) + Postgres + Redis |
