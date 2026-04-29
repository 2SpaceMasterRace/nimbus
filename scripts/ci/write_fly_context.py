#!/usr/bin/env python3
"""Emit shell exports for the Fly app target and rollback image."""

from __future__ import annotations

import argparse
import json
import shlex
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

_SUCCESS_STATUSES = {"complete", "completed", "success", "successful", "succeeded"}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="Path to fly.toml")
    parser.add_argument(
        "--releases-json",
        help="Optional path to fly releases --json --image output",
    )
    return parser.parse_args()


def _load_fly_app(config_path: Path) -> str:
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    app = config.get("app")
    if isinstance(app, str) and app.strip():
        return app.strip()
    msg = f"{config_path} does not define a non-empty Fly app name"
    raise ValueError(msg)


def _iter_dicts(node: object) -> Iterator[dict[str, object]]:
    if isinstance(node, dict):
        yield {str(key): value for key, value in node.items()}
        for value in node.values():
            yield from _iter_dicts(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_dicts(item)


def _string_field(
    candidate: dict[str, object], *, names: tuple[str, ...]
) -> str | None:
    lowered = {name.lower() for name in names}
    for key, value in candidate.items():
        if key.lower() in lowered and isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _image_field(candidate: dict[str, object]) -> str | None:
    for key, value in candidate.items():
        if "image" in key.lower() and isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _version_field(candidate: dict[str, object]) -> int | None:
    version = _string_field(candidate, names=("version",))
    if version is None:
        return None
    try:
        return int(version)
    except ValueError:
        return None


def _select_previous_release_image(releases_json_path: Path) -> str:
    payload = json.loads(releases_json_path.read_text(encoding="utf-8"))
    candidates: list[tuple[int | None, str]] = []
    for candidate in _iter_dicts(payload):
        status = _string_field(candidate, names=("status",))
        image = _image_field(candidate)
        if status is None or image is None:
            continue
        if status.lower() in _SUCCESS_STATUSES:
            candidates.append((_version_field(candidate), image))
    if not candidates:
        return ""
    if any(version is not None for version, _image in candidates):
        versioned: list[tuple[int, str]] = [
            (version, image) for version, image in candidates if version is not None
        ]
        versioned.sort(key=lambda item: item[0], reverse=True)
        return versioned[0][1]
    return candidates[0][1]


def _emit_export(name: str, value: str) -> None:
    print(f"export {name}={shlex.quote(value)}")


def main() -> int:
    args = _parse_args()
    app = _load_fly_app(Path(args.config))
    _emit_export("FLY_APP", app)
    _emit_export("FLY_BASE_URL", f"https://{app}.fly.dev")
    previous_image = ""
    if args.releases_json:
        previous_image = _select_previous_release_image(Path(args.releases_json))
    _emit_export("PREVIOUS_RELEASE_IMAGE", previous_image)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
