"""机器人协议示例共享校验。

本模块不连接硬件。运动示例必须通过显式确认门，避免复制代码后意外执行。
"""

from __future__ import annotations

import math
from collections.abc import Iterable

MOTION_CONFIRM_TEXT = "I_HAVE_VERIFIED_THE_ROBOT_CELL"


def vector6(values: Iterable[float], *, name: str) -> tuple[float, ...]:
    """转换并验证有限六维向量。"""
    result = tuple(float(value) for value in values)
    if len(result) != 6:
        raise ValueError(f"{name} 必须恰好包含6个数，当前为 {len(result)}")
    if not all(math.isfinite(value) for value in result):
        raise ValueError(f"{name} 包含 NaN 或无穷值")
    return result


def in_closed_range(value: float, low: float, high: float, *, name: str) -> float:
    number = float(value)
    if not math.isfinite(number) or not low <= number <= high:
        raise ValueError(f"{name} 必须在 [{low}, {high}]，当前为 {value!r}")
    return number


def require_motion_confirmation(execute: bool, confirmation: str | None) -> None:
    """运动双重确认：布尔执行开关 + 固定确认文本。"""
    if not execute:
        raise PermissionError("示例默认禁止运动；请显式设置 execute=True")
    if confirmation != MOTION_CONFIRM_TEXT:
        raise PermissionError(
            "运动确认文本不匹配；完成现场安全检查后传入 "
            f"{MOTION_CONFIRM_TEXT!r}"
        )


def format_number(value: float) -> str:
    """生成不使用科学计数法的协议数字。"""
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("协议数字必须是有限值")
    text = f"{number:.9f}".rstrip("0").rstrip(".")
    return text if text not in {"", "-0"} else "0"
