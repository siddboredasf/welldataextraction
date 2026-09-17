#!/usr/bin/env python3
"""Create a dynamic long-form metadata layer and conservative registry."""

from __future__ import annotations

import argparse
import csv
import re
from collections import defaultdict
from pathlib import Path

IDENTITY_FIELDS = {"equipment_tag", "serial_number"}

LONG_FIELDS = [
    "evidence_id",
    "equipment_id",
    "equipment_tag",
    "serial_number",
    "field_name",
    "field_value",
    "source_page",
    "tag_anchor_page",
    "table_index",
    "linkage_method",
    "evidence_tier",
    "confidence",
    "source_text",
    "bbox",
    "source_kind",
    "selection_score",
    "selection_status",
    "source_review_status",
    "promotion_status",
    "promotion_reason",
]

REVIEW_FIELDS = LONG_FIELDS + ["review_reason"]

REGISTRY_FIELDS = [
    "equipment_id",
    "equipment_tag",
    "serial_number",
    "source_pages",
    "metadata_status",
    "review_status",
]


def clean(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def normalize(value: object) -> str:
    value = clean(value).casefold()
    return re.sub(r"[^a-z0-9]+", "", value)


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise SystemExit(f"CSV not found: {path}")

    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(
    path: Path,
    fields: list[str],
    rows: list[dict[str, str]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fields,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)


def identity_key(row: dict[str, str]) -> str:
    tag = clean(row.get("equipment_tag")).upper()
    serial = clean(row.get("serial_number"))
    return f"{tag}::{serial}"


def build_registry(
    rows: list[dict[str, str]],
) -> tuple[list[dict[str, str]], dict[str, dict[str, str]]]:
    identities: dict[str, dict[str, str]] = {}

    for row in rows:
        tag = clean(row.get("equipment_tag")).upper()
        serial = clean(row.get("serial_number"))

        if not tag:
            continue

        key = f"{tag}::{serial}"
        identity = identities.setdefault(
            key,
            {
                "equipment_tag": tag,
                "serial_number": serial,
                "source_pages": [],
            },
        )

        page = clean(row.get("source_page"))
        if page and page not in identity["source_pages"]:
            identity["source_pages"].append(page)

    registry = []
    by_key = {}

    for index, key in enumerate(sorted(identities), start=1):
        identity = identities[key]
        record = {
            "equipment_id": f"EQ-{index:04d}",
            "equipment_tag": identity["equipment_tag"],
            "serial_number": identity["serial_number"],
            "source_pages": "|".join(identity["source_pages"]),
            "metadata_status": "candidate_evidence_only",
            "review_status": "needs_metadata_review",
        }
        registry.append(record)
        by_key[key] = record

    return registry, by_key


def is_heading_like(row: dict[str, str]) -> bool:
    field_name = normalize(row.get("field_name"))
    field_value = normalize(row.get("field_value"))

    if not field_value:
        return True

    return bool(field_name and field_name == field_value)


def promotion_decision(
    row: dict[str, str],
    record: dict[str, str] | None,
) -> tuple[str, str]:
    if record is None:
        return "review", "unmatched equipment identity"

    field_name = clean(row.get("field_name"))
    field_value = clean(row.get("field_value"))

    if not field_name or not field_value:
        return "review", "missing field name or field value"

    if is_heading_like(row):
        return "review", "field value equals field label or is empty"

    evidence_tier = clean(row.get("evidence_tier"))
    selection_status = clean(row.get("selection_status"))
    source_review_status = clean(row.get("review_status"))

    if (
        evidence_tier == "tier_1_direct_tag_row"
        and field_name in IDENTITY_FIELDS
    ):
        return "eligible", "direct identity-row evidence"

    if (
        evidence_tier == "tier_1_direct_or_linked_row"
        and field_name not in IDENTITY_FIELDS
        and selection_status in {
            "selected",
            "selected_with_review",
        }
    ):
        return "eligible", "direct or linked metadata evidence"

    # Step 6C/6D certificate reconstruction emits long-form evidence
    # from direct Ser. No. + Tag OCR rows. The record identity is direct;
    # page-level certificate fields retain source-page provenance.
    if evidence_tier == "tier_1_dense_record_evidence":
        if field_name in IDENTITY_FIELDS:
            return "eligible", "direct dense serial/tag identity evidence"

        if field_name in {
            "model",
            "equipment_description",
            "range_or_rating",
            "set_point",
            "declaration_reference",
            "purchase_order",
            "test_certificate_reference",
        }:
            return (
                "eligible",
                "same-certificate metadata with source-page provenance",
            )

        if field_name == "order_acceptance_number":
            return (
                "review",
                "derived from declaration-reference suffix",
            )

        return "review", "unmapped dense certificate field"

    return "review", "shared, inherited, or review-status evidence"


def long_form_row(
    row: dict[str, str],
    record: dict[str, str] | None,
    status: str,
    reason: str,
) -> dict[str, str]:
    return {
        "evidence_id": clean(row.get("evidence_id")),
        "equipment_id": record["equipment_id"] if record else "",
        "equipment_tag": clean(row.get("equipment_tag")).upper(),
        "serial_number": clean(row.get("serial_number")),
        "field_name": clean(row.get("field_name")),
        "field_value": clean(row.get("field_value")),
        "source_page": clean(row.get("source_page")),
        "tag_anchor_page": clean(row.get("tag_anchor_page")),
        "table_index": clean(row.get("table_index")),
        "linkage_method": clean(row.get("linkage_method")),
        "evidence_tier": clean(row.get("evidence_tier")),
        "confidence": clean(row.get("confidence")),
        "source_text": clean(row.get("source_text")),
        "bbox": clean(row.get("bbox")),
        "source_kind": clean(row.get("source_kind")),
        "selection_score": clean(row.get("selection_score")),
        "selection_status": clean(row.get("selection_status")),
        "source_review_status": clean(row.get("review_status")),
        "promotion_status": status,
        "promotion_reason": reason,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Promote scored evidence into dynamic long-form metadata "
            "without a document-specific field schema."
        )
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--metadata", required=True, type=Path)
    parser.add_argument("--review", required=True, type=Path)
    args = parser.parse_args()

    input_rows = read_csv(args.input)
    registry, by_key = build_registry(input_rows)

    metadata_rows = []
    review_rows = []
    eligible_by_field: defaultdict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)

    for input_row in input_rows:
        key = identity_key(input_row)
        record = by_key.get(key)
        status, reason = promotion_decision(input_row, record)
        output_row = long_form_row(input_row, record, status, reason)
        metadata_rows.append(output_row)

        field_name = clean(input_row.get("field_name"))

        if status == "eligible" and field_name not in IDENTITY_FIELDS:
            eligible_by_field[(key, field_name)].append(output_row)
        elif status != "eligible":
            review_rows.append({
                **output_row,
                "review_reason": reason,
            })

    promoted_keys = set()

    for (key, field_name), values in eligible_by_field.items():
        value_keys = {
            normalize(row.get("field_value"))
            for row in values
            if normalize(row.get("field_value"))
        }

        if len(value_keys) > 1:
            for row in values:
                review_rows.append({
                    **row,
                    "promotion_status": "review",
                    "promotion_reason": "competing eligible values",
                    "review_reason": "competing eligible values",
                })
            continue

        if not value_keys:
            continue

        record = by_key[key]
        promoted_keys.add(key)

        pages = [page for page in record["source_pages"].split("|") if page]
        for row in values:
            page = clean(row.get("source_page"))
            if page and page not in pages:
                pages.append(page)
        record["source_pages"] = "|".join(pages)

    for key, record in by_key.items():
        if key in promoted_keys:
            record["metadata_status"] = "eligible_metadata_available"
            record["review_status"] = "field_level_review_required"
        else:
            record["metadata_status"] = "no_eligible_metadata"
            record["review_status"] = "needs_metadata_review"

    write_csv(args.registry, REGISTRY_FIELDS, registry)
    write_csv(args.metadata, LONG_FIELDS, metadata_rows)
    write_csv(args.review, REVIEW_FIELDS, review_rows)

    print(f"Input evidence rows: {len(input_rows)}")
    print(f"Equipment identities: {len(registry)}")
    print(f"Long-form metadata rows: {len(metadata_rows)}")
    print(f"Review rows: {len(review_rows)}")
    print(f"Registry: {args.registry}")
    print(f"Metadata: {args.metadata}")
    print(f"Review: {args.review}")


if __name__ == "__main__":
    main()