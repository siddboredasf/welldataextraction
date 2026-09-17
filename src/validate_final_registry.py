#!/usr/bin/env python3

import argparse
import csv
import re
from pathlib import Path


def clean(value):
    return " ".join(str(value or "").split()).strip()


def digits(value):
    return re.sub(r"\D", "", clean(value))


def parse_args():
    parser = argparse.ArgumentParser(
        description="Validate final equipment registry identities."
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main():
    args = parse_args()

    with args.input.open(
        encoding="utf-8-sig",
        newline="",
    ) as handle:
        rows = list(csv.DictReader(handle))
        fields = list(rows[0].keys()) if rows else []

    for extra in [
        "identity_validation_status",
        "identity_validation_reason",
    ]:
        if extra not in fields:
            fields.append(extra)

    output_rows = []

    for row in rows:
        tag = clean(row.get("equipment_tag"))
        serial = clean(row.get("serial_number"))

        if not tag:
            status = "review_required"
            reason = "missing equipment tag"

        elif not serial:
            status = "review_required"
            reason = "missing serial number"

        elif len(digits(serial)) < 6:
            status = "review_required"
            reason = "serial number has fewer than six digits"

        elif len(serial) != len(digits(serial)):
            status = "review_required"
            reason = "serial number contains non-digit characters"

        elif len(serial) == 6:
            status = "review_required"
            reason = (
                "six-digit serial may require a leading zero; "
                "verify against source"
            )

        else:
            status = "valid"
            reason = "identity fields present"

        row["identity_validation_status"] = status
        row["identity_validation_reason"] = reason
        output_rows.append(row)

    args.output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with args.output.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fields,
        )
        writer.writeheader()
        writer.writerows(output_rows)

    print(f"Input rows: {len(rows)}")
    print(f"Output: {args.output}")

    for row in output_rows:
        if row["identity_validation_status"] != "valid":
            print(
                row["equipment_tag"],
                "|",
                row["serial_number"],
                "|",
                row["identity_validation_status"],
                "|",
                row["identity_validation_reason"],
            )


if __name__ == "__main__":
    main()
