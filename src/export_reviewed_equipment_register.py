#!/usr/bin/env python3
"""
Create a reviewed equipment-register copy from generated source data plus
human-approved decision YAML.

Safety properties:
- Never overwrites the input register.
- Applies only decision=accept_equipment_name.
- Preserves original columns and values.
- Adds review provenance columns to the export.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import yaml


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def read_approved_names(path: Path) -> dict[str, dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    decisions = data.get("decisions", {})

    approved = {}

    for tag, item in decisions.items():
        if item.get("decision") != "accept_equipment_name":
            continue

        name = str(item.get("equipment_name", "")).strip()

        if not name:
            continue

        approved[str(tag).strip().upper()] = item

    return approved


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-csv", required=True, type=Path)
    parser.add_argument("--decisions-yaml", required=True, type=Path)
    parser.add_argument("--output-csv", required=True, type=Path)
    parser.add_argument("--output-xlsx", required=True, type=Path)
    args = parser.parse_args()

    if not args.input_csv.is_file():
        raise SystemExit(f"Input register not found: {args.input_csv}")

    if not args.decisions_yaml.is_file():
        raise SystemExit(f"Decision YAML not found: {args.decisions_yaml}")

    columns, rows = read_csv(args.input_csv)
    approved = read_approved_names(args.decisions_yaml)

    provenance_columns = [
        "equipment_name_review_decision",
        "equipment_name_review_confidence",
        "equipment_name_review_pages",
        "equipment_name_review_source",
    ]

    output_columns = list(columns)

    for column in provenance_columns:
        if column not in output_columns:
            output_columns.append(column)

    applied = []
    unresolved = []

    for row in rows:
        tag = str(row.get("equipment_tag", "")).strip().upper()
        decision = approved.get(tag)

        for column in provenance_columns:
            row.setdefault(column, "")

        if decision is None:
            unresolved.append(tag)
            continue

        row["equipment_name"] = str(decision["equipment_name"]).strip()
        row["equipment_name_review_decision"] = "accept_equipment_name"
        row["equipment_name_review_confidence"] = str(
            decision.get("confidence", "")
        ).strip()
        row["equipment_name_review_pages"] = " | ".join(
            str(page) for page in decision.get("evidence_pages", [])
        )
        row["equipment_name_review_source"] = str(
            args.decisions_yaml
        )

        applied.append(tag)

    args.output_csv.parent.mkdir(parents=True, exist_ok=True)

    with args.output_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=output_columns,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)

    try:
        import pandas as pd

        frame = pd.DataFrame(rows, columns=output_columns)

        with pd.ExcelWriter(args.output_xlsx, engine="openpyxl") as writer:
            frame.to_excel(
                writer,
                sheet_name="reviewed_equipment_register",
                index=False,
            )

    except Exception as error:
        print(f"Excel export skipped: {error}")

    print(f"Source rows read: {len(rows)}")
    print(f"Approved name decisions loaded: {len(approved)}")
    print(f"Names applied: {len(applied)}")
    print(f"Rows left without an approved name: {len(unresolved)}")
    print(f"Output CSV: {args.output_csv}")
    print(f"Output XLSX: {args.output_xlsx}")

    print("\nApplied:")
    for tag in sorted(applied):
        print(f"- {tag}")

    print("\nStill unresolved:")
    for tag in sorted(tag for tag in unresolved if tag):
        print(f"- {tag}")


if __name__ == "__main__":
    main()
