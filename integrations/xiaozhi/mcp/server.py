"""Servidor MCP para Xiaozhi AI (Soporta WebSocket Cloud y Local Streamable-HTTP).
Conecta las herramientas de SmartFridge con el asistente de voz Xiaozhi.
"""
import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Asegurar que la raíz del proyecto esté en el path
CUR_DIR = Path(__file__).resolve().parent
ROOT = Path(__file__).resolve().parents[3]

for p in (CUR_DIR, ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import config
import websockets
from mcp.server.mcpserver import MCPServer

try:
    from tools import (
        consultar_tickets_pendientes,
        procesar_tickets_pendientes,
        consultar_refrigerador,
        consumir_alimento,
    )
except ImportError:
    from integrations.xiaozhi.mcp.tools import (
        consultar_tickets_pendientes,
        procesar_tickets_pendientes,
        consultar_refrigerador,
        consumir_alimento,
    )

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("Xiaozhi-MCP")

# ==========================================
# CONFIGURACIÓN MCP
# ==========================================

mcp = MCPServer(
    name="SmartFridge-Xiaozhi",
    instructions="Servidor MCP para que el asistente de voz Xiaozhi consulte y procese "
                 "tickets de compra recibidos por WhatsApp y gestione el refrigerador inteligente."
)

# Registro de tools
mcp.tool()(consultar_tickets_pendientes)
mcp.tool()(procesar_tickets_pendientes)
mcp.tool()(consultar_refrigerador)
mcp.tool()(consumir_alimento)


# ==========================================
# CLIENTE WEBSOCKET (CONEXIÓN XIAOZHI CLOUD)
# ==========================================

async def handle_xiaozhi_message(ws, raw_msg: str):
    """Maneja las peticiones JSON-RPC enviadas por Xiaozhi Cloud."""
    try:
        msg = json.loads(raw_msg)
    except Exception as e:
        logger.error(f"Mensaje invalido recibido: {e}")
        return

    msg_id = msg.get("id")
    method = msg.get("method")
    params = msg.get("params", {})

    logger.info(f"[XIAOZHI] Peticion: method='{method}' id={msg_id}")

    # 1. Handshake inicial
    if method == "initialize":
        resp = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {"listChanged": False}
                },
                "serverInfo": {
                    "name": "SmartFridge-Xiaozhi",
                    "version": "1.0.0"
                }
            }
        }
        await ws.send(json.dumps(resp))
        logger.info("[OK] Handshake 'initialize' completado con Xiaozhi.")

    elif method == "notifications/initialized":
        logger.info("[OK] Sesion inicializada con Xiaozhi Cloud.")

    # 2. Descubrimiento de herramientas
    elif method == "tools/list":
        tools_list = await mcp.list_tools()
        tools_data = [
            {
                "name": t.name,
                "description": (t.description or "").strip(),
                "inputSchema": t.input_schema or {"type": "object", "properties": {}}
            }
            for t in tools_list
        ]
        resp = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "tools": tools_data
            }
        }
        await ws.send(json.dumps(resp))
        logger.info(f"[OK] Enviadas {len(tools_data)} herramientas a Xiaozhi.")

    # 3. Invocación de herramientas (cuando el usuario habla con Xiaozhi)
    elif method == "tools/call":
        tool_name = params.get("name")
        arguments = params.get("arguments", {})
        logger.info(f"[TOOL] Ejecutando herramienta '{tool_name}' con args: {arguments}")

        try:
            call_res = await mcp.call_tool(tool_name, arguments)
            content_list = []
            for c in call_res.content:
                if hasattr(c, "text"):
                    content_list.append({"type": "text", "text": c.text})
                elif hasattr(c, "model_dump"):
                    content_list.append(c.model_dump())
                else:
                    content_list.append({"type": "text", "text": str(c)})

            resp = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": content_list,
                    "isError": getattr(call_res, "is_error", False)
                }
            }
            logger.info(f"[OK] Herramienta '{tool_name}' ejecutada con exito.")
        except Exception as e:
            logger.error(f"[ERROR] Error al ejecutar '{tool_name}': {e}", exc_info=True)
            resp = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": f"Error ejecutando herramienta: {str(e)}"}],
                    "isError": True
                }
            }

        await ws.send(json.dumps(resp))

    # 4. Heartbeat / Ping
    elif method == "ping":
        resp = {"jsonrpc": "2.0", "id": msg_id, "result": {}}
        await ws.send(json.dumps(resp))

    else:
        logger.warning(f"Metodo desconocido recibido: {method}")


async def run_websocket_client(ws_url: str):
    """Mantiene la conexión WebSocket activa con Xiaozhi Cloud con reconexión automática."""
    clean_url = ws_url.split("?")[0] if "?" in ws_url else ws_url
    print(f"\n[INFO] Conectando al WebSocket de Xiaozhi Cloud ({clean_url})...")

    backoff = 3
    while True:
        try:
            async with websockets.connect(
                ws_url,
                ping_interval=20,
                ping_timeout=20,
                max_size=10 * 1024 * 1024
            ) as ws:
                print("\n" + "="*60)
                print("[CONECTADO] CONEXION EXITOSA A XIAOZHI CLOUD MCP ENDPOINT!")
                print("            Tu panel web de Xiaozhi ahora estara en: Connected (Verde).")
                print("            Ya puedes hablarle a tu asistente Xiaozhi por voz.")
                print("="*60 + "\n")
                backoff = 3  # reiniciar tiempo de espera tras conexión exitosa

                async for message in ws:
                    await handle_xiaozhi_message(ws, message)

        except (websockets.exceptions.ConnectionClosedError, websockets.exceptions.ConnectionClosedOK) as e:
            logger.warning(f"Conexion con Xiaozhi cerrada ({e}). Reconectando en {backoff}s...")
        except Exception as e:
            logger.error(f"Error de red o conexion ({e}). Reintentando en {backoff}s...")

        await asyncio.sleep(backoff)
        backoff = min(backoff * 1.5, 30)


# ==========================================
# EJECUTOR PRINCIPAL
# ==========================================

def main():
    parser = argparse.ArgumentParser(description="Servidor MCP de SmartFridge para Xiaozhi")
    parser.add_argument("--ws", type=str, default="", help="URL del endpoint WebSocket de Xiaozhi (wss://api.xiaozhi.me/mcp/?token=...)")
    parser.add_argument("--local", action="store_true", help="Forzar modo HTTP local en puerto 3001")
    parser.add_argument("--port", type=int, default=3001, help="Puerto HTTP local si se usa --local")
    args = parser.parse_args()

    ws_url = args.ws or config.XIAOZHI_MCP_URL

    if not args.local and ws_url:
        print("Modo: Conexion WebSocket directa a Xiaozhi Cloud (api.xiaozhi.me)")
        try:
            asyncio.run(run_websocket_client(ws_url))
        except KeyboardInterrupt:
            print("\nServidor MCP detenido por el usuario.")
    else:
        print(f"Modo: Servidor HTTP Local (Streamable-HTTP) en http://127.0.0.1:{args.port}/mcp")
        mcp.run(
            transport="streamable-http",
            host="127.0.0.1",
            port=args.port,
            json_response=True
        )


if __name__ == "__main__":
    main()
