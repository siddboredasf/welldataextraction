#!/usr/bin/env python3
"""Build a universal review queue from logical table rows.

This tool is document-agnostic. It does not know whether rows come from a
QCP, calibration sheet, certificate, traceability report, drawing register,
or another table family.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build a generic table-row review queue from logical rows."
        )
    )

    parser.add_argument(
        "--input-jsonl",
        type=Path,
        required=True,
        help=(
            "logical_table_rows.jsonl produced by "
            "build_logical_table_rows.py."
        ),
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory for generic review queue outputs.",
    )

    parser.add_argument(
        "--include-candidates",
        action="store_true",
        help=(
            "Include candidate rows as well as rows already requiring review."
        ),
    )

    return parser.parse_args()


def clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def read_records(path: Path) -> list[dict[str, Any]]:
    records = []

    for raw_line in path.read_text(
        encoding="utf-8"
    ).splitlines():
        if not raw_line.strip():
            continue

        record = json.loads(raw_line)

        if isinstance(record, dict):
            records.append(record)

    return records


def review_priority(record: dict[str, Any]) -> str:
    reasons = clean(
        record.get("review_reason")
        or record.get("review_reasons")
        or ""
    ).lower()

    confidence = float(
        record.get("average_confidence", 0.0)
        or 0.0
    )

    if (
        "no_numbered_record_start" in reasons
        or "missing" in reasons
        or confidence < 0.80
    ):
        return "high"

    if (
        "continuation" in reasons
        or "merged" in reasons
        or "inconsistent" in reasons
    ):
        return "medium"

    return "low"


def source_rows(record: dict[str, Any]) -> str:
    values = record.get("source_visual_rows", [])

    if isinstance(values, str):
        return values

    if isinstance(values, list):
        return ",".join(str(value) for value in values)

    return ""


def main() -> None:
    args = parse_args()

    if not args.input_jsonl.is_file():
        raise SystemExit(
            f"Input JSONL not found: {args.input_jsonl}"
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)

    records = read_records(args.input_jsonl)

    if not records:
        raise SystemExit(
            f"No records found in {args.input_jsonl}"
        )

    selected = []

    for record in records:
        review_required = clean(
            record.get("review_required", "")
        ).lower()

        status = clean(record.get("status", "")).lower()

        requires_review = (
            review_required in {"yes", "true", "1"}
            or status in {
                "needs_review",
                "review",
                "unstructured",
            }
        )

        if requires_review or args.include_candidates:
            selected.append(record)

    selected.sort(
        key=lambda record: (
            {"high": 0, "medium": 1, "low": 2}.get(
                review_priority(record),
                3,
            ),
            int(record.get("page", 0) or 0),
            int(record.get("table_index", 0) or 0),
            int(record.get("logical_row_number", 0) or 0),
        )
    )

    queue_rows = []

    for index, record in enumerate(selected, start=1):
        queue_rows.append({
            "review_id": f"TABLE-{index:06d}",
            "review_priority": review_priority(record),
            "document_id": record.get("document_id", ""),
            "page": record.get("page", ""),
            "table_index": record.get("table_index", ""),
            "logical_row_number": record.get(
                "logical_row_number",
                "",
            ),
            "record_type": record.get("record_type", ""),
            "record_text": clean(
                record.get("record_text", "")
            ),
            "source_visual_rows": source_rows(record),
            "source_visual_row_count": record.get(
                "source_visual_row_count",
                "",
            ),
            "average_confidence": record.get(
                "average_confidence",
                "",
            ),
            "row_bbox": json.dumps(
                record.get("row_bbox", {}),
                ensure_ascii=False,
            ),
            "status": record.get("status", ""),
            "review_required": record.get(
                "review_required",
                "",
            ),
            "review_reasons": clean(
                record.get("review_reason")
                or record.get("review_reasons")
                or ""
            ),
        })

    columns = [
        "review_id",
        "review_priority",
        "document_id",
        "page",
        "table_index",
        "logical_row_number",
        "record_type",
        "record_text",
        "source_visual_rows",
        "source_visual_row_count",
        "average_confidence",
        "row_bbox",
        "status",
        "review_required",
        "review_reasons",
    ]

    csv_path = args.output_dir / "table_row_review_queue.csv"
    jsonl_path = args.output_dir / "table_row_review_queue.jsonl"
    summary_path = args.output_dir / "table_row_review_summary.json"

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
        writer.writerows(queue_rows)

    with jsonl_path.open(
        "w",
        encoding="utf-8",
    ) as handle:
        for queue_row, record in zip(
            queue_rows,
            selected,
        ):
            handle.write(
                json.dumps(
                    {
                        "review": queue_row,
                        "raw_record": record,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    priority_counts = Counter(
        row["review_priority"]
        for row in queue_rows
    )

    summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_jsonl": str(args.input_jsonl),
        "include_candidates": args.include_candidates,
        "records_input": len(records),
        "records_selected": len(selected),
        "priority_counts": dict(
            sorted(priority_counts.items())
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

    print("Universal table-row review queue complete.")
    print(f"Records input: {len(records)}")
    print(f"Records selected: {len(selected)}")
    print(f"Priority counts: {dict(priority_counts)}")
    print(f"CSV: {csv_path}")
    print(f"JSONL: {jsonl_path}")
    print(f"Summary: {summary_path}")


if __name__ == "__main__":
    main()
