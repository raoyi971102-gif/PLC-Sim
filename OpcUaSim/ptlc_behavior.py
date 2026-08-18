"""PTLC V2 行为契约快照的轻量加载器。

PLC-Sim 不导入 PTLC 应用代码；这里只消费从 V2 参考仓库机械快照下来的
``ptlc.plc_choreography/v1`` YAML，提供合法动作码、步序和错误码真源。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


STATIONS = (
    "Sampling", "Collect", "Develop", "PhotoScrape",
    "FeedLift", "Pump", "Rail", "StagingA",
)


@dataclass(frozen=True)
class ActionContract:
    code: int
    name: str
    kind: str
    steps: tuple[int, ...]
    errors: tuple[int, ...]
    summary: str = ""


@dataclass(frozen=True)
class StationContract:
    station: str
    accepts: tuple[int, ...]
    unknown_code_error: int
    actions: Mapping[int, ActionContract]
    source_sha256: str

    def action(self, code: int) -> ActionContract | None:
        return self.actions.get(int(code))


def default_behavior_dir() -> Path:
    return Path(__file__).resolve().with_name("config") / "ptlc_behavior"


def _step_numbers(raw_steps: Any) -> tuple[int, ...]:
    result: list[int] = []
    for item in raw_steps or ():
        if not isinstance(item, Mapping) or "step" not in item:
            continue
        value = int(item["step"])
        if value not in result:
            result.append(value)
    return tuple(result)


def load_station_contract(path: Path) -> StationContract:
    if yaml is None:
        raise RuntimeError("PTLC behavior profile 需要 PyYAML")
    raw_bytes = path.read_bytes()
    payload = yaml.safe_load(raw_bytes.decode("utf-8")) or {}
    if payload.get("schema") != "ptlc.plc_choreography/v1":
        raise ValueError(f"行为规格 schema 不受支持: {path}")
    station = str(payload.get("station", ""))
    if station not in STATIONS:
        raise ValueError(f"行为规格工位非法: {station!r} ({path})")
    dispatcher = payload.get("dispatcher") or {}
    accepts = tuple(int(value) for value in dispatcher.get("accepts", ()))
    if not accepts:
        raise ValueError(f"行为规格缺少 dispatcher.accepts: {path}")
    actions: dict[int, ActionContract] = {}
    for raw_code, raw_action in (payload.get("actions") or {}).items():
        code = int(raw_code)
        item = raw_action or {}
        actions[code] = ActionContract(
            code=code,
            name=str(item.get("name", f"action_{code}")),
            kind=str(item.get("kind", "generic")),
            steps=_step_numbers(item.get("steps")),
            errors=tuple(int(value) for value in (item.get("errors") or {}).keys()),
            summary=str(item.get("summary", "")),
        )
    return StationContract(
        station=station,
        accepts=accepts,
        unknown_code_error=int(dispatcher.get("unknown_code_error", 101)),
        actions=actions,
        source_sha256=hashlib.sha256(raw_bytes).hexdigest(),
    )


def load_behavior_contracts(directory: Path | None = None) -> dict[str, StationContract]:
    root = (directory or default_behavior_dir()).resolve()
    result: dict[str, StationContract] = {}
    for path in sorted(root.glob("*.yaml")):
        contract = load_station_contract(path)
        if contract.station in result:
            raise ValueError(f"行为规格工位重复: {contract.station}")
        result[contract.station] = contract
    missing = sorted(set(STATIONS) - set(result))
    if missing:
        raise ValueError(f"PTLC 行为规格缺少工位: {', '.join(missing)}")
    return result

