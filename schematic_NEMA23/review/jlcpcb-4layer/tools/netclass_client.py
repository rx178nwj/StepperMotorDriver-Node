import asyncio
import json
import sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pathlib import Path

async def main():
    params = StdioServerParameters(command=sys.executable, args=[str(Path(__file__).with_name('netclass_mcp.py'))])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(sys.argv[1], {'pcb_path': sys.argv[2]})
            for block in result.content:
                if block.type == 'text':
                    print(block.text)
            if getattr(result, 'isError', getattr(result, 'is_error', False)):
                raise RuntimeError('MCP tool failed')

asyncio.run(main())
