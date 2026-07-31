from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import math
import time
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .catalog import (
    InteractionCatalog,
    PointCatalog,
    resource_id,
    resource_location,
    resource_type_of,
)
from .contracts import (
    CommandRecord,
    CommandState,
    RobotMotionFailed,
    RobotRejected,
    RobotResultUnknown,
    RuntimeResult,
    StaleSequenceError,
)
from .journal import CommandJournal


@dataclass(frozen=True)
class CommissioningSession:
    session_id: str
    controller_boot_id: str
    point_set_version: str
    calibration_version: str
    tool_profile: str
    payload_profile: str
    external_axis_context: Mapping[str, Any]
    speed_cap_percent: float
    expires_at: float


class RobotEdgeDriver:
    """Deep runtime module behind the Uni-Lab Profile driver seam."""

    def __init__(self, *, plc: Any, driver_config: Mapping[str, Any]) -> None:
        self._connection = plc
        self._config = dict(driver_config)
        raw_macros = self._config.get("macros")
        if not isinstance(raw_macros, Mapping) or not raw_macros:
            raise ValueError("driver_config.macros must be a non-empty object")
        self._actions = {str(name) for name in raw_macros}
        self._interactions = InteractionCatalog.from_reference(
            _mapping(self._config.get("interaction_catalog"), "interaction_catalog")
        )
        self._points = PointCatalog.from_reference(
            _mapping(self._config.get("point_catalog"), "point_catalog")
        )
        self._journal = CommandJournal(
            str(self._config.get("journal_path") or ":memory:")
        )
        self._permit_config = dict(_mapping(self._config.get("permit"), "permit"))
        self._commissioning_config = dict(
            _mapping(self._config.get("commissioning"), "commissioning")
        )
        self._motion_lock = asyncio.Lock()
        self._session_lock = asyncio.Lock()
        self._sessions: dict[str, CommissioningSession] = {}

    async def run_macro(
        self,
        macro: str,
        *,
        inputs: Mapping[str, Any],
    ) -> RuntimeResult:
        action = str(macro)
        if action not in self._actions:
            raise ValueError(f"action is not declared by the active profile: {action}")
        if not isinstance(inputs, Mapping):
            raise TypeError("action inputs must be an object")
        handler = getattr(self, f"_action_{action}", None)
        if handler is None or not callable(handler):
            raise ValueError(f"driver does not implement declared action: {action}")
        return await handler(dict(inputs))

    async def _action_query_status(self, _: Mapping[str, Any]) -> RuntimeResult:
        try:
            status = dict(await self._call("read_status"))
            permit = await self._read_permit(required=False)
            status["motion_permit"] = permit
            return self._success(
                "query_status",
                {
                    "success": True,
                    "status": status,
                    "unresolved_commands": self._journal.unresolved_count(),
                },
            )
        except Exception as exc:  # noqa: BLE001 - read-only connection boundary
            return self._failure("query_status", str(exc), physical_state="not_started")

    async def _action_pick(self, inputs: Mapping[str, Any]) -> RuntimeResult:
        try:
            self._require_no_commissioning_session()
            resource = _mapping(inputs.get("resource"), "resource")
            parameters = _optional_mapping(inputs.get("parameters"), "parameters")
            binding, payload_profile, bounded_parameters = (
                self._interactions.resolve_pick(resource, parameters)
            )
            mount_resource_id, site = resource_location(resource)
            request = {
                **self._command_envelope(inputs),
                "action": "pick",
                "protocol_version": self._interactions.protocol_version,
                "resource": _resource_snapshot(resource),
                "resource_type": resource_type_of(resource),
                "source_location": {
                    "mount_resource_id": mount_resource_id,
                    "site": site,
                },
                **binding.to_request_fields(),
                "payload_profile": payload_profile,
                "parameters": bounded_parameters,
            }
        except Exception as exc:  # noqa: BLE001 - fail closed before dispatch
            return self._failure("pick", str(exc), physical_state="not_started")
        return await self._dispatch("pick", request, "execute_skill")

    async def _action_place(self, inputs: Mapping[str, Any]) -> RuntimeResult:
        try:
            self._require_no_commissioning_session()
            resource = _mapping(inputs.get("resource"), "resource")
            target_mount = _mapping(
                inputs.get("target_mount_resource"),
                "target_mount_resource",
            )
            target_site = _required_text(inputs.get("target_site"), "target_site")
            parameters = _optional_mapping(inputs.get("parameters"), "parameters")
            binding, payload_profile, bounded_parameters = (
                self._interactions.resolve_place(
                    resource,
                    target_mount,
                    target_site,
                    parameters,
                )
            )
            request = {
                **self._command_envelope(inputs),
                "action": "place",
                "protocol_version": self._interactions.protocol_version,
                "resource": _resource_snapshot(resource),
                "resource_type": resource_type_of(resource),
                "target_location": {
                    "mount_resource_id": resource_id(
                        target_mount,
                        name="target_mount_resource",
                    ),
                    "site": target_site,
                },
                **binding.to_request_fields(),
                "payload_profile": payload_profile,
                "parameters": bounded_parameters,
            }
        except Exception as exc:  # noqa: BLE001 - fail closed before dispatch
            return self._failure("place", str(exc), physical_state="not_started")
        return await self._dispatch("place", request, "execute_skill")

    async def _action_get_command(self, inputs: Mapping[str, Any]) -> RuntimeResult:
        command_id = _required_text(inputs.get("command_id"), "command_id")
        record = self._journal.get(command_id)
        if record is None:
            return self._success(
                "get_command",
                {
                    "success": True,
                    "found": False,
                    "state": "",
                    "command": {},
                },
            )
        return self._success(
            "get_command",
            {
                "success": True,
                "found": True,
                "state": record.state.value,
                "command": _public_record(record),
            },
        )

    async def _action_reconcile(self, inputs: Mapping[str, Any]) -> RuntimeResult:
        command_id = _required_text(inputs.get("command_id"), "command_id")
        record = self._journal.get(command_id)
        if record is None:
            return self._failure(
                "reconcile",
                f"unknown command_id: {command_id}",
                physical_state="not_started",
            )
        if record.state != CommandState.UNKNOWN:
            return self._result_for_record("reconcile", record)
        try:
            observation = await self._call("reconcile", dict(record.request))
            return self._apply_observation("reconcile", record, observation)
        except Exception as exc:  # noqa: BLE001 - reconciliation must stay UNKNOWN
            updated = self._journal.update(
                command_id,
                CommandState.UNKNOWN,
                result=record.result,
                error=f"reconcile failed: {exc}",
            )
            return self._result_for_record("reconcile", updated)

    async def _action_request_controlled_stop(
        self,
        inputs: Mapping[str, Any],
    ) -> RuntimeResult:
        command_id = _required_text(inputs.get("command_id"), "command_id")
        reason = _required_text(inputs.get("reason"), "reason")
        record = self._journal.get(command_id)
        if record is None:
            return self._failure(
                "request_controlled_stop",
                f"unknown command_id: {command_id}",
                physical_state="not_started",
            )
        if record.state.terminal:
            return self._result_for_record("request_controlled_stop", record)
        confirmed, detail = await self._attempt_stop(command_id, reason)
        if confirmed:
            updated = self._journal.update(
                command_id,
                CommandState.CANCELED,
                result={"stop": detail},
            )
        else:
            updated = self._journal.update(
                command_id,
                CommandState.UNKNOWN,
                result={"stop": detail},
                error="controlled stop could not be confirmed",
            )
        return self._result_for_record("request_controlled_stop", updated)

    async def _action_begin_commissioning(
        self,
        inputs: Mapping[str, Any],
    ) -> RuntimeResult:
        try:
            if not self._points.approved:
                raise ValueError("point set is not approved for commissioning")
            if self._journal.unresolved_count() > 0:
                raise ValueError("UNKNOWN commands must be reconciled first")
            session_id = _required_text(inputs.get("session_id"), "session_id")
            point_set_version = _required_text(
                inputs.get("point_set_version"),
                "point_set_version",
            )
            calibration_version = _required_text(
                inputs.get("calibration_version"),
                "calibration_version",
            )
            tool_profile = _required_text(
                inputs.get("tool_profile"),
                "tool_profile",
            )
            payload_profile = _required_text(
                inputs.get("payload_profile"),
                "payload_profile",
            )
            external_axis_context = _mapping(
                inputs.get("external_axis_context"),
                "external_axis_context",
            )
            speed_cap = _finite_number(
                inputs.get("speed_cap_percent"),
                "speed_cap_percent",
            )
            if not 1 <= speed_cap <= 20:
                raise ValueError("speed_cap_percent must be in [1, 20]")
            max_session_s = int(self._commissioning_config.get("max_session_s") or 1800)
            expires_in_s = int(inputs.get("expires_in_s") or max_session_s)
            if not 60 <= expires_in_s <= max_session_s:
                raise ValueError(f"expires_in_s must be in [60, {max_session_s}]")
            if point_set_version != self._points.version:
                raise ValueError("point_set_version does not match deployed catalog")
            if calibration_version != self._points.calibration_version:
                raise ValueError("calibration_version does not match deployed catalog")
            status, _ = await self._assert_motion_ready()
            controller_boot_id = _required_text(
                status.get("controller_boot_id"),
                "status.controller_boot_id",
            )
            _require_status_context(
                status,
                calibration_version=calibration_version,
                tool_profile=tool_profile,
                payload_profile=payload_profile,
                external_axis_context=external_axis_context,
            )
            session = CommissioningSession(
                session_id=session_id,
                controller_boot_id=controller_boot_id,
                point_set_version=point_set_version,
                calibration_version=calibration_version,
                tool_profile=tool_profile,
                payload_profile=payload_profile,
                external_axis_context=dict(external_axis_context),
                speed_cap_percent=speed_cap,
                expires_at=time.time() + expires_in_s,
            )
            async with self._session_lock:
                self._drop_expired_sessions()
                if self._sessions:
                    raise ValueError("another commissioning session is active")
                if self._motion_lock.locked():
                    raise ValueError("a physical motion is already in progress")
                self._sessions[session_id] = session
            return self._success(
                "begin_commissioning",
                {
                    "success": True,
                    "session_id": session_id,
                    "expires_at": session.expires_at,
                    "controller_boot_id": controller_boot_id,
                },
            )
        except Exception as exc:  # noqa: BLE001 - fail closed before session creation
            return self._failure(
                "begin_commissioning",
                str(exc),
                physical_state="not_started",
            )

    async def _action_move_to_point(
        self,
        inputs: Mapping[str, Any],
    ) -> RuntimeResult:
        try:
            session_id = _required_text(inputs.get("session_id"), "session_id")
            session = self._require_session(session_id)
            status, _ = await self._assert_motion_ready()
            if status.get("controller_boot_id") != session.controller_boot_id:
                self._sessions.pop(session_id, None)
                raise ValueError("controller restarted; commissioning session expired")
            _require_status_context(
                status,
                calibration_version=session.calibration_version,
                tool_profile=session.tool_profile,
                payload_profile=session.payload_profile,
                external_axis_context=session.external_axis_context,
            )
            point, motion, speed = self._points.resolve(
                _required_text(inputs.get("point_ref"), "point_ref"),
                motion=(
                    None
                    if inputs.get("motion") in (None, "")
                    else str(inputs.get("motion"))
                ),
                speed_percent=(
                    None
                    if inputs.get("speed_percent") is None
                    else _finite_number(inputs.get("speed_percent"), "speed_percent")
                ),
            )
            if speed > session.speed_cap_percent:
                raise ValueError(
                    f"speed_percent exceeds session cap {session.speed_cap_percent}"
                )
            offset = self._validate_offset(
                _optional_mapping(inputs.get("offset"), "offset")
            )
            request = {
                **self._command_envelope(inputs),
                "action": "move_to_point",
                "protocol_version": self._interactions.protocol_version,
                "commissioning_session_id": session_id,
                "point_set_version": session.point_set_version,
                "calibration_version": session.calibration_version,
                "tool_profile": session.tool_profile,
                "payload_profile": session.payload_profile,
                "external_axis_context": dict(session.external_axis_context),
                "point_ref": _required_text(inputs.get("point_ref"), "point_ref"),
                "point": point,
                "motion": motion,
                "speed_percent": speed,
                "offset": offset,
            }
        except Exception as exc:  # noqa: BLE001 - fail closed before point dispatch
            return self._failure(
                "move_to_point",
                str(exc),
                physical_state="not_started",
            )
        return await self._dispatch("move_to_point", request, "execute_point")

    async def _action_end_commissioning(
        self,
        inputs: Mapping[str, Any],
    ) -> RuntimeResult:
        session_id = _required_text(inputs.get("session_id"), "session_id")
        async with self._session_lock:
            existed = self._sessions.pop(session_id, None) is not None
        if not existed:
            return self._failure(
                "end_commissioning",
                f"unknown commissioning session: {session_id}",
                physical_state="not_started",
            )
        return self._success(
            "end_commissioning",
            {"success": True, "session_id": session_id},
        )

    async def _dispatch(
        self,
        action: str,
        request: Mapping[str, Any],
        connection_method: str,
    ) -> RuntimeResult:
        command_id = str(request["command_id"])
        fingerprint = _fingerprint(request)
        existing = self._journal.get(command_id)
        if existing is not None:
            if existing.fingerprint != fingerprint:
                return self._failure(
                    action,
                    "command_id already exists with a different request",
                    physical_state="not_started",
                    outputs={
                        "command_id": command_id,
                        "state": CommandState.REJECTED.value,
                    },
                )
            return self._result_for_record(action, existing)
        if self._journal.unresolved_count() > 0:
            return self._failure(
                action,
                "an UNKNOWN command must be reconciled before new motion",
                physical_state="not_started",
                outputs={
                    "command_id": command_id,
                    "state": CommandState.REJECTED.value,
                },
            )

        try:
            self._journal.accept_sequence(
                str(request["source_boot_id"]),
                int(request["monotonic_sequence"]),
            )
        except (ValueError, StaleSequenceError) as exc:
            return self._failure(
                action,
                str(exc),
                physical_state="not_started",
                outputs={
                    "command_id": command_id,
                    "state": CommandState.REJECTED.value,
                },
            )

        self._journal.create(
            command_id=command_id,
            fingerprint=fingerprint,
            action=action,
            request=request,
        )
        try:
            status, permit = await self._assert_motion_ready()
        except Exception as exc:  # noqa: BLE001 - permit/status adapter boundary
            record = self._journal.update(
                command_id,
                CommandState.REJECTED,
                error=str(exc),
            )
            return self._result_for_record(action, record)

        async with self._motion_lock:
            if action in {"pick", "place"}:
                try:
                    self._require_no_commissioning_session()
                except ValueError as exc:
                    record = self._journal.update(
                        command_id,
                        CommandState.REJECTED,
                        error=str(exc),
                    )
                    return self._result_for_record(action, record)
            self._journal.update(command_id, CommandState.DISPATCHING)
            dispatch_request = {
                **dict(request),
                "pre_dispatch_context": {
                    "controller_boot_id": _required_text(
                        status.get("controller_boot_id"),
                        "status.controller_boot_id",
                    ),
                    "permit_source": str(permit.get("source") or ""),
                },
            }
            try:
                observation = await self._call(connection_method, dispatch_request)
            except asyncio.CancelledError:
                confirmed, detail = await self._attempt_stop(
                    command_id,
                    "Uni-Lab action cancelled",
                )
                self._journal.update(
                    command_id,
                    CommandState.CANCELED if confirmed else CommandState.UNKNOWN,
                    result={"stop": detail},
                    error="" if confirmed else "cancel stop could not be confirmed",
                )
                raise
            except RobotRejected as exc:
                record = self._journal.update(
                    command_id,
                    CommandState.REJECTED,
                    error=str(exc),
                )
                return self._result_for_record(action, record)
            except RobotMotionFailed as exc:
                record = self._journal.update(
                    command_id,
                    CommandState.FAILED,
                    error=str(exc),
                )
                return self._result_for_record(action, record)
            except RobotResultUnknown as exc:
                record = self._journal.update(
                    command_id,
                    CommandState.UNKNOWN,
                    error=str(exc),
                )
                return self._result_for_record(action, record)
            except Exception as exc:  # noqa: BLE001 - post-dispatch means UNKNOWN
                record = self._journal.update(
                    command_id,
                    CommandState.UNKNOWN,
                    error=f"unexpected dispatch error: {exc}",
                )
                return self._result_for_record(action, record)
        record = self._journal.get(command_id)
        assert record is not None
        return self._apply_observation(action, record, observation)

    def _apply_observation(
        self,
        action: str,
        record: CommandRecord,
        observation: Any,
    ) -> RuntimeResult:
        if not isinstance(observation, Mapping):
            updated = self._journal.update(
                record.command_id,
                CommandState.UNKNOWN,
                error="connection returned a non-object observation",
            )
            return self._result_for_record(action, updated)
        raw_state = _enum_text(observation.get("state"))
        try:
            state = CommandState(raw_state)
        except ValueError:
            state = CommandState.UNKNOWN
            observation = {
                **dict(observation),
                "connection_state": raw_state,
            }
        if state in {CommandState.VALIDATED, CommandState.DISPATCHING}:
            state = CommandState.UNKNOWN
        if state == CommandState.SUCCEEDED:
            try:
                _validate_completion_witness(observation.get("witness"))
            except Exception as exc:  # noqa: BLE001 - invalid witness means UNKNOWN
                state = CommandState.UNKNOWN
                observation = {
                    **dict(observation),
                    "witness_error": str(exc),
                }
        updated = self._journal.update(
            record.command_id,
            state,
            result=dict(observation),
            error=(
                ""
                if state == CommandState.SUCCEEDED
                else str(observation.get("message") or record.error)
            ),
        )
        return self._result_for_record(action, updated)

    async def _assert_motion_ready(
        self,
    ) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
        status = dict(await self._call("read_status"))
        if _enum_text(status.get("connection")) != "ONLINE":
            raise ValueError("robot connection is not ONLINE")
        allowed_authority = {
            str(value)
            for value in self._permit_config.get(
                "allowed_control_authority",
                ["REMOTE_AUTO"],
            )
        }
        authority = _enum_text(status.get("control_authority"))
        if authority not in allowed_authority:
            raise ValueError(
                f"control authority {authority or 'UNKNOWN'} is not allowed"
            )
        allowed_safety = {
            str(value)
            for value in self._permit_config.get(
                "allowed_safety_state",
                ["NORMAL"],
            )
        }
        safety = _enum_text(status.get("safety_state"))
        if safety not in allowed_safety:
            raise ValueError(f"safety state {safety or 'UNKNOWN'} is not allowed")
        operational = _enum_text(status.get("operational_state"))
        if operational != "IDLE":
            raise ValueError(
                f"operational state {operational or 'UNKNOWN'} is not allowed"
            )
        permit = await self._read_permit(
            required=bool(self._permit_config.get("required", True))
        )
        if permit and permit.get("granted") is not True:
            raise ValueError("motion permit is not granted")
        return status, permit

    async def _read_permit(self, *, required: bool) -> Mapping[str, Any]:
        method = getattr(self._connection, "read_motion_permit", None)
        if method is None or not callable(method):
            if required:
                raise ValueError("Robot Connection has no motion permit source")
            return {}
        permit = dict(await self._call("read_motion_permit"))
        observed_at = _finite_number(permit.get("observed_at"), "permit.observed_at")
        max_age_s = _finite_number(
            self._permit_config.get("max_age_s", 0.5),
            "permit.max_age_s",
        )
        age = time.time() - observed_at
        if age < -0.1 or age > max_age_s:
            raise ValueError(
                f"motion permit is stale: age={age:.3f}s, max={max_age_s:.3f}s"
            )
        return permit

    async def _attempt_stop(
        self,
        command_id: str,
        reason: str,
    ) -> tuple[bool, Mapping[str, Any]]:
        try:
            result = await self._call("controlled_stop", command_id, reason)
        except Exception as exc:  # noqa: BLE001 - stopping needs a confirmed result
            return False, {"confirmed": False, "error": str(exc)}
        if isinstance(result, Mapping):
            detail = dict(result)
            return detail.get("confirmed") is True, detail
        return bool(result), {"confirmed": bool(result)}

    async def _call(self, method_name: str, *args: Any) -> Any:
        method = getattr(self._connection, method_name, None)
        if method is None or not callable(method):
            raise TypeError(f"Robot Connection does not implement {method_name}")
        if inspect.iscoroutinefunction(method):
            return await method(*args)
        result = await asyncio.to_thread(method, *args)
        if inspect.isawaitable(result):
            return await result
        return result

    def _command_envelope(self, inputs: Mapping[str, Any]) -> dict[str, Any]:
        command_id = _required_text(inputs.get("command_id"), "command_id")
        if len(command_id) > 128:
            raise ValueError("command_id must be at most 128 characters")
        source_boot_id = _required_text(
            inputs.get("source_boot_id"),
            "source_boot_id",
        )
        sequence = inputs.get("monotonic_sequence")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            raise ValueError("monotonic_sequence must be a positive integer")
        return {
            "command_id": command_id,
            "source_boot_id": source_boot_id,
            "monotonic_sequence": sequence,
            "requested_at": time.time(),
        }

    def _require_no_commissioning_session(self) -> None:
        self._drop_expired_sessions()
        if self._sessions:
            raise ValueError("production motion is blocked by commissioning session")

    def _require_session(self, session_id: str) -> CommissioningSession:
        self._drop_expired_sessions()
        session = self._sessions.get(session_id)
        if session is None:
            raise ValueError(f"commissioning session is not active: {session_id}")
        return session

    def _drop_expired_sessions(self) -> None:
        now = time.time()
        expired = [
            session_id
            for session_id, session in self._sessions.items()
            if session.expires_at <= now
        ]
        for session_id in expired:
            self._sessions.pop(session_id, None)

    def _validate_offset(
        self,
        offset: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        if not offset:
            return {
                "frame": "site",
                "translation_mm": [0.0, 0.0, 0.0],
                "rotation_deg": [0.0, 0.0, 0.0],
            }
        unknown = sorted(set(offset) - {"frame", "translation_mm", "rotation_deg"})
        if unknown:
            raise ValueError(f"unsupported offset field: {unknown[0]}")
        frame = str(offset.get("frame") or "site")
        if frame not in {"site", "tool", "base"}:
            raise ValueError("offset.frame must be site/tool/base")
        translation = _vector3(offset.get("translation_mm"), "translation_mm")
        rotation = _vector3(offset.get("rotation_deg"), "rotation_deg")
        translation_cap = float(
            self._commissioning_config.get("max_offset_translation_mm", 5.0)
        )
        rotation_cap = float(
            self._commissioning_config.get("max_offset_rotation_deg", 2.0)
        )
        if any(abs(value) > translation_cap for value in translation):
            raise ValueError(
                f"offset translation exceeds {translation_cap} mm per axis"
            )
        if any(abs(value) > rotation_cap for value in rotation):
            raise ValueError(f"offset rotation exceeds {rotation_cap} deg per axis")
        return {
            "frame": frame,
            "translation_mm": translation,
            "rotation_deg": rotation,
        }

    def _result_for_record(
        self,
        action: str,
        record: CommandRecord,
    ) -> RuntimeResult:
        outputs = {
            "success": record.state == CommandState.SUCCEEDED,
            "state": record.state.value,
            "command_id": record.command_id,
            **dict(record.result),
        }
        if record.state == CommandState.SUCCEEDED:
            return self._success(action, outputs)
        if record.state == CommandState.CANCELED:
            return RuntimeResult(
                action_ref=f"robot.{action}",
                terminal="cancelled",
                outputs=outputs,
                error=record.error,
                physical_state="confirmed_stopped",
            )
        if record.state == CommandState.UNKNOWN:
            return self._failure(
                action,
                record.error or "command result is UNKNOWN",
                physical_state="unknown",
                reconcile_required=True,
                outputs=outputs,
            )
        if record.state in {CommandState.VALIDATED, CommandState.DISPATCHING}:
            return self._failure(
                action,
                "command is already in flight; do not redispatch",
                physical_state="unknown",
                reconcile_required=True,
                outputs=outputs,
            )
        return self._failure(
            action,
            record.error or record.state.value,
            physical_state=(
                "not_started" if record.state == CommandState.REJECTED else "confirmed"
            ),
            outputs=outputs,
        )

    @staticmethod
    def _success(action: str, outputs: Mapping[str, Any]) -> RuntimeResult:
        return RuntimeResult(
            action_ref=f"robot.{action}",
            terminal="succeeded",
            outputs=dict(outputs),
        )

    @staticmethod
    def _failure(
        action: str,
        error: str,
        *,
        physical_state: str,
        reconcile_required: bool = False,
        outputs: Mapping[str, Any] | None = None,
    ) -> RuntimeResult:
        values = {
            "success": False,
            **dict(outputs or {}),
        }
        return RuntimeResult(
            action_ref=f"robot.{action}",
            terminal="failed",
            outputs=values,
            error=error,
            physical_state=physical_state,
            reconcile_required=reconcile_required,
        )


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be an object")
    return value


def _optional_mapping(value: Any, name: str) -> Mapping[str, Any] | None:
    if value is None:
        return None
    return _mapping(value, name)


def _required_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise TypeError(f"{name} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number")
    return number


def _vector3(value: Any, name: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"{name} must contain exactly three numbers")
    return [_finite_number(item, name) for item in value]


def _enum_text(value: Any) -> str:
    if isinstance(value, Enum):
        value = value.value
    return str(value or "").strip().upper()


def _fingerprint(request: Mapping[str, Any]) -> str:
    stable = dict(request)
    stable.pop("requested_at", None)
    payload = json.dumps(
        stable,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _resource_snapshot(resource: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": resource_id(resource),
        "resource_type": resource_type_of(resource),
        "revision": resource.get("revision"),
    }


def _validate_completion_witness(value: Any) -> None:
    witness = _mapping(value, "witness")
    for field in ("controller_command_id", "observed_at", "final_state"):
        if field not in witness or witness[field] in (None, ""):
            raise ValueError(f"completion witness is missing {field}")
    _finite_number(witness["observed_at"], "witness.observed_at")


def _require_status_context(
    status: Mapping[str, Any],
    *,
    calibration_version: str,
    tool_profile: str,
    payload_profile: str,
    external_axis_context: Mapping[str, Any],
) -> None:
    expected = {
        "calibration_version": calibration_version,
        "tool_profile": tool_profile,
        "payload_profile": payload_profile,
    }
    for field, value in expected.items():
        if status.get(field) != value:
            raise ValueError(f"status {field} does not match commissioning context")
    observed_axis = status.get("external_axis_context")
    if not isinstance(observed_axis, Mapping) or dict(observed_axis) != dict(
        external_axis_context
    ):
        raise ValueError("external axis context does not match")


def _public_record(record: CommandRecord) -> dict[str, Any]:
    return {
        "command_id": record.command_id,
        "action": record.action,
        "state": record.state.value,
        "result": dict(record.result),
        "error": record.error,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }
