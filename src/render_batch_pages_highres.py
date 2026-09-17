#!/usr/bin/env python3
"""Render high-resolution PDF page images for a generic batch CSV.

Reads page numbers directly from a batch created by build_processing_batches.py.
It does not depend on a pre-existing selected_pages image directory and does
not contain document-specific page lists, tags, or vendor rules.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import fitz


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Render the PDF pages listed in a generic batch CSV at high "
            "resolution."
        )
    )

    parser.add_argument(
        "--batch",
        type=Path,
        required=True,
        help="Batch CSV created by build_processing_batches.py.",
    )

    parser.add_argument(
        "--pdf",
        type=Path,
        default=None,
        help=(
            "PDF source file. Default: data/raw/<document-id>.pdf, where "
            "document-id is read from the batch."
        ),
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory for high-resolution page PNG files.",
    )

    parser.add_argument(
        "--dpi",
        type=int,
        default=400,
        help="Render DPI; default is 400.",
    )

    parser.add_argument(
        "--overwrite",
        action="store_true",
        help=(
            "Destructively delete and recreate the output directory before "
            "rendering. Default behavior safely resumes and skips valid "
            "existing page PNGs."
        ),
    )

    parser.add_argument(
        "--verify-existing",
        action="store_true",
        help=(
            "Open existing PNGs with PyMuPDF before treating them as "
            "complete. Useful after an interrupted or uncertain run."
        ),
    )

    return parser.parse_args()



def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_valid_png(
    path: Path,
    verify_contents: bool,
) -> bool:
    """Return whether a prior rendered PNG can safely be reused."""
    try:
        if not path.is_file() or path.stat().st_size == 0:
            return False

        if not verify_contents:
            return True

        image = fitz.open(path)
        try:
            return len(image) >= 1
        finally:
            image.close()

    except (OSError, RuntimeError, ValueError):
        return False


def atomic_save_pixmap(
    pixmap: fitz.Pixmap,
    output_path: Path,
) -> None:
    """Avoid exposing a partially written PNG as a completed page."""
    temporary_path = output_path.with_name(
        f"{output_path.stem}.partial{output_path.suffix}"
    )

    try:
        if temporary_path.exists():
            temporary_path.unlink()

        pixmap.save(str(temporary_path))
        temporary_path.replace(output_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def atomic_write_json(
    path: Path,
    payload: dict,
) -> None:
    """Write manifests atomically so interruption cannot corrupt them."""
    temporary_path = path.with_name(path.name + ".partial")

    try:
        temporary_path.write_text(
            json.dumps(payload, indent=2),
            encoding="utf-8",
        )
        temporary_path.replace(path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def main() -> None:
    args = parse_args()

    if not args.batch.is_file():
        raise SystemExit(f"Batch CSV not found: {args.batch}")

    if args.dpi < 72:
        raise SystemExit("--dpi must be at least 72.")

    with args.batch.open(
        encoding="utf-8",
        newline="",
    ) as handle:
        rows = list(csv.DictReader(handle))

    if not rows:
        raise SystemExit(f"Batch CSV is empty: {args.batch}")

    required_columns = {"document_id", "page"}

    missing_columns = required_columns - set(rows[0])

    if missing_columns:
        raise SystemExit(
            "Batch is missing required columns: "
            + ", ".join(sorted(missing_columns))
        )

    document_ids = {
        row["document_id"].strip()
        for row in rows
        if row["document_id"].strip()
    }

    if len(document_ids) != 1:
        raise SystemExit(
            "Batch must contain exactly one document_id. Found: "
            + ", ".join(sorted(document_ids))
        )

    document_id = next(iter(document_ids))

    pages = sorted({
        int(row["page"])
        for row in rows
    })

    pdf_path = (
        args.pdf
        if args.pdf is not None
        else Path(f"data/raw/{document_id}.pdf")
    )

    if not pdf_path.is_file():
        raise SystemExit(f"PDF not found: {pdf_path}")

    if args.output_dir.exists() and args.overwrite:
        shutil.rmtree(args.output_dir)

    args.output_dir.mkdir(parents=True, exist_ok=True)

    scale = args.dpi / 72.0
    matrix = fitz.Matrix(scale, scale)

    manifest_path = args.output_dir / "render_manifest.json"

    manifest = {
        "created_at_utc": utc_now(),
        "source_batch": str(args.batch),
        "document_id": document_id,
        "source_pdf": str(pdf_path),
        "dpi": args.dpi,
        "page_count": len(pages),
        "requested_pages": pages,
        "rendered_pages": [],
        "skipped_existing_pages": [],
        "failed_pages": [],
        "resume_mode": not args.overwrite,
        "verify_existing": args.verify_existing,
        "status": "running",
    }

    atomic_write_json(manifest_path, manifest)

    document = fitz.open(pdf_path)

    try:
        page_total = len(document)

        invalid_pages = [
            page
            for page in pages
            if page < 1 or page > page_total
        ]

        if invalid_pages:
            values = ", ".join(
                str(page)
                for page in invalid_pages
            )
            raise SystemExit(
                f"Requested pages outside PDF range 1-{page_total}: "
                f"{values}"
            )

        for page_number in pages:
            output_path = (
                args.output_dir / f"page_{page_number:03d}.png"
            )

            if not args.overwrite and is_valid_png(
                output_path,
                verify_contents=args.verify_existing,
            ):
                manifest["skipped_existing_pages"].append({
                    "page": page_number,
                    "image": str(output_path),
                })

                print(
                    f"Skipped existing page {page_number:03d}: "
                    f"{output_path.name}"
                )
                continue

            try:
                pixmap = document.load_page(
                    page_number - 1
                ).get_pixmap(
                    matrix=matrix,
                    alpha=False,
                )

                atomic_save_pixmap(
                    pixmap=pixmap,
                    output_path=output_path,
                )

                manifest["rendered_pages"].append({
                    "page": page_number,
                    "image": str(output_path),
                    "width": pixmap.width,
                    "height": pixmap.height,
                })

                print(
                    f"Rendered page {page_number:03d}: "
                    f"{pixmap.width}x{pixmap.height}"
                )

            except Exception as error:
                manifest["failed_pages"].append({
                    "page": page_number,
                    "image": str(output_path),
                    "error": str(error),
                })
                manifest["status"] = "failed"
                manifest["failed_at_utc"] = utc_now()
                atomic_write_json(manifest_path, manifest)
                raise

    finally:
        document.close()

    manifest["completed_at_utc"] = utc_now()
    manifest["status"] = "completed"
    atomic_write_json(manifest_path, manifest)

    print("Batch rendering complete.")
    print(f"Document ID: {document_id}")
    print(f"Pages requested: {len(pages)}")
    print(
        "Pages rendered this run: "
        f"{len(manifest['rendered_pages'])}"
    )
    print(
        "Existing pages reused: "
        f"{len(manifest['skipped_existing_pages'])}"
    )
    print(f"Output directory: {args.output_dir}")
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
