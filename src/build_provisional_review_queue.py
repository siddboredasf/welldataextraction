#!/usr/bin/env python3

import argparse
import csv
import re
from collections import defaultdict
from pathlib import Path


FIELDS = [
    "review_id",
    "equipment_tag",
    "serial_number",
    "field_name",
    "provisional_value",
    "evidence_values",
    "evidence_ids",
    "evidence_pages",
    "comparison_status",
    "recommended_action",
    "record_status",
    "review_status",
    "link_basis",
    "field_inheritance_note",
]


def clean(value):
    return " ".join(str(value or "").split()).strip()


def norm(value):
    value = clean(value).casefold()
    return re.sub(r"[^a-z0-9]+", "", value)


def args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--provisional", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def read_csv(path):
    if not path.is_file():
        raise SystemExit(f"File not found: {path}")

    with path.open(
        encoding="utf-8-sig",
        newline="",
    ) as handle:
        return list(csv.DictReader(handle))


def identity(tag, serial):
    return norm(tag), norm(serial)


def main():
    cli = args()
    provisional_rows = read_csv(cli.provisional)
    evidence_rows = read_csv(cli.evidence)

    evidence_groups = defaultdict(list)

    for row in evidence_rows:
        key = identity(
            row.get("equipment_tag"),
            row.get("serial_number"),
        )

        field = clean(row.get("field_name"))
        value = clean(row.get("field_value"))

        if key[0] and field and value:
            evidence_groups[(key, field)].append(row)

    output = []

    field_map = {
        "Model": "model",
        "Set Point": "set_point",
        "Declaration Reference": "declaration_reference",
        "Order Acceptance Number": "order_acceptance_number",
        "Purchase Order": "purchase_order",
        "Test Certificate Reference": "test_certificate_reference",
        "Equipment Description": "equipment_description",
        "Range / Rating": "range_rating",
    }

    for provisional in provisional_rows:
        tag = clean(provisional.get("Equipment Tag"))
        serial = clean(provisional.get("Serial Number"))
        key = identity(tag, serial)

        for source_field, field in field_map.items():
            provisional_value = clean(
                provisional.get(source_field)
            )

            candidates = evidence_groups.get(
                (key, field),
                [],
            )

            evidence_values = list(dict.fromkeys(
                clean(row.get("field_value"))
                for row in candidates
                if clean(row.get("field_value"))
            ))

            evidence_ids = list(dict.fromkeys(
                clean(row.get("evidence_id"))
                for row in candidates
                if clean(row.get("evidence_id"))
            ))

            evidence_pages = list(dict.fromkeys(
                clean(row.get("source_page"))
                for row in candidates
                if clean(row.get("source_page"))
            ))

            provisional_norm = norm(provisional_value)
            evidence_norm = {
                norm(value)
                for value in evidence_values
            }

            if not provisional_value and not evidence_values:
                status = "no_value"
                action = "ignore"

            elif provisional_norm in evidence_norm:
                status = "agrees"
                action = "accept"

            elif field == "set_point" and any(
                provisional_norm in value_norm
                for value_norm in evidence_norm
            ):
                status = "normalized_agreement"
                action = "accept"

            elif provisional_value and not evidence_values:
                status = "provisional_only"
                action = "accept_provisional_with_provenance"

            elif provisional_value and evidence_values:
                status = "reference_preferred"
                action = "use_provisional_flag_evidence"

            else:
                status = "evidence_only"
                action = "review_evidence"

            output.append({
                "review_id": (
                    f"{tag}|{serial}|{field}"
                ),
                "equipment_tag": tag,
                "serial_number": serial,
                "field_name": field,
                "provisional_value": provisional_value,
                "evidence_values": "|".join(evidence_values),
                "evidence_ids": "|".join(evidence_ids),
                "evidence_pages": "|".join(evidence_pages),
                "comparison_status": status,
                "recommended_action": action,
                "record_status": clean(
                    provisional.get("Record Status")
                ),
                "review_status": clean(
                    provisional.get("Review Status")
                ),
                "link_basis": clean(
                    provisional.get("Link Basis")
                ),
                "field_inheritance_note": clean(
                    provisional.get("Field Inheritance Note")
                ),
            })

    cli.output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with cli.output.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=FIELDS,
        )
        writer.writeheader()
        writer.writerows(output)

    print(f"Provisional rows: {len(provisional_rows)}")
    print(f"Evidence rows: {len(evidence_rows)}")
    print(f"Review rows: {len(output)}")
    print(f"Output: {cli.output}")


if __name__ == "__main__":
    main()
