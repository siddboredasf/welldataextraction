#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import re
from collections import defaultdict
from pathlib import Path


def clean(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def normalize(value: str) -> str:
    value = clean(value).upper()
    value = value.replace("—", "-")
    value = value.replace("–", "-")
    value = re.sub(r"[^A-Z0-9]+", " ", value)
    return " ".join(value.split())


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--provisional", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    evidence = read_rows(args.evidence)
    provisional = read_rows(args.provisional)

    provisional_by_tag = {}

    for row in provisional:
        tag = clean(
            row.get("Equipment Tag")
            or row.get("equipment_tag")
        ).upper()

        if tag:
            provisional_by_tag[tag] = row

    output = []

    for row in evidence:
        tag = clean(row.get("equipment_tag")).upper()
        field = clean(row.get("field_name"))
        value = clean(row.get("field_value"))

        old = provisional_by_tag.get(tag, {})

        provisional_field_map = {
            "serial_number": "Serial Number",
            "model": "Model",
            "equipment_description": "Equipment Description",
            "range_or_rating": "Range / Rating",
            "set_point": "Set Point",
            "declaration_reference": "Declaration Reference",
            "order_acceptance_number": (
                "Order Acceptance Number"
            ),
            "purchase_order": "Purchase Order",
            "test_certificate_reference": (
                "Test Certificate Reference"
            ),
        }

        provisional_value = clean(
            old.get(provisional_field_map.get(field, ""), "")
        )

        evidence_norm = normalize(value)
        provisional_norm = normalize(provisional_value)

        if not provisional_value:
            status = "not_in_provisional"
        elif evidence_norm == provisional_norm:
            status = "matches_provisional"
        else:
            status = "conflicts_with_provisional"

        result = dict(row)
        result["provisional_value"] = provisional_value
        result["provisional_comparison"] = status

        if status == "matches_provisional":
            result["recommended_action"] = (
                "Retain source evidence and consider promotion "
                "after confirming linkage scope."
            )
        elif status == "not_in_provisional":
            result["recommended_action"] = (
                "Inspect source evidence; no provisional comparison "
                "available."
            )
        else:
            result["recommended_action"] = (
                "Do not promote automatically; resolve source "
                "and linkage conflict."
            )

        output.append(result)

    if not output:
        raise SystemExit("No evidence rows found.")

    fields = list(output[0].keys())

    args.output.parent.mkdir(parents=True, exist_ok=True)

    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(output)

    print("Evidence rows:", len(evidence))
    print("Compared rows:", len(output))

    counts = defaultdict(int)

    for row in output:
        counts[row["provisional_comparison"]] += 1

    print("Comparison statuses:", dict(counts))
    print(f"Output: {args.output}")


if __name__ == "__main__":
    main()
