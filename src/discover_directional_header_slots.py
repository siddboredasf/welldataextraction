#!/usr/bin/env python3
"""
Discover numeric header slots for directional measurement groups.

The script infers a table's directional column schema from:
- OCR text identifying directional measurement groups;
- their horizontal geometry;
- numeric cells in the header area above the first tag/body row.

It constructs non-overlapping x territories using midpoints between adjacent
direction-header group centres. Therefore each numeric header cell can belong
to only one direction.

No fixed direction count, slot count, nominal values, page IDs, units, or tag
prefixes are encoded.
"""

from __future__ import annotations

import argparse
import csv
import re
from collections import defaultdict
from pathlib import Path


DIRECTION_TERMS = {
    "rising": "rising",
    "falling": "falling",
    "increasing": "increasing",
    "decreasing": "decreasing",
    "ascending": "increasing",
    "descending": "decreasing",
    "forward": "forward",
    "reverse": "reverse",
}

MEASUREMENT_TERMS = {
    "pressure",
    "reading",
    "measurement",
    "measured",
    "test",
    "value",
    "actual",
    "observed",
    "indicated",
}

NUMBER_RE = re.compile(r"^[-+]?\d+(?:[.,]\d+)?$")
TAG_RE = re.compile(r"\b\d{3,12}-\d{1,6}\b", flags=re.IGNORECASE)

OUTPUT_COLUMNS = [
    "document_id",
    "page",
    "table_id",
    "direction",
    "slot_index",
    "header_value",
    "header_x_centre",
    "header_y_centre",
    "header_bbox",
    "direction_header_text",
    "direction_header_bbox",
    "direction_territory",
    "header_slot_support",
    "source_kind",
]

REVIEW_COLUMNS = [
    "document_id",
    "page",
    "table_id",
    "direction",
    "review_reason",
    "direction_header_text",
    "body_start_y",
]


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def norm(value: str) -> str:
    return clean(value).lower()


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


def direction_from_text(text: str) -> str:
    content = norm(text)

    for term, direction in DIRECTION_TERMS.items():
        if re.search(rf"\b{re.escape(term)}\b", content):
            return direction

    return ""


def has_measurement_context(text: str) -> bool:
    content = norm(text)

    return any(
        re.search(rf"\b{re.escape(term)}\b", content)
        for term in MEASUREMENT_TERMS
    )


def is_tag_like(cell: dict[str, str]) -> bool:
    return bool(TAG_RE.search(clean(cell.get("text", ""))))


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


def x_clusters(
    cells: list[dict[str, str]],
) -> list[list[dict[str, str]]]:
    """
    Cluster duplicate OCR cells from the same header x position.
    """
    if not cells:
        return []

    centres = sorted(x_mid(cell) for cell in cells)
    gaps = [
        right - left
        for left, right in zip(centres, centres[1:])
        if right > left
    ]

    close_gaps = [
        gap
        for gap in gaps
        if 5.0 <= gap <= 90.0
    ]

    tolerance = (
        max(25.0, min(80.0, median(close_gaps) * 1.30))
        if close_gaps
        else 55.0
    )

    output: list[list[dict[str, str]]] = []

    for cell in sorted(cells, key=x_mid):
        if not output:
            output.append([cell])
            continue

        current_centre = sum(
            x_mid(item)
            for item in output[-1]
        ) / len(output[-1])

        if abs(x_mid(cell) - current_centre) <= tolerance:
            output[-1].append(cell)
        else:
            output.append([cell])

    return output


def y_clusters(
    cells: list[dict[str, str]],
) -> list[list[dict[str, str]]]:
    """
    Cluster numeric cells into candidate horizontal header rows.
    """
    if not cells:
        return []

    heights = sorted(
        max(
            1.0,
            as_float(cell.get("y2", ""))
            - as_float(cell.get("y1", "")),
        )
        for cell in cells
    )

    tolerance = max(18.0, median(heights) * 0.70)
    output: list[list[dict[str, str]]] = []

    for cell in sorted(cells, key=lambda item: (y_mid(item), x_mid(item))):
        if not output:
            output.append([cell])
            continue

        current_centre = sum(
            y_mid(item)
            for item in output[-1]
        ) / len(output[-1])

        if abs(y_mid(cell) - current_centre) <= tolerance:
            output[-1].append(cell)
        else:
            output.append([cell])

    return output


def directional_header_groups(
    cells: list[dict[str, str]],
) -> dict[str, dict[str, object]]:
    """
    Merge duplicate OCR fragments for each detected direction.
    """
    by_direction: dict[str, list[dict[str, str]]] = defaultdict(list)

    for cell in cells:
        direction = direction_from_text(cell.get("text", ""))

        if not direction:
            continue

        if not has_measurement_context(cell.get("text", "")):
            continue

        by_direction[direction].append(cell)

    groups = {}

    for direction, fragments in by_direction.items():
        left = min(as_float(cell.get("x1", "")) for cell in fragments)
        right = max(as_float(cell.get("x2", "")) for cell in fragments)
        top = min(as_float(cell.get("y1", "")) for cell in fragments)
        bottom = max(as_float(cell.get("y2", "")) for cell in fragments)

        texts = sorted({
            clean(cell.get("text", ""))
            for cell in fragments
        })

        groups[direction] = {
            "direction": direction,
            "x1": left,
            "x2": right,
            "y1": top,
            "y2": bottom,
            "x_centre": (left + right) / 2,
            "text": " | ".join(texts),
            "bbox": f"[{left:.2f}, {top:.2f}, {right:.2f}, {bottom:.2f}]",
        }

    return groups


def directional_territories(
    groups: dict[str, dict[str, object]],
    table_left: float,
    table_right: float,
) -> dict[str, tuple[float, float]]:
    """
    Divide the table into non-overlapping territories using midpoints between
    direction header centres.

    The left/right outer edges remain bounded by the table's observed extent.
    """
    ordered = sorted(
        groups.values(),
        key=lambda group: float(group["x_centre"]),
    )

    territories = {}

    for index, group in enumerate(ordered):
        direction = str(group["direction"])

        if index == 0:
            left = table_left
        else:
            previous = ordered[index - 1]
            left = (
                float(previous["x_centre"])
                + float(group["x_centre"])
            ) / 2

        if index == len(ordered) - 1:
            right = table_right
        else:
            following = ordered[index + 1]
            right = (
                float(group["x_centre"])
                + float(following["x_centre"])
            ) / 2

        territories[direction] = (left, right)

    return territories


def select_numeric_header_band(
    cells: list[dict[str, str]],
) -> list[dict[str, str]]:
    """
    Choose the numeric header row with the widest distinct x coverage.

    Candidate cells have already been restricted above the body start and to
    one non-overlapping directional territory.
    """
    candidates = []

    for band in y_clusters(cells):
        numeric = [
            cell
            for cell in band
            if numeric_value(cell) is not None
        ]

        if len(numeric) < 2:
            continue

        distinct = len(x_clusters(numeric))

        if distinct < 2:
            continue

        span = max(x_mid(cell) for cell in numeric) - min(
            x_mid(cell) for cell in numeric
        )

        candidates.append((
            distinct,
            span,
            -median([y_mid(cell) for cell in numeric]),
            numeric,
        ))

    if not candidates:
        return []

    candidates.sort(
        key=lambda item: (
            item[0],
            item[1],
            item[2],
        ),
        reverse=True,
    )

    return candidates[0][3]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cells-csv", required=True, type=Path)
    parser.add_argument("--output-csv", required=True, type=Path)
    parser.add_argument("--review-csv", required=True, type=Path)
    args = parser.parse_args()

    cells = read_csv(args.cells_csv)

    tables: dict[
        tuple[str, str],
        list[dict[str, str]],
    ] = defaultdict(list)

    for cell in cells:
        page = clean(cell.get("page", ""))
        table_id = clean(cell.get("table_id", ""))

        if page and table_id:
            tables[(page, table_id)].append(cell)

    output_rows = []
    review_rows = []

    for (page, table_id), table_cells in sorted(
        tables.items(),
        key=lambda item: (
            int(item[0][0] or 0),
            item[0][1],
        ),
    ):
        groups = directional_header_groups(table_cells)

        if not groups:
            continue

        table_left = min(
            as_float(cell.get("x1", ""))
            for cell in table_cells
        )
        table_right = max(
            as_float(cell.get("x2", ""))
            for cell in table_cells
        )

        territories = directional_territories(
            groups,
            table_left,
            table_right,
        )

        tag_y_values = [
            y_mid(cell)
            for cell in table_cells
            if is_tag_like(cell)
        ]

        body_start_y = min(tag_y_values) if tag_y_values else None

        for direction, group in groups.items():
            if body_start_y is None:
                review_rows.append({
                    "document_id": "",
                    "page": page,
                    "table_id": table_id,
                    "direction": direction,
                    "review_reason": (
                        "No tag-like body anchor found; cannot isolate "
                        "numeric header cells."
                    ),
                    "direction_header_text": str(group["text"]),
                    "body_start_y": "",
                })
                continue

            territory_left, territory_right = territories[direction]

            # Header values must be above the first identified body/tag row.
            # Allow content above or close to the directional header, but
            # never include body-row values.
            header_bottom = body_start_y - 15.0

            candidates = [
                cell
                for cell in table_cells
                if numeric_value(cell) is not None
                and y_mid(cell) <= header_bottom
                and territory_left <= x_mid(cell) <= territory_right
            ]

            header_band = select_numeric_header_band(candidates)

            if not header_band:
                review_rows.append({
                    "document_id": "",
                    "page": page,
                    "table_id": table_id,
                    "direction": direction,
                    "review_reason": (
                        "No numeric header row was found inside the "
                        "directional territory before body content."
                    ),
                    "direction_header_text": str(group["text"]),
                    "body_start_y": f"{body_start_y:.2f}",
                })
                continue

            for slot_index, cluster in enumerate(
                x_clusters(header_band),
                start=1,
            ):
                selected = max(cluster, key=confidence)
                value = numeric_value(selected)

                output_rows.append({
                    "document_id": selected.get("document_id", ""),
                    "page": page,
                    "table_id": table_id,
                    "direction": direction,
                    "slot_index": str(slot_index),
                    "header_value": f"{value:g}",
                    "header_x_centre": f"{x_mid(selected):.2f}",
                    "header_y_centre": f"{y_mid(selected):.2f}",
                    "header_bbox": bbox(selected),
                    "direction_header_text": str(group["text"]),
                    "direction_header_bbox": str(group["bbox"]),
                    "direction_territory": (
                        f"[{territory_left:.2f}, {territory_right:.2f}]"
                    ),
                    "header_slot_support": str(len(cluster)),
                    "source_kind": "non_overlapping_directional_header_geometry",
                })

    write_csv(args.output_csv, OUTPUT_COLUMNS, output_rows)
    write_csv(args.review_csv, REVIEW_COLUMNS, review_rows)

    print(f"Cell rows read: {len(cells)}")
    print(f"Header-derived directional slots: {len(output_rows)}")
    print(f"Header slot review rows: {len(review_rows)}")
    print(f"Output: {args.output_csv}")


if __name__ == "__main__":
    main()
