from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CommandState(str, Enum):
    VALIDATED = "VALIDATED"
    DISPATCHING = "DISPATCHING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"

    @property
    def terminal(self) -> bool:
        return self in {
            CommandState.SUCCEEDED,
            CommandState.FAILED,
            CommandState.CANCELED,
            CommandState.REJECTED,
        }


class RobotConnectionError(RuntimeError):
    """Robot Connection Interface 的错误基类。"""


class RobotRejected(RobotConnectionError):
    """控制器明确拒绝，且连接能够确认运动没有开始。"""


class RobotMotionFailed(RobotConnectionError):
    """控制器给出了明确的运动失败终态。"""


class RobotResultUnknown(RobotConnectionError):
    """动作可能已经下发，但无法确认最终状态。"""


class StaleSequenceError(ValueError):
    """同一来源启动周期中的命令序号发生倒退或重放。"""


@dataclass(frozen=True)
class RuntimeResult:
    action_ref: str
    terminal: str
    outputs: dict[str, Any]
    error: str = ""
    physical_state: str = "confirmed"
    reconcile_required: bool = False


@dataclass(frozen=True)
class CommandRecord:
    command_id: str
    fingerprint: str
    action: str
    state: CommandState
    request: Mapping[str, Any]
    result: Mapping[str, Any] = field(default_factory=dict)
    error: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
