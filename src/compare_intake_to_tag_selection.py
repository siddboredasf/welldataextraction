#!/usr/bin/env python3
"""Compare advisory document intake classifications to tag-page OCR selection.

This script never modifies selected_pages.txt or the authoritative tag batch.
It creates an auditable comparison and a separate supplemental review batch
for high-priority, native-text-classified pages not selected by tag OCR.
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path


RELEVANT_CLASSES = {
    "certificate",
    "material_traceability",
    "inspection_plan",
    "inspection_report",
    "spare_parts",
    "calibration",
    "pressure_test",
    "ndt",
    "equipment_metadata",
}

RELEVANT_ROUTES = {
    "table_extraction",
    "ocr_key_value",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare Step 0 document intake classifications with "
            "Step 1 tag-page selection without altering authoritative inputs."
        )
    )
    parser.add_argument(
        "--classification-csv",
        type=Path,
        required=True,
        help="page_classification.csv from classify_document_pages.py.",
    )
    parser.add_argument(
        "--profile-inventory-csv",
        type=Path,
        required=True,
        help="page_inventory.csv from profile_document.py.",
    )
    parser.add_argument(
        "--selected-pages",
        type=Path,
        required=True,
        help="selected_pages.txt from select_tag_pages.py.",
    )
    parser.add_argument(
        "--review-candidate-pages",
        type=Path,
        default=None,
        help="Optional review_candidate_pages.txt from select_tag_pages.py.",
    )
    parser.add_argument(
        "--document-id",
        required=True,
        help="Document identifier.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory for comparison CSV and summary.",
    )
    parser.add_argument(
        "--supplemental-batch-csv",
        type=Path,
        required=True,
        help="Advisory-only supplemental review batch CSV.",
    )
    parser.add_argument(
        "--minimum-score",
        type=int,
        default=100,
        help=(
            "Minimum classification score for supplemental review pages. "
            "Default: 100."
        ),
    )
    return parser.parse_args()


def read_page_numbers(path: Path | None) -> set[int]:
    if path is None or not path.is_file():
        return set()

    pages = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        value = line.strip()
        if value.isdigit():
            pages.add(int(value))
    return pages


def read_csv_by_page(path: Path) -> dict[int, dict[str, str]]:
    if not path.is_file():
        raise SystemExit(f"CSV not found: {path}")

    rows: dict[int, dict[str, str]] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            value = str(row.get("page", "")).strip()
            if value.isdigit():
                rows[int(value)] = row
    return rows


def as_int(value: str | None) -> int:
    try:
        return int(str(value or "").strip())
    except ValueError:
        return 0


def intake_relevant(row: dict[str, str]) -> bool:
    selected_class = row.get("selected_class", "").strip()
    route = row.get("recommended_route", "").strip()
    priority = row.get("priority", "").strip().lower()

    return (
        priority == "high"
        and selected_class in RELEVANT_CLASSES
        and route in RELEVANT_ROUTES
    )


def comparison_flag(
    *,
    page: int,
    classification: dict[str, str],
    selected_pages: set[int],
    review_pages: set[int],
) -> str:
    selected = page in selected_pages
    review_candidate = page in review_pages
    relevant = intake_relevant(classification)
    selected_class = classification.get("selected_class", "").strip()

    if selected and relevant:
        return "tag_selected_intake_agrees"
    if selected:
        return "tag_selected_intake_disagrees"
    if relevant:
        return "intake_relevant_not_tag_selected"
    if review_candidate:
        return "weak_tag_evidence"
    if not selected_class:
        return "unknown_or_low_evidence"
    return "not_selected_not_intake_relevant"


def review_priority(
    *,
    flag: str,
    classification: dict[str, str],
    profile: dict[str, str],
) -> str:
    if flag == "intake_relevant_not_tag_selected":
        return "high"

    if flag == "weak_tag_evidence":
        return "medium"

    weak_text = str(profile.get("weak_text", "")).strip().lower()
    image_heavy = str(profile.get("image_heavy", "")).strip().lower()

    if flag == "tag_selected_intake_disagrees" and (
        weak_text in {"true", "1", "yes"}
        or image_heavy in {"true", "1", "yes"}
    ):
        return "medium"

    if classification.get("priority", "").strip().lower() == "high":
        return "medium"

    return "low"


def main() -> None:
    args = parse_args()

    if args.minimum_score < 0:
        raise SystemExit("--minimum-score cannot be negative.")

    classifications = read_csv_by_page(args.classification_csv)
    profiles = read_csv_by_page(args.profile_inventory_csv)
    selected_pages = read_page_numbers(args.selected_pages)
    review_pages = read_page_numbers(args.review_candidate_pages)

    all_pages = sorted(
        set(classifications)
        | set(profiles)
        | selected_pages
        | review_pages
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.supplemental_batch_csv.parent.mkdir(parents=True, exist_ok=True)

    comparison_rows: list[dict[str, str | int]] = []
    supplemental_rows: list[dict[str, str | int]] = []
    flag_counts: Counter[str] = Counter()
    priority_counts: Counter[str] = Counter()

    for page in all_pages:
        classification = classifications.get(page, {})
        profile = profiles.get(page, {})

        selected_class = classification.get("selected_class", "").strip()
        route = classification.get("recommended_route", "").strip()
        priority = classification.get("priority", "").strip()
        score = as_int(classification.get("classification_score"))

        flag = comparison_flag(
            page=page,
            classification=classification,
            selected_pages=selected_pages,
            review_pages=review_pages,
        )
        intake_priority = review_priority(
            flag=flag,
            classification=classification,
            profile=profile,
        )

        row: dict[str, str | int] = {
            "document_id": args.document_id,
            "page": page,
            "tag_selected": str(page in selected_pages).lower(),
            "tag_review_candidate": str(page in review_pages).lower(),
            "selected_class": selected_class,
            "matched_classes": classification.get("matched_classes", ""),
            "matched_keywords": classification.get("matched_keywords", ""),
            "recommended_route": route,
            "priority": priority,
            "classification_score": score,
            "text_characters": classification.get("text_characters", ""),
            "word_count": classification.get("word_count", ""),
            "preview": classification.get("preview", ""),
            "profile_weak_text": profile.get("weak_text", ""),
            "profile_image_heavy": profile.get("image_heavy", ""),
            "profile_native_text_available": profile.get(
                "native_text_available",
                profile.get("has_native_text", ""),
            ),
            "comparison_flag": flag,
            "intake_review_priority": intake_priority,
        }
        comparison_rows.append(row)
        flag_counts[flag] += 1
        priority_counts[intake_priority] += 1

        qualifies = (
            page not in selected_pages
            and intake_relevant(classification)
            and score >= args.minimum_score
        )
        if qualifies:
            supplemental_rows.append({
                "document_id": args.document_id,
                "page": page,
                "recommended_route": route,
                "reason": (
                    "intake_"
                    f"{selected_class}_not_tag_selected"
                ),
            })

    comparison_path = args.output_dir / "tag_selection_comparison.csv"
    summary_path = (
        args.output_dir / "tag_selection_comparison_summary.txt"
    )

    fields = [
        "document_id",
        "page",
        "tag_selected",
        "tag_review_candidate",
        "selected_class",
        "matched_classes",
        "matched_keywords",
        "recommended_route",
        "priority",
        "classification_score",
        "text_characters",
        "word_count",
        "preview",
        "profile_weak_text",
        "profile_image_heavy",
        "profile_native_text_available",
        "comparison_flag",
        "intake_review_priority",
    ]

    with comparison_path.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(comparison_rows)

    supplemental_fields = [
        "document_id",
        "page",
        "recommended_route",
        "reason",
    ]
    with args.supplemental_batch_csv.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=supplemental_fields,
        )
        writer.writeheader()
        writer.writerows(supplemental_rows)

    with summary_path.open("w", encoding="utf-8") as handle:
        handle.write("Document intake versus tag-selection comparison\n")
        handle.write("===============================================\n\n")
        handle.write(f"Document ID: {args.document_id}\n")
        handle.write(f"Pages represented: {len(all_pages)}\n")
        handle.write(f"Tag OCR selected pages: {len(selected_pages)}\n")
        handle.write(
            f"Tag OCR review-candidate pages: {len(review_pages)}\n"
        )
        handle.write(
            "Advisory supplemental review pages: "
            f"{len(supplemental_rows)}\n"
        )
        handle.write(
            f"Supplemental minimum classification score: "
            f"{args.minimum_score}\n"
        )
        handle.write("\nComparison flags:\n")
        for flag, count in sorted(flag_counts.items()):
            handle.write(f"- {flag}: {count}\n")
        handle.write("\nIntake review priorities:\n")
        for priority, count in sorted(priority_counts.items()):
            handle.write(f"- {priority}: {count}\n")
        handle.write("\nOutputs:\n")
        handle.write(f"- {comparison_path}\n")
        handle.write(f"- {args.supplemental_batch_csv}\n")

    print("Intake/tag-selection comparison complete.")
    print(f"Document ID: {args.document_id}")
    print(f"Pages represented: {len(all_pages)}")
    print(f"Tag OCR selected pages: {len(selected_pages)}")
    print(
        "Advisory supplemental review pages: "
        f"{len(supplemental_rows)}"
    )
    print(f"Comparison: {comparison_path}")
    print(f"Summary: {summary_path}")
    print(
        "Supplemental batch (not automatically processed): "
        f"{args.supplemental_batch_csv}"
    )


if __name__ == "__main__":
    main()
