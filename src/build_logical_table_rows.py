#!/usr/bin/env python3
"""Build logical rows from OCR-derived visual rows.

This generic intermediate transformation merges wrapped visual OCR rows into
logical records. It is useful for inspection plans, registers, certificates,
and form tables where a single logical row spans multiple visual lines.

The script does not assign final semantic columns. It preserves page/table
context, source row numbers, text, confidence, and review indicators so later
schema-specific reconstructors can safely interpret the records.
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


ACTIVITY_START_PATTERN = re.compile(
    r"^\s*(\d{1,4})\s*[\.\)\-:]\s*(\S.+)$"
)

NUMBER_ONLY_PATTERN = re.compile(
    r"^\s*(\d{1,4})\s*[\.\)\-:]?\s*$"
)

BULLET_START_PATTERN = re.compile(
    r"^\s*[-•–]\s+\S+"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Merge visual OCR rows into generic logical table rows."
        )
    )

    parser.add_argument(
        "--input-csv",
        type=Path,
        required=True,
        help=(
            "visual_table_rows.csv created by "
            "export_visual_table_rows.py."
        ),
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory for logical-row CSV, JSONL, and summary files.",
    )

    parser.add_argument(
        "--maximum-gap",
        type=float,
        default=180.0,
        help=(
            "Maximum vertical gap in pixels allowed between visual-row "
            "continuations before starting a new unnumbered record "
            "(default: 180)."
        ),
    )

    return parser.parse_args()


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def classify_visual_row(text: str) -> tuple[str, str]:
    """Classify rows without assuming a document-specific table schema."""

    text = clean_text(text)

    if not text:
        return "blank", ""

    activity_match = ACTIVITY_START_PATTERN.match(text)

    if activity_match:
        return "numbered_start", activity_match.group(1)

    if NUMBER_ONLY_PATTERN.match(text):
        return "number_only", text

    if BULLET_START_PATTERN.match(text):
        return "bullet_continuation", ""

    return "continuation_or_unstructured", ""


def logical_row_status(
    record_type: str,
    source_rows: list[dict[str, str]],
) -> tuple[str, str]:
    """Return a conservative status and reason for downstream review."""

    confidences = [
        to_float(row.get("average_confidence"))
        for row in source_rows
    ]

    average_confidence = (
        sum(confidences) / len(confidences)
        if confidences
        else 0.0
    )

    if record_type == "numbered_start" and average_confidence >= 0.85:
        return "candidate", ""

    if record_type == "numbered_start":
        return "needs_review", "low_average_ocr_confidence"

    if record_type == "unstructured":
        return "needs_review", "no_numbered_record_start"

    return "needs_review", "continuation_without_clear_start"


def build_logical_rows(
    visual_rows: list[dict[str, str]],
    maximum_gap: float,
) -> list[dict[str, Any]]:
    """Merge sequential visual rows into conservative logical records."""

    logical_rows: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for row in visual_rows:
        text = clean_text(row.get("row_text", ""))

        if not text:
            continue

        row_type, activity_number = classify_visual_row(text)

        row_y1 = to_float(row.get("row_y1"))
        row_y2 = to_float(row.get("row_y2"))

        starts_new_record = row_type == "numbered_start"

        if current is not None:
            gap = row_y1 - current["row_y2"]

            # A large geometry gap is strong evidence that a record ended.
            if gap > maximum_gap:
                starts_new_record = True

        if starts_new_record:
            if current is not None:
                logical_rows.append(current)

            current = {
                "document_id": row.get("document_id", ""),
                "page": to_int(row.get("page")),
                "table_index": to_int(row.get("table_index")),
                "activity_number": activity_number,
                "record_type": "numbered_start",
                "source_rows": [row],
                "row_y1": row_y1,
                "row_y2": row_y2,
                "row_x1": to_float(row.get("row_x1")),
                "row_x2": to_float(row.get("row_x2")),
            }

            continue

        if current is None:
            current = {
                "document_id": row.get("document_id", ""),
                "page": to_int(row.get("page")),
                "table_index": to_int(row.get("table_index")),
                "activity_number": "",
                "record_type": "unstructured",
                "source_rows": [row],
                "row_y1": row_y1,
                "row_y2": row_y2,
                "row_x1": to_float(row.get("row_x1")),
                "row_x2": to_float(row.get("row_x2")),
            }

            continue

        # A continuation is allowed only inside the same page/table.
        same_table = (
            current["document_id"] == row.get("document_id", "")
            and current["page"] == to_int(row.get("page"))
            and current["table_index"] == to_int(
                row.get("table_index")
            )
        )

        if not same_table:
            logical_rows.append(current)

            current = {
                "document_id": row.get("document_id", ""),
                "page": to_int(row.get("page")),
                "table_index": to_int(row.get("table_index")),
                "activity_number": (
                    activity_number
                    if row_type == "numbered_start"
                    else ""
                ),
                "record_type": (
                    "numbered_start"
                    if row_type == "numbered_start"
                    else "unstructured"
                ),
                "source_rows": [row],
                "row_y1": row_y1,
                "row_y2": row_y2,
                "row_x1": to_float(row.get("row_x1")),
                "row_x2": to_float(row.get("row_x2")),
            }

            continue

        current["source_rows"].append(row)
        current["row_y1"] = min(current["row_y1"], row_y1)
        current["row_y2"] = max(current["row_y2"], row_y2)
        current["row_x1"] = min(
            current["row_x1"],
            to_float(row.get("row_x1")),
        )
        current["row_x2"] = max(
            current["row_x2"],
            to_float(row.get("row_x2")),
        )

    if current is not None:
        logical_rows.append(current)

    return logical_rows


def main() -> None:
    args = parse_args()

    if not args.input_csv.is_file():
        raise SystemExit(
            f"Input CSV not found: {args.input_csv}"
        )

    if args.maximum_gap <= 0:
        raise SystemExit("--maximum-gap must be greater than zero.")

    args.output_dir.mkdir(parents=True, exist_ok=True)

    with args.input_csv.open(
        encoding="utf-8",
        newline="",
    ) as handle:
        visual_rows = list(csv.DictReader(handle))

    if not visual_rows:
        raise SystemExit(f"Input CSV is empty: {args.input_csv}")

    visual_rows.sort(
        key=lambda row: (
            row.get("document_id", ""),
            to_int(row.get("page")),
            to_int(row.get("table_index")),
            to_int(row.get("row_number")),
        )
    )

    grouped: dict[tuple[str, int, int], list[dict[str, str]]] = {}

    for row in visual_rows:
        key = (
            row.get("document_id", ""),
            to_int(row.get("page")),
            to_int(row.get("table_index")),
        )
        grouped.setdefault(key, []).append(row)

    records: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    record_type_counts: Counter[str] = Counter()

    for _, rows in grouped.items():
        logical_rows = build_logical_rows(
            visual_rows=rows,
            maximum_gap=args.maximum_gap,
        )

        for logical_number, logical in enumerate(
            logical_rows,
            start=1,
        ):
            source_rows = logical["source_rows"]

            source_texts = [
                clean_text(row.get("row_text", ""))
                for row in source_rows
            ]

            source_numbers = [
                to_int(row.get("row_number"))
                for row in source_rows
            ]

            source_confidences = [
                to_float(row.get("average_confidence"))
                for row in source_rows
            ]

            average_confidence = (
                sum(source_confidences)
                / len(source_confidences)
                if source_confidences
                else 0.0
            )

            status, review_reason = logical_row_status(
                record_type=logical["record_type"],
                source_rows=source_rows,
            )

            record = {
                "document_id": logical["document_id"],
                "page": logical["page"],
                "table_index": logical["table_index"],
                "logical_row_number": logical_number,
                "activity_number": logical["activity_number"],
                "record_type": logical["record_type"],
                "record_text": " ".join(source_texts),
                "source_visual_rows": source_numbers,
                "source_visual_row_count": len(source_rows),
                "average_confidence": round(
                    average_confidence,
                    6,
                ),
                "row_bbox": {
                    "x1": logical["row_x1"],
                    "y1": logical["row_y1"],
                    "x2": logical["row_x2"],
                    "y2": logical["row_y2"],
                },
                "status": status,
                "review_required": (
                    "yes"
                    if status == "needs_review"
                    else "no"
                ),
                "review_reason": review_reason,
            }

            records.append(record)
            status_counts[status] += 1
            record_type_counts[logical["record_type"]] += 1

    csv_path = args.output_dir / "logical_table_rows.csv"
    jsonl_path = args.output_dir / "logical_table_rows.jsonl"
    summary_path = args.output_dir / "logical_table_rows_summary.json"

    columns = [
        "document_id",
        "page",
        "table_index",
        "logical_row_number",
        "activity_number",
        "record_type",
        "record_text",
        "source_visual_rows",
        "source_visual_row_count",
        "average_confidence",
        "row_bbox",
        "status",
        "review_required",
        "review_reason",
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

        for record in records:
            csv_record = dict(record)
            csv_record["source_visual_rows"] = ",".join(
                str(value)
                for value in record["source_visual_rows"]
            )
            csv_record["row_bbox"] = json.dumps(
                record["row_bbox"]
            )
            writer.writerow(csv_record)

    with jsonl_path.open(
        "w",
        encoding="utf-8",
    ) as handle:
        for record in records:
            handle.write(
                json.dumps(
                    record,
                    ensure_ascii=False,
                )
                + "\n"
            )

    summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_csv": str(args.input_csv),
        "maximum_gap": args.maximum_gap,
        "visual_rows_input": len(visual_rows),
        "logical_rows_created": len(records),
        "status_counts": dict(sorted(status_counts.items())),
        "record_type_counts": dict(
            sorted(record_type_counts.items())
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

    print("Logical table-row build complete.")
    print(f"Visual rows input: {len(visual_rows)}")
    print(f"Logical rows created: {len(records)}")
    print(
        "Candidate logical rows: "
        f"{status_counts['candidate']}"
    )
    print(
        "Logical rows needing review: "
        f"{status_counts['needs_review']}"
    )
    print(f"CSV: {csv_path}")
    print(f"JSONL: {jsonl_path}")
    print(f"Summary: {summary_path}")


if __name__ == "__main__":
    main()
