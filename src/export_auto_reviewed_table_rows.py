#!/usr/bin/env python3
"""Split universal table-row auto-review output into accepted and unresolved sets.

The tool is document-agnostic. It reads table_row_auto_review.csv and creates
separate accepted and unresolved outputs without interpreting document type,
table schema, equipment tags, certificates, calibration values, or QCP fields.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ACCEPTED_STATUSES = {
    "auto_accepted",
    "auto_accepted_low_confidence",
    "auto_accepted_low_structure",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Split automated table-row review results into accepted and "
            "unresolved universal evidence sets."
        )
    )

    parser.add_argument(
        "--input-csv",
        type=Path,
        required=True,
        help=(
            "table_row_auto_review.csv produced by "
            "auto_review_table_rows.py."
        ),
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Root directory for accepted and unresolved exports.",
    )

    return parser.parse_args()


def clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def write_csv(
    path: Path,
    rows: list[dict[str, str]],
    fieldnames: list[str],
) -> None:
    with path.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fieldnames,
        )
        writer.writeheader()
        writer.writerows(rows)


def write_jsonl(
    path: Path,
    rows: list[dict[str, str]],
) -> None:
    with path.open(
        "w",
        encoding="utf-8",
    ) as handle:
        for row in rows:
            handle.write(
                json.dumps(
                    row,
                    ensure_ascii=False,
                )
                + "\n"
            )


def main() -> None:
    args = parse_args()

    if not args.input_csv.is_file():
        raise SystemExit(
            f"Input CSV not found: {args.input_csv}"
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)

    accepted_dir = args.output_dir / "accepted"
    unresolved_dir = args.output_dir / "unresolved"

    accepted_dir.mkdir(parents=True, exist_ok=True)
    unresolved_dir.mkdir(parents=True, exist_ok=True)

    with args.input_csv.open(
        encoding="utf-8",
        newline="",
    ) as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        fieldnames = reader.fieldnames or []

    if not rows:
        raise SystemExit(
            f"Input CSV is empty: {args.input_csv}"
        )

    required_column = "automated_review_status"

    if required_column not in fieldnames:
        raise SystemExit(
            "Input CSV is missing required column: "
            f"{required_column}"
        )

    accepted = []
    unresolved = []
    decision_counts = Counter()

    for row in rows:
        status = clean(
            row.get("automated_review_status")
        )

        decision_counts[status] += 1

        if status in ACCEPTED_STATUSES:
            accepted.append(row)
        else:
            unresolved.append(row)

    accepted_csv = (
        accepted_dir / "table_rows_auto_accepted.csv"
    )
    accepted_jsonl = (
        accepted_dir / "table_rows_auto_accepted.jsonl"
    )

    unresolved_csv = (
        unresolved_dir / "table_rows_needing_review.csv"
    )
    unresolved_jsonl = (
        unresolved_dir / "table_rows_needing_review.jsonl"
    )

    write_csv(
        path=accepted_csv,
        rows=accepted,
        fieldnames=fieldnames,
    )

    write_jsonl(
        path=accepted_jsonl,
        rows=accepted,
    )

    write_csv(
        path=unresolved_csv,
        rows=unresolved,
        fieldnames=fieldnames,
    )

    write_jsonl(
        path=unresolved_jsonl,
        rows=unresolved,
    )

    summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_csv": str(args.input_csv),
        "records_input": len(rows),
        "accepted_records": len(accepted),
        "unresolved_records": len(unresolved),
        "decision_counts": dict(
            sorted(decision_counts.items())
        ),
        "accepted_statuses": sorted(ACCEPTED_STATUSES),
        "outputs": {
            "accepted_csv": str(accepted_csv),
            "accepted_jsonl": str(accepted_jsonl),
            "unresolved_csv": str(unresolved_csv),
            "unresolved_jsonl": str(unresolved_jsonl),
        },
    }

    summary_path = args.output_dir / "auto_review_export_summary.json"

    summary_path.write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8",
    )

    print("Automated review export complete.")
    print(f"Records input: {len(rows)}")
    print(f"Accepted records: {len(accepted)}")
    print(f"Unresolved records: {len(unresolved)}")
    print(f"Accepted CSV: {accepted_csv}")
    print(f"Unresolved CSV: {unresolved_csv}")
    print(f"Summary: {summary_path}")


if __name__ == "__main__":
    main()
