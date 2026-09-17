#!/usr/bin/env python3
"""Run image-based table-region extraction for one generic batch CSV.

The script:
1. Reads a batch CSV produced by build_processing_batches.py.
2. Finds the selected high-resolution page image for each batch row.
3. Creates a batch-specific image directory using symlinks by default.
4. Runs src/extract_table_regions.py on those images.
5. Writes a manifest recording every resolved input image and output.

No document-specific page numbers, tags, table coordinates, or image layouts
are hard-coded.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run image-based table-region extraction for one processing batch."
        )
    )

    parser.add_argument(
        "--batch",
        type=Path,
        required=True,
        help="Batch CSV created by build_processing_batches.py.",
    )

    parser.add_argument(
        "--rendered-image-dir",
        type=Path,
        required=True,
        help=(
            "Directory containing high-resolution rendered page images, "
            "for example data/rendered/<document-id>/selected_pages_highres."
        ),
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help=(
            "Root directory for this batch's resolved inputs, OCR outputs, "
            "and manifest."
        ),
    )

    parser.add_argument(
        "--extractor-script",
        type=Path,
        default=Path("src/extract_table_regions.py"),
        help=(
            "Path to the existing image-based table extractor "
            "(default: src/extract_table_regions.py)."
        ),
    )

    parser.add_argument(
        "--min-table-width-ratio",
        type=float,
        default=0.30,
        help="Passed through to extract_table_regions.py.",
    )

    parser.add_argument(
        "--min-table-height-ratio",
        type=float,
        default=0.12,
        help="Passed through to extract_table_regions.py.",
    )

    parser.add_argument(
        "--copy-images",
        action="store_true",
        help=(
            "Copy source images instead of creating symlinks. "
            "Use this if your environment does not support symlinks."
        ),
    )

    parser.add_argument(
        "--verify-existing-json",
        action="store_true",
        help=(
            "Parse existing page JSON files before treating them as "
            "completed. Empty files are always treated as incomplete."
        ),
    )

    parser.add_argument(
        "--force-reprocess",
        action="store_true",
        help=(
            "Run the extractor for every page in the batch, even when "
            "an existing valid detected_tables/page_###.json exists."
        ),
    )

    return parser.parse_args()


def page_number_from_name(path: Path) -> int | None:
    match = re.search(
        r"page[_-]?0*(\d+)",
        path.stem,
        flags=re.IGNORECASE,
    )

    if not match:
        return None

    return int(match.group(1))


def discover_images(image_dir: Path) -> dict[int, Path]:
    images: dict[int, Path] = {}

    for path in sorted(image_dir.iterdir()):
        if not path.is_file():
            continue

        if path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue

        page = page_number_from_name(path)

        if page is None:
            continue

        if page in images:
            raise SystemExit(
                "More than one image resolves to page "
                f"{page:03d} in {image_dir}. Rename files so every "
                "page has one unique image."
            )

        images[page] = path

    return images



def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_valid_detection_json(
    path: Path,
    verify_contents: bool,
) -> bool:
    """Return whether an existing per-page OCR result is safe to reuse."""
    try:
        if not path.is_file() or path.stat().st_size == 0:
            return False

        if not verify_contents:
            return True

        json.loads(path.read_text(encoding="utf-8"))
        return True

    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ):
        return False


def atomic_write_json(
    path: Path,
    payload: dict,
) -> None:
    """Prevent an interruption from leaving a corrupt manifest."""
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


def destination_matches_source(
    source: Path,
    destination: Path,
    copy_images: bool,
) -> bool:
    """Check whether a prior link/copy already represents this source."""
    try:
        if destination.is_symlink():
            return destination.resolve() == source.resolve()

        if not destination.is_file():
            return False

        if copy_images:
            return (
                destination.stat().st_size > 0
                and destination.stat().st_size == source.stat().st_size
            )

        return False

    except OSError:
        return False


def safe_link_or_copy(
    source: Path,
    destination: Path,
    copy_images: bool,
) -> str:
    """Create a batch image only when the existing one is not reusable."""
    if destination_matches_source(
        source=source,
        destination=destination,
        copy_images=copy_images,
    ):
        return "reused"

    if destination.exists() or destination.is_symlink():
        destination.unlink()

    if copy_images:
        import shutil

        shutil.copy2(source, destination)
        return "copied"

    relative_source = os.path.relpath(
        source.resolve(),
        destination.parent.resolve(),
    )

    destination.symlink_to(relative_source)
    return "symlinked"


def main() -> None:
    args = parse_args()

    if not args.batch.is_file():
        raise SystemExit(f"Batch CSV not found: {args.batch}")

    if not args.rendered_image_dir.is_dir():
        raise SystemExit(
            "Rendered-image directory not found: "
            f"{args.rendered_image_dir}"
        )

    if not args.extractor_script.is_file():
        raise SystemExit(
            "Table-region extractor script not found: "
            f"{args.extractor_script}"
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)

    input_image_dir = args.output_dir / "input_images"
    extractor_output_dir = args.output_dir / "detected_tables"
    resume_input_dir = args.output_dir / "input_images_pending"
    manifest_path = args.output_dir / "batch_region_manifest.json"

    input_image_dir.mkdir(parents=True, exist_ok=True)
    extractor_output_dir.mkdir(parents=True, exist_ok=True)

    # Rebuild the disposable pending-only input directory on every run.
    # Never remove detected_tables or the permanent input_images directory.
    if resume_input_dir.exists() or resume_input_dir.is_symlink():
        if resume_input_dir.is_symlink() or resume_input_dir.is_file():
            resume_input_dir.unlink()
        else:
            shutil.rmtree(resume_input_dir)

    resume_input_dir.mkdir(parents=True, exist_ok=True)

    with args.batch.open(
        encoding="utf-8",
        newline="",
    ) as handle:
        batch_rows = list(csv.DictReader(handle))

    if not batch_rows:
        raise SystemExit(f"Batch CSV is empty: {args.batch}")

    required_columns = {
        "document_id",
        "page",
        "recommended_route",
    }

    missing_columns = required_columns - set(batch_rows[0])

    if missing_columns:
        raise SystemExit(
            "Batch is missing required column(s): "
            + ", ".join(sorted(missing_columns))
        )

    document_ids = {
        row["document_id"].strip()
        for row in batch_rows
        if row["document_id"].strip()
    }

    if len(document_ids) != 1:
        raise SystemExit(
            "A batch must contain exactly one document_id. Found: "
            + ", ".join(sorted(document_ids))
        )

    document_id = next(iter(document_ids))

    routes = {
        row["recommended_route"].strip()
        for row in batch_rows
        if row["recommended_route"].strip()
    }

    if routes != {"table_extraction"}:
        raise SystemExit(
            "This runner accepts only table_extraction batches. Found: "
            + ", ".join(sorted(routes))
        )

    available_images = discover_images(args.rendered_image_dir)

    resolved_inputs = []
    missing_pages = []
    completed_pages = []
    pending_pages = []

    for row in batch_rows:
        page = int(row["page"])
        source_image = available_images.get(page)

        if source_image is None:
            missing_pages.append(page)
            continue

        destination_name = (
            f"page_{page:03d}{source_image.suffix.lower()}"
        )
        destination = input_image_dir / destination_name

        mode = safe_link_or_copy(
            source=source_image,
            destination=destination,
            copy_images=args.copy_images,
        )

        output_json = (
            extractor_output_dir / f"page_{page:03d}.json"
        )

        output_complete = (
            not args.force_reprocess
            and is_valid_detection_json(
                output_json,
                verify_contents=args.verify_existing_json,
            )
        )

        record = {
            "page": page,
            "source_image": str(source_image),
            "batch_image": str(destination),
            "output_json": str(output_json),
            "transfer_mode": mode,
            "selected_class": row.get("selected_class", ""),
            "priority": row.get("priority", ""),
            "classification_score": row.get(
                "classification_score",
                "",
            ),
            "output_status": (
                "reused_completed"
                if output_complete
                else "pending_extraction"
            ),
        }

        resolved_inputs.append(record)

        if output_complete:
            completed_pages.append(page)
            continue

        pending_destination = (
            resume_input_dir / destination_name
        )

        pending_mode = safe_link_or_copy(
            source=source_image,
            destination=pending_destination,
            copy_images=args.copy_images,
        )

        record["pending_batch_image"] = str(
            pending_destination
        )
        record["pending_transfer_mode"] = pending_mode
        pending_pages.append(page)

    if missing_pages:
        message = ", ".join(
            f"{page:03d}"
            for page in sorted(missing_pages)
        )

        raise SystemExit(
            "Could not resolve rendered image(s) for page(s): "
            f"{message}. Expected names containing page_### in "
            f"{args.rendered_image_dir}"
        )

    command = [
        sys.executable,
        str(args.extractor_script),
        "--document-id",
        document_id,
        "--image-dir",
        str(resume_input_dir),
        "--output-dir",
        str(extractor_output_dir),
        "--min-table-width-ratio",
        str(args.min_table_width_ratio),
        "--min-table-height-ratio",
        str(args.min_table_height_ratio),
    ]

    manifest = {
        "created_at_utc": utc_now(),
        "source_batch": str(args.batch),
        "document_id": document_id,
        "batch_pages": len(batch_rows),
        "rendered_image_dir": str(args.rendered_image_dir),
        "input_image_dir": str(input_image_dir),
        "pending_input_image_dir": str(resume_input_dir),
        "extractor_script": str(args.extractor_script),
        "extractor_output_dir": str(extractor_output_dir),
        "min_table_width_ratio": args.min_table_width_ratio,
        "min_table_height_ratio": args.min_table_height_ratio,
        "verify_existing_json": args.verify_existing_json,
        "force_reprocess": args.force_reprocess,
        "completed_pages_reused": completed_pages,
        "pending_pages": pending_pages,
        "command": command if pending_pages else [],
        "resolved_inputs": resolved_inputs,
        "status": "prepared",
    }

    atomic_write_json(manifest_path, manifest)

    print("Prepared image-based table extraction batch.")
    print(f"Document ID: {document_id}")
    print(f"Pages resolved: {len(resolved_inputs)}")
    print(f"Completed outputs reused: {len(completed_pages)}")
    print(f"Pages pending extraction: {len(pending_pages)}")
    print(f"All batch images: {input_image_dir}")
    print(f"Pending-only images: {resume_input_dir}")

    if not pending_pages:
        manifest["completed_at_utc"] = utc_now()
        manifest["extractor_exit_code"] = 0
        manifest["status"] = "skipped_completed"
        atomic_write_json(manifest_path, manifest)

        print(
            "All expected page JSONs already exist; "
            "table-region extraction skipped."
        )
        print(f"Manifest: {manifest_path}")
        return

    manifest["status"] = "running"
    manifest["started_at_utc"] = utc_now()
    atomic_write_json(manifest_path, manifest)

    print("Running existing table-region extractor for pending pages only:")
    print(" ".join(command))

    completed = subprocess.run(
        command,
        check=False,
    )

    manifest["completed_at_utc"] = utc_now()
    manifest["extractor_exit_code"] = completed.returncode
    manifest["status"] = (
        "completed"
        if completed.returncode == 0
        else "failed"
    )

    atomic_write_json(manifest_path, manifest)

    if completed.returncode != 0:
        raise SystemExit(
            "extract_table_regions.py failed with exit code "
            f"{completed.returncode}. Check the output above and "
            f"the manifest: {manifest_path}"
        )

    print("Image-based table-region extraction complete.")
    print(f"Extractor output: {extractor_output_dir}")
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
