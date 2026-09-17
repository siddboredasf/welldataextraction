#!/usr/bin/env python3
"""Create upright review crops for generic table rows.

The script reads generic review records and coordinate-aware OCR evidence. It
does not contain document-specific fields such as QCP activity numbers,
calibration setpoints, directions, equipment tags, or fixed column centres.

Accepted review-record sources:
- logical_table_rows.jsonl
- table_row_review_queue.jsonl
- any JSONL containing a raw_record with:
  document_id, page, table_index, source_visual_rows, row_bbox

The script uses visual_table_rows.jsonl to retrieve exact OCR line boxes and
creates one upright crop per review record.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create generic upright image crops for table-row review."
        )
    )

    parser.add_argument(
        "--review-jsonl",
        type=Path,
        required=True,
        help=(
            "Generic review JSONL. It may contain raw_record objects or "
            "direct logical-row records."
        ),
    )

    parser.add_argument(
        "--visual-rows-jsonl",
        type=Path,
        required=True,
        help=(
            "visual_table_rows.jsonl produced by "
            "export_visual_table_rows.py."
        ),
    )

    parser.add_argument(
        "--detected-tables-dir",
        type=Path,
        required=True,
        help=(
            "Directory containing page_###.json output from "
            "extract_table_regions.py."
        ),
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory for generic review crops and manifest.",
    )

    parser.add_argument(
        "--padding-x",
        type=int,
        default=100,
        help="Horizontal padding in pixels (default: 100).",
    )

    parser.add_argument(
        "--padding-y",
        type=int,
        default=100,
        help="Vertical padding in pixels (default: 100).",
    )

    parser.add_argument(
        "--draw-boxes",
        action="store_true",
        help="Draw transformed OCR line boxes in red.",
    )

    return parser.parse_args()


def clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records = []

    for raw_line in path.read_text(
        encoding="utf-8"
    ).splitlines():
        if not raw_line.strip():
            continue

        value = json.loads(raw_line)

        if isinstance(value, dict):
            records.append(value)

    return records


def unwrap_review_record(
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Accept direct records or wrappers containing raw_record."""

    raw_record = payload.get("raw_record")

    if isinstance(raw_record, dict):
        record = dict(raw_record)

        if "review_id" not in record:
            record["review_id"] = payload.get(
                "review_id",
                payload.get("review", {}).get(
                    "review_id",
                    "",
                )
                if isinstance(payload.get("review"), dict)
                else "",
            )

        return record

    record = dict(payload)

    review = payload.get("review")

    if (
        not record.get("review_id")
        and isinstance(review, dict)
    ):
        record["review_id"] = review.get(
            "review_id",
            "",
        )

    return record


def load_visual_rows(
    path: Path,
) -> dict[tuple[str, int, int, int], dict[str, Any]]:
    rows = {}

    for payload in read_jsonl(path):
        key = (
            clean(payload.get("document_id")),
            to_int(payload.get("page")),
            to_int(payload.get("table_index")),
            to_int(payload.get("row_number")),
        )

        rows[key] = payload

    return rows


def load_page_table(
    detected_tables_dir: Path,
    page: int,
    table_index: int,
) -> tuple[dict[str, Any], dict[str, Any], Path]:
    page_path = detected_tables_dir / f"page_{page:03d}.json"

    if not page_path.is_file():
        raise FileNotFoundError(
            f"Page OCR JSON not found: {page_path}"
        )

    payload = json.loads(
        page_path.read_text(encoding="utf-8")
    )

    tables = payload.get("tables", [])

    for table in tables:
        if not isinstance(table, dict):
            continue

        if to_int(table.get("table_index")) != table_index:
            continue

        crop_file = Path(str(table.get("crop_file", "")))

        if not crop_file.is_file():
            raise FileNotFoundError(
                f"Table crop not found: {crop_file}"
            )

        return payload, table, crop_file

    raise ValueError(
        f"Table {table_index} not found on page {page}"
    )


def bbox_from_visual_row(
    row: dict[str, Any],
) -> dict[str, float] | None:
    bbox = row.get("row_bbox", {})

    if not isinstance(bbox, dict):
        return None

    result = {
        "x1": to_float(bbox.get("x1")),
        "y1": to_float(bbox.get("y1")),
        "x2": to_float(bbox.get("x2")),
        "y2": to_float(bbox.get("y2")),
    }

    if result["x2"] <= result["x1"]:
        return None

    if result["y2"] <= result["y1"]:
        return None

    return result


def bbox_union(
    boxes: list[dict[str, float]],
) -> dict[str, float] | None:
    if not boxes:
        return None

    return {
        "x1": min(box["x1"] for box in boxes),
        "y1": min(box["y1"] for box in boxes),
        "x2": max(box["x2"] for box in boxes),
        "y2": max(box["y2"] for box in boxes),
    }


def rotate_point(
    x: float,
    y: float,
    width: int,
    height: int,
    rotation_degrees: int,
) -> tuple[float, float]:
    rotation = rotation_degrees % 360

    if rotation == 0:
        return x, y

    if rotation == 90:
        return y, width - x

    if rotation == 180:
        return width - x, height - y

    if rotation == 270:
        return height - y, x

    raise ValueError(
        f"Unsupported rotation: {rotation_degrees}"
    )


def rotate_bbox(
    bbox: dict[str, float],
    width: int,
    height: int,
    rotation_degrees: int,
) -> dict[str, float]:
    points = [
        (bbox["x1"], bbox["y1"]),
        (bbox["x2"], bbox["y1"]),
        (bbox["x1"], bbox["y2"]),
        (bbox["x2"], bbox["y2"]),
    ]

    rotated = [
        rotate_point(
            x=x,
            y=y,
            width=width,
            height=height,
            rotation_degrees=rotation_degrees,
        )
        for x, y in points
    ]

    xs = [point[0] for point in rotated]
    ys = [point[1] for point in rotated]

    return {
        "x1": min(xs),
        "y1": min(ys),
        "x2": max(xs),
        "y2": max(ys),
    }


def clamp_bounds(
    bbox: dict[str, float],
    width: int,
    height: int,
    padding_x: int,
    padding_y: int,
) -> tuple[int, int, int, int]:
    left = max(0, round(bbox["x1"] - padding_x))
    top = max(0, round(bbox["y1"] - padding_y))
    right = min(width, round(bbox["x2"] + padding_x))
    bottom = min(height, round(bbox["y2"] + padding_y))

    if right <= left or bottom <= top:
        raise ValueError(
            "Invalid crop bounds after padding."
        )

    return left, top, right, bottom


def source_rows_from_record(
    record: dict[str, Any],
) -> list[int]:
    values = record.get("source_visual_rows", [])

    if isinstance(values, str):
        return [
            to_int(value)
            for value in values.split(",")
            if value.strip()
        ]

    if isinstance(values, list):
        return [
            to_int(value)
            for value in values
        ]

    row_number = record.get("row_number")

    if row_number is not None:
        return [to_int(row_number)]

    return []


def safe_name(value: str) -> str:
    value = clean(value)

    if not value:
        return "review"

    return re.sub(
        r"[^A-Za-z0-9_.-]+",
        "_",
        value,
    )


def main() -> None:
    args = parse_args()

    for path in (
        args.review_jsonl,
        args.visual_rows_jsonl,
    ):
        if not path.is_file():
            raise SystemExit(f"Input file not found: {path}")

    if not args.detected_tables_dir.is_dir():
        raise SystemExit(
            "Detected-tables directory not found: "
            f"{args.detected_tables_dir}"
        )

    if args.padding_x < 0 or args.padding_y < 0:
        raise SystemExit("Padding must not be negative.")

    args.output_dir.mkdir(parents=True, exist_ok=True)

    review_payloads = read_jsonl(args.review_jsonl)
    visual_rows = load_visual_rows(args.visual_rows_jsonl)

    if not review_payloads:
        raise SystemExit(
            f"No review records found in {args.review_jsonl}"
        )

    created = []
    failures = []

    for index, payload in enumerate(
        review_payloads,
        start=1,
    ):
        record = unwrap_review_record(payload)

        review_id = clean(
            record.get("review_id")
            or f"TABLE-{index:06d}"
        )

        document_id = clean(record.get("document_id"))
        page = to_int(record.get("page"))
        table_index = to_int(record.get("table_index"))
        source_row_numbers = source_rows_from_record(record)

        try:
            selected_visual_rows = []

            for row_number in source_row_numbers:
                key = (
                    document_id,
                    page,
                    table_index,
                    row_number,
                )

                row = visual_rows.get(key)

                if row is None:
                    raise KeyError(
                        "Visual row not found for "
                        f"{document_id}/page {page}/table "
                        f"{table_index}/row {row_number}"
                    )

                selected_visual_rows.append(row)

            boxes = [
                bbox_from_visual_row(row)
                for row in selected_visual_rows
            ]

            boxes = [
                box
                for box in boxes
                if box is not None
            ]

            row_bbox = bbox_union(boxes)

            if row_bbox is None:
                raise ValueError(
                    "No valid row bounding boxes were found."
                )

            payload_page, table, table_crop = load_page_table(
                detected_tables_dir=args.detected_tables_dir,
                page=page,
                table_index=table_index,
            )

            table_bbox = table.get("bbox", {})

            if not isinstance(table_bbox, dict):
                raise ValueError("Invalid table bbox.")

            offset_x = to_float(table_bbox.get("x1"))
            offset_y = to_float(table_bbox.get("y1"))

            with Image.open(table_crop) as source:
                source_image = source.convert("RGB")

            source_width, source_height = source_image.size

            source_bbox = {
                "x1": row_bbox["x1"] - offset_x,
                "y1": row_bbox["y1"] - offset_y,
                "x2": row_bbox["x2"] - offset_x,
                "y2": row_bbox["y2"] - offset_y,
            }

            rotation = (
                to_int(
                    payload_page.get(
                        "selected_rotation_degrees",
                        0,
                    )
                )
                % 360
            )

            display_image = source_image.rotate(
                rotation,
                expand=True,
            )

            display_bbox = rotate_bbox(
                bbox=source_bbox,
                width=source_width,
                height=source_height,
                rotation_degrees=rotation,
            )

            left, top, right, bottom = clamp_bounds(
                bbox=display_bbox,
                width=display_image.width,
                height=display_image.height,
                padding_x=args.padding_x,
                padding_y=args.padding_y,
            )

            review_image = display_image.crop(
                (left, top, right, bottom)
            )

            if args.draw_boxes:
                draw = ImageDraw.Draw(review_image)

                for box in boxes:
                    source_box = {
                        "x1": box["x1"] - offset_x,
                        "y1": box["y1"] - offset_y,
                        "x2": box["x2"] - offset_x,
                        "y2": box["y2"] - offset_y,
                    }

                    display_box = rotate_bbox(
                        bbox=source_box,
                        width=source_width,
                        height=source_height,
                        rotation_degrees=rotation,
                    )

                    draw.rectangle(
                        (
                            round(display_box["x1"] - left),
                            round(display_box["y1"] - top),
                            round(display_box["x2"] - left),
                            round(display_box["y2"] - top),
                        ),
                        outline="red",
                        width=3,
                    )

            output_name = (
                f"{safe_name(review_id)}"
                f"__page_{page:03d}"
                f"__table_{table_index:02d}.png"
            )

            output_path = args.output_dir / output_name
            review_image.save(output_path)

            created.append({
                "review_id": review_id,
                "document_id": document_id,
                "page": page,
                "table_index": table_index,
                "source_visual_rows": source_row_numbers,
                "rotation_degrees": rotation,
                "source_table_crop": str(table_crop),
                "output_crop": str(output_path),
                "row_bbox_page_coordinates": row_bbox,
                "row_bbox_table_coordinates": source_bbox,
                "display_bbox": display_bbox,
                "display_crop_bounds": {
                    "left": left,
                    "top": top,
                    "right": right,
                    "bottom": bottom,
                },
                "status": "created",
            })

            print(
                f"Created {review_id}: "
                f"page={page:03d}, "
                f"table={table_index:02d}"
            )

        except Exception as error:
            failures.append({
                "review_id": review_id,
                "document_id": document_id,
                "page": page,
                "table_index": table_index,
                "source_visual_rows": source_row_numbers,
                "error": str(error),
            })

            print(f"Failed {review_id}: {error}")

    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "review_jsonl": str(args.review_jsonl),
        "visual_rows_jsonl": str(args.visual_rows_jsonl),
        "detected_tables_dir": str(args.detected_tables_dir),
        "padding_x": args.padding_x,
        "padding_y": args.padding_y,
        "draw_boxes": args.draw_boxes,
        "crops_created": len(created),
        "failures": failures,
        "items": created,
    }

    manifest_path = (
        args.output_dir / "table_row_review_crops_manifest.json"
    )

    manifest_path.write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8",
    )

    print("Generic table-row review crops complete.")
    print(f"Crops created: {len(created)}")
    print(f"Failures: {len(failures)}")
    print(f"Output directory: {args.output_dir}")
    print(f"Manifest: {manifest_path}")

    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
