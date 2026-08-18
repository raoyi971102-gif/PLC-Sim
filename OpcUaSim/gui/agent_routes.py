"""Handshake-agent lifecycle and PTLC fault-injection routes."""

from __future__ import annotations

import asyncio
import logging
import subprocess
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

try:
    from ..cli import runtime_command
    from ..common import runtime_data_dir
except ImportError:  # Source checkout: ``import gui.backend``.
    from cli import runtime_command
    from common import runtime_data_dir

from .backend_state import STATE, read_json_file, write_json_file
from .processes import (
    ROOT,
    find_python_exe,
    pipe_to_logger,
    python_subprocess_env,
    stop_subprocess,
)

router = APIRouter()
log = logging.getLogger("gui.agent")

SZLAB_WORKFLOW_IDS = (
    "szlab_magnetic_stirring_workflow",
    "szlab_photoshotting_workflow",
    "szlab_robot_action_workflow",
    "s04_robot_stirring_workflow",
    "s06_robot_workflow",
    "s07_robot_workflow",
    "szlab_s07_solid_addition_workflow",
    "s08_cap_workflow",
    "s09_移液调试",
    "szlab_stack_s05_s06_workflow",
    "szlab_mixer_workflow",
    "szlab_mixer_pump_production",
    "szlab_material_s06_workflow",
    "s07_粉桶与烧杯搬运后固体称量",
    "s_z_lab_标准物料转运",
    "s_z_lab_单样品全流程_物料感知",
    "s_z_lab_单样品原子流程_无_s07_扫码",
    "s_z_lab_双任务单样品原子流程_无_s07_扫码",
    "s_z_lab_烧杯五工位搬运",
)
SZLAB_WORKFLOW_ALIASES = (
    "s07_material_dosing",
    "szlab_s09_pipetting_workflow",
)


class AgentStartReq(BaseModel):
    host: str = "127.0.0.1"
    port: int = 4855
    config: str | None = None  # 可选 yaml
    csv: str | None = None  # 兼容旧 GUI 字段；SZLab 代理不读 CSV
    profile: str = "szlab"
    workflow: str | None = None
    position: int | None = Field(default=None, ge=1, le=6)
    pump: int | None = Field(default=None, ge=1, le=3)
    delay_ms: int | None = Field(default=None, ge=0, le=3_600_000)
    poll_ms: int | None = Field(default=None, ge=5, le=60_000)
    time_scale: float | None = Field(default=None, gt=0, le=1000)
    s09_remaining_volume_ml: float | None = Field(default=None, gt=0)
    s07_balance_reading: float | None = None
    s09_balance_reading: float | None = None


def _extend_szlab_command(cmd: list[str], req: AgentStartReq) -> dict[str, Any]:
    """校验并附加 SZLab 工作流调试参数，返回实际生效的显式覆盖项。"""
    options: dict[str, Any] = {}
    if req.workflow:
        if req.workflow not in ("all", *SZLAB_WORKFLOW_IDS, *SZLAB_WORKFLOW_ALIASES):
            raise HTTPException(400, f"未知 SZLab 工作流: {req.workflow}")
        options["workflow"] = req.workflow
        cmd.extend(["--workflow", req.workflow])

    option_specs = (
        ("position", "--position"),
        ("pump", "--pump"),
        ("delay_ms", "--delay-ms"),
        ("poll_ms", "--poll-ms"),
        ("s09_remaining_volume_ml", "--s09-remaining-volume-ml"),
        ("s07_balance_reading", "--s07-balance-reading"),
        ("s09_balance_reading", "--s09-balance-reading"),
    )
    for field_name, flag in option_specs:
        value = getattr(req, field_name)
        if value is not None:
            options[field_name] = value
            cmd.extend([flag, str(value)])
    return options


def _extend_ptlc_command(cmd: list[str], req: AgentStartReq) -> dict[str, Any]:
    """附加 PTLC 通用状态机参数；SZLab 专属工作流字段不会传入。"""
    options: dict[str, Any] = {}
    for field_name, flag in (("delay_ms", "--delay-ms"), ("poll_ms", "--poll-ms")):
        value = getattr(req, field_name)
        if value is not None:
            options[field_name] = value
            cmd.extend([flag, str(value)])
    if req.time_scale is not None:
        options["time_scale"] = req.time_scale
        cmd.extend(["--time-scale", str(req.time_scale)])
    return options


@router.post("/api/agent/start")
async def api_agent_start(req: AgentStartReq) -> dict[str, Any]:
    """启动握手代理并确认进程通过最小存活窗口。

    参数：``req`` 描述代理类型、OPC UA 地址及调试参数。
    返回：代理进程身份、实际类型和生效参数。
    异常：外部托管、重复启动、参数非法或子进程立即退出时抛出 HTTP 错误；
    已退出进程绝不作为运行中的代理发布给 GUI。
    """

    if STATE.attached:
        raise HTTPException(400, "已挂接外部 Agent，由进程管理器托管")
    if STATE.agent_proc is not None and STATE.agent_proc.poll() is None:
        raise HTTPException(400, "Handshake Agent 已在运行")
    url = f"opc.tcp://{req.host}:{req.port}/xuse_sim/"
    profile = (req.profile or "szlab").strip().lower()
    if profile not in {"szlab", "ptlc"}:
        raise HTTPException(400, "未知握手仿真类型，仅支持 szlab/ptlc")
    command = "ptlc-handshake" if profile == "ptlc" else "szlab-handshake"
    script = (
        "ptlc_handshake_agent.py" if profile == "ptlc" else "szlab_handshake_agent.py"
    )
    cmd = runtime_command(
        command,
        ROOT / script,
        ["--url", url],
        python_executable=find_python_exe(),
    )
    if req.config:
        cmd.extend(["--config", req.config])
    options = (
        _extend_ptlc_command(cmd, req)
        if profile == "ptlc"
        else _extend_szlab_command(cmd, req)
    )
    if profile == "ptlc":
        runtime_root = runtime_data_dir() / "runtime"
        fault_file = runtime_root / "ptlc-faults.json"
        state_file = runtime_root / "ptlc-state.json"
        if not fault_file.exists():
            write_json_file(fault_file, {})
        cmd.extend(["--fault-file", str(fault_file), "--state-file", str(state_file)])
        STATE.ptlc_fault_file = str(fault_file)
        STATE.ptlc_state_file = str(state_file)
    else:
        STATE.ptlc_fault_file = None
        STATE.ptlc_state_file = None

    log.info("启动 Handshake Agent: %s", " ".join(cmd))
    proc = await asyncio.to_thread(
        subprocess.Popen,
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(ROOT),
        bufsize=0,
        env=python_subprocess_env(),
    )
    pipe_to_logger(proc, "agent")
    STATE.agent_proc = proc
    STATE.agent_profile = profile
    await asyncio.sleep(0.3)
    exit_code = proc.poll()
    if exit_code is not None:
        STATE.agent_proc = None
        STATE.agent_profile = None
        raise HTTPException(
            500,
            f"{profile} 握手代理启动后立即退出（exit_code={exit_code}），请查看日志",
        )
    return {
        "ok": True,
        "pid": proc.pid,
        "profile": profile,
        "options": options,
    }


@router.post("/api/agent/stop")
async def api_agent_stop() -> dict[str, Any]:
    if STATE.attached:
        raise HTTPException(400, "外部 Agent 由进程管理器托管，请在 Supervisor 侧停止")
    result = await stop_subprocess("agent_proc")
    STATE.agent_profile = None
    return result


class PtlcFaultReq(BaseModel):
    station: str
    action_code: int
    outcome: str = "done"


@router.get("/api/agent/ptlc/state")
async def api_ptlc_agent_state() -> dict[str, Any]:
    return {
        "ok": True,
        "running": bool(STATE.agent_proc and STATE.agent_proc.poll() is None),
        "state": read_json_file(STATE.ptlc_state_file),
        "faults": read_json_file(STATE.ptlc_fault_file) or {},
    }


@router.post("/api/agent/ptlc/fault")
async def api_ptlc_agent_fault(req: PtlcFaultReq) -> dict[str, Any]:
    if req.station not in {
        "Sampling",
        "Collect",
        "Develop",
        "PhotoScrape",
        "FeedLift",
        "Pump",
        "Rail",
        "StagingA",
    }:
        raise HTTPException(400, f"未知 PTLC 工位: {req.station}")
    outcome = req.outcome.strip().lower()
    if outcome not in {"done", "reject", "error", "hang", "interrupt", "clear"}:
        raise HTTPException(400, f"未知故障结果: {req.outcome}")
    path = (
        Path(STATE.ptlc_fault_file)
        if STATE.ptlc_fault_file
        else (runtime_data_dir() / "runtime" / "ptlc-faults.json")
    )
    payload = read_json_file(str(path)) or {}
    station_faults = payload.setdefault(req.station, {})
    if outcome == "clear" or outcome == "done":
        station_faults.pop(str(req.action_code), None)
    else:
        station_faults[str(req.action_code)] = outcome
    if not station_faults:
        payload.pop(req.station, None)
    await asyncio.to_thread(write_json_file, path, payload)
    STATE.ptlc_fault_file = str(path)
    return {"ok": True, "faults": payload}


# -- SSE 日志流 ------------------------------------------------------------
