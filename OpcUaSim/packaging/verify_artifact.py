"""Reject incomplete or incorrectly named native release artifacts."""

from __future__ import annotations

import argparse
from pathlib import Path


MEBIBYTE = 1024 * 1024
MIN_WINDOWS_INSTALLER_BYTES = 10 * MEBIBYTE
MIN_MACOS_DMG_BYTES = 10 * MEBIBYTE


def verify_windows_installer(
    path: Path,
    version: str,
    *,
    minimum_bytes: int = MIN_WINDOWS_INSTALLER_BYTES,
) -> int:
    expected_name = f"OpcUaSim-Setup-Windows-x64-v{version}.exe"
    _verify_name_and_size(path, expected_name, minimum_bytes)
    with path.open("rb") as artifact:
        if artifact.read(2) != b"MZ":
            raise ValueError(f"Windows installer has no PE header: {path}")
    return path.stat().st_size


def verify_macos_dmg(
    path: Path,
    version: str,
    arch: str,
    *,
    minimum_bytes: int = MIN_MACOS_DMG_BYTES,
) -> int:
    if arch not in {"arm64", "x64"}:
        raise ValueError(f"Unsupported macOS architecture: {arch}")
    expected_name = f"OpcUaSim-macOS-{arch}-v{version}.dmg"
    size = _verify_name_and_size(
        path,
        expected_name,
        max(minimum_bytes, 512),
    )
    with path.open("rb") as artifact:
        artifact.seek(size - 512)
        if artifact.read(4) != b"koly":
            raise ValueError(f"macOS installer has no UDIF trailer: {path}")
    return size


def _verify_name_and_size(
    path: Path,
    expected_name: str,
    minimum_bytes: int,
) -> int:
    if path.name != expected_name:
        raise ValueError(
            f"Unexpected artifact name {path.name!r}; expected {expected_name!r}"
        )
    if not path.is_file():
        raise FileNotFoundError(path)
    size = path.stat().st_size
    if size < minimum_bytes:
        raise ValueError(
            f"Artifact is incomplete: {path} is {size} bytes, "
            f"expected at least {minimum_bytes}"
        )
    return size


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="platform", required=True)

    windows = subparsers.add_parser("windows")
    windows.add_argument("path", type=Path)
    windows.add_argument("--version", required=True)

    macos = subparsers.add_parser("macos")
    macos.add_argument("path", type=Path)
    macos.add_argument("--version", required=True)
    macos.add_argument("--arch", choices=("arm64", "x64"), required=True)

    args = parser.parse_args()
    if args.platform == "windows":
        size = verify_windows_installer(args.path, args.version)
    else:
        size = verify_macos_dmg(args.path, args.version, args.arch)
    print(f"Verified {args.path} ({size / MEBIBYTE:.1f} MiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
