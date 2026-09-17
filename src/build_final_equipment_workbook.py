#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import pandas as pd


def read_csv(path: Path) -> pd.DataFrame:
    if not path.is_file():
        return pd.DataFrame()

    return pd.read_csv(
        path,
        dtype=str,
        keep_default_na=False,
        encoding="utf-8-sig",
    )


def add_link_columns(
    measurements: pd.DataFrame,
    registry: pd.DataFrame,
) -> pd.DataFrame:
    if measurements.empty or registry.empty:
        return measurements

    registry_tags = set(
        registry.get("equipment_tag", pd.Series(dtype=str))
    )

    if "equipment_tag" in measurements.columns:
        measurements["registry_match"] = (
            measurements["equipment_tag"]
            .isin(registry_tags)
            .map({True: "matched", False: "unmatched"})
        )

    if "serial_number" in measurements.columns:
        registry_serials = set(
            registry.get("serial_number", pd.Series(dtype=str))
        )

        measurements["serial_match"] = (
            measurements["serial_number"]
            .isin(registry_serials)
            .map({True: "matched", False: "unmatched"})
        )

    return measurements


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the final equipment and measurement workbook."
    )

    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--measurements", required=True, type=Path)
    parser.add_argument("--metadata", required=True, type=Path)
    parser.add_argument("--comparison", required=True, type=Path)
    parser.add_argument("--review", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)

    args = parser.parse_args()

    registry = read_csv(args.registry)
    measurements = read_csv(args.measurements)
    metadata = read_csv(args.metadata)
    comparison = read_csv(args.comparison)
    review = read_csv(args.review)

    measurements = add_link_columns(
        measurements,
        registry,
    )

    lineage = pd.DataFrame([
        {
            "sheet_name": "Equipment Registry",
            "source_file": str(args.registry),
            "source_stage": "Step 7G",
            "join_key": "equipment_tag",
            "transformation": "Validated final registry copied unchanged",
            "row_count": len(registry),
        },
        {
            "sheet_name": "Measurement Readings",
            "source_file": str(args.measurements),
            "source_stage": "Step 3D",
            "join_key": "equipment_tag",
            "transformation": (
                "Measurement rows retained separately and linked "
                "to the equipment registry"
            ),
            "row_count": len(measurements),
        },
        {
            "sheet_name": "Metadata Evidence",
            "source_file": str(args.metadata),
            "source_stage": "Step 6G",
            "join_key": "equipment_tag",
            "transformation": "Promoted evidence copied unchanged",
            "row_count": len(metadata),
        },
        {
            "sheet_name": "Provisional Comparison",
            "source_file": str(args.comparison),
            "source_stage": "Step 7D",
            "join_key": "equipment_tag + field_name",
            "transformation": "Comparison output copied unchanged",
            "row_count": len(comparison),
        },
        {
            "sheet_name": "Review Queue",
            "source_file": str(args.review),
            "source_stage": "Step 7E",
            "join_key": "equipment_tag + field_name",
            "transformation": "Review output copied unchanged",
            "row_count": len(review),
        },
    ])

    readme = pd.DataFrame([
        ["Workbook", "Final equipment and measurement package"],
        ["Registry grain", "One row per equipment item"],
        ["Measurement grain", "One row per measurement observation"],
        ["Primary link", "equipment_tag"],
        ["Secondary link", "serial_number where available"],
        ["Identity rule", (
            "Evidence-linked status requires serial-number evidence"
        )],
        ["Source policy", (
            "Raw and validated source sheets are preserved separately"
        )],
    ], columns=["item", "value"])

    args.output.parent.mkdir(parents=True, exist_ok=True)

    with pd.ExcelWriter(args.output, engine="openpyxl") as writer:
        readme.to_excel(
            writer,
            sheet_name="README",
            index=False,
        )
        registry.to_excel(
            writer,
            sheet_name="Equipment Registry",
            index=False,
        )
        measurements.to_excel(
            writer,
            sheet_name="Measurement Readings",
            index=False,
        )
        metadata.to_excel(
            writer,
            sheet_name="Metadata Evidence",
            index=False,
        )
        comparison.to_excel(
            writer,
            sheet_name="Provisional Comparison",
            index=False,
        )
        review.to_excel(
            writer,
            sheet_name="Review Queue",
            index=False,
        )
        lineage.to_excel(
            writer,
            sheet_name="Lineage",
            index=False,
        )

    print(f"Registry rows: {len(registry)}")
    print(f"Measurement rows: {len(measurements)}")
    print(f"Metadata evidence rows: {len(metadata)}")
    print(f"Workbook: {args.output}")


if __name__ == "__main__":
    main()