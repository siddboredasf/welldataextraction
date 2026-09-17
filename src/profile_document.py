#!/usr/bin/env python3
"""Create a document-agnostic PDF profile for downstream extraction routing."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pymupdf


DEFAULT_WEAK_TEXT_CHARS = 80
DEFAULT_WEAK_TEXT_WORDS = 15
DEFAULT_IMAGE_HEAVY_COUNT = 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Profile a PDF and write page-level routing evidence."
    )
    parser.add_argument("--pdf", type=Path, required=True, help="Source PDF.")
    parser.add_argument(
        "--document-id",
        help="Optional document identifier. Defaults to the PDF filename stem.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Output directory. Defaults to data/outputs/<document-id>/profile.",
    )
    parser.add_argument(
        "--weak-text-chars",
        type=int,
        default=DEFAULT_WEAK_TEXT_CHARS,
        help=f"Native-text character threshold; default: {DEFAULT_WEAK_TEXT_CHARS}.",
    )
    parser.add_argument(
        "--weak-text-words",
        type=int,
        default=DEFAULT_WEAK_TEXT_WORDS,
        help=f"Native-text word threshold; default: {DEFAULT_WEAK_TEXT_WORDS}.",
    )
    parser.add_argument(
        "--image-heavy-count",
        type=int,
        default=DEFAULT_IMAGE_HEAVY_COUNT,
        help=f"Embedded-image threshold; default: {DEFAULT_IMAGE_HEAVY_COUNT}.",
    )
    return parser.parse_args()


def derive_document_id(pdf_path: Path) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", pdf_path.stem).strip("_")
    return value or "document"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalise_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def printable_ratio(text: str) -> float:
    if not text:
        return 0.0
    printable = sum(character.isprintable() for character in text)
    return printable / len(text)


def alpha_numeric_ratio(text: str) -> float:
    if not text:
        return 0.0
    alpha_numeric = sum(character.isalnum() for character in text)
    return alpha_numeric / len(text)


def page_route(row: dict, weak_chars: int, weak_words: int, image_heavy_count: int) -> str:
    if row["native_text_characters"] < weak_chars or row["native_text_words"] < weak_words:
        return "render_ocr"
    if row["alpha_numeric_ratio"] < 0.25:
        return "render_ocr"
    if row["embedded_image_count"] >= image_heavy_count:
        return "native_text_plus_visual_check"
    return "native_text"


def main() -> None:
    args = parse_args()

    if not args.pdf.is_file():
        raise SystemExit(f"PDF not found: {args.pdf}")
    if args.weak_text_chars < 0 or args.weak_text_words < 0:
        raise SystemExit("Weak-text thresholds must not be negative.")

    document_id = args.document_id or derive_document_id(args.pdf)
    output_dir = args.output_dir or Path("data/outputs") / document_id / "profile"
    output_dir.mkdir(parents=True, exist_ok=True)

    document = pymupdf.open(args.pdf)
    rows = []

    try:
        for page_index, page in enumerate(document):
            raw_text = page.get_text("text", sort=True)
            text = normalise_text(raw_text)
            rect = page.rect
            row = {
                "page": page_index + 1,
                "width_points": round(rect.width, 2),
                "height_points": round(rect.height, 2),
                "page_rotation": page.rotation,
                "native_text_characters": len(text),
                "native_text_words": len(text.split()),
                "printable_ratio": round(printable_ratio(text), 4),
                "alpha_numeric_ratio": round(alpha_numeric_ratio(text), 4),
                "embedded_image_count": len(page.get_images(full=True)),
                "native_text_preview": text[:350],
            }
            row["recommended_route"] = page_route(
                row,
                args.weak_text_chars,
                args.weak_text_words,
                args.image_heavy_count,
            )
            rows.append(row)

            if (page_index + 1) % 50 == 0 or page_index + 1 == len(document):
                print(f"Profiled page {page_index + 1}/{len(document)}")
    finally:
        metadata = document.metadata
        page_count = len(document)
        toc_count = len(document.get_toc(simple=False))
        document.close()

    inventory_path = output_dir / "page_inventory.csv"
    manifest_path = output_dir / "manifest.json"
    weak_text_path = output_dir / "weak_text_pages.txt"
    native_text_path = output_dir / "native_text_pages.txt"
    visual_check_path = output_dir / "native_text_plus_visual_check_pages.txt"

    columns = [
        "page",
        "width_points",
        "height_points",
        "page_rotation",
        "native_text_characters",
        "native_text_words",
        "printable_ratio",
        "alpha_numeric_ratio",
        "embedded_image_count",
        "recommended_route",
        "native_text_preview",
    ]

    with inventory_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)

    routes = {
        "render_ocr": [row["page"] for row in rows if row["recommended_route"] == "render_ocr"],
        "native_text": [row["page"] for row in rows if row["recommended_route"] == "native_text"],
        "native_text_plus_visual_check": [
            row["page"]
            for row in rows
            if row["recommended_route"] == "native_text_plus_visual_check"
        ],
    }

    for filename, pages in (
        (weak_text_path, routes["render_ocr"]),
        (native_text_path, routes["native_text"]),
        (visual_check_path, routes["native_text_plus_visual_check"]),
    ):
        filename.write_text(
            "".join(f"{page:03d}\n" for page in pages),
            encoding="utf-8",
        )

    manifest = {
        "document_id": document_id,
        "source_pdf": str(args.pdf),
        "source_pdf_sha256": sha256(args.pdf),
        "source_pdf_size_bytes": args.pdf.stat().st_size,
        "profiled_at_utc": datetime.now(timezone.utc).isoformat(),
        "page_count": page_count,
        "toc_entry_count": toc_count,
        "pdf_metadata": metadata,
        "thresholds": {
            "weak_text_characters": args.weak_text_chars,
            "weak_text_words": args.weak_text_words,
            "image_heavy_count": args.image_heavy_count,
        },
        "route_counts": {route: len(pages) for route, pages in routes.items()},
        "outputs": {
            "page_inventory": str(inventory_path),
            "weak_text_pages": str(weak_text_path),
            "native_text_pages": str(native_text_path),
            "visual_check_pages": str(visual_check_path),
        },
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print("\nDocument profile complete.")
    print(f"Document ID: {document_id}")
    print(f"Pages: {page_count}")
    print(f"Native-text route: {len(routes['native_text'])}")
    print(f"Native-text + visual-check route: {len(routes['native_text_plus_visual_check'])}")
    print(f"Render/OCR route: {len(routes['render_ocr'])}")
    print(f"Inventory: {inventory_path}")
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
