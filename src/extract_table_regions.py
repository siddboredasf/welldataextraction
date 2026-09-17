from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from paddleocr import PaddleOCR


TAG_HEADER_PATTERN = re.compile(
    r"\b(?:tag|equipment\s*tag|asset\s*(?:id|no|number))\b",
    re.IGNORECASE,
)
FIELD_HEADER_PATTERN = re.compile(
    r"\b(?:serial|model|device|range|position|instrument)\b",
    re.IGNORECASE,
)
NUMERIC_HYPHEN_TAG_PATTERN = re.compile(
    r"^\d{2,8}(?:[-_/]\d{1,8})+$"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Detect tables, OCR all orientations, and select the "
            "best table-layout result per page."
        )
    )
    parser.add_argument("--document-id", required=True)
    parser.add_argument(
        "--image-dir",
        default=None,
        help="Default: data/rendered/<document-id>/selected_pages_highres",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help=(
            "Default: data/outputs/<document-id>/"
            "paddleocr_detected_tables"
        ),
    )
    parser.add_argument(
        "--min-table-width-ratio",
        type=float,
        default=0.30,
    )
    parser.add_argument(
        "--min-table-height-ratio",
        type=float,
        default=0.12,
    )
    return parser.parse_args()


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def bbox_from_points(points: list[list[float]]) -> dict[str, float]:
    xs = [float(point[0]) for point in points]
    ys = [float(point[1]) for point in points]

    return {
        "x1": min(xs),
        "y1": min(ys),
        "x2": max(xs),
        "y2": max(ys),
    }


def page_number_from_path(path: Path) -> int | None:
    match = re.search(r"page[_-](\d+)", path.stem, re.IGNORECASE)
    return int(match.group(1)) if match else None


def bbox_iou(
    left: dict[str, float],
    right: dict[str, float],
) -> float:
    x1 = max(left["x1"], right["x1"])
    y1 = max(left["y1"], right["y1"])
    x2 = min(left["x2"], right["x2"])
    y2 = min(left["y2"], right["y2"])

    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    left_area = (
        (left["x2"] - left["x1"])
        * (left["y2"] - left["y1"])
    )
    right_area = (
        (right["x2"] - right["x1"])
        * (right["y2"] - right["y1"])
    )
    union = left_area + right_area - intersection

    return intersection / union if union else 0.0


def deduplicate_lines(
    lines: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []

    for line in sorted(
        lines,
        key=lambda item: item["confidence"],
        reverse=True,
    ):
        duplicate = any(
            line["text"].upper() == existing["text"].upper()
            and bbox_iou(line["bbox"], existing["bbox"]) >= 0.50
            for existing in kept
        )

        if not duplicate:
            kept.append(line)

    return sorted(
        kept,
        key=lambda item: (
            item["bbox"]["y1"],
            item["bbox"]["x1"],
        ),
    )


def predict_lines(
    ocr: PaddleOCR,
    image: np.ndarray,
    offset_x: int,
    offset_y: int,
    table_index: int,
    source_kind: str,
) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []

    for result in ocr.predict(image):
        payload = result.json
        if isinstance(payload, str):
            payload = json.loads(payload)

        data = payload.get("res", payload)
        texts = data.get("rec_texts", data.get("texts", []))
        scores = data.get("rec_scores", data.get("scores", []))
        polygons = data.get(
            "rec_polys",
            data.get("dt_polys", data.get("polys", [])),
        )

        for text, confidence, polygon in zip(texts, scores, polygons):
            text = clean_text(text)
            if not text:
                continue

            points = [
                [float(point[0]), float(point[1])]
                for point in polygon
            ]
            bbox = bbox_from_points(points)

            lines.append(
                {
                    "text": text,
                    "confidence": float(confidence),
                    "bbox": {
                        "x1": bbox["x1"] + offset_x,
                        "y1": bbox["y1"] + offset_y,
                        "x2": bbox["x2"] + offset_x,
                        "y2": bbox["y2"] + offset_y,
                    },
                    "source_kind": source_kind,
                    "table_index": table_index,
                }
            )

    return lines


def merge_overlapping_regions(
    regions: list[tuple[int, int, int, int]],
) -> list[tuple[int, int, int, int]]:
    if not regions:
        return []

    pending = sorted(regions, key=lambda item: (item[1], item[0]))
    merged: list[tuple[int, int, int, int]] = []

    while pending:
        x1, y1, x2, y2 = pending.pop(0)
        changed = True

        while changed:
            changed = False
            remaining = []

            for ox1, oy1, ox2, oy2 in pending:
                overlaps = not (
                    x2 < ox1
                    or ox2 < x1
                    or y2 < oy1
                    or oy2 < y1
                )

                close_vertically = (
                    abs(oy1 - y2) < 60
                    or abs(y1 - oy2) < 60
                )

                overlap_width = min(x2, ox2) - max(x1, ox1)
                smaller_width = min(x2 - x1, ox2 - ox1)
                similar_span = overlap_width > smaller_width * 0.50

                if overlaps or (close_vertically and similar_span):
                    x1 = min(x1, ox1)
                    y1 = min(y1, oy1)
                    x2 = max(x2, ox2)
                    y2 = max(y2, oy2)
                    changed = True
                else:
                    remaining.append((ox1, oy1, ox2, oy2))

            pending = remaining

        merged.append((x1, y1, x2, y2))

    return merged


def find_table_regions(
    image: np.ndarray,
    min_width_ratio: float,
    min_height_ratio: float,
) -> list[tuple[int, int, int, int]]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    inverted = cv2.bitwise_not(gray)

    binary = cv2.adaptiveThreshold(
        inverted,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        31,
        -8,
    )

    height, width = binary.shape[:2]
    combined = np.zeros_like(binary)

    for divisor in (30, 45, 60, 80):
        horizontal_kernel = cv2.getStructuringElement(
            cv2.MORPH_RECT,
            (max(12, width // divisor), 1),
        )
        vertical_kernel = cv2.getStructuringElement(
            cv2.MORPH_RECT,
            (1, max(12, height // divisor)),
        )

        horizontal = cv2.morphologyEx(
            binary,
            cv2.MORPH_OPEN,
            horizontal_kernel,
        )
        vertical = cv2.morphologyEx(
            binary,
            cv2.MORPH_OPEN,
            vertical_kernel,
        )

        combined = cv2.bitwise_or(
            combined,
            cv2.bitwise_or(horizontal, vertical),
        )

    combined = cv2.dilate(
        combined,
        cv2.getStructuringElement(cv2.MORPH_RECT, (19, 19)),
        iterations=2,
    )

    contours, _ = cv2.findContours(
        combined,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE,
    )

    minimum_width = int(width * min_width_ratio)
    minimum_height = int(height * min_height_ratio)
    regions = []

    for contour in contours:
        x, y, region_width, region_height = cv2.boundingRect(contour)

        if region_width < minimum_width:
            continue

        if region_height < minimum_height:
            continue

        if region_width * region_height < width * height * 0.04:
            continue

        padding_x = max(12, int(region_width * 0.02))
        padding_y = max(12, int(region_height * 0.02))

        regions.append(
            (
                max(0, x - padding_x),
                max(0, y - padding_y),
                min(width, x + region_width + padding_x),
                min(height, y + region_height + padding_y),
            )
        )

    return merge_overlapping_regions(regions)


def expand_regions_along_axis(
    regions: list[tuple[int, int, int, int]],
    image_width: int,
    image_height: int,
) -> list[tuple[int, int, int, int]]:
    expanded = []

    for x1, y1, x2, y2 in regions:
        width = x2 - x1
        height = y2 - y1

        if width >= image_width * 0.70 and height <= image_height * 0.35:
            margin = max(40, int(height * 0.30))
            expanded.append(
                (
                    x1,
                    max(0, y1 - margin),
                    x2,
                    min(image_height, y2 + int(image_height * 0.55)),
                )
            )
            continue

        if height >= image_height * 0.70 and width <= image_width * 0.35:
            margin = max(40, int(width * 0.30))
            expanded.append(
                (
                    max(0, x1 - margin),
                    y1,
                    min(image_width, x2 + int(image_width * 0.55)),
                    y2,
                )
            )
            continue

        expanded.append((x1, y1, x2, y2))

    return merge_overlapping_regions(expanded)


def ocr_table_crop(
    ocr: PaddleOCR,
    crop: np.ndarray,
    offset_x: int,
    offset_y: int,
    table_index: int,
) -> list[dict[str, Any]]:
    crop_height, crop_width = crop.shape[:2]

    lines = predict_lines(
        ocr=ocr,
        image=crop,
        offset_x=offset_x,
        offset_y=offset_y,
        table_index=table_index,
        source_kind="detected_table_crop",
    )

    if crop_width >= 1800:
        tile_count = 5
        overlap_ratio = 0.30
        tile_width = int(
            crop_width / (
                tile_count - (tile_count - 1) * overlap_ratio
            )
        )
        step = int(tile_width * (1.0 - overlap_ratio))

        starts = [
            min(index * step, crop_width - tile_width)
            for index in range(tile_count)
        ]

        for tile_index, start_x in enumerate(
            sorted(set(starts)),
            start=1,
        ):
            end_x = min(crop_width, start_x + tile_width)
            tile = crop[:, start_x:end_x]

            lines.extend(
                predict_lines(
                    ocr=ocr,
                    image=tile,
                    offset_x=offset_x + start_x,
                    offset_y=offset_y,
                    table_index=table_index,
                    source_kind=f"detected_table_tile_{tile_index}",
                )
            )

    return deduplicate_lines(lines)


def rotate_image(
    image: np.ndarray,
    degrees: int,
) -> np.ndarray:
    if degrees == 0:
        return image

    if degrees == 90:
        return cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)

    if degrees == 180:
        return cv2.rotate(image, cv2.ROTATE_180)

    if degrees == 270:
        return cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)

    raise ValueError(f"Unsupported rotation: {degrees}")


def table_score(table: dict[str, Any]) -> float:
    lines = table.get("lines", [])
    texts = [line["text"] for line in lines]

    tag_headers = sum(
        bool(TAG_HEADER_PATTERN.search(text))
        for text in texts
    )
    field_headers = sum(
        bool(FIELD_HEADER_PATTERN.search(text))
        for text in texts
    )
    unique_tags = {
        text
        for text in texts
        if NUMERIC_HYPHEN_TAG_PATTERN.fullmatch(text)
    }
    high_confidence = sum(
        line["confidence"] >= 0.90
        for line in lines
    )

    return (
        tag_headers * 10.0
        + len(unique_tags) * 2.0
        + field_headers * 1.0
        + min(5.0, high_confidence / 100.0)
    )


def run_orientation(
    ocr: PaddleOCR,
    image: np.ndarray,
    degrees: int,
    min_width_ratio: float,
    min_height_ratio: float,
    crops_dir: Path,
    page: int,
) -> dict[str, Any]:
    rotated = rotate_image(image, degrees)
    image_height, image_width = rotated.shape[:2]

    regions = find_table_regions(
        rotated,
        min_width_ratio,
        min_height_ratio,
    )
    regions = expand_regions_along_axis(
        regions,
        image_width,
        image_height,
    )

    tables = []

    for table_index, (x1, y1, x2, y2) in enumerate(
        regions,
        start=1,
    ):
        crop = rotated[y1:y2, x1:x2]

        if crop.size == 0:
            continue

        crop_path = crops_dir / (
            f"page_{page:03d}_rot_{degrees}_"
            f"table_{table_index:02d}.png"
        )
        cv2.imwrite(str(crop_path), crop)

        lines = ocr_table_crop(
            ocr=ocr,
            crop=crop,
            offset_x=x1,
            offset_y=y1,
            table_index=table_index,
        )

        for line in lines:
            line["page_rotation_degrees"] = degrees

        tables.append(
            {
                "table_index": table_index,
                "bbox": {
                    "x1": x1,
                    "y1": y1,
                    "x2": x2,
                    "y2": y2,
                },
                "crop_file": str(crop_path),
                "lines": lines,
                "score": round(
                    table_score({"lines": lines}),
                    4,
                ),
            }
        )

    orientation_score = sum(table["score"] for table in tables)

    return {
        "rotation_degrees": degrees,
        "tables": tables,
        "score": round(orientation_score, 4),
    }


def main() -> None:
    args = parse_args()

    image_dir = Path(
        args.image_dir
        or f"data/rendered/{args.document_id}/selected_pages_highres"
    )
    output_dir = Path(
        args.output_dir
        or (
            f"data/outputs/{args.document_id}/"
            "paddleocr_detected_tables"
        )
    )
    crops_dir = output_dir / "crops"

    output_dir.mkdir(parents=True, exist_ok=True)
    crops_dir.mkdir(parents=True, exist_ok=True)

    image_paths = sorted(
        [
            *image_dir.glob("page_*.png"),
            *image_dir.glob("page_*.jpg"),
            *image_dir.glob("page_*.jpeg"),
        ]
    )

    if not image_paths:
        raise SystemExit(f"No page images found in: {image_dir}")

    ocr = PaddleOCR(
        lang="en",
        use_textline_orientation=True,
        text_det_limit_side_len=6000,
        text_det_limit_type="max",
    )

    pages_with_tables = 0
    tables_detected = 0
    lines_detected = 0

    for image_path in image_paths:
        page = page_number_from_path(image_path)
        if page is None:
            continue

        image = cv2.imread(str(image_path))
        if image is None:
            print(f"Skipping unreadable image: {image_path}")
            continue

        orientations = [
            run_orientation(
                ocr=ocr,
                image=image,
                degrees=degrees,
                min_width_ratio=args.min_table_width_ratio,
                min_height_ratio=args.min_table_height_ratio,
                crops_dir=crops_dir,
                page=page,
            )
            for degrees in (0, 90, 180, 270)
        ]

        best = max(
            orientations,
            key=lambda item: (
                item["score"],
                len(item["tables"]),
                -item["rotation_degrees"],
            ),
        )

        tables = best["tables"]
        table_count = len(tables)
        line_count = sum(len(table["lines"]) for table in tables)

        payload = {
            "document_id": args.document_id,
            "page": page,
            "source_image": str(image_path),
            "selected_rotation_degrees": best["rotation_degrees"],
            "selected_score": best["score"],
            "orientation_scores": [
                {
                    "rotation_degrees": item["rotation_degrees"],
                    "score": item["score"],
                    "table_count": len(item["tables"]),
                }
                for item in orientations
            ],
            "tables": tables,
        }

        output_path = output_dir / f"page_{page:03d}.json"
        output_path.write_text(
            json.dumps(payload, indent=2),
            encoding="utf-8",
        )

        if tables:
            pages_with_tables += 1

        tables_detected += table_count
        lines_detected += line_count

        print(
            f"Page {page:03d}: "
            f"rotation={best['rotation_degrees']}° | "
            f"score={best['score']:.2f} | "
            f"tables={table_count} | "
            f"OCR lines={line_count}"
        )

    print()
    print(f"Pages scanned: {len(image_paths)}")
    print(f"Pages with selected tables: {pages_with_tables}")
    print(f"Selected table regions: {tables_detected}")
    print(f"Selected table OCR lines: {lines_detected}")
    print(f"Saved selected table OCR: {output_dir}")


if __name__ == "__main__":
    main()
