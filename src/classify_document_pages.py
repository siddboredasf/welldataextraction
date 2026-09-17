#!/usr/bin/env python3
"""Classify document pages using a configurable YAML taxonomy.

The script is document-agnostic. It reads a page-text index, page-level text
files, and a YAML taxonomy. It produces page classifications and a processing
queue without fixed document IDs, page numbers, equipment tags, or vendor rules.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Classify native-text pages using a YAML taxonomy and "
            "produce a document-processing queue."
        )
    )

    parser.add_argument(
        "--page-index",
        type=Path,
        required=True,
        help="CSV produced by extract_native_text.py.",
    )

    parser.add_argument(
        "--text-dir",
        type=Path,
        required=True,
        help="Directory containing page_###.txt files.",
    )

    parser.add_argument(
        "--taxonomy-config",
        type=Path,
        required=True,
        help="YAML taxonomy configuration file.",
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
        help="Directory for classification outputs.",
    )

    return parser.parse_args()


def load_taxonomy(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise SystemExit(f"Taxonomy config not found: {path}")

    with path.open(encoding="utf-8") as handle:
        taxonomy = yaml.safe_load(handle)

    if not isinstance(taxonomy, dict):
        raise SystemExit("Taxonomy YAML must contain a mapping.")

    classes = taxonomy.get("classes")

    if not isinstance(classes, dict) or not classes:
        raise SystemExit(
            "Taxonomy YAML must contain a non-empty 'classes' mapping."
        )

    for class_name, definition in classes.items():
        if not isinstance(definition, dict):
            raise SystemExit(
                f"Taxonomy class '{class_name}' must be a mapping."
            )

        keywords = definition.get("keywords")

        if not isinstance(keywords, list) or not keywords:
            raise SystemExit(
                f"Taxonomy class '{class_name}' needs a non-empty "
                "'keywords' list."
            )

        for keyword in keywords:
            if not isinstance(keyword, str):
                raise SystemExit(
                    f"Invalid keyword in taxonomy class '{class_name}': "
                    f"{keyword!r}. Every keyword must be a string. "
                    "Quote YAML values containing ':' or other YAML syntax."
                )

        if not definition.get("route"):
            raise SystemExit(
                f"Taxonomy class '{class_name}' has no route."
            )

        if not definition.get("priority"):
            raise SystemExit(
                f"Taxonomy class '{class_name}' has no priority."
            )

    return taxonomy


def read_page_text(text_dir: Path, page_number: int) -> str:
    path = text_dir / f"page_{page_number:03d}.txt"

    if not path.is_file():
        return ""

    return path.read_text(encoding="utf-8")


def match_keywords(text: str, keywords: list[str]) -> list[str]:
    lowered = text.lower()
    matches = []

    for keyword in keywords:
        if not isinstance(keyword, str):
            raise TypeError(
                "Every taxonomy keyword must be a string. "
                f"Found {keyword!r} instead. "
                "Quote YAML values that contain ':' or other YAML syntax."
            )

        if keyword.lower() in lowered:
            matches.append(keyword)

    return matches


def route_rank(route: str) -> int:
    ranks = {
        "manual_review": 0,
        "native_text": 1,
        "native_text_plus_visual_check": 2,
        "drawing_review": 3,
        "ocr_key_value": 4,
        "table_extraction": 5,
    }

    return ranks.get(route, 0)


def priority_rank(priority: str) -> int:
    ranks = {
        "low": 1,
        "medium": 2,
        "high": 3,
    }

    return ranks.get(priority.lower(), 0)


def classify_page(
    text: str,
    classes: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Return all matching classes plus one evidence-based routing decision."""

    matched_classes = []
    matched_keywords = []
    candidates = []

    for class_name, definition in classes.items():
        matches = match_keywords(text, definition["keywords"])

        if not matches:
            continue

        matched_classes.append(class_name)

        matched_keywords.extend(
            f"{class_name}:{keyword}"
            for keyword in matches
        )

        # Prefer specific, multi-word phrases over broad single-word terms.
        # This prevents an index page from being routed as a table simply
        # because it lists table/certificate/drawing section headings.
        keyword_word_count = sum(
            len(keyword.split())
            for keyword in matches
        )

        max_keyword_words = max(
            len(keyword.split())
            for keyword in matches
        )

        class_score = (
            max_keyword_words * 100
            + keyword_word_count * 10
            + priority_rank(definition["priority"]) * 2
        )

        candidates.append({
            "class_name": class_name,
            "route": definition["route"],
            "priority": definition["priority"],
            "matched_keywords": matches,
            "score": class_score,
        })

    # Index/register pages are structural pages. A strong index phrase should
    # override unrelated classes caused by the index listing all document types.
    index_candidate = next(
        (
            candidate
            for candidate in candidates
            if candidate["class_name"] == "index"
            and any(
                len(keyword.split()) >= 2
                for keyword in candidate["matched_keywords"]
            )
        ),
        None,
    )

    if index_candidate is not None:
        selected = index_candidate

    elif candidates:
        selected = max(
            candidates,
            key=lambda candidate: (
                candidate["score"],
                priority_rank(candidate["priority"]),
                route_rank(candidate["route"]),
            ),
        )

    else:
        selected = {
            "class_name": "",
            "route": "manual_review",
            "priority": "low",
            "matched_keywords": [],
            "score": 0,
        }

    return {
        "matched_classes": matched_classes,
        "matched_keywords": matched_keywords,
        "selected_class": selected["class_name"],
        "recommended_route": selected["route"],
        "priority": selected["priority"],
        "classification_score": selected["score"],
    }


def main() -> None:
    args = parse_args()

    if not args.page_index.is_file():
        raise SystemExit(f"Page index not found: {args.page_index}")

    if not args.text_dir.is_dir():
        raise SystemExit(f"Text directory not found: {args.text_dir}")

    taxonomy = load_taxonomy(args.taxonomy_config)
    classes = taxonomy["classes"]

    args.output_dir.mkdir(parents=True, exist_ok=True)

    with args.page_index.open(
        encoding="utf-8",
        newline="",
    ) as handle:
        page_rows = list(csv.DictReader(handle))

    classifications = []
    class_counter = Counter()
    route_counter = Counter()
    priority_counter = Counter()

    for page_row in page_rows:
        page_number = int(page_row["page"])
        text = read_page_text(args.text_dir, page_number)

        classification = classify_page(text, classes)

        matched_classes = classification["matched_classes"]
        matched_keywords = classification["matched_keywords"]
        selected_class = classification["selected_class"]
        selected_route = classification["recommended_route"]
        selected_priority = classification["priority"]
        classification_score = classification["classification_score"]

        for class_name in matched_classes:
            class_counter[class_name] += 1

        route_counter[selected_route] += 1
        priority_counter[selected_priority] += 1

        classifications.append({
            "document_id": args.document_id,
            "page": page_number,
            "selected_class": selected_class,
            "matched_classes": " | ".join(matched_classes),
            "matched_keywords": " | ".join(matched_keywords),
            "recommended_route": selected_route,
            "priority": selected_priority,
            "classification_score": classification_score,
            "text_characters": page_row.get(
                "text_characters",
                "",
            ),
            "word_count": page_row.get(
                "word_count",
                "",
            ),
            "preview": page_row.get("preview", ""),
        })

    classification_path = (
        args.output_dir / "page_classification.csv"
    )

    queue_path = (
        args.output_dir / "processing_queue.csv"
    )

    summary_path = (
        args.output_dir / "classification_summary.json"
    )

    columns = [
        "document_id",
        "page",
        "selected_class",
        "matched_classes",
        "matched_keywords",
        "recommended_route",
        "priority",
        "classification_score",
        "text_characters",
        "word_count",
        "preview",
    ]

    with classification_path.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=columns,
        )

        writer.writeheader()
        writer.writerows(classifications)

    queue_rows = sorted(
        classifications,
        key=lambda row: (
            -priority_rank(row["priority"]),
            -route_rank(row["recommended_route"]),
            -int(row["classification_score"]),
            row["page"],
        ),
    )

    with queue_path.open(
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

    summary = {
        "document_id": args.document_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "page_count": len(classifications),
        "taxonomy_config": str(args.taxonomy_config),
        "class_page_counts": dict(sorted(class_counter.items())),
        "route_page_counts": dict(sorted(route_counter.items())),
        "priority_page_counts": dict(
            sorted(priority_counter.items())
        ),
        "outputs": {
            "page_classification": str(classification_path),
            "processing_queue": str(queue_path),
        },
    }

    summary_path.write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8",
    )

    print("Document-page classification complete.")
    print(f"Document ID: {args.document_id}")
    print(f"Pages classified: {len(classifications)}")

    print("Routes:")
    for route, count in sorted(route_counter.items()):
        print(f"  {route}: {count}")

    print("Matched classes:")
    for class_name, count in sorted(class_counter.items()):
        print(f"  {class_name}: {count}")

    print(f"Classification: {classification_path}")
    print(f"Processing queue: {queue_path}")
    print(f"Summary: {summary_path}")


if __name__ == "__main__":
    main()
