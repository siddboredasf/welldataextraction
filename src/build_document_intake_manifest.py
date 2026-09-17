#!/usr/bin/env python3
"""Validate Step 0 artifacts and write a stable intake manifest and summary."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate document-intake artifacts and write an intake "
            "manifest plus a readable summary."
        )
    )
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--document-id", required=True)
    parser.add_argument("--taxonomy-config", type=Path, required=True)
    parser.add_argument("--profile-inventory-csv", type=Path, required=True)
    parser.add_argument("--native-text-index-csv", type=Path, required=True)
    parser.add_argument("--classification-csv", type=Path, required=True)
    parser.add_argument("--processing-queue-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def read_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise SystemExit(f"Required intake artifact missing: {path}")

    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> None:
    args = parse_args()

    if not args.pdf.is_file():
        raise SystemExit(f"PDF not found: {args.pdf}")
    if not args.taxonomy_config.is_file():
        raise SystemExit(
            f"Taxonomy config not found: {args.taxonomy_config}"
        )

    profile_rows = read_rows(args.profile_inventory_csv)
    native_text_rows = read_rows(args.native_text_index_csv)
    classification_rows = read_rows(args.classification_csv)
    queue_rows = read_rows(args.processing_queue_csv)

    profile_pages = {
        str(row.get("page", "")).strip()
        for row in profile_rows
        if str(row.get("page", "")).strip()
    }
    text_pages = {
        str(row.get("page", "")).strip()
        for row in native_text_rows
        if str(row.get("page", "")).strip()
    }
    classification_pages = {
        str(row.get("page", "")).strip()
        for row in classification_rows
        if str(row.get("page", "")).strip()
    }

    if profile_pages != text_pages or profile_pages != classification_pages:
        raise SystemExit(
            "Intake page sets do not match across profile, native-text, "
            "and classification outputs."
        )

    class_counts = Counter(
        row.get("selected_class", "").strip() or "unclassified"
        for row in classification_rows
    )
    route_counts = Counter(
        row.get("recommended_route", "").strip() or "manual_review"
        for row in classification_rows
    )
    priority_counts = Counter(
        row.get("priority", "").strip() or "low"
        for row in classification_rows
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output_dir / "intake_manifest.json"
    summary_path = args.output_dir / "intake_summary.txt"

    payload = {
        "intake_version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "document_id": args.document_id,
        "source_pdf": str(args.pdf),
        "page_count": len(classification_rows),
        "taxonomy_config": str(args.taxonomy_config),
        "artifacts": {
            "profile_inventory_csv": str(args.profile_inventory_csv),
            "native_text_index_csv": str(args.native_text_index_csv),
            "classification_csv": str(args.classification_csv),
            "processing_queue_csv": str(args.processing_queue_csv),
        },
        "counts": {
            "profile_rows": len(profile_rows),
            "native_text_rows": len(native_text_rows),
            "classification_rows": len(classification_rows),
            "processing_queue_rows": len(queue_rows),
            "class_page_counts": dict(sorted(class_counts.items())),
            "route_page_counts": dict(sorted(route_counts.items())),
            "priority_page_counts": dict(sorted(priority_counts.items())),
        },
    }

    manifest_path.write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )

    with summary_path.open("w", encoding="utf-8") as handle:
        handle.write("Document intake summary\n")
        handle.write("========================\n\n")
        handle.write(f"Document ID: {args.document_id}\n")
        handle.write(f"Source PDF: {args.pdf}\n")
        handle.write(f"Pages profiled/classified: {len(classification_rows)}\n")
        handle.write(f"Taxonomy: {args.taxonomy_config}\n")
        handle.write("\nSelected classes:\n")
        for name, count in sorted(class_counts.items()):
            handle.write(f"- {name}: {count}\n")
        handle.write("\nSuggested routes:\n")
        for name, count in sorted(route_counts.items()):
            handle.write(f"- {name}: {count}\n")
        handle.write("\nPriorities:\n")
        for name, count in sorted(priority_counts.items()):
            handle.write(f"- {name}: {count}\n")
        handle.write("\nArtifacts:\n")
        for name, path in payload["artifacts"].items():
            handle.write(f"- {name}: {path}\n")

    print("Document intake manifest complete.")
    print(f"Document ID: {args.document_id}")
    print(f"Pages validated: {len(classification_rows)}")
    print(f"Manifest: {manifest_path}")
    print(f"Summary: {summary_path}")


if __name__ == "__main__":
    main()
