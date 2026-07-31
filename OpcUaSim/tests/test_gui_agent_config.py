from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from gui.backend import AgentStartReq, SZLAB_WORKFLOW_IDS, _extend_szlab_command
from szlab_handshake_agent import WORKFLOW_IDS


def test_workflow_catalog_matches_agent_and_gui() -> None:
    html = (
        Path(__file__).parents[1] / "gui" / "static" / "index.html"
    ).read_text(encoding="utf-8")

    assert SZLAB_WORKFLOW_IDS == WORKFLOW_IDS
    for workflow_id in WORKFLOW_IDS:
        assert f'value="{workflow_id}"' in html


def test_szlab_agent_options_are_forwarded_to_cli() -> None:
    req = AgentStartReq(
        profile="szlab",
        workflow="s04_robot_stirring_workflow",
        position=5,
        pump=3,
        delay_ms=250,
        poll_ms=40,
        s09_remaining_volume_ml=88.5,
    )
    cmd = ["python", "szlab_handshake_agent.py"]

    options = _extend_szlab_command(cmd, req)

    assert options == {
        "workflow": "s04_robot_stirring_workflow",
        "position": 5,
        "pump": 3,
        "delay_ms": 250,
        "poll_ms": 40,
        "s09_remaining_volume_ml": 88.5,
    }
    assert cmd == [
        "python",
        "szlab_handshake_agent.py",
        "--workflow",
        "s04_robot_stirring_workflow",
        "--position",
        "5",
        "--pump",
        "3",
        "--delay-ms",
        "250",
        "--poll-ms",
        "40",
        "--s09-remaining-volume-ml",
        "88.5",
    ]


def test_szlab_agent_rejects_unknown_workflow() -> None:
    req = AgentStartReq(profile="szlab", workflow="unknown_workflow")

    with pytest.raises(HTTPException) as exc_info:
        _extend_szlab_command([], req)

    assert exc_info.value.status_code == 400
    assert "未知 SZLab 工作流" in str(exc_info.value.detail)


@pytest.mark.parametrize(
    ("field_name", "value"),
    (("position", 7), ("pump", 0), ("poll_ms", 4), ("delay_ms", -1)),
)
def test_szlab_agent_rejects_out_of_range_parameters(
    field_name: str,
    value: int,
) -> None:
    with pytest.raises(ValidationError):
        AgentStartReq(profile="szlab", **{field_name: value})
