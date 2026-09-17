#!/usr/bin/env python3
"""
Rebuild tag-centred measurement rows using a canonical header-slot schema.

Inputs:
- coordinate-aware OCR cell grid;
- canonical directional header schema inferred from the document.

For every accepted tag anchor, the script:
- uses tag y-coordinate to form a local horizontal row window;
- assigns numeric cells to the nearest canonical x-slot for each direction;
- retains at most one highest-confidence OCR cell per tag/direction/slot;
- exports explicit coverage gaps.

No fixed number of slots, directions, nominal values, page count, or tag
prefix is assumed by this script.
"""

from __future__ import annotations

import argparse
import csv
import re
from collections import Counter, defaultdict
from pathlib import Path


TAG_PATTERNS = (
    (
        "numeric_hyphen",
        re.compile(r"\b\d{3,12}-\d{1,6}\b"),
    ),
    (
        "alpha_numeric_hyphen",
        re.compile(r"\b[A-Z]{1,12}-\d{1,8}[A-Z]?\b"),
    ),
    (
        "alpha_numeric_underscore",
        re.compile(r"\b[A-Z]{1,12}_\d{1,8}[A-Z]?\b"),
    ),
    (
        "alpha_numeric_compact",
        re.compile(r"\b[A-Z]{1,12}\d{2,12}[A-Z0-9]*\b"),
    ),
    (
        "multi_hyphen_identifier",
        re.compile(
            r"\b[A-Z0-9]{1,12}(?:-[A-Z0-9]{1,12}){2,3}\b"
        ),
    ),
)

NUMBER_RE = re.compile(r"^[-+]?\d+(?:[.,]\d+)?$")

ROW_COLUMNS = [
    "calibration_row_id",
    "document_id",
    "page",
    "table_id",
    "series_identifier",
    "position_candidate",
    "tag_y_centre",
    "row_y_window",
    "row_status",
    "source_kind",
]

OBSERVATION_COLUMNS = [
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
    "y_distance",
    "source_kind",
    "assignment_status",
]

REVIEW_COLUMNS = [
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
]

DUPLICATE_COLUMNS = [
    "series_identifier",
    "page",
    "table_id",
    "candidate_count",
    "chosen_tag_y_centre",
    "all_tag_y_centres",
]


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def as_float(value: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def x_mid(cell: dict[str, str]) -> float:
    return (
        as_float(cell.get("x1", ""))
        + as_float(cell.get("x2", ""))
    ) / 2


def y_mid(cell: dict[str, str]) -> float:
    return (
        as_float(cell.get("y1", ""))
        + as_float(cell.get("y2", ""))
    ) / 2


def confidence(cell: dict[str, str]) -> float:
    return as_float(cell.get("ocr_confidence", ""))


def bbox(cell: dict[str, str]) -> str:
    return (
        f"[{as_float(cell.get('x1', '')):.2f}, "
        f"{as_float(cell.get('y1', '')):.2f}, "
        f"{as_float(cell.get('x2', '')):.2f}, "
        f"{as_float(cell.get('y2', '')):.2f}]"
    )


def numeric_value(cell: dict[str, str]) -> float | None:
    text = clean(cell.get("text", "")).replace(",", ".")

    if not NUMBER_RE.fullmatch(text):
        return None

    try:
        return float(text)
    except ValueError:
        return None


def tag_family(value: str) -> str:
    """
    Return the identifier family for a complete normalized identifier.
    """
    value = clean(value).upper()

    if re.fullmatch(r"\d{3,12}-\d{1,6}", value):
        return "numeric_hyphen"

    if re.fullmatch(r"[A-Z]{1,12}-\d{1,8}[A-Z]?", value):
        return "alpha_numeric_hyphen"

    if re.fullmatch(r"[A-Z]{1,12}_\d{1,8}[A-Z]?", value):
        return "alpha_numeric_underscore"

    if re.fullmatch(r"[A-Z]{1,12}\d{2,12}[A-Z0-9]*", value):
        return "alpha_numeric_compact"

    if re.fullmatch(
        r"[A-Z0-9]{1,12}(?:-[A-Z0-9]{1,12}){2,3}",
        value,
    ):
        return "multi_hyphen_identifier"

    return ""


def normalize_tag(family: str, value: str) -> str:
    """
    Normalize identifiers without changing their semantic spelling.

    Numeric-hyphen identifiers retain the existing behavior:
    003656-0001 becomes 3656-1.
    """
    value = clean(value).upper()

    if family == "numeric_hyphen":
        prefix, suffix = value.split("-", 1)
        return f"{int(prefix)}-{int(suffix)}"

    return value


def tag_from_cell(cell: dict[str, str]) -> str:
    """
    Extract the longest supported identifier from an OCR cell.

    The longest match prevents a shorter embedded token from being selected
    when the cell contains a complete identifier.
    """
    text = clean(cell.get("text", "")).upper()
    candidates: list[tuple[str, str]] = []

    for family, pattern in TAG_PATTERNS:
        for match in pattern.finditer(text):
            candidate = match.group(0).strip(".,;:()[]{}")

            if not candidate:
                continue

            if NUMBER_RE.fullmatch(candidate):
                continue

            candidates.append((family, candidate))

    if not candidates:
        return ""

    family, value = max(
        candidates,
        key=lambda item: len(item[1]),
    )

    normalized = normalize_tag(family, value)

    if not tag_family(normalized):
        return ""

    return normalized


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


def median(values: list[float]) -> float:
    if not values:
        return 0.0

    values = sorted(values)
    middle = len(values) // 2

    if len(values) % 2:
        return values[middle]

    return (values[middle - 1] + values[middle]) / 2


def choose_anchor(
    cells: list[dict[str, str]],
) -> dict[str, str]:
    return max(
        cells,
        key=lambda cell: (
            confidence(cell),
            -x_mid(cell),
        ),
    )


def table_key(cell: dict[str, str]) -> tuple[str, str]:
    return (
        clean(cell.get("page", "")),
        clean(cell.get("table_id", "")),
    )


def slots_by_direction(
    schema: list[dict[str, str]],
) -> dict[str, list[dict[str, str]]]:
    output: dict[str, list[dict[str, str]]] = defaultdict(list)

    for row in schema:
        if clean(row.get("promotion_status", "")) != "canonical":
            continue

        direction = clean(row.get("direction", ""))

        if direction:
            output[direction].append(row)

    for slots in output.values():
        slots.sort(
            key=lambda row: as_float(
                row.get("canonical_x_centre", "")
            )
        )

    return output


def slot_tolerance(
    slots: list[dict[str, str]],
) -> float:
    centres = [
        as_float(row.get("canonical_x_centre", ""))
        for row in slots
    ]

    gaps = [
        right - left
        for left, right in zip(centres, centres[1:])
        if right > left
    ]

    spacing = median(gaps) if gaps else 120.0

    return max(35.0, min(120.0, spacing * 0.47))


def nearest_slot(
    x_value: float,
    slots: list[dict[str, str]],
) -> tuple[dict[str, str], float] | None:
    if not slots:
        return None

    slot = min(
        slots,
        key=lambda row: abs(
            x_value
            - as_float(row.get("canonical_x_centre", ""))
        ),
    )

    distance = abs(
        x_value
        - as_float(slot.get("canonical_x_centre", ""))
    )

    if distance > slot_tolerance(slots):
        return None

    return slot, distance


def dominant_prefix(
    tags_by_id: dict[str, list[dict[str, str]]],
) -> str:
    """
    Select the dominant numeric prefix or non-numeric identifier family.

    Numeric-hyphen identifiers retain prefix-based filtering, which removes
    OCR variants such as 366-27 from a 3656-* table.
    """
    numeric_prefix_counts: Counter[str] = Counter()
    family_counts: Counter[str] = Counter()

    for tag, cells in tags_by_id.items():
        match = re.fullmatch(
            r"(\d{3,12})-(\d{1,6})",
            tag,
        )

        if match:
            numeric_prefix_counts[match.group(1)] += len(cells)
            continue

        family = tag_family(tag)

        if family:
            family_counts[family] += len(cells)

    if numeric_prefix_counts:
        prefix, _ = numeric_prefix_counts.most_common(1)[0]
        return f"numeric_prefix:{prefix}"

    if family_counts:
        family, _ = family_counts.most_common(1)[0]
        return f"family:{family}"

    return ""


def accepted_identifier(
    tag: str,
    dominant_key: str,
) -> bool:
    """
    Accept only identifiers matching the dominant table structure.
    """
    if not dominant_key:
        return False

    if dominant_key.startswith("numeric_prefix:"):
        prefix = dominant_key.split(":", 1)[1]
        match = re.fullmatch(
            r"(\d{3,12})-(\d{1,6})",
            tag,
        )

        return bool(match and match.group(1) == prefix)

    if dominant_key.startswith("family:"):
        family = dominant_key.split(":", 1)[1]
        return tag_family(tag) == family

    return False


def position_candidate(
    attached: list[dict[str, str]],
    all_slots: list[dict[str, str]],
) -> str:
    if not all_slots:
        return ""

    left_measurement_x = min(
        as_float(slot.get("canonical_x_centre", ""))
        for slot in all_slots
    )

    candidates = []

    for cell in attached:
        value = numeric_value(cell)

        if value is None:
            continue

        if x_mid(cell) >= left_measurement_x - 200:
            continue

        if value.is_integer() and 0 < value <= 10000:
            candidates.append((x_mid(cell), int(value)))

    return str(sorted(candidates)[0][1]) if candidates else ""


DIRECTION_WORDS = {
    "rising": ("rising", "increasing", "ascending"),
    "falling": ("falling", "decreasing", "descending"),
    "forward": ("forward",),
    "reverse": ("reverse",),
}


def table_direction_evidence(
    table_cells: list[dict[str, str]],
) -> set[str]:
    """
    Return directions explicitly evidenced in the current table's OCR text.
    """
    found = set()

    for cell in table_cells:
        words = set(
            re.findall(
                r"[a-z]+",
                clean(cell.get("text", "")).lower(),
            )
        )

        for direction, terms in DIRECTION_WORDS.items():
            if words.intersection(terms):
                found.add(direction)

    return found


def table_has_canonical_direction_evidence(
    table_cells: list[dict[str, str]],
    canonical_directions: set[str],
) -> bool:
    """
    A table is eligible only if its own OCR contains evidence for every
    direction represented in the canonical schema.
    """
    if not canonical_directions:
        return False

    found = table_direction_evidence(table_cells)

    return canonical_directions.issubset(found)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--document-id", required=True)
    parser.add_argument("--cells-csv", required=True, type=Path)
    parser.add_argument("--schema-csv", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--row-window",
        type=float,
        default=62.0,
        help="Vertical distance from tag anchor used to attach row cells.",
    )
    args = parser.parse_args()

    cells = read_csv(args.cells_csv)
    schema = read_csv(args.schema_csv)
    direction_slots = slots_by_direction(schema)

    if not direction_slots:
        raise SystemExit(
            "No canonical schema slots found. "
            "Check canonical_directional_header_schema.csv."
        )

    cells_by_table: dict[
        tuple[str, str],
        list[dict[str, str]],
    ] = defaultdict(list)

    for cell in cells:
        page, table_id = table_key(cell)

        if page and table_id:
            cells_by_table[(page, table_id)].append(cell)

    row_rows = []
    observation_rows = []
    review_rows = []
    duplicate_rows = []

    row_number = 0
    observation_number = 0

    all_slots = [
        slot
        for slots in direction_slots.values()
        for slot in slots
    ]

    canonical_directions = set(direction_slots)

    for (page, table_id), table_cells in sorted(
        cells_by_table.items(),
        key=lambda item: (
            int(item[0][0] or 0),
            item[0][1],
        ),
    ):
        if not table_has_canonical_direction_evidence(
            table_cells,
            canonical_directions,
        ):
            continue

        tags_by_id: dict[str, list[dict[str, str]]] = defaultdict(list)

        for cell in table_cells:
            tag = tag_from_cell(cell)

            if tag:
                tags_by_id[tag].append(cell)

        dominant_key = dominant_prefix(tags_by_id)

        dominant_numeric_prefix = ""
        if dominant_key.startswith("numeric_prefix:"):
            dominant_numeric_prefix = dominant_key.split(
                ":", 1
            )[1]

        for tag, candidates in sorted(
            tags_by_id.items(),
            key=lambda item: item[0],
        ):
            numeric_match = re.fullmatch(
                r"(\d{3,12})-(\d{1,6})",
                tag,
            )

            if numeric_match:
                if (
                    not dominant_numeric_prefix
                    or numeric_match.group(1)
                    != dominant_numeric_prefix
                ):
                    continue
            elif not accepted_identifier(tag, dominant_key):
                continue

            anchor = choose_anchor(candidates)
            tag_y = y_mid(anchor)

            if len(candidates) > 1:
                duplicate_rows.append({
                    "series_identifier": tag,
                    "page": page,
                    "table_id": table_id,
                    "candidate_count": str(len(candidates)),
                    "chosen_tag_y_centre": f"{tag_y:.2f}",
                    "all_tag_y_centres": ",".join(
                        f"{y_mid(cell):.2f}"
                        for cell in sorted(candidates, key=y_mid)
                    ),
                })

            attached = [
                cell
                for cell in table_cells
                if abs(y_mid(cell) - tag_y) <= args.row_window
            ]

            position = position_candidate(attached, all_slots)

            candidate_cells: dict[
                tuple[str, str],
                list[tuple[dict[str, str], float]],
            ] = defaultdict(list)

            for cell in attached:
                if numeric_value(cell) is None:
                    continue

                for direction, slots in direction_slots.items():
                    assignment = nearest_slot(x_mid(cell), slots)

                    if assignment is None:
                        continue

                    slot, distance = assignment

                    candidate_cells[
                        (
                            direction,
                            slot["canonical_slot"],
                        )
                    ].append((cell, distance))

            if not candidate_cells:
                continue

            row_number += 1
            row_id = (
                f"{args.document_id}:canonical_row:"
                f"p{page}:"
                f"{row_number:03d}"
            )

            row_rows.append({
                "calibration_row_id": row_id,
                "document_id": args.document_id,
                "page": page,
                "table_id": table_id,
                "series_identifier": tag,
                "position_candidate": position,
                "tag_y_centre": f"{tag_y:.2f}",
                "row_y_window": f"±{args.row_window:.2f}",
                "row_status": "candidate",
                "source_kind": "tag_anchor_canonical_header_schema",
            })

            for direction, slots in direction_slots.items():
                for slot in slots:
                    slot_id = slot["canonical_slot"]
                    candidates_for_slot = candidate_cells.get(
                        (direction, slot_id),
                        [],
                    )

                    if not candidates_for_slot:
                        review_rows.append({
                            "calibration_row_id": row_id,
                            "document_id": args.document_id,
                            "page": page,
                            "table_id": table_id,
                            "series_identifier": tag,
                            "direction": direction,
                            "canonical_slot": slot_id,
                            "nominal_header_value": slot.get(
                                "canonical_header_value",
                                "",
                            ),
                            "canonical_x_centre": slot.get(
                                "canonical_x_centre",
                                "",
                            ),
                            "tag_y_centre": f"{tag_y:.2f}",
                            "review_reason": (
                                "No numeric OCR cell in the tag-centred "
                                "row window matched this canonical slot."
                            ),
                        })
                        continue

                    chosen, distance = max(
                        candidates_for_slot,
                        key=lambda item: (
                            confidence(item[0]),
                            -item[1],
                            -abs(y_mid(item[0]) - tag_y),
                        ),
                    )

                    observation_number += 1

                    observation_rows.append({
                        "canonical_observation_id": (
                            f"{row_id}:{direction}:{slot_id}:"
                            f"{observation_number:06d}"
                        ),
                        "calibration_row_id": row_id,
                        "document_id": args.document_id,
                        "page": page,
                        "table_id": table_id,
                        "series_identifier": tag,
                        "position_candidate": position,
                        "direction": direction,
                        "canonical_slot": slot_id,
                        "nominal_header_value": slot.get(
                            "canonical_header_value",
                            "",
                        ),
                        "observed_value": f"{numeric_value(chosen):g}",
                        "ocr_confidence": f"{confidence(chosen):.6f}",
                        "bbox": bbox(chosen),
                        "observed_x_centre": f"{x_mid(chosen):.2f}",
                        "canonical_x_centre": slot.get(
                            "canonical_x_centre",
                            "",
                        ),
                        "x_distance": f"{distance:.2f}",
                        "y_distance": (
                            f"{abs(y_mid(chosen) - tag_y):.2f}"
                        ),
                        "source_kind": (
                            "tag_anchor_canonical_header_schema"
                        ),
                        "assignment_status": "candidate",
                    })

    write_csv(
        args.output_dir / "calibration_rows_canonical.csv",
        ROW_COLUMNS,
        row_rows,
    )
    write_csv(
        args.output_dir / "calibration_observations_canonical.csv",
        OBSERVATION_COLUMNS,
        observation_rows,
    )
    write_csv(
        args.output_dir / "calibration_canonical_slot_review.csv",
        REVIEW_COLUMNS,
        review_rows,
    )
    write_csv(
        args.output_dir / "calibration_canonical_duplicate_tag_cells.csv",
        DUPLICATE_COLUMNS,
        duplicate_rows,
    )

    unique_tags = {
        row["series_identifier"]
        for row in row_rows
    }

    print(f"Cell rows read: {len(cells)}")
    print(f"Canonical schema directions: {len(direction_slots)}")
    print(
        "Canonical schema slots: "
        f"{sum(len(items) for items in direction_slots.values())}"
    )
    print(f"Tag-anchored calibration rows: {len(row_rows)}")
    print(f"Unique tag identifiers: {len(unique_tags)}")
    print(f"Canonical observations: {len(observation_rows)}")
    print(f"Missing-slot review rows: {len(review_rows)}")
    print(f"Duplicate tag OCR records: {len(duplicate_rows)}")
    print(f"Output directory: {args.output_dir}")


if __name__ == "__main__":
    main()