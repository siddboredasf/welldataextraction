#!/usr/bin/env python3
"""
Resolve missing canonical calibration slots from a completed secondary layer.

Primary source:
- calibration_canonical_slot_review.csv

Secondary source:
- a prior completed calibration CSV

A primary review gap is resolved only when the secondary layer has an exact
match on:
- equipment/tag identifier,
- direction,
- nominal setpoint.

The script does not overwrite primary OCR-derived observations. It produces
separate resolution and audit files with source provenance.
"""

from __future__ import annotations

import argparse
import csv
import re
from collections import defaultdict
from pathlib import Path


RESOLVED_COLUMNS = [
    "resolution_id",
    "calibration_row_id",
    "document_id",
    "page",
    "table_id",
    "series_identifier",
    "direction",
    "canonical_slot",
    "nominal_header_value",
    "resolved_observed_value",
    "resolution_status",
    "resolution_source",
    "secondary_source_row_number",
    "secondary_source_identifier",
    "secondary_source_direction",
    "secondary_source_nominal",
    "secondary_source_value",
    "secondary_source_record_id",
    "secondary_source_table_id",
    "secondary_source_bbox",
    "secondary_source_ocr_confidence",
    "resolution_note",
]

UPDATED_REVIEW_COLUMNS = [
    "calibration_row_id",
    "document_id",
    "page",
    "table_id",
    "series_identifier",
    "direction",
    "canonical_slot",
    "nominal_header_value",
    "canonical_x_centre",
    "tag_y_centre",
    "review_reason",
    "secondary_resolution_status",
    "secondary_resolution_value",
    "secondary_resolution_source",
    "secondary_resolution_note",
]

AUDIT_COLUMNS = [
    "calibration_row_id",
    "series_identifier",
    "direction",
    "nominal_header_value",
    "audit_status",
    "candidate_count",
    "candidate_values",
    "details",
]


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def normalized_number(value: str) -> str:
    text = clean(value).replace(",", ".")

    try:
        return f"{float(text):g}"
    except ValueError:
        return text.lower()


def is_numeric(value: str) -> bool:
    try:
        float(clean(value).replace(",", "."))
        return True
    except ValueError:
        return False


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return reader.fieldnames or [], list(reader)


def write_csv(
    path: Path,
    columns: list[str],
    rows: list[dict[str, str]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=columns,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)


def find_column(
    columns: list[str],
    candidates: list[str],
) -> str:
    lookup = {
        column.lower().strip(): column
        for column in columns
    }

    for candidate in candidates:
        column = lookup.get(candidate.lower())

        if column:
            return column

    return ""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--review-csv", required=True, type=Path)
    parser.add_argument("--completed-csv", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    review_columns, review_rows = read_csv(args.review_csv)
    completed_columns, completed_rows = read_csv(args.completed_csv)

    tag_column = find_column(
        completed_columns,
        [
            "series_identifier",
            "equipment_tag",
            "tag",
            "tag_no",
            "tag_number",
            "identifier",
        ],
    )
    direction_column = find_column(
        completed_columns,
        [
            "direction",
            "cycle_direction",
        ],
    )
    nominal_column = find_column(
        completed_columns,
        [
            "nominal_header_value",
            "setpoint_bar",
            "nominal_value",
            "nominal",
            "setpoint",
            "test_point",
        ],
    )
    observed_column = find_column(
        completed_columns,
        [
            "observed_value",
            "observed_pressure_bar",
            "value",
            "reading",
            "actual_value",
            "measured_value",
        ],
    )

    missing = [
        label
        for label, column in {
            "tag": tag_column,
            "direction": direction_column,
            "nominal": nominal_column,
            "observed": observed_column,
        }.items()
        if not column
    ]

    if missing:
        raise SystemExit(
            "Secondary CSV missing required logical columns: "
            + ", ".join(missing)
            + ". Available columns: "
            + ", ".join(completed_columns)
        )

    secondary_index: dict[
        tuple[str, str, str],
        list[tuple[int, dict[str, str]]],
    ] = defaultdict(list)

    for row_number, row in enumerate(completed_rows, start=2):
        tag = clean(row.get(tag_column, ""))
        direction = clean(row.get(direction_column, "")).lower()
        nominal = normalized_number(row.get(nominal_column, ""))
        observed = clean(row.get(observed_column, ""))

        if not tag or not direction or not nominal:
            continue

        if not is_numeric(observed):
            continue

        secondary_index[(tag, direction, nominal)].append(
            (row_number, row)
        )

    resolved_rows = []
    updated_review_rows = []
    unresolved_rows = []
    audit_rows = []
    resolution_number = 0

    for review in review_rows:
        tag = clean(review.get("series_identifier", ""))
        direction = clean(review.get("direction", "")).lower()
        nominal = normalized_number(
            review.get("nominal_header_value", "")
        )

        matches = secondary_index.get(
            (tag, direction, nominal),
            [],
        )

        updated = dict(review)
        updated["secondary_resolution_status"] = ""
        updated["secondary_resolution_value"] = ""
        updated["secondary_resolution_source"] = ""
        updated["secondary_resolution_note"] = ""

        candidate_values = sorted({
            normalized_number(row.get(observed_column, ""))
            for _, row in matches
        })

        if matches and len(candidate_values) == 1:
            source_row_number, source_row = matches[0]
            resolved_value = clean(
                source_row.get(observed_column, "")
            )

            resolution_number += 1

            resolved_rows.append({
                "resolution_id": (
                    f"secondary_resolution:{resolution_number:04d}"
                ),
                "calibration_row_id": review.get(
                    "calibration_row_id",
                    "",
                ),
                "document_id": review.get("document_id", ""),
                "page": review.get("page", ""),
                "table_id": review.get("table_id", ""),
                "series_identifier": tag,
                "direction": direction,
                "canonical_slot": review.get(
                    "canonical_slot",
                    "",
                ),
                "nominal_header_value": review.get(
                    "nominal_header_value",
                    "",
                ),
                "resolved_observed_value": resolved_value,
                "resolution_status": "resolved_from_completed_layer",
                "resolution_source": str(args.completed_csv),
                "secondary_source_row_number": str(
                    source_row_number
                ),
                "secondary_source_identifier": clean(
                    source_row.get(tag_column, "")
                ),
                "secondary_source_direction": clean(
                    source_row.get(direction_column, "")
                ),
                "secondary_source_nominal": clean(
                    source_row.get(nominal_column, "")
                ),
                "secondary_source_value": resolved_value,
                "secondary_source_record_id": clean(
                    source_row.get("record_id", "")
                ),
                "secondary_source_table_id": clean(
                    source_row.get("source_table_id", "")
                ),
                "secondary_source_bbox": clean(
                    source_row.get("bbox", "")
                ),
                "secondary_source_ocr_confidence": clean(
                    source_row.get("ocr_confidence", "")
                ),
                "resolution_note": (
                    "Exact tag, direction, and nominal-setpoint match "
                    "from completed secondary layer."
                ),
            })

            updated["secondary_resolution_status"] = (
                "resolved_from_completed_layer"
            )
            updated["secondary_resolution_value"] = resolved_value
            updated["secondary_resolution_source"] = str(
                args.completed_csv
            )
            updated["secondary_resolution_note"] = (
                "Exact tag, direction, and nominal-setpoint match."
            )

        elif not matches:
            updated["secondary_resolution_status"] = (
                "unresolved_no_secondary_match"
            )
            updated["secondary_resolution_note"] = (
                "No exact tag/direction/nominal match in completed layer."
            )
            unresolved_rows.append(updated)

        else:
            updated["secondary_resolution_status"] = (
                "unresolved_secondary_conflict"
            )
            updated["secondary_resolution_note"] = (
                "Multiple distinct secondary observed values matched "
                "the same tag/direction/nominal key."
            )
            unresolved_rows.append(updated)

            audit_rows.append({
                "calibration_row_id": review.get(
                    "calibration_row_id",
                    "",
                ),
                "series_identifier": tag,
                "direction": direction,
                "nominal_header_value": review.get(
                    "nominal_header_value",
                    "",
                ),
                "audit_status": "secondary_value_conflict",
                "candidate_count": str(len(matches)),
                "candidate_values": ",".join(candidate_values),
                "details": (
                    "No automatic value chosen because completed layer "
                    "contains conflicting exact-key readings."
                ),
            })

        updated_review_rows.append(updated)

    updated_columns = list(review_columns)

    for column in (
        "secondary_resolution_status",
        "secondary_resolution_value",
        "secondary_resolution_source",
        "secondary_resolution_note",
    ):
        if column not in updated_columns:
            updated_columns.append(column)

    write_csv(
        args.output_dir / "calibration_secondary_resolutions.csv",
        RESOLVED_COLUMNS,
        resolved_rows,
    )
    write_csv(
        args.output_dir
        / "calibration_canonical_slot_review_resolved.csv",
        updated_columns,
        updated_review_rows,
    )
    write_csv(
        args.output_dir
        / "calibration_canonical_slot_review_unresolved.csv",
        updated_columns,
        unresolved_rows,
    )
    write_csv(
        args.output_dir / "calibration_secondary_resolution_audit.csv",
        AUDIT_COLUMNS,
        audit_rows,
    )

    print(f"Primary review gaps read: {len(review_rows)}")
    print(f"Completed rows indexed: {len(secondary_index)}")
    print(f"Resolved from completed layer: {len(resolved_rows)}")
    print(f"Still unresolved: {len(unresolved_rows)}")
    print(f"Secondary conflicts: {len(audit_rows)}")
    print(f"Output directory: {args.output_dir}")


if __name__ == "__main__":
    main()
