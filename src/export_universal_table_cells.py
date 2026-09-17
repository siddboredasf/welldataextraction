#!/usr/bin/env python3
"""
Export generic coordinate-aware table cells from detected-table OCR JSON.

Supported OCR JSON structure:
{
  "document_id": "...",
  "page": 9,
  "tables": [
    {
      "table_index": 1,
      "bbox": {...},
      "lines": [
        {
          "text": "...",
          "confidence": 0.99,
          "bbox": {"x1": ..., "y1": ..., "x2": ..., "y2": ...},
          "source_kind": "..."
        }
      ]
    }
  ]
}

The exporter is document-agnostic. It does not infer equipment, calibration,
pressure, vendor, or tag syntax. It preserves OCR line geometry and adds
layout-derived row/column indices for downstream record classification.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any


OUTPUT_COLUMNS = [
    "document_id",
    "page",
    "table_id",
    "table_index",
    "source_json",
    "source_crop",
    "token_id",
    "text",
    "x1",
    "y1",
    "x2",
    "y2",
    "width",
    "height",
    "ocr_confidence",
    "row_index",
    "column_index",
    "cell_id",
    "source_kind",
    "page_rotation_degrees",
]


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def get_bbox(item: dict[str, Any]) -> tuple[float, float, float, float] | None:
    bbox = item.get("bbox")

    if isinstance(bbox, dict):
        x1 = as_float(bbox.get("x1"))
        y1 = as_float(bbox.get("y1"))
        x2 = as_float(bbox.get("x2"))
        y2 = as_float(bbox.get("y2"))

        if None not in (x1, y1, x2, y2) and x2 > x1 and y2 > y1:
            return x1, y1, x2, y2

    for prefix in ("", "box_"):
        x1 = as_float(item.get(f"{prefix}x1"))
        y1 = as_float(item.get(f"{prefix}y1"))
        x2 = as_float(item.get(f"{prefix}x2"))
        y2 = as_float(item.get(f"{prefix}y2"))

        if None not in (x1, y1, x2, y2) and x2 > x1 and y2 > y1:
            return x1, y1, x2, y2

    return None


def overlap_ratio(
    first: dict[str, Any],
    second: dict[str, Any],
) -> float:
    left = max(first["x1"], second["x1"])
    top = max(first["y1"], second["y1"])
    right = min(first["x2"], second["x2"])
    bottom = min(first["y2"], second["y2"])

    if right <= left or bottom <= top:
        return 0.0

    intersection = (right - left) * (bottom - top)
    first_area = (first["x2"] - first["x1"]) * (first["y2"] - first["y1"])
    second_area = (
        (second["x2"] - second["x1"])
        * (second["y2"] - second["y1"])
    )

    smaller = min(first_area, second_area)

    return intersection / smaller if smaller else 0.0


def deduplicate_lines(
    lines: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Tile OCR and full-crop OCR often emit the same text at overlapping boxes.
    Keep the highest-confidence instance of a near-identical OCR line.
    """
    ordered = sorted(
        lines,
        key=lambda item: (
            -item["confidence"],
            item["y1"],
            item["x1"],
        ),
    )

    kept: list[dict[str, Any]] = []

    for item in ordered:
        normalized = clean_text(item["text"]).lower()

        duplicate = False

        for existing in kept:
            existing_text = clean_text(existing["text"]).lower()

            if normalized != existing_text:
                continue

            if overlap_ratio(item, existing) >= 0.65:
                duplicate = True
                break

        if not duplicate:
            kept.append(item)

    return sorted(
        kept,
        key=lambda item: (
            (item["y1"] + item["y2"]) / 2,
            item["x1"],
        ),
    )


def group_rows(lines: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    if not lines:
        return []

    heights = sorted(
        max(1.0, item["y2"] - item["y1"])
        for item in lines
    )
    median_height = heights[len(heights) // 2]
    tolerance = max(15.0, median_height * 0.80)

    rows: list[list[dict[str, Any]]] = []

    for item in lines:
        centre_y = (item["y1"] + item["y2"]) / 2

        if not rows:
            rows.append([item])
            continue

        last_row = rows[-1]
        last_centre_y = sum(
            (candidate["y1"] + candidate["y2"]) / 2
            for candidate in last_row
        ) / len(last_row)

        if abs(centre_y - last_centre_y) <= tolerance:
            last_row.append(item)
        else:
            rows.append([item])

    return rows


def assign_columns(rows: list[list[dict[str, Any]]]) -> None:
    """
    Infer stable layout columns from horizontal centres. This is an evidence
    aid, not a claim that all tables have perfect rectangular cell grids.
    """
    all_lines = [
        item
        for row in rows
        for item in row
    ]

    if not all_lines:
        return

    widths = sorted(
        max(1.0, item["x2"] - item["x1"])
        for item in all_lines
    )
    median_width = widths[len(widths) // 2]
    tolerance = max(35.0, median_width * 1.20)

    centres = sorted(
        (item["x1"] + item["x2"]) / 2
        for item in all_lines
    )

    anchors: list[float] = []

    for centre in centres:
        if not anchors or centre - anchors[-1] > tolerance:
            anchors.append(centre)

    for row_index, row in enumerate(rows, start=1):
        row.sort(key=lambda item: item["x1"])

        for item in row:
            centre_x = (item["x1"] + item["x2"]) / 2
            column_index = min(
                range(len(anchors)),
                key=lambda index: abs(centre_x - anchors[index]),
            ) + 1

            item["row_index"] = row_index
            item["column_index"] = column_index
            item["cell_id"] = (
                f"r{row_index:03d}_c{column_index:03d}"
            )


def extract_page_lines(
    payload: dict[str, Any],
    json_path: Path,
) -> list[dict[str, Any]]:
    page = str(payload.get("page", "")).strip()
    document_id = str(payload.get("document_id", "")).strip()
    rotation = str(payload.get("selected_rotation_degrees", "")).strip()

    output: list[dict[str, Any]] = []
    tables = payload.get("tables", [])

    if not isinstance(tables, list):
        return output

    for table_position, table in enumerate(tables, start=1):
        if not isinstance(table, dict):
            continue

        table_index = str(
            table.get("table_index", table_position)
        ).strip()

        crop_file = str(table.get("crop_file", "")).strip()
        table_id = (
            f"{document_id or 'document'}:"
            f"p{page or 'unknown'}:"
            f"t{int(table_index):02d}"
        )

        lines = table.get("lines", [])

        if not isinstance(lines, list):
            continue

        candidates: list[dict[str, Any]] = []

        for line_position, line in enumerate(lines, start=1):
            if not isinstance(line, dict):
                continue

            text = clean_text(line.get("text", ""))

            if not text:
                continue

            bbox = get_bbox(line)

            if bbox is None:
                continue

            confidence = as_float(line.get("confidence"))

            candidates.append({
                "document_id": document_id,
                "page": page,
                "table_id": table_id,
                "table_index": table_index,
                "source_json": str(json_path),
                "source_crop": crop_file,
                "token_id": str(line_position),
                "text": text,
                "x1": bbox[0],
                "y1": bbox[1],
                "x2": bbox[2],
                "y2": bbox[3],
                "confidence": confidence if confidence is not None else 0.0,
                "source_kind": str(
                    line.get("source_kind", "detected_table_ocr")
                ).strip(),
                "page_rotation_degrees": str(
                    line.get(
                        "page_rotation_degrees",
                        rotation,
                    )
                ).strip(),
            })

        candidates = deduplicate_lines(candidates)
        rows = group_rows(candidates)
        assign_columns(rows)

        for row in rows:
            output.extend(row)

    return output


def parse_pages_list(path: Path | None) -> set[int] | None:
    """Read an optional one-based page list; return None for all pages."""
    if path is None:
        return None

    if not path.is_file():
        raise SystemExit(f"Pages list not found: {path}")

    pages: set[int] = set()

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        value = raw_line.strip()

        if not value or value.startswith("#"):
            continue

        if not value.isdigit() or int(value) < 1:
            raise SystemExit(
                f"Invalid one-based page number {value!r} in: {path}"
            )

        pages.add(int(value))

    return pages


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--document-id", required=True)
    parser.add_argument("--ocr-json-dir", required=True, type=Path)
    parser.add_argument(
        "--pages-list",
        type=Path,
        help="Optional one-based page list; defaults to every OCR JSON page.",
    )
    parser.add_argument("--output-csv", required=True, type=Path)
    args = parser.parse_args()

    if not args.ocr_json_dir.is_dir():
        raise SystemExit(
            f"OCR JSON directory not found: {args.ocr_json_dir}"
        )

    requested_pages = parse_pages_list(args.pages_list)
    json_files = sorted(args.ocr_json_dir.glob("page_*.json"))

    if requested_pages is not None:
        json_files = [
            path
            for path in json_files
            if path.stem.removeprefix("page_").isdigit()
            and int(path.stem.removeprefix("page_")) in requested_pages
        ]

    if not json_files:
        raise SystemExit(
            f"No page JSON files found in: {args.ocr_json_dir}"
        )

    output_rows: list[dict[str, str]] = []
    pages_with_cells = 0

    for json_path in json_files:
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue

        if not isinstance(payload, dict):
            continue

        lines = extract_page_lines(payload, json_path)

        if lines:
            pages_with_cells += 1

        for item in lines:
            output_rows.append({
                "document_id": (
                    item["document_id"] or args.document_id
                ),
                "page": item["page"],
                "table_id": item["table_id"],
                "table_index": item["table_index"],
                "source_json": item["source_json"],
                "source_crop": item["source_crop"],
                "token_id": item["token_id"],
                "text": item["text"],
                "x1": f"{item['x1']:.2f}",
                "y1": f"{item['y1']:.2f}",
                "x2": f"{item['x2']:.2f}",
                "y2": f"{item['y2']:.2f}",
                "width": f"{item['x2'] - item['x1']:.2f}",
                "height": f"{item['y2'] - item['y1']:.2f}",
                "ocr_confidence": f"{item['confidence']:.6f}",
                "row_index": str(item.get("row_index", "")),
                "column_index": str(item.get("column_index", "")),
                "cell_id": str(item.get("cell_id", "")),
                "source_kind": item["source_kind"],
                "page_rotation_degrees": item[
                    "page_rotation_degrees"
                ],
            })

    args.output_csv.parent.mkdir(parents=True, exist_ok=True)

    with args.output_csv.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=OUTPUT_COLUMNS,
        )
        writer.writeheader()
        writer.writerows(output_rows)

    if requested_pages is not None:
        print(
            "Requested pages: "
            + ", ".join(str(page) for page in sorted(requested_pages))
        )
    if requested_pages is not None:
        print(
            "Requested pages: "
            + ", ".join(str(page) for page in sorted(requested_pages))
        )
    print(f"JSON files inspected: {len(json_files)}")
    print(f"Pages with OCR grid rows: {pages_with_cells}")
    print(f"Cell/token rows written: {len(output_rows)}")
    print(f"Output: {args.output_csv}")


if __name__ == "__main__":
    main()
