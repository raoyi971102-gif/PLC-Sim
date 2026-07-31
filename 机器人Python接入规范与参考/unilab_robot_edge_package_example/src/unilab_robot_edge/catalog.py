from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

import yaml


def load_catalog_document(
    reference: Mapping[str, Any], *, name: str
) -> Mapping[str, Any]:
    if "path" in reference:
        path = Path(str(reference["path"])).expanduser()
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    else:
        package = str(reference.get("package") or "")
        resource = str(reference.get("resource") or "")
        if not package or not resource:
            raise ValueError(f"{name} requires package/resource or path")
        text = resources.files(package).joinpath(resource).read_text(encoding="utf-8")
        document = yaml.safe_load(text)
    if not isinstance(document, Mapping):
        raise TypeError(f"{name} must contain an object")
    return document


@dataclass(frozen=True)
class InteractionBinding:
    key: str
    operation: str
    mount_resource_id: str
    site: str
    allowed_resource_types: tuple[str, ...]
    skill_id: str
    program_version: str
    point_set_version: str
    tool_profile: str
    parameter_rules: Mapping[str, Mapping[str, Any]]

    def to_request_fields(self) -> dict[str, Any]:
        return {
            "interaction_profile": self.key,
            "operation": self.operation,
            "mount_resource_id": self.mount_resource_id,
            "site": self.site,
            "skill_id": self.skill_id,
            "program_version": self.program_version,
            "point_set_version": self.point_set_version,
            "tool_profile": self.tool_profile,
        }


class InteractionCatalog:
    def __init__(self, document: Mapping[str, Any]) -> None:
        if document.get("schema_version") != 1:
            raise ValueError("interaction catalog schema_version must be 1")
        self.protocol_version = _required_text(
            document.get("protocol_version"), "protocol_version"
        )
        raw_payloads = document.get("payload_profiles")
        raw_bindings = document.get("bindings")
        if not isinstance(raw_payloads, Mapping) or not isinstance(
            raw_bindings, Mapping
        ):
            raise TypeError("interaction catalog requires payload_profiles/bindings")
        self._payload_profiles = {
            str(resource_type): _required_text(
                raw.get("profile_id") if isinstance(raw, Mapping) else None,
                f"payload_profiles.{resource_type}.profile_id",
            )
            for resource_type, raw in raw_payloads.items()
        }
        self._bindings: dict[str, InteractionBinding] = {}
        for key, raw in raw_bindings.items():
            if not isinstance(raw, Mapping):
                raise TypeError(f"binding {key} must be an object")
            allowed = raw.get("allowed_resource_types")
            if not isinstance(allowed, list) or not allowed:
                raise ValueError(f"binding {key} requires allowed_resource_types")
            rules = raw.get("parameter_rules") or {}
            if not isinstance(rules, Mapping):
                raise TypeError(f"binding {key} parameter_rules must be an object")
            binding = InteractionBinding(
                key=str(key),
                operation=_required_text(raw.get("operation"), f"{key}.operation"),
                mount_resource_id=_required_text(
                    raw.get("mount_resource_id"), f"{key}.mount_resource_id"
                ),
                site=_required_text(raw.get("site"), f"{key}.site"),
                allowed_resource_types=tuple(str(item) for item in allowed),
                skill_id=_required_text(raw.get("skill_id"), f"{key}.skill_id"),
                program_version=_required_text(
                    raw.get("program_version"), f"{key}.program_version"
                ),
                point_set_version=_required_text(
                    raw.get("point_set_version"), f"{key}.point_set_version"
                ),
                tool_profile=_required_text(
                    raw.get("tool_profile"), f"{key}.tool_profile"
                ),
                parameter_rules={
                    str(name): dict(rule)
                    for name, rule in rules.items()
                    if isinstance(rule, Mapping)
                },
            )
            expected_key = (
                f"{binding.mount_resource_id}/{binding.site}/{binding.operation}"
            )
            if binding.key != expected_key:
                raise ValueError(
                    f"binding key {binding.key!r} must equal {expected_key!r}"
                )
            self._bindings[binding.key] = binding

    @classmethod
    def from_reference(cls, reference: Mapping[str, Any]) -> InteractionCatalog:
        return cls(load_catalog_document(reference, name="interaction_catalog"))

    def resolve_pick(
        self,
        resource: Mapping[str, Any],
        parameters: Mapping[str, Any] | None,
    ) -> tuple[InteractionBinding, str, dict[str, Any]]:
        mount_resource_id, site = resource_location(resource)
        return self._resolve(
            mount_resource_id,
            site,
            "pick",
            resource,
            parameters,
        )

    def resolve_place(
        self,
        resource: Mapping[str, Any],
        target_mount_resource: Mapping[str, Any],
        target_site: str,
        parameters: Mapping[str, Any] | None,
    ) -> tuple[InteractionBinding, str, dict[str, Any]]:
        mount_resource_id = resource_id(
            target_mount_resource, name="target_mount_resource"
        )
        return self._resolve(
            mount_resource_id,
            _required_text(target_site, "target_site"),
            "place",
            resource,
            parameters,
        )

    def _resolve(
        self,
        mount_resource_id: str,
        site: str,
        operation: str,
        resource: Mapping[str, Any],
        parameters: Mapping[str, Any] | None,
    ) -> tuple[InteractionBinding, str, dict[str, Any]]:
        key = f"{mount_resource_id}/{site}/{operation}"
        binding = self._bindings.get(key)
        if binding is None:
            raise ValueError(f"no Interaction Profile for {key}")
        resource_type = resource_type_of(resource)
        if resource_type not in binding.allowed_resource_types:
            raise ValueError(
                f"resource type {resource_type!r} is not allowed by {binding.key}"
            )
        payload_profile = self._payload_profiles.get(resource_type)
        if payload_profile is None:
            raise ValueError(f"no payload profile for resource type {resource_type}")
        return (
            binding,
            payload_profile,
            _validate_parameters(
                parameters or {},
                binding.parameter_rules,
            ),
        )


class PointCatalog:
    def __init__(self, document: Mapping[str, Any]) -> None:
        if document.get("schema_version") != 1:
            raise ValueError("point catalog schema_version must be 1")
        raw_meta = document.get("point_set")
        raw_points = document.get("points")
        if not isinstance(raw_meta, Mapping) or not isinstance(raw_points, Mapping):
            raise TypeError("point catalog requires point_set/points")
        self.metadata = dict(raw_meta)
        self.version = _required_text(raw_meta.get("version"), "point_set.version")
        self.calibration_version = _required_text(
            raw_meta.get("calibration_version"),
            "point_set.calibration_version",
        )
        self.approved = raw_meta.get("approved") is True
        self._points = {
            str(point_ref): dict(point)
            for point_ref, point in raw_points.items()
            if isinstance(point, Mapping)
        }
        if not self._points:
            raise ValueError("point catalog must contain points")

    @classmethod
    def from_reference(cls, reference: Mapping[str, Any]) -> PointCatalog:
        return cls(load_catalog_document(reference, name="point_catalog"))

    def resolve(
        self,
        point_ref: str,
        *,
        motion: str | None,
        speed_percent: float | None,
    ) -> tuple[dict[str, Any], str, float]:
        reference = _required_text(point_ref, "point_ref")
        raw = self._points.get(reference)
        if raw is None:
            raise ValueError(f"unknown point_ref: {reference}")
        if raw.get("validation_status") != "validated":
            raise ValueError(f"point {reference} is not validated")
        allowed = raw.get("allowed_motion")
        if not isinstance(allowed, list) or not allowed:
            raise ValueError(f"point {reference} has no allowed_motion")
        selected_motion = str(motion or allowed[0])
        if selected_motion not in {str(value) for value in allowed}:
            raise ValueError(
                f"motion {selected_motion} is not allowed for point {reference}"
            )
        point_cap = _finite_number(
            raw.get("max_speed_percent"),
            f"{reference}.max_speed_percent",
        )
        selected_speed = (
            point_cap
            if speed_percent is None
            else _finite_number(
                speed_percent,
                "speed_percent",
            )
        )
        if not 0 < selected_speed <= point_cap:
            raise ValueError(
                f"speed_percent must be in (0, {point_cap}] for {reference}"
            )
        return dict(raw), selected_motion, selected_speed


def resource_id(resource: Mapping[str, Any], *, name: str = "resource") -> str:
    if not isinstance(resource, Mapping):
        raise TypeError(f"{name} must be an object")
    return _required_text(
        resource.get("id") or resource.get("unilabos_uuid"),
        f"{name}.id",
    )


def resource_type_of(resource: Mapping[str, Any]) -> str:
    if not isinstance(resource, Mapping):
        raise TypeError("resource must be an object")
    return _required_text(
        resource.get("resource_type") or resource.get("type"),
        "resource.resource_type",
    )


def resource_location(resource: Mapping[str, Any]) -> tuple[str, str]:
    if not isinstance(resource, Mapping):
        raise TypeError("resource must be an object")
    location = resource.get("location")
    if isinstance(location, Mapping):
        parent_value = location.get("parent_id") or location.get("mount_resource_id")
        site_value = location.get("site")
    else:
        parent = resource.get("parent")
        parent_value = (
            parent.get("id")
            if isinstance(parent, Mapping)
            else resource.get("parent_id")
        )
        site_value = resource.get("site")
    return (
        _required_text(parent_value, "resource.location.parent_id"),
        _required_text(site_value, "resource.location.site"),
    )


def _validate_parameters(
    values: Mapping[str, Any],
    rules: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    if not isinstance(values, Mapping):
        raise TypeError("parameters must be an object")
    unknown = sorted(set(values) - set(rules))
    if unknown:
        raise ValueError(f"unsupported bounded parameter: {unknown[0]}")
    result: dict[str, Any] = {}
    for name, value in values.items():
        rule = rules[name]
        value_type = str(rule.get("type") or "")
        if value_type == "number":
            number = _finite_number(value, name)
            minimum = float(rule.get("minimum", -math.inf))
            maximum = float(rule.get("maximum", math.inf))
            if not minimum <= number <= maximum:
                raise ValueError(f"{name} must be in [{minimum}, {maximum}]")
            result[name] = number
        elif value_type == "string":
            text = _required_text(value, name)
            allowed = rule.get("enum")
            if isinstance(allowed, list) and text not in {
                str(item) for item in allowed
            }:
                raise ValueError(f"{name} is not in the allowed enum")
            result[name] = text
        else:
            raise ValueError(f"unsupported parameter rule type for {name}")
    return result


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
