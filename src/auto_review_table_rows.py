#!/usr/bin/env python3
"""Automatically assess generic table-row review items.

This is a document-agnostic second-pass reviewer. It does not know document
types, equipment tags, QCP fields, certificate fields, calibration columns,
or fixed table layouts.

It uses only general evidence:
- OCR confidence
- amount and quality of extracted text
- number of merged source rows
- whether a review crop exists
- whether the row is duplicated within the same page/table
- existing extraction/review flags

It writes a provisional automated-review decision:
- auto_accepted
- auto_accepted_low_confidence
- needs_human_review

The original OCR and review queue are never modified.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Automatically score generic table-row review items."
        )
    )

    parser.add_argument(
        "--review-queue-csv",
        type=Path,
        required=True,
        help=(
            "table_row_review_queue.csv produced by "
            "build_table_row_review_queue.py."
        ),
    )

    parser.add_argument(
        "--crop-manifest-json",
        type=Path,
        required=True,
        help=(
            "table_row_review_crops_manifest.json produced by "
            "create_table_row_review_crops.py."
        ),
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory for automated-review CSV, JSONL, and summary.",
    )

    parser.add_argument(
        "--accept-score",
        type=int,
        default=70,
        help=(
            "Minimum quality score for auto_accepted "
            "(default: 70, range: 0-100)."
        ),
    )

    parser.add_argument(
        "--provisional-score",
        type=int,
        default=45,
        help=(
            "Minimum quality score for auto_accepted_low_confidence "
            "(default: 45, range: 0-100)."
        ),
    )

    parser.add_argument(
        "--minimum-confidence",
        type=float,
        default=0.80,
        help=(
            "Average OCR confidence considered strong "
            "(default: 0.80)."
        ),
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


def normalise_for_duplicate_check(value: str) -> str:
    value = clean(value).upper()
    return re.sub(r"[^A-Z0-9]+", "", value)


def source_row_count(row: dict[str, str]) -> int:
    value = clean(row.get("source_visual_row_count"))

    if value:
        return to_int(value)

    values = clean(row.get("source_visual_rows"))

    if not values:
        return 0

    return len([
        item
        for item in values.split(",")
        if item.strip()
    ])


def load_crop_manifest(path: Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))

    items = payload.get("items", [])

    if not isinstance(items, list):
        return {}

    result = {}

    for item in items:
        if not isinstance(item, dict):
            continue

        review_id = clean(item.get("review_id"))

        if review_id:
            result[review_id] = item

    return result


def score_row(
    row: dict[str, str],
    crop: dict[str, Any] | None,
    duplicate_count: int,
    minimum_confidence: float,
) -> tuple[int, list[str], list[str]]:
    """Return score, positive evidence, and caution flags."""

    score = 0
    positive = []
    cautions = []

    text = clean(row.get("record_text"))
    confidence = to_float(row.get("average_confidence"))
    source_count = source_row_count(row)

    alphanumeric = sum(
        character.isalnum()
        for character in text
    )

    text_length = len(text)
    alphanumeric_ratio = (
        alphanumeric / text_length
        if text_length
        else 0.0
    )

    if crop is not None:
        crop_path = Path(str(crop.get("output_crop", "")))

        if crop_path.is_file():
            score += 20
            positive.append("review_crop_exists")
        else:
            cautions.append("crop_path_missing")
    else:
        cautions.append("crop_manifest_entry_missing")

    if confidence >= minimum_confidence:
        score += 30
        positive.append("strong_average_ocr_confidence")
    elif confidence >= 0.65:
        score += 18
        positive.append("moderate_average_ocr_confidence")
        cautions.append("moderate_ocr_confidence")
    elif confidence > 0:
        score += 5
        cautions.append("low_ocr_confidence")
    else:
        cautions.append("missing_ocr_confidence")

    if text_length >= 30:
        score += 20
        positive.append("substantive_text")
    elif text_length >= 12:
        score += 10
        positive.append("short_but_nonempty_text")
    else:
        cautions.append("very_short_or_empty_text")

    if alphanumeric_ratio >= 0.55:
        score += 15
        positive.append("readable_alphanumeric_content")
    elif alphanumeric_ratio >= 0.35:
        score += 7
        cautions.append("mixed_or_noisy_text")
    else:
        cautions.append("mostly_symbols_or_noise")

    if source_count == 1:
        score += 15
        positive.append("single_visual_row")
    elif 2 <= source_count <= 4:
        score += 7
        positive.append("bounded_wrapped_rows")
        cautions.append("merged_visual_rows")
    elif source_count > 4:
        cautions.append("many_merged_visual_rows")
    else:
        cautions.append("source_rows_unknown")

    if duplicate_count == 1:
        score += 10
        positive.append("unique_within_page_table")
    else:
        cautions.append("possible_duplicate_record")

    existing_reasons = clean(
        row.get("review_reasons")
        or row.get("review_reason")
        or ""
    ).lower()

    structural_reason_terms = (
        "no_numbered_record_start",
        "unstructured",
        "continuation_without_clear_start",
    )

    hard_failure_terms = (
        "missing_inspection_detail",
        "missing_activity_number",
        "no_table_candidate_detected",
    )

    if any(term in existing_reasons for term in structural_reason_terms):
        score -= 8
        cautions.append("low_structure_confidence")

    if any(term in existing_reasons for term in hard_failure_terms):
        score -= 25
        cautions.append("existing_hard_extraction_issue")

    score = max(0, min(100, score))

    return score, positive, cautions


def decision_from_score(
    score: int,
    cautions: list[str],
    accept_score: int,
    provisional_score: int,
) -> str:
    hard_failures = {
        "crop_path_missing",
        "crop_manifest_entry_missing",
        "very_short_or_empty_text",
        "mostly_symbols_or_noise",
        "existing_hard_extraction_issue",
        "missing_ocr_confidence",
    }

    if any(caution in hard_failures for caution in cautions):
        return "needs_human_review"

    if score >= accept_score:
        if "low_structure_confidence" in cautions:
            return "auto_accepted_low_structure"
        return "auto_accepted"

    if score >= provisional_score:
        return "auto_accepted_low_confidence"

    return "needs_human_review"


def main() -> None:
    args = parse_args()

    if not args.review_queue_csv.is_file():
        raise SystemExit(
            "Review queue CSV not found: "
            f"{args.review_queue_csv}"
        )

    if not args.crop_manifest_json.is_file():
        raise SystemExit(
            "Crop manifest JSON not found: "
            f"{args.crop_manifest_json}"
        )

    if not 0 <= args.provisional_score <= 100:
        raise SystemExit(
            "--provisional-score must be between 0 and 100."
        )

    if not 0 <= args.accept_score <= 100:
        raise SystemExit(
            "--accept-score must be between 0 and 100."
        )

    if args.provisional_score > args.accept_score:
        raise SystemExit(
            "--provisional-score cannot exceed --accept-score."
        )

    if not 0.0 <= args.minimum_confidence <= 1.0:
        raise SystemExit(
            "--minimum-confidence must be between 0.0 and 1.0."
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)

    with args.review_queue_csv.open(
        encoding="utf-8",
        newline="",
    ) as handle:
        queue_rows = list(csv.DictReader(handle))

    if not queue_rows:
        raise SystemExit(
            f"Review queue is empty: {args.review_queue_csv}"
        )

    crop_manifest = load_crop_manifest(args.crop_manifest_json)

    duplicate_groups: dict[
        tuple[str, str, str, str],
        int,
    ] = defaultdict(int)

    for row in queue_rows:
        key = (
            clean(row.get("document_id")),
            clean(row.get("page")),
            clean(row.get("table_index")),
            normalise_for_duplicate_check(
                row.get("record_text", "")
            ),
        )

        duplicate_groups[key] += 1

    reviewed_rows = []
    decision_counts = Counter()

    for row in queue_rows:
        review_id = clean(row.get("review_id"))

        key = (
            clean(row.get("document_id")),
            clean(row.get("page")),
            clean(row.get("table_index")),
            normalise_for_duplicate_check(
                row.get("record_text", "")
            ),
        )

        duplicate_count = duplicate_groups[key]

        crop = crop_manifest.get(review_id)

        score, positive, cautions = score_row(
            row=row,
            crop=crop,
            duplicate_count=duplicate_count,
            minimum_confidence=args.minimum_confidence,
        )

        decision = decision_from_score(
            score=score,
            cautions=cautions,
            accept_score=args.accept_score,
            provisional_score=args.provisional_score,
        )

        decision_counts[decision] += 1

        reviewed = dict(row)
        reviewed.update({
            "automated_review_status": decision,
            "automated_review_score": score,
            "automated_positive_evidence": " | ".join(positive),
            "automated_cautions": " | ".join(cautions),
            "review_crop": (
                str(crop.get("output_crop", ""))
                if crop is not None
                else ""
            ),
            "automated_reviewed_at_utc": datetime.now(
                timezone.utc
            ).isoformat(),
        })

        reviewed_rows.append(reviewed)

    fieldnames = list(queue_rows[0].keys()) + [
        "automated_review_status",
        "automated_review_score",
        "automated_positive_evidence",
        "automated_cautions",
        "review_crop",
        "automated_reviewed_at_utc",
    ]

    csv_path = args.output_dir / "table_row_auto_review.csv"
    jsonl_path = args.output_dir / "table_row_auto_review.jsonl"
    summary_path = args.output_dir / "table_row_auto_review_summary.json"

    with csv_path.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fieldnames,
        )
        writer.writeheader()
        writer.writerows(reviewed_rows)

    with jsonl_path.open(
        "w",
        encoding="utf-8",
    ) as handle:
        for row in reviewed_rows:
            handle.write(
                json.dumps(
                    row,
                    ensure_ascii=False,
                )
                + "\n"
            )

    summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "review_queue_csv": str(args.review_queue_csv),
        "crop_manifest_json": str(args.crop_manifest_json),
        "rows_scored": len(reviewed_rows),
        "accept_score": args.accept_score,
        "provisional_score": args.provisional_score,
        "minimum_confidence": args.minimum_confidence,
        "decision_counts": dict(
            sorted(decision_counts.items())
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

    print("Universal automated table-row review complete.")
    print(f"Rows scored: {len(reviewed_rows)}")
    print(
        "Decisions: "
        + ", ".join(
            f"{name}={count}"
            for name, count in sorted(
                decision_counts.items()
            )
        )
    )
    print(f"CSV: {csv_path}")
    print(f"JSONL: {jsonl_path}")
    print(f"Summary: {summary_path}")


if __name__ == "__main__":
    main()
