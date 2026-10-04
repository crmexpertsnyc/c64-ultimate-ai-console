"""Stdio launcher for the C64 Ultimate MCP server — works from any working directory.

MCP clients such as Claude Desktop and Claude Code start stdio servers without honouring a
working directory, so this script puts its own folder on the import path first.

    <backend>/.venv/Scripts/python.exe <backend>/c64_mcp.py          (Windows)
    <backend>/.venv/bin/python <backend>/c64_mcp.py                   (macOS / Linux)

Set MCP_API_BASE if the console is not at http://127.0.0.1:8064.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.mcp.server import main  # noqa: E402

if __name__ == "__main__":
    main()
