"""Single installed command for every OpcUaSim runtime mode."""

from __future__ import annotations

import importlib
import sys
from typing import Optional, Sequence


COMMANDS = {
    "gui": "gui.backend",
    "server": "server",
    "handshake": "handshake_agent",
    "szlab-handshake": "szlab_handshake_agent",
    "ino": "ino_mcp.cli",
}


def _usage() -> str:
    return """usage: opcua-sim [command] [options]

OpcUaSim installed command. With no command, starts the Web GUI.

commands:
  gui                Start the Web GUI (default)
  server             Start the CSV-driven OPC UA Server
  handshake          Start the generic XUSE handshake agent
  szlab-handshake    Start the SZLab Poly Studio handshake agent
  ino                Run the optional InoProShop MCP CLI

Run `opcua-sim <command> --help` for command-specific options.
"""


def _qualified_module(module_name: str) -> str:
    return f"{__package__}.{module_name}" if __package__ else module_name


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in {"-h", "--help"}:
        print(_usage(), end="")
        return 0
    if args and args[0] in {"-V", "--version"}:
        try:
            from . import __version__
        except ImportError:  # Direct source execution compatibility.
            from __init__ import __version__
        print(__version__)
        return 0

    command = args.pop(0) if args else "gui"
    module_name = COMMANDS.get(command)
    if module_name is None:
        print(f"opcua-sim: unknown command: {command}", file=sys.stderr)
        print("Run `opcua-sim --help` to list commands.", file=sys.stderr)
        return 2

    module = importlib.import_module(_qualified_module(module_name))
    entry = getattr(module, "main", None)
    if not callable(entry):
        raise RuntimeError(f"{module.__name__} does not expose main()")

    previous_argv = sys.argv
    sys.argv = [f"opcua-sim {command}", *args]
    try:
        return int(entry() or 0)
    finally:
        sys.argv = previous_argv
