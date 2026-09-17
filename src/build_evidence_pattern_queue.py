#!/usr/bin/env python3

import argparse
import csv
import hashlib
from collections import defaultdict
from pathlib import Path


FIELDS = [
    "pattern_review_id",
    "field_name",
    "candidate_value",
    "source_text",
    "equipment_count",
    "equipment_ids",
    "equipment_tags",
    "evidence_count",
    "source_pages",
    "evidence_ids",
    "evidence_tiers",
    "max_confidence",
    "pattern_status",
    "recommended_action",
    "reviewed_value",
    "reviewer",
    "review_notes",
]


def clean(value):
    return " ".join(str(value or "").split()).strip()


def norm(value):
    return clean(value).casefold()


def number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def is_label_only(field_name, field_value, source_text):
    field = norm(field_name)
    value = norm(field_value)
    source = norm(source_text)

    if not field or not value:
        return True

    if value == field:
        return True

    if source and source == value:
        return True

    if source and source == field:
        return True

    return False


def make_id(field_name, value, source_text):
    raw = "|".join([
        norm(field_name),
        norm(value),
        norm(source_text),
    ]).encode("utf-8")

    return "PAT-" + hashlib.sha1(raw).hexdigest()[:12].upper()


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Group repeated evidence by field, value, and source text."
        )
    )

    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)

    return parser.parse_args()


def main():
    args = parse_args()

    if not args.input.is_file():
        raise SystemExit(f"Input not found: {args.input}")

    with args.input.open(
        encoding="utf-8-sig",
        newline="",
    ) as handle:
        rows = list(csv.DictReader(handle))

    patterns = defaultdict(list)

    for row in rows:
        field_name = clean(row.get("field_name"))
        value = clean(row.get("field_value"))
        source_text = clean(row.get("source_text"))

        if is_label_only(
            field_name,
            value,
            source_text,
        ):
            continue

        key = (
            norm(field_name),
            norm(value),
            norm(source_text),
        )

        patterns[key].append(row)

    output_rows = []

    for (
        field_key,
        value_key,
        source_text_key,
    ), pattern_rows in sorted(patterns.items()):
        first = pattern_rows[0]

        equipment_ids = list(dict.fromkeys(
            clean(row.get("equipment_id"))
            for row in pattern_rows
            if clean(row.get("equipment_id"))
        ))

        equipment_tags = list(dict.fromkeys(
            clean(row.get("equipment_tag"))
            for row in pattern_rows
            if clean(row.get("equipment_tag"))
        ))

        pages = list(dict.fromkeys(
            clean(row.get("source_page"))
            for row in pattern_rows
            if clean(row.get("source_page"))
        ))

        evidence_ids = list(dict.fromkeys(
            clean(row.get("evidence_id"))
            for row in pattern_rows
            if clean(row.get("evidence_id"))
        ))

        tiers = list(dict.fromkeys(
            clean(row.get("evidence_tier"))
            for row in pattern_rows
            if clean(row.get("evidence_tier"))
        ))

        max_confidence = max(
            number(row.get("confidence"))
            for row in pattern_rows
        )

        action = (
            "review_once_then_propagate"
            if len(equipment_ids) > 1
            else "review_equipment_specific"
        )

        output_rows.append({
            "pattern_review_id": make_id(
                field_key,
                value_key,
                source_text_key,
            ),
            "field_name": clean(first.get("field_name")),
            "candidate_value": clean(first.get("field_value")),
            "source_text": clean(first.get("source_text")),
            "equipment_count": len(equipment_ids),
            "equipment_ids": "|".join(equipment_ids),
            "equipment_tags": "|".join(equipment_tags),
            "evidence_count": len(pattern_rows),
            "source_pages": "|".join(pages),
            "evidence_ids": "|".join(evidence_ids),
            "evidence_tiers": "|".join(tiers),
            "max_confidence": max_confidence,
            "pattern_status": "pending",
            "recommended_action": action,
            "reviewed_value": "",
            "reviewer": "",
            "review_notes": "",
        })

    args.output.parent.mkdir(parents=True, exist_ok=True)

    with args.output.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=FIELDS,
        )
        writer.writeheader()
        writer.writerows(output_rows)

    print(f"Input evidence rows: {len(rows)}")
    print(f"Evidence patterns: {len(output_rows)}")
    print(f"Output: {args.output}")


if __name__ == "__main__":
    main()
