import anyio
import sys
from pathlib import Path

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


async def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    python_exe = repo_root / ".venv" / "Scripts" / "python.exe"
    server = StdioServerParameters(
        command=str(python_exe),
        args=["-m", "logdog.mcp_server"],
        env={"LOGDOG_DB_PATH": "./data/logdog.db"},
        cwd=str(repo_root),
    )

    print("starting stdio_client...", file=sys.stderr, flush=True)
    async with stdio_client(server) as (read_stream, write_stream):
        print("stdio_client connected; initializing...", file=sys.stderr, flush=True)
        session = ClientSession(read_stream, write_stream)
        async with session:
            with anyio.fail_after(10):
                await session.initialize()

            tools = await session.list_tools()
            print("tools:", [t.name for t in tools.tools])

            with anyio.fail_after(10):
                res = await session.call_tool("recent", {"limit": 5})
            if res.structuredContent is not None:
                print("recent.structuredContent:", res.structuredContent)
            else:
                print("recent.content:", [c.model_dump() for c in res.content])

            with anyio.fail_after(10):
                res2 = await session.call_tool("query", {"app": "demo", "contains": "hello", "limit": 5})
            if res2.structuredContent is not None:
                print("query.structuredContent:", res2.structuredContent)
            else:
                print("query.content:", [c.model_dump() for c in res2.content])


if __name__ == "__main__":
    anyio.run(main)

