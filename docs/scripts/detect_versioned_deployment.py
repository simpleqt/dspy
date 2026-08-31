#!/usr/bin/env python3
"""Detect whether the production documentation tree has completed the Mike cutover."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def is_versioned_deployment(deployment: Path) -> bool:
    inventory_path = deployment / "versions.json"
    if not inventory_path.exists():
        return False

    inventory = json.loads(inventory_path.read_text())
    if not isinstance(inventory, list) or not any(entry.get("version") == "current" for entry in inventory):
        raise RuntimeError("production versions.json does not contain Current")
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--deployment", type=Path, required=True)
    args = parser.parse_args()
    print(f"enabled={str(is_versioned_deployment(args.deployment)).lower()}")


if __name__ == "__main__":
    main()
