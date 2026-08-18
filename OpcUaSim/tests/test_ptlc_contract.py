from __future__ import annotations

import os
from pathlib import Path

import pytest

from common import load_ptlc_nodes


ROOT = Path(__file__).parents[1]
SNAPSHOT = ROOT / "config" / "ptlc_nodes.yaml"


def test_ptlc_snapshot_covers_v2_types_arrays_and_nested_gvl() -> None:
    nodes = load_ptlc_nodes(SNAPSHOT)
    by_name = {node.name_cn: node for node in nodes}

    assert len(nodes) >= 250
    assert set(node.data_type for node in nodes) == {
        "BOOLEAN", "BYTE", "INT16", "INT32", "FLOAT", "DOUBLE", "STRING"
    }
    assert by_name["PLC_Axis_CommOperational"].array_len == 11
    assert by_name["Tank_State"].array_len == 8
    assert by_name["Rail_Pos_Target"].array_len == 6
    assert by_name["PLC_Ready"].browse_path == (
        "DeviceSet", "Inovance-ARM-Linux", "Resources",
        "Application", "GlobalVars", "Host_Computer",
    )
    for station in (
        "Sampling", "Collect", "Develop", "PhotoScrape",
        "FeedLift", "Pump", "Rail", "StagingA",
    ):
        for field in (
            "ActionCode", "RequestSeq", "Start", "Reset", "State", "ActiveCode",
            "AcceptedSeq", "CompletedSeq", "Step", "ErrorCode", "SafeState", "Retryable",
        ):
            assert f"{station}_L2_{field}" in by_name


def test_optional_reference_contract_has_not_drifted() -> None:
    root = os.environ.get("PTLC_REFERENCE_ROOT")
    if not root:
        pytest.skip("set PTLC_REFERENCE_ROOT to enable cross-repository drift check")
    reference = Path(root) / "eit_ptlc" / "config" / "plc_nodes.yaml"
    if not reference.is_file():
        pytest.skip(f"PTLC reference node map not found: {reference}")

    expected = load_ptlc_nodes(reference)
    actual = load_ptlc_nodes(SNAPSHOT)
    normalize = lambda nodes: [
        (item.name_cn, item.data_type, item.array_len, item.browse_path)
        for item in nodes
    ]
    assert normalize(actual) == normalize(expected)
