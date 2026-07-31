from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class RobotConnection(Protocol):
    """现场厂家连接必须满足的 Interface。

    方法可以是同步或异步。运动方法必须至多下发一次，并在返回前形成明确终态；
    无法确认时应抛出 ``RobotResultUnknown``，不得在 Adapter 内静默重发。
    """

    def read_status(self) -> Mapping[str, Any]: ...

    def read_motion_permit(self) -> Mapping[str, Any]: ...

    def execute_skill(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def execute_point(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def controlled_stop(
        self,
        command_id: str,
        reason: str,
    ) -> Mapping[str, Any] | bool: ...

    def reconcile(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...
