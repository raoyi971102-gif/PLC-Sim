from __future__ import annotations

from pathlib import Path

import pytest
import tomllib
import yaml
from unilab_robot_edge import profile_path
from unilab_robot_edge.driver import RobotEdgeDriver

PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def test_pyproject_registers_unilab_driver_entry_point() -> None:
    document = tomllib.loads(
        (PACKAGE_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    entry_points = document["project"]["entry-points"]["unilabos.drivers"]
    assert entry_points["unilab_robot_edge"] == (
        "unilab_robot_edge.driver:RobotEdgeDriver"
    )


def test_profile_and_device_yaml_are_objects() -> None:
    profile_root = PACKAGE_ROOT / "src/unilab_robot_edge/profile"
    profile = yaml.safe_load(
        (profile_root / "package.yaml").read_text(encoding="utf-8")
    )
    device = yaml.safe_load((profile_root / "device.yaml").read_text(encoding="utf-8"))
    assert profile["schema_version"] == 1
    assert device["schema_version"] == 2
    assert profile["default_device_binding"]["driver_key"] == "unilab_robot_edge"


def test_current_unilab_profile_loader_accepts_package() -> None:
    profile_loader = pytest.importorskip("unilabos.runtime.profile_loader")
    loaded = profile_loader.ProfileLoader(
        driver_catalog={"unilab_robot_edge": RobotEdgeDriver}
    ).load(profile_path())

    assert loaded.profile_id == "unilab_robot_edge_example"
    assert "robot.pick" in loaded.action_catalog
    assert "robot.move_to_point" in loaded.action_catalog
