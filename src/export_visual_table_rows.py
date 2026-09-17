#!/usr/bin/env python3
"""Export coordinate-aware OCR into audit-ready visual table rows.

This module converts JSON output from extract_table_regions.py into a stable
intermediate format. It does not assume a particular table schema, so it can
be used for inspection plans, traceability tables, certificates, forms, and
other engineering-document tables.

Default source policy is crop_only because full table-crop OCR is usually less
fragmented than overlapping tile OCR.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from table_layout import cluster_rows, enrich_lines, row_bbox, row_text


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Export visual OCR rows from image-based table extraction JSON."
        )
    )

    parser.add_argument(
        "--input-dir",
        type=Path,
        required=True,
        help=(
            "Directory containing page_###.json files from "
            "extract_table_regions.py."
        ),
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory for visual-row CSV, JSONL, and summary outputs.",
    )

    parser.add_argument(
        "--source-policy",
        choices=[
            "crop_only",
            "tiles_only",
            "prefer_crop",
            "all",
        ],
        default="crop_only",
        help=(
            "OCR source selection policy; crop_only is recommended for "
            "geometry-preserving default extraction."
        ),
    )

    parser.add_argument(
        "--minimum-confidence",
        type=float,
        default=0.70,
        help="Minimum OCR confidence retained in rows (default: 0.70).",
    )

    return parser.parse_args()


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def bbox_from_line(
    line: dict[str, Any],
) -> tuple[float, float, float, float]:
    """Read OCR boxes stored either as a mapping or [x1, y1, x2, y2]."""

    bbox = line.get("bbox", {})

    if isinstance(bbox, dict):
        return (
            float(bbox.get("x1", 0.0)),
            float(bbox.get("y1", 0.0)),
            float(bbox.get("x2", 0.0)),
            float(bbox.get("y2", 0.0)),
        )

    if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
        return (
            float(bbox[0]),
            float(bbox[1]),
            float(bbox[2]),
            float(bbox[3]),
        )

    raise ValueError(
        "Unsupported OCR bbox. Expected a mapping with x1/y1/x2/y2 "
        f"or a four-value list, received: {bbox!r}"
    )


def bbox_area(line: dict[str, Any]) -> float:
    x1, y1, x2, y2 = bbox_from_line(line)
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


def bbox_iou(
    left: dict[str, Any],
    right: dict[str, Any],
) -> float:
    lx1, ly1, lx2, ly2 = bbox_from_line(left)
    rx1, ry1, rx2, ry2 = bbox_from_line(right)

    x1 = max(lx1, rx1)
    y1 = max(ly1, ry1)
    x2 = min(lx2, rx2)
    y2 = min(ly2, ry2)

    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)

    left_area = bbox_area(left)
    right_area = bbox_area(right)

    union = left_area + right_area - intersection

    return intersection / union if union else 0.0


def source_rank(line: dict[str, Any]) -> int:
    source = str(line.get("source_kind", ""))

    if source == "detected_table_crop":
        return 3

    if source.startswith("detected_table_tile"):
        return 2

    return 1


def select_source_lines(
    lines: list[dict[str, Any]],
    source_policy: str,
) -> list[dict[str, Any]]:
    crop_lines = [
        line
        for line in lines
        if str(line.get("source_kind", ""))
        == "detected_table_crop"
    ]

    tile_lines = [
        line
        for line in lines
        if str(line.get("source_kind", "")).startswith(
            "detected_table_tile"
        )
    ]

    if source_policy == "crop_only":
        return crop_lines

    if source_policy == "tiles_only":
        return tile_lines

    if source_policy == "all":
        return lines

    return crop_lines if crop_lines else tile_lines


def deduplicate_exact_lines(
    lines: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Remove only exact text duplicates at overlapping coordinates."""

    kept: list[dict[str, Any]] = []

    for line in sorted(
        lines,
        key=lambda item: (
            source_rank(item),
            float(item.get("confidence", 0.0)),
            bbox_area(item),
        ),
        reverse=True,
    ):
        text = clean_text(line.get("text", ""))

        if not text:
            continue

        duplicate = any(
            text.upper()
            == clean_text(existing.get("text", "")).upper()
            and bbox_iou(line, existing) >= 0.35
            for existing in kept
        )

        if not duplicate:
            kept.append(line)

    return sorted(
        kept,
        key=lambda item: (
            bbox_from_line(item)[1],
            bbox_from_line(item)[0],
        ),
    )


def serialise_line(line: dict[str, Any]) -> dict[str, Any]:
    x1, y1, x2, y2 = bbox_from_line(line)

    return {
        "text": clean_text(line.get("text", "")),
        "confidence": round(
            float(line.get("confidence", 0.0)),
            6,
        ),
        "bbox": {
            "x1": x1,
            "y1": y1,
            "x2": x2,
            "y2": y2,
        },
        "source_kind": str(line.get("source_kind", "")),
    }


def main() -> None:
    args = parse_args()

    if not args.input_dir.is_dir():
        raise SystemExit(
            f"Input directory not found: {args.input_dir}"
        )

    if not 0.0 <= args.minimum_confidence <= 1.0:
        raise SystemExit(
            "--minimum-confidence must be between 0.0 and 1.0."
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)

    page_files = sorted(args.input_dir.glob("page_*.json"))

    if not page_files:
        raise SystemExit(
            f"No page_*.json files found in: {args.input_dir}"
        )

    csv_rows: list[dict[str, Any]] = []
    jsonl_rows: list[dict[str, Any]] = []

    page_count = 0
    table_count = 0
    visual_row_count = 0
    source_counts: Counter[str] = Counter()

    for page_file in page_files:
        payload = json.loads(
            page_file.read_text(encoding="utf-8")
        )

        page_count += 1

        document_id = str(payload.get("document_id", ""))
        page = int(payload.get("page", 0))
        source_image = str(payload.get("source_image", ""))
        rotation = payload.get("selected_rotation_degrees")
        orientation_score = payload.get("selected_score")

        tables = payload.get("tables", [])

        if not isinstance(tables, list):
            continue

        for table in tables:
            if not isinstance(table, dict):
                continue

            table_count += 1

            table_index = int(table.get("table_index", 0))
            table_bbox = table.get("bbox", {})
            raw_lines = table.get("lines", [])

            if not isinstance(raw_lines, list):
                raw_lines = []

            selected_lines = select_source_lines(
                lines=raw_lines,
                source_policy=args.source_policy,
            )

            confidence_lines = [
                line
                for line in selected_lines
                if float(line.get("confidence", 0.0))
                >= args.minimum_confidence
            ]

            deduplicated = deduplicate_exact_lines(
                confidence_lines
            )

            enriched = enrich_lines(deduplicated)
            rows = cluster_rows(enriched)

            for line in deduplicated:
                source_counts[
                    str(line.get("source_kind", "unknown"))
                ] += 1

            for row_number, row in enumerate(rows, start=1):
                x1, y1, x2, y2 = row_bbox(row)
                row_lines = [
                    serialise_line(line)
                    for line in row
                ]

                average_confidence = (
                    sum(
                        line["confidence"]
                        for line in row_lines
                    )
                    / len(row_lines)
                    if row_lines
                    else 0.0
                )

                record = {
                    "document_id": document_id,
                    "page": page,
                    "source_image": source_image,
                    "page_rotation_degrees": rotation,
                    "orientation_score": orientation_score,
                    "table_index": table_index,
                    "table_bbox": table_bbox,
                    "crop_file": str(table.get("crop_file", "")),
                    "row_number": row_number,
                    "row_text": row_text(row),
                    "row_bbox": {
                        "x1": x1,
                        "y1": y1,
                        "x2": x2,
                        "y2": y2,
                    },
                    "line_count": len(row_lines),
                    "average_confidence": round(
                        average_confidence,
                        6,
                    ),
                    "lines": row_lines,
                }

                jsonl_rows.append(record)

                csv_rows.append({
                    "document_id": document_id,
                    "page": page,
                    "table_index": table_index,
                    "row_number": row_number,
                    "row_text": record["row_text"],
                    "row_x1": x1,
                    "row_y1": y1,
                    "row_x2": x2,
                    "row_y2": y2,
                    "line_count": len(row_lines),
                    "average_confidence": record[
                        "average_confidence"
                    ],
                    "page_rotation_degrees": rotation,
                    "orientation_score": orientation_score,
                    "source_image": source_image,
                    "crop_file": record["crop_file"],
                })

                visual_row_count += 1

    csv_path = args.output_dir / "visual_table_rows.csv"
    jsonl_path = args.output_dir / "visual_table_rows.jsonl"
    summary_path = args.output_dir / "visual_table_rows_summary.json"

    columns = [
        "document_id",
        "page",
        "table_index",
        "row_number",
        "row_text",
        "row_x1",
        "row_y1",
        "row_x2",
        "row_y2",
        "line_count",
        "average_confidence",
        "page_rotation_degrees",
        "orientation_score",
        "source_image",
        "crop_file",
    ]

    with csv_path.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=columns,
        )
        writer.writeheader()
        writer.writerows(csv_rows)

    with jsonl_path.open(
        "w",
        encoding="utf-8",
    ) as handle:
        for row in jsonl_rows:
            handle.write(
                json.dumps(
                    row,
                    ensure_ascii=False,
                )
                + "\n"
            )

    summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_dir": str(args.input_dir),
        "source_policy": args.source_policy,
        "minimum_confidence": args.minimum_confidence,
        "pages_processed": page_count,
        "tables_processed": table_count,
        "visual_rows_exported": visual_row_count,
        "retained_line_sources": dict(
            sorted(source_counts.items())
        ),
        "outputs": {
            "csv": str(csv_path),
            "jsonl": str(jsonl_path),
        },
    }

    summary_path.write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8",
    )

    print("Visual table-row export complete.")
    print(f"Pages processed: {page_count}")
    print(f"Tables processed: {table_count}")
    print(f"Visual rows exported: {visual_row_count}")
    print(f"CSV: {csv_path}")
    print(f"JSONL: {jsonl_path}")
    print(f"Summary: {summary_path}")


if __name__ == "__main__":
    main()
