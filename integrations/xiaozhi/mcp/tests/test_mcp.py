import asyncio
import sys
from mcp import Client

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


async def main():
    print("Conectando al MCP de SmartFridge para Xiaozhi...")

    async with Client("http://127.0.0.1:3001/mcp") as client:
        print("Conectado correctamente al servidor MCP.")
        print()

        # 1. Listar herramientas disponibles
        tools_result = await client.list_tools()
        tools_list = tools_result.tools
        print(f"Herramientas registradas ({len(tools_list)}):")
        for t in tools_list:
            desc = (t.description or "")[:80].replace("\n", " ")
            print(f" - {t.name}: {desc}...")
        print()

        # 2. Consultar tickets pendientes
        print("1. Ejecutando consultar_tickets_pendientes...")
        res1 = await client.call_tool("consultar_tickets_pendientes", {})
        for content in res1.content:
            if hasattr(content, "text"):
                print("   [Voz Xiaozhi]:", content.text)
        print()

        # 3. Consultar refrigerador
        print("2. Ejecutando consultar_refrigerador...")
        res2 = await client.call_tool("consultar_refrigerador", {"filtro": "todos"})
        for content in res2.content:
            if hasattr(content, "text"):
                print("   [Voz Xiaozhi]:", content.text)
        print()


if __name__ == "__main__":
    asyncio.run(main())