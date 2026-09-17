#!/usr/bin/env python3

import argparse
import csv
import re
from collections import defaultdict
from pathlib import Path


OUTPUT_FIELDS = [
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
    "provisional_record_status",
    "provisional_review_status",
    "link_basis",
    "field_inheritance_note",
]


FIELD_MAP = {
    "Model": "model",
    "Equipment Description": "equipment_description",
    "Range / Rating": "range_rating",
    "Set Point": "set_point",
    "Declaration Reference": "declaration_reference",
    "Order Acceptance Number": "order_acceptance_number",
    "Purchase Order": "purchase_order",
    "Test Certificate Reference": "test_certificate_reference",
}


def clean(value):
    return " ".join(str(value or "").split()).strip()


def norm(value):
    value = clean(value).casefold()
    return re.sub(r"[^a-z0-9]+", "", value)


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Compare a wide provisional equipment register "
            "with long-form evidence."
        )
    )

    parser.add_argument(
        "--provisional",
        required=True,
        type=Path,
    )

    parser.add_argument(
        "--evidence",
        required=True,
        type=Path,
    )

    parser.add_argument(
        "--output",
        required=True,
        type=Path,
    )

    return parser.parse_args()


def read_csv(path):
    if not path.is_file():
        raise SystemExit(f"File not found: {path}")

    with path.open(
        encoding="utf-8-sig",
        newline="",
    ) as handle:
        return list(csv.DictReader(handle))


def identity_key(tag, serial):
    return (
        norm(tag),
        norm(serial),
    )


def main():
    args = parse_args()

    provisional_rows = read_csv(args.provisional)
    evidence_rows = read_csv(args.evidence)

    evidence_by_identity_field = defaultdict(list)

    for row in evidence_rows:
        key = identity_key(
            row.get("equipment_tag"),
            row.get("serial_number"),
        )

        field_name = clean(row.get("field_name"))
        field_value = clean(row.get("field_value"))

        if not key[0] or not field_name or not field_value:
            continue

        evidence_by_identity_field[
            (key, field_name)
        ].append(row)

    review_rows = []

    for provisional in provisional_rows:
        tag = clean(provisional.get("Equipment Tag"))
        serial = clean(provisional.get("Serial Number"))
        key = identity_key(tag, serial)

        for provisional_field, evidence_field in FIELD_MAP.items():
            provisional_value = clean(
                provisional.get(provisional_field)
            )

            candidates = evidence_by_identity_field.get(
                (key, evidence_field),
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

            elif provisional_norm and provisional_norm in evidence_norm:
                status = "agrees"
                action = "retain_provisional"

            elif (
                evidence_field == "set_point"
                and provisional_norm
                and any(
                    provisional_norm in value_norm
                    or value_norm.endswith(provisional_norm)
                    for value_norm in evidence_norm
                )
            ):
                status = "normalized_agreement"
                action = "retain_provisional"

            elif (
                provisional_value
                and len(evidence_norm) > 1
            ):
                status = "conflict"
                action = "review_conflict"

            elif provisional_value and evidence_values:
                status = "semantic_mismatch"
                action = "review_field_mapping"

            elif provisional_value and not evidence_values:
                status = "provisional_only"
                action = "retain_with_provenance_review"

            else:
                status = "evidence_only"
                action = "review_evidence_candidate"

            raw_id = "|".join([
                tag,
                serial,
                evidence_field,
            ])

            review_rows.append({
                "review_id": "CMP-" + str(
                    abs(hash(raw_id))
                ),
                "equipment_tag": tag,
                "serial_number": serial,
                "field_name": evidence_field,
                "provisional_value": provisional_value,
                "evidence_values": "|".join(evidence_values),
                "evidence_ids": "|".join(evidence_ids),
                "evidence_pages": "|".join(evidence_pages),
                "comparison_status": status,
                "recommended_action": action,
                "provisional_record_status": clean(
                    provisional.get("Record Status")
                ),
                "provisional_review_status": clean(
                    provisional.get("Review Status")
                ),
                "link_basis": clean(
                    provisional.get("Link Basis")
                ),
                "field_inheritance_note": clean(
                    provisional.get("Field Inheritance Note")
                ),
            })

    args.output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with args.output.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=OUTPUT_FIELDS,
        )
        writer.writeheader()
        writer.writerows(review_rows)

    print(f"Provisional rows: {len(provisional_rows)}")
    print(f"Evidence rows: {len(evidence_rows)}")
    print(f"Comparison rows: {len(review_rows)}")
    print(f"Output: {args.output}")


if __name__ == "__main__":
    main()
