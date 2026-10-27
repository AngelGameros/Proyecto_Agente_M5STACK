"""Herramientas MCP de SmartFridge para el asistente Xiaozhi.
Permiten a Xiaozhi consultar tickets recibidos por WhatsApp, procesar alimentos
con visión, y administrar el refrigerador mediante voz.
"""
import sys
from datetime import date
from pathlib import Path

# Asegurar acceso a los módulos raíz de SmartFridge
ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx
import config
from services.ticket_processor import (
    list_pending_tickets,
    process_all_pending_tickets
)

API_URL = config.API_BASE_URL.rstrip("/")


async def consultar_tickets_pendientes() -> str:
    """
    Consulta si hay tickets de compra recibidos por WhatsApp pendientes de procesar.
    Útil cuando el usuario pregunta a Xiaozhi si tiene tickets o compras pendientes.
    """
    pending = list_pending_tickets()
    total = len(pending)
    if total == 0:
        return "No tienes ningún ticket de compra pendiente recibido por WhatsApp."

    return (
        f"Tienes {total} ticket{'s' if total > 1 else ''} pendiente{'s' if total > 1 else ''} "
        f"de WhatsApp listo{'s' if total > 1 else ''} para procesar. "
        f"Pídeme que los procese diciendo: 'procesa los tickets pendientes'."
    )


async def procesar_tickets_pendientes(limite: int = 5) -> str:
    """
    Procesa las fotos de tickets pendientes de WhatsApp: digitaliza los productos
    alimenticios con visión artificial, calcula su caducidad y los almacena en el refrigerador.
    """
    try:
        resultado = process_all_pending_tickets(limit=limite)
        return resultado
    except Exception as e:
        return f"Ocurrió un error al procesar los tickets: {str(e)}"


async def consultar_refrigerador(filtro: str = "todos") -> str:
    """
    Consulta los alimentos guardados en el refrigerador inteligente.
    filtro puede ser 'todos' o 'por_caducar'.
    Indica qué alimentos hay y alerta de los que caducan pronto.
    """
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            res = await client.get(f"{API_URL}/foods")
            if res.status_code != 200:
                return f"No pude consultar el refrigerador (Error {res.status_code})."
            foods = res.json()

        # Filtrar solo los no consumidos
        activos = [f for f in foods if f.get("status") != "consumed"]

        if not activos:
            return "Tu refrigerador está completamente vacío actualmente."

        por_caducar = []
        nombres = []

        for f in activos:
            nombre = f.get("name", "Alimento")
            nombres.append(nombre)
            exp = f.get("expiration_date")
            if exp:
                try:
                    dias = (date.fromisoformat(exp) - date.today()).days
                    if dias <= 3:
                        por_caducar.append(f"{nombre} (en {dias} días)")
                except Exception:
                    pass

        if filtro.lower() == "por_caducar":
            if not por_caducar:
                return "Ningún alimento en tu refrigerador está próximo a caducar en los siguientes 3 días."
            return f"Alimentos próximos a caducar: {', '.join(por_caducar)}."

        resumen = f"Tienes {len(activos)} alimentos en el refrigerador: {', '.join(nombres[:8])}."
        if len(nombres) > 8:
            resumen += f" y {len(nombres) - 8} más."

        if por_caducar:
            resumen += f" ¡Atención! Están próximos a caducar: {', '.join(por_caducar)}."

        return resumen

    except Exception as e:
        return f"Error al conectar con el refrigerador: {str(e)}"


async def consumir_alimento(nombre_alimento: str) -> str:
    """
    Marca un alimento como consumido en el refrigerador cuando el usuario
    avisa por voz que se lo terminó o consumió.
    """
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            res = await client.get(f"{API_URL}/foods")
            if res.status_code != 200:
                return f"No se pudo consultar el refrigerador (Error {res.status_code})."
            foods = res.json()

            # Buscar alimento por coincidencia de texto
            objetivo = None
            term = nombre_alimento.lower().strip()
            for f in foods:
                if f.get("status") != "consumed" and term in f.get("name", "").lower():
                    objetivo = f
                    break

            if not objetivo:
                return f"No encontré '{nombre_alimento}' disponible en tu refrigerador."

            food_id = objetivo["id"]
            consume_res = await client.post(f"{API_URL}/foods/{food_id}/consume")
            if consume_res.status_code == 200:
                return f"Listo, he marcado '{objetivo['name']}' como consumido en tu refrigerador."
            return f"Hubo un error al marcar el alimento: {consume_res.text}"

    except Exception as e:
        return f"Error al actualizar alimento: {str(e)}"
