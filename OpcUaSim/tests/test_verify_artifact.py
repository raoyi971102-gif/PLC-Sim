from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


PACKAGING_DIRECTORY = Path(__file__).parents[1] / "packaging"
VERIFY_ARTIFACT_PATH = PACKAGING_DIRECTORY / "verify_artifact.py"


def _load_verify_artifact() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "opcua_sim_verify_artifact",
        VERIFY_ARTIFACT_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VERIFY_ARTIFACT = _load_verify_artifact()


def test_verify_windows_installer_checks_name_size_and_header(tmp_path) -> None:
    installer = tmp_path / "OpcUaSim-Setup-Windows-x64-v0.2.4.exe"
    installer.write_bytes(b"MZ" + b"\0" * 30)

    assert VERIFY_ARTIFACT.verify_windows_installer(
        installer,
        "0.2.4",
        minimum_bytes=32,
    ) == 32


def test_verify_windows_installer_rejects_bad_header(tmp_path) -> None:
    installer = tmp_path / "OpcUaSim-Setup-Windows-x64-v0.2.4.exe"
    installer.write_bytes(b"NO" + b"\0" * 30)

    with pytest.raises(ValueError, match="PE header"):
        VERIFY_ARTIFACT.verify_windows_installer(
            installer,
            "0.2.4",
            minimum_bytes=32,
        )


def test_verify_windows_installer_rejects_incomplete_file(tmp_path) -> None:
    installer = tmp_path / "OpcUaSim-Setup-Windows-x64-v0.2.4.exe"
    installer.write_bytes(b"MZ")

    with pytest.raises(ValueError, match="incomplete"):
        VERIFY_ARTIFACT.verify_windows_installer(
            installer,
            "0.2.4",
            minimum_bytes=32,
        )


def test_verify_macos_dmg_checks_name_size_and_udif_trailer(tmp_path) -> None:
    installer = tmp_path / "OpcUaSim-macOS-arm64-v0.2.4.dmg"
    contents = bytearray(1024)
    contents[-512:-508] = b"koly"
    installer.write_bytes(contents)

    assert VERIFY_ARTIFACT.verify_macos_dmg(
        installer,
        "0.2.4",
        "arm64",
        minimum_bytes=1024,
    ) == 1024


def test_verify_macos_dmg_rejects_bad_trailer(tmp_path) -> None:
    installer = tmp_path / "OpcUaSim-macOS-x64-v0.2.4.dmg"
    installer.write_bytes(b"\0" * 1024)

    with pytest.raises(ValueError, match="UDIF trailer"):
        VERIFY_ARTIFACT.verify_macos_dmg(
            installer,
            "0.2.4",
            "x64",
            minimum_bytes=1024,
        )


def test_verify_macos_dmg_rejects_wrong_release_name(tmp_path) -> None:
    installer = tmp_path / "OpcUaSim-macOS-arm64-v0.2.3.dmg"
    installer.write_bytes(b"\0" * 1024)

    with pytest.raises(ValueError, match="Unexpected artifact name"):
        VERIFY_ARTIFACT.verify_macos_dmg(
            installer,
            "0.2.4",
            "arm64",
            minimum_bytes=1024,
        )
