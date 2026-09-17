#!/usr/bin/env python3
"""Reconcile primary canonical calibration evidence with optional secondary corrections."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

OUTPUT_COLUMNS = [
    "series_identifier",
    "direction",
    "canonical_slot",
    "nominal_header_value",
    "observed_value",
    "primary_observed_value",
    "secondary_observed_value",
    "observation_status",
    "review_status",
    "page",
    "table_id",
    "calibration_row_id",
    "bbox",
    "ocr_confidence",
    "x_distance",
    "y_distance",
    "resolution_source",
    "resolution_basis",
]

AUDIT_COLUMNS = [
    "series_identifier",
    "direction",
    "canonical_slot",
    "nominal_header_value",
    "status",
    "primary_source",
    "secondary_source",
    "primary_value",
    "secondary_value",
    "selected_value",
    "details",
]


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise SystemExit(f"CSV not found: {path}")
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, columns: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def clean(value: str) -> str:
    return str(value or "").strip()


def normalized_number(value: str) -> str:
    raw = clean(value).replace(",", ".")
    try:
        number = float(raw)
    except ValueError:
        return raw
    return f"{number:g}"


def key_from_primary(row: dict[str, str]) -> tuple[str, str, str]:
    return (
        clean(row.get("series_identifier", "")),
        clean(row.get("direction", "")).lower(),
        normalized_number(row.get("nominal_header_value", "")),
    )


def key_from_secondary(row: dict[str, str]) -> tuple[str, str, str]:
    return (
        clean(row.get("series_identifier", "")),
        clean(row.get("direction", "")).lower(),
        normalized_number(row.get("nominal_header_value", "")),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--primary-csv", required=True, type=Path)
    parser.add_argument("--review-csv", required=True, type=Path)
    parser.add_argument("--secondary-csv", required=True, type=Path)
    parser.add_argument("--output-csv", required=True, type=Path)
    parser.add_argument("--audit-csv", required=True, type=Path)
    args = parser.parse_args()

    primary_rows = read_csv(args.primary_csv)
    review_rows = read_csv(args.review_csv)
    secondary_rows = read_csv(args.secondary_csv)

    secondary_by_key: dict[tuple[str, str, str], list[dict[str, str]]] = {}
    for row in secondary_rows:
        key = key_from_secondary(row)
        secondary_by_key.setdefault(key, []).append(row)

    primary_by_key = {key_from_primary(row): row for row in primary_rows}
    review_by_key = {key_from_primary(row): row for row in review_rows}

    all_keys = set(primary_by_key) | set(review_by_key)
    output_rows: list[dict[str, str]] = []
    audit_rows: list[dict[str, str]] = []

    for key in sorted(all_keys, key=lambda item: (item[0], item[1], float(item[2]) if item[2] else float("inf"))):
        tag, direction, nominal = key
        primary = primary_by_key.get(key)
        review = review_by_key.get(key, {})
        secondary_matches = secondary_by_key.get(key, [])
        secondary_values = {
            clean(row.get("resolved_observed_value", ""))
            for row in secondary_matches
            if clean(row.get("resolved_observed_value", ""))
        }

        primary_value = clean(primary.get("observed_value", "")) if primary else ""
        secondary_value = next(iter(secondary_values), "") if len(secondary_values) == 1 else ""

        if primary_value:
            selected_value = primary_value
            status = "primary_observation"
            review_status = "primary"
            basis = "primary canonical OCR observation"
            details = "Primary canonical observation retained."
        elif len(secondary_values) == 1:
            selected_value = secondary_value
            status = "resolved_from_completed_layer"
            review_status = "secondary_verified"
            basis = "exact tag + direction + nominal-setpoint match"
            details = "Primary slot was missing; secondary verified value selected."
        elif len(secondary_values) > 1:
            selected_value = ""
            status = "unresolved_secondary_conflict"
            review_status = "review_required"
            basis = "conflicting secondary exact-key values"
            details = "Multiple secondary values matched the same key."
        else:
            selected_value = ""
            status = "unresolved"
            review_status = "review_required"
            basis = "no primary or secondary observation"
            details = "No observation available for this canonical key."

        source = primary or review or {}
        output_rows.append({
            "series_identifier": tag,
            "direction": direction,
            "canonical_slot": clean(source.get("canonical_slot", "")),
            "nominal_header_value": clean(source.get("nominal_header_value", nominal)),
            "observed_value": selected_value,
            "primary_observed_value": primary_value,
            "secondary_observed_value": secondary_value,
            "observation_status": status,
            "review_status": review_status,
            "page": clean(source.get("page", "")),
            "table_id": clean(source.get("table_id", "")),
            "calibration_row_id": clean(source.get("calibration_row_id", "")),
            "bbox": clean(source.get("bbox", "")),
            "ocr_confidence": clean(source.get("ocr_confidence", "")),
            "x_distance": clean(source.get("x_distance", "")),
            "y_distance": clean(source.get("y_distance", "")),
            "resolution_source": (
                clean(secondary_matches[0].get("resolution_source", ""))
                if secondary_matches else ""
            ),
            "resolution_basis": basis,
        })

        audit_rows.append({
            "series_identifier": tag,
            "direction": direction,
            "canonical_slot": clean(source.get("canonical_slot", "")),
            "nominal_header_value": clean(source.get("nominal_header_value", nominal)),
            "status": status,
            "primary_source": str(args.primary_csv) if primary else "",
            "secondary_source": str(args.secondary_csv) if secondary_matches else "",
            "primary_value": primary_value,
            "secondary_value": secondary_value,
            "selected_value": selected_value,
            "details": details,
        })

    write_csv(args.output_csv, OUTPUT_COLUMNS, output_rows)
    write_csv(args.audit_csv, AUDIT_COLUMNS, audit_rows)

    print(f"Primary canonical rows read: {len(primary_rows)}")
    print(f"Primary review rows read: {len(review_rows)}")
    print(f"Secondary resolution rows read: {len(secondary_rows)}")
    print(f"Reconciled rows written: {len(output_rows)}")
    print(f"Primary observations: {sum(row['observation_status'] == 'primary_observation' for row in output_rows)}")
    print(f"Secondary resolutions: {sum(row['observation_status'] == 'resolved_from_completed_layer' for row in output_rows)}")
    print(f"Unresolved rows: {sum(row['review_status'] == 'review_required' for row in output_rows)}")
    print(f"Output: {args.output_csv}")
    print(f"Audit: {args.audit_csv}")


if __name__ == "__main__":
    main()