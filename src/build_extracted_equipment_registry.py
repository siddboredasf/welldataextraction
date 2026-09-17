#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
from pathlib import Path


def load_equipment_ids(path: Path | None) -> set[str]:
    if path is None or not path.is_file():
        return set()

    with path.open(encoding="utf-8-sig", newline="") as handle:
        return {
            row.get("equipment_id", "").strip()
            for row in csv.DictReader(handle)
            if row.get("equipment_id", "").strip()
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--auto",
        type=Path,
        help="Optional metadata auto-accept decision CSV.",
    )
    parser.add_argument(
        "--queue",
        type=Path,
        help="Optional grouped metadata review queue CSV.",
    )
    args = parser.parse_args()

    if not args.input.is_file():
        raise SystemExit(f"Input registry not found: {args.input}")

    with args.input.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        fields = list(reader.fieldnames or [])

    for field in (
        "review_status",
        "registry_status",
        "validation_status",
    ):
        if field not in fields:
            fields.append(field)

    auto_accepted_equipment_ids = load_equipment_ids(args.auto)
    queued_equipment_ids = load_equipment_ids(args.queue)

    for row in rows:
        equipment_id = row.get("equipment_id", "").strip()

        row["registry_status"] = "extracted"

        if equipment_id in queued_equipment_ids:
            row["review_status"] = "field_level_review_required"
            row["validation_status"] = "review_required"
        elif equipment_id in auto_accepted_equipment_ids:
            row["review_status"] = "auto_accepted"
            row["validation_status"] = "auto_accepted"
        else:
            row["validation_status"] = "review_required"

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Input rows: {len(rows)}")
    print(
        "Auto-accepted equipment: "
        f"{sum(row.get('review_status') == 'auto_accepted' for row in rows)}"
    )
    print(
        "Equipment requiring review: "
        f"{sum(row.get('review_status') == 'field_level_review_required' for row in rows)}"
    )
    print(f"Output: {args.output}")


if __name__ == "__main__":
    main()
