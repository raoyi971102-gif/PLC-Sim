"""PyInstaller entry point for the native OpcUaSim desktop application."""

from __future__ import annotations

from opcua_sim.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
