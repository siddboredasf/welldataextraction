#!/usr/bin/env python3
"""
Assign tag-anchored directional observations to canonical header slots.

Inputs:
- calibration_observations_tag_anchored.csv
- canonical_directional_header_schema.csv

The schema was inferred from OCR header geometry. This stage maps body numeric
cells to the nearest same-direction canonical x-centre, preserving all source
geometry and marking uncertain assignments for review.

No expected number of slots, nominal values, pages, tags, or units is assumed.
"""

from __future__ import annotations

import argparse
import csv
import re
from collections import defaultdict
from pathlib import Path


OUTPUT_COLUMNS = [
    "canonical_observation_id",
    "calibration_row_id",
    "document_id",
    "page",
    "table_id",
    "series_identifier",
    "position_candidate",
    "direction",
    "canonical_slot",
    "nominal_header_value",
    "observed_value",
    "ocr_confidence",
    "bbox",
    "observed_x_centre",
    "canonical_x_centre",
    "x_distance",
    "assignment_status",
    "source_kind",
]

REVIEW_COLUMNS = [
    "calibration_row_id",
    "document_id",
    "page",
    "table_id",
    "series_identifier",
    "direction",
    "observed_value",
    "observed_x_centre",
    "review_reason",
]


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def as_float(value: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def parse_bbox(value: str) -> tuple[float, float, float, float] | None:
    match = re.fullmatch(
        r"\[\s*"
        r"([-+]?\d+(?:\.\d+)?)\s*,\s*"
        r"([-+]?\d+(?:\.\d+)?)\s*,\s*"
        r"([-+]?\d+(?:\.\d+)?)\s*,\s*"
        r"([-+]?\d+(?:\.\d+)?)\s*"
        r"\]",
        clean(value),
    )

    if not match:
        return None

    x1, y1, x2, y2 = map(float, match.groups())

    if x2 <= x1 or y2 <= y1:
        return None

    return x1, y1, x2, y2


def observed_x(row: dict[str, str]) -> float | None:
    if row.get("x_centre", ""):
        return as_float(row["x_centre"])

    box = parse_bbox(row.get("bbox", ""))

    if box is None:
        return None

    return (box[0] + box[2]) / 2


def median(values: list[float]) -> float:
    if not values:
        return 0.0

    ordered = sorted(values)
    middle = len(ordered) // 2

    if len(ordered) % 2:
        return ordered[middle]

    return (ordered[middle - 1] + ordered[middle]) / 2


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--observations-csv", required=True, type=Path)
    parser.add_argument("--schema-csv", required=True, type=Path)
    parser.add_argument("--output-csv", required=True, type=Path)
    parser.add_argument("--review-csv", required=True, type=Path)
    args = parser.parse_args()

    observations = read_csv(args.observations_csv)
    schema_rows = read_csv(args.schema_csv)

    slots_by_direction: dict[
        str,
        list[dict[str, str]],
    ] = defaultdict(list)

    for row in schema_rows:
        if row.get("promotion_status") != "canonical":
            continue

        direction = clean(row.get("direction", ""))

        if direction:
            slots_by_direction[direction].append(row)

    for slots in slots_by_direction.values():
        slots.sort(
            key=lambda row: as_float(
                row.get("canonical_x_centre", "")
            )
        )

    output_rows = []
    review_rows = []
    sequence = 0

    for observation in observations:
        direction = clean(observation.get("direction", ""))
        centre = observed_x(observation)
        slots = slots_by_direction.get(direction, [])

        if centre is None:
            review_rows.append({
                "calibration_row_id": observation.get(
                    "calibration_row_id",
                    "",
                ),
                "document_id": observation.get("document_id", ""),
                "page": observation.get("page", ""),
                "table_id": observation.get("table_id", ""),
                "series_identifier": observation.get(
                    "series_identifier",
                    "",
                ),
                "direction": direction,
                "observed_value": observation.get("value", ""),
                "observed_x_centre": "",
                "review_reason": "Could not parse body-cell x coordinate.",
            })
            continue

        if not slots:
            review_rows.append({
                "calibration_row_id": observation.get(
                    "calibration_row_id",
                    "",
                ),
                "document_id": observation.get("document_id", ""),
                "page": observation.get("page", ""),
                "table_id": observation.get("table_id", ""),
                "series_identifier": observation.get(
                    "series_identifier",
                    "",
                ),
                "direction": direction,
                "observed_value": observation.get("value", ""),
                "observed_x_centre": f"{centre:.2f}",
                "review_reason": (
                    "No canonical header slots available for this direction."
                ),
            })
            continue

        slot = min(
            slots,
            key=lambda row: abs(
                centre
                - as_float(row.get("canonical_x_centre", ""))
            ),
        )

        canonical_x = as_float(slot.get("canonical_x_centre", ""))
        distance = abs(centre - canonical_x)

        x_positions = [
            as_float(row.get("canonical_x_centre", ""))
            for row in slots
        ]

        gaps = [
            right - left
            for left, right in zip(x_positions, x_positions[1:])
            if right > left
        ]

        spacing = median(gaps) if gaps else 100.0
        max_distance = max(35.0, min(120.0, spacing * 0.45))

        if distance > max_distance:
            review_rows.append({
                "calibration_row_id": observation.get(
                    "calibration_row_id",
                    "",
                ),
                "document_id": observation.get("document_id", ""),
                "page": observation.get("page", ""),
                "table_id": observation.get("table_id", ""),
                "series_identifier": observation.get(
                    "series_identifier",
                    "",
                ),
                "direction": direction,
                "observed_value": observation.get("value", ""),
                "observed_x_centre": f"{centre:.2f}",
                "review_reason": (
                    "Observed cell is too far from every canonical "
                    "header-slot x-centre."
                ),
            })
            continue

        sequence += 1

        output_rows.append({
            "canonical_observation_id": f"canonical:{sequence:06d}",
            "calibration_row_id": observation.get(
                "calibration_row_id",
                "",
            ),
            "document_id": observation.get("document_id", ""),
            "page": observation.get("page", ""),
            "table_id": observation.get("table_id", ""),
            "series_identifier": observation.get(
                "series_identifier",
                "",
            ),
            "position_candidate": observation.get(
                "position_candidate",
                "",
            ),
            "direction": direction,
            "canonical_slot": slot.get("canonical_slot", ""),
            "nominal_header_value": slot.get(
                "canonical_header_value",
                "",
            ),
            "observed_value": observation.get("value", ""),
            "ocr_confidence": observation.get(
                "ocr_confidence",
                "",
            ),
            "bbox": observation.get("bbox", ""),
            "observed_x_centre": f"{centre:.2f}",
            "canonical_x_centre": f"{canonical_x:.2f}",
            "x_distance": f"{distance:.2f}",
            "assignment_status": "candidate",
            "source_kind": (
                "tag_anchor_plus_canonical_header_geometry"
            ),
        })

    write_csv(args.output_csv, OUTPUT_COLUMNS, output_rows)
    write_csv(args.review_csv, REVIEW_COLUMNS, review_rows)

    print(f"Input observations: {len(observations)}")
    print(f"Canonical observations assigned: {len(output_rows)}")
    print(f"Assignment review rows: {len(review_rows)}")
    print(f"Output: {args.output_csv}")
    print(f"Review queue: {args.review_csv}")


if __name__ == "__main__":
    main()
