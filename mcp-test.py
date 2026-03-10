import asyncio
from server.client import billing_mcp_client

async def main():
    tools = await billing_mcp_client.discover_tools()
    for t in tools:
        print(f"{t['name']}: {t['description']}")

asyncio.run(main())