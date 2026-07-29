#!/usr/bin/env python3
"""Fail-closed reader for critical-review qualification and live panel roles."""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence, cast

try:
    import yaml
except ModuleNotFoundError:
    venv_python = Path(__file__).resolve().parent / ".venv/bin/python"
    if (
        __name__ != "__main__"
        or not venv_python.is_file()
        or Path(sys.executable).resolve() == venv_python.resolve()
    ):
        raise
    os.execv(venv_python, [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]])

SCHEMA_VERSION = 3
DEFAULT_QUALIFICATION = Path.home() / ".omp/agent/skills/critical-review/qualification.yml"
LIVE_GROUPS = {
    "initialCritics": ("primary_critic", True),
    "targetedRefuters": ("targeted_refuter", True),
    "evaluationOnly": ("evaluation_only", False),
    "disabled": ("disabled", False),
}


class QualificationError(ValueError):
    """The qualification record cannot safely drive dispatch or evaluation."""


@dataclass(frozen=True)
class LiveReviewer:
    family: str
    agent: str
    lens: str


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise QualificationError(f"{field} must be a mapping")
    return cast(Mapping[str, object], value)


def _names(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise QualificationError(f"{field} must be a list of family names")
    sequence = cast(Sequence[object], value)
    names: list[str] = []
    for item in sequence:
        if not isinstance(item, str) or not item.strip():
            raise QualificationError(f"{field} contains a non-name entry")
        names.append(item.strip())
    if len(names) != len(set(names)):
        raise QualificationError(f"{field} contains duplicate families")
    return tuple(names)


def validate_qualification(document: object) -> Mapping[str, object]:
    root = _mapping(document, "qualification")
    if root.get("schemaVersion") != SCHEMA_VERSION:
        raise QualificationError(
            f"qualification schemaVersion must be {SCHEMA_VERSION}, got {root.get('schemaVersion')!r}"
        )

    live = _mapping(root.get("liveDispatch"), "liveDispatch")
    panel_id = live.get("panelId")
    if not isinstance(panel_id, str) or not panel_id.strip():
        raise QualificationError("liveDispatch.panelId must be a non-empty string")

    reviewers = _mapping(root.get("reviewers"), "reviewers")
    memberships: dict[str, str] = {}
    groups: dict[str, tuple[str, ...]] = {}
    for group, (role, dispatch_enabled) in LIVE_GROUPS.items():
        families = _names(live.get(group), f"liveDispatch.{group}")
        groups[group] = families
        for family in families:
            if family in memberships:
                raise QualificationError(
                    f"reviewer {family!r} appears in both {memberships[family]} and {group}"
                )
            memberships[family] = group
            entry = _mapping(reviewers.get(family), f"reviewers.{family}")
            if entry.get("dispatchRole") != role:
                raise QualificationError(
                    f"reviewers.{family}.dispatchRole must be {role!r} for {group}"
                )
            if entry.get("dispatchEnabled") is not dispatch_enabled:
                raise QualificationError(
                    f"reviewers.{family}.dispatchEnabled disagrees with {group}"
                )
            if not isinstance(entry.get("evaluationEnabled"), bool):
                raise QualificationError(
                    f"reviewers.{family}.evaluationEnabled must be boolean"
                )

            earned = dispatch_enabled or entry.get("evaluationEnabled") is True
            if earned:
                required = (
                    ("providerCanary", "passed"),
                    ("schemaValid", True),
                    ("readOnlyBoundary", "passed"),
                )
                missing = [name for name, expected in required if entry.get(name) != expected]
                if missing:
                    raise QualificationError(
                        f"reviewers.{family} enabled without proven {', '.join(missing)}"
                    )
                for name in ("agent", "model"):
                    value = entry.get(name)
                    if not isinstance(value, str) or not value.strip():
                        raise QualificationError(f"reviewers.{family}.{name} is missing")

    reviewer_names = set(reviewers.keys())
    assigned_names = set(memberships)
    if reviewer_names != assigned_names:
        missing = sorted(reviewer_names - assigned_names)
        unknown = sorted(assigned_names - reviewer_names)
        raise QualificationError(
            f"liveDispatch membership mismatch: unassigned={missing}, unknown={unknown}"
        )
    return root


def load_qualification(path: Path = DEFAULT_QUALIFICATION) -> Mapping[str, object]:
    if not path.is_file():
        raise QualificationError(f"qualification file is not readable: {path}")
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise QualificationError(f"qualification file cannot be parsed: {exc}") from exc
    return validate_qualification(document)


def reviewers(document: Mapping[str, object]) -> Mapping[str, object]:
    return _mapping(document.get("reviewers"), "reviewers")


def live_reviewers(document: Mapping[str, object], mode: str) -> tuple[LiveReviewer, ...]:
    group = {"initial": "initialCritics", "targeted-refuter": "targetedRefuters"}.get(mode)
    if group is None:
        raise QualificationError(f"unsupported live review mode: {mode}")
    live = _mapping(document.get("liveDispatch"), "liveDispatch")
    entries = reviewers(document)
    result: list[LiveReviewer] = []
    for family in _names(live.get(group), f"liveDispatch.{group}"):
        entry = _mapping(entries.get(family), f"reviewers.{family}")
        lens_value = entry.get("lens")
        result.append(
            LiveReviewer(
                family=family,
                agent=cast(str, entry["agent"]),
                lens=lens_value if isinstance(lens_value, str) else "",
            )
        )
    return tuple(result)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("initial", "targeted-refuter"))
    parser.add_argument("--qualification", type=Path, default=DEFAULT_QUALIFICATION)
    args = parser.parse_args(argv)
    try:
        selected = live_reviewers(load_qualification(args.qualification), args.mode)
    except QualificationError as exc:
        parser.error(str(exc))
    print(json.dumps([reviewer.__dict__ for reviewer in selected], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
