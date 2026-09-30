"""Claude Desktop compatibility: forward tools to HTTP, never start AtlasBrain."""
import asyncio
import sys
from contextlib import asynccontextmanager
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server


@asynccontextmanager
async def connection(url):
    async with streamable_http_client(url) as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            info = await session.initialize()
            yield session, info


async def run(url):
    # Fresh sessions let the bridge recover after a daemon restart.
    async with connection(url) as (session, info):
        instructions = info.instructions
    async def list_tools(context, params):
        async with connection(url) as (session, info):
            if hasattr(Server, 'call_tool'):
                return await session.list_tools(cursor=params.cursor if params else None)
            return await session.list_tools(params=params)
    async def call_tool(context, params):
        async with connection(url) as (session, info):
            return await session.call_tool(params.name, params.arguments or {})
    if hasattr(Server, 'call_tool'):  # SDK 1.x
        server = Server('atlasbrain-http-bridge', instructions=instructions)
        @server.list_tools()
        async def legacy_list():
            return (await list_tools(None, None)).tools
        @server.call_tool(validate_input=False)
        async def legacy_call(name, arguments):
            async with connection(url) as (session, info):
                return await session.call_tool(name, arguments or {})
    else:  # SDK 2.x
        server = Server('atlasbrain-http-bridge', instructions=instructions,
                        on_list_tools=list_tools, on_call_tool=call_tool)
    async with stdio_server() as streams:
        await server.run(*streams, server.create_initialization_options())


if __name__ == '__main__':
    asyncio.run(run(sys.argv[1]))
