#!/usr/bin/env python3
"""Extract native text for a configurable page range or page-list file."""

from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pymupdf


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract native PDF text into per-page files and a searchable index."
    )
    parser.add_argument("--pdf", type=Path, required=True, help="Source PDF.")
    parser.add_argument("--document-id", required=True, help="Document identifier.")
    parser.add_argument("--output-dir", type=Path, required=True, help="Output directory.")
    parser.add_argument("--page-start", type=int, help="First page, inclusive.")
    parser.add_argument("--page-end", type=int, help="Last page, inclusive.")
    parser.add_argument("--pages-file", type=Path, help="Optional file containing one page number per line.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing page text files.")
    return parser.parse_args()


def normalise_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def read_pages_file(path: Path) -> set[int]:
    if not path.is_file():
        raise SystemExit(f"Pages file not found: {path}")
    pages = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        value = line.strip()
        if value:
            pages.add(int(value))
    return pages


def resolve_pages(args: argparse.Namespace, page_count: int) -> list[int]:
    if args.pages_file:
        pages = read_pages_file(args.pages_file)
    else:
        start = args.page_start or 1
        end = args.page_end or page_count
        pages = set(range(start, end + 1))

    invalid = sorted(page for page in pages if page < 1 or page > page_count)
    if invalid:
        raise SystemExit(f"Invalid page numbers for a {page_count}-page PDF: {invalid[:20]}")
    return sorted(pages)


def main() -> None:
    args = parse_args()
    if not args.pdf.is_file():
        raise SystemExit(f"PDF not found: {args.pdf}")

    pages_dir = args.output_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)

    document = pymupdf.open(args.pdf)
    pages = resolve_pages(args, len(document))
    rows = []
    combined_sections = []

    try:
        for page_number in pages:
            output_path = pages_dir / f"page_{page_number:03d}.txt"
            text = document[page_number - 1].get_text("text", sort=True).strip()
            compact = normalise_text(text)

            if args.overwrite or not output_path.exists():
                output_path.write_text(text, encoding="utf-8")

            rows.append({
                "document_id": args.document_id,
                "page": page_number,
                "text_characters": len(compact),
                "word_count": len(compact.split()),
                "page_text_file": str(output_path),
                "preview": compact[:400],
            })

            combined_sections.append(
                f"{'=' * 90}\nPAGE {page_number:03d}\n{'-' * 90}\n{text}\n"
            )

            if len(rows) % 50 == 0 or len(rows) == len(pages):
                print(f"Extracted {len(rows)}/{len(pages)} pages")
    finally:
        document.close()

    index_path = args.output_dir / "page_text_index.csv"
    combined_text_path = args.output_dir / "full_document_text.txt"
    summary_path = args.output_dir / "native_text_summary.json"

    with index_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    combined_text_path.write_text("\n".join(combined_sections), encoding="utf-8")
    summary_path.write_text(
        json.dumps({
            "document_id": args.document_id,
            "source_pdf": str(args.pdf),
            "page_count_extracted": len(rows),
            "pages": pages,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "outputs": {
                "pages_dir": str(pages_dir),
                "page_index": str(index_path),
                "combined_text": str(combined_text_path),
            },
        }, indent=2),
        encoding="utf-8",
    )

    print("\nNative-text extraction complete.")
    print(f"Pages extracted: {len(rows)}")
    print(f"Page index: {index_path}")
    print(f"Combined text: {combined_text_path}")
    print(f"Summary: {summary_path}")


if __name__ == "__main__":
    main()
