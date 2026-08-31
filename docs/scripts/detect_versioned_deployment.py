#!/usr/bin/env python3
"""Detect whether the production documentation tree has completed the Mike cutover."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def deployment_state(deployment: Path) -> tuple[bool, str]:
    inventory_path = deployment / "versions.json"
    if not inventory_path.exists():
        return False, "master"

    inventory = json.loads(inventory_path.read_text())
    current = next(
        (entry for entry in inventory if isinstance(entry, dict) and entry.get("version") == "current"), None
    )
    if not isinstance(inventory, list) or current is None:
        raise RuntimeError("production versions.json does not contain Current")
    renderer = current.get("properties", {}).get("renderer")
    return True, "master" if renderer == "zensical" else "versioned-docs"


def is_versioned_deployment(deployment: Path) -> bool:
    return deployment_state(deployment)[0]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--deployment", type=Path, required=True)
    args = parser.parse_args()
    enabled, target = deployment_state(args.deployment)
    print(f"enabled={str(enabled).lower()}")
    print(f"target={target}")


if __name__ == "__main__":
    main()
