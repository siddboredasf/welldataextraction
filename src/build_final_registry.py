#!/usr/bin/env python3

import argparse
import csv
import re
from collections import defaultdict
from pathlib import Path


def clean(value):
    return " ".join(str(value or "").split()).strip()


def norm(value):
    value = clean(value).casefold()
    return re.sub(r"[^a-z0-9]+", "", value)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Build the final wide registry from resolved metadata."
    )
    parser.add_argument("--provisional", required=True, type=Path)
    parser.add_argument("--resolved", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def read_csv(path):
    if not path.is_file():
        raise SystemExit(f"File not found: {path}")

    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def main():
    args = parse_args()

    provisional_rows = read_csv(args.provisional)
    resolved_rows = read_csv(args.resolved)

    equipment = {}
    identity_to_id = {}

    for index, row in enumerate(provisional_rows, start=1):
        tag = clean(row.get("Equipment Tag"))
        serial = clean(row.get("Serial Number"))

        equipment_id = f"EQ-{index:04d}"

        equipment[equipment_id] = {
            "equipment_id": equipment_id,
            "equipment_tag": tag,
            "serial_number": serial,
            "metadata_status": "identity_only",
            "review_status": clean(row.get("Review Status")),
            "identity_source_status": (
                "missing_serial"
                if not serial
                else "provisional_only"
            ),
        }

        identity_to_id[(norm(tag), norm(serial))] = equipment_id

    resolved_by_equipment = defaultdict(dict)
    dynamic_fields = set()

    for row in resolved_rows:
        status = clean(row.get("resolution_status"))
        value = clean(row.get("resolved_value"))
        field_name = clean(row.get("field_name"))

        if status not in {
            "resolved",
            "resolved_with_reference",
        }:
            continue

        if not field_name or not value:
            continue

        key = identity_to_id.get((
            norm(row.get("equipment_tag")),
            norm(row.get("serial_number")),
        ))

        if not key:
            continue

        if field_name == "serial_number":
            equipment[key]["identity_source_status"] = (
                "evidence_linked"
            )

        resolved_by_equipment[key][field_name] = value
        dynamic_fields.add(field_name)

        if status == "resolved":
            equipment[key]["metadata_status"] = (
                "metadata_resolved"
            )
        elif equipment[key]["metadata_status"] != "metadata_resolved":
            equipment[key]["metadata_status"] = (
                "metadata_resolved_with_reference"
            )

    base_fields = [
        "equipment_id",
        "equipment_tag",
        "serial_number",
        "metadata_status",
        "review_status",
        "identity_source_status",
    ]

    output_fields = base_fields + sorted(
        field
        for field in dynamic_fields
        if field not in base_fields
    )

    output_rows = []

    for equipment_id in sorted(equipment):
        row = dict(equipment[equipment_id])
        row.update(resolved_by_equipment.get(equipment_id, {}))
        output_rows.append({
            field: row.get(field, "")
            for field in output_fields
        })

    args.output.parent.mkdir(parents=True, exist_ok=True)

    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=output_fields,
        )
        writer.writeheader()
        writer.writerows(output_rows)

    print(f"Provisional equipment rows: {len(provisional_rows)}")
    print(f"Resolved metadata rows: {len(resolved_rows)}")
    print(f"Final equipment rows: {len(output_rows)}")
    print(f"Dynamic metadata fields: {len(dynamic_fields)}")
    print(f"Output: {args.output}")


if __name__ == "__main__":
    main()
