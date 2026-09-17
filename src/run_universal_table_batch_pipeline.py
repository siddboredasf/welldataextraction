#!/usr/bin/env python3
"""Run the universal table-extraction pipeline for one or more batch CSV files.

Stages:
1. Render high-resolution batch pages from the source PDF.
2. Detect table regions and run image OCR.
3. Export coordinate-aware visual rows.
4. Build generic logical rows.
5. Build a generic table-row review queue.
6. Create generic review crops.
7. Run automated generic review.
8. Split accepted and unresolved evidence records.

The pipeline is document-agnostic. It contains no fixed document IDs, page
ranges, table schemas, equipment tags, calibration setpoints, or QCP logic.

Every batch receives its own output folder, manifest, status file, and logs.
Completed stages are skipped by default, including expensive OCR, unless
--force-stage or --force-all is supplied.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


STAGES = (
    "render",
    "image_ocr",
    "visual_rows",
    "logical_rows",
    "review_queue",
    "review_crops",
    "auto_review",
    "decision_export",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the universal, resumable table-extraction pipeline "
            "for one or more batch CSV files."
        )
    )

    batch_group = parser.add_mutually_exclusive_group(required=True)

    batch_group.add_argument(
        "--batch",
        type=Path,
        help="One table-extraction batch CSV to process.",
    )

    batch_group.add_argument(
        "--batches-dir",
        type=Path,
        help=(
            "Directory containing table_extraction_*_batch_*.csv files."
        ),
    )

    parser.add_argument(
        "--output-root",
        type=Path,
        required=True,
        help=(
            "Root output directory. Each batch gets a folder beneath it."
        ),
    )

    parser.add_argument(
        "--pdf",
        type=Path,
        default=None,
        help=(
            "Optional source PDF. When omitted, the batch renderer uses "
            "data/raw/<document-id>.pdf."
        ),
    )

    parser.add_argument(
        "--dpi",
        type=int,
        default=300,
        help=(
            "High-resolution render DPI for batch pages "
            "(default: 300)."
        ),
    )

    parser.add_argument(
        "--min-table-width-ratio",
        type=float,
        default=0.30,
        help=(
            "Minimum table width ratio passed to image table detection "
            "(default: 0.30)."
        ),
    )

    parser.add_argument(
        "--min-table-height-ratio",
        type=float,
        default=0.12,
        help=(
            "Minimum table height ratio passed to image table detection "
            "(default: 0.12)."
        ),
    )

    parser.add_argument(
        "--minimum-confidence",
        type=float,
        default=0.70,
        help=(
            "Minimum OCR confidence retained during visual-row export "
            "(default: 0.70)."
        ),
    )

    parser.add_argument(
        "--maximum-gap",
        type=float,
        default=180.0,
        help=(
            "Maximum vertical gap for generic logical-row continuation "
            "merging (default: 180 pixels)."
        ),
    )

    parser.add_argument(
        "--accept-score",
        type=int,
        default=70,
        help=(
            "Automated-review score for auto_accepted "
            "(default: 70)."
        ),
    )

    parser.add_argument(
        "--provisional-score",
        type=int,
        default=45,
        help=(
            "Automated-review score for auto_accepted_low_confidence "
            "(default: 45)."
        ),
    )

    parser.add_argument(
        "--auto-review-confidence",
        type=float,
        default=0.80,
        help=(
            "Average OCR confidence treated as strong by automated review "
            "(default: 0.80)."
        ),
    )

    parser.add_argument(
        "--padding-x",
        type=int,
        default=100,
        help="Horizontal crop padding in pixels (default: 100).",
    )

    parser.add_argument(
        "--padding-y",
        type=int,
        default=100,
        help="Vertical crop padding in pixels (default: 100).",
    )

    parser.add_argument(
        "--draw-boxes",
        action="store_true",
        help="Draw source row boxes in red on generic review crops.",
    )

    parser.add_argument(
        "--copy-images",
        action="store_true",
        help=(
            "Copy rendered images instead of symlinking them into each "
            "image-OCR batch input directory."
        ),
    )

    parser.add_argument(
        "--start-batch",
        type=int,
        default=None,
        help=(
            "When using --batches-dir, start at this batch number "
            "(for example 2)."
        ),
    )

    parser.add_argument(
        "--stop-batch",
        type=int,
        default=None,
        help=(
            "When using --batches-dir, stop at this batch number "
            "(inclusive)."
        ),
    )

    parser.add_argument(
        "--force-stage",
        choices=STAGES,
        action="append",
        default=[],
        help=(
            "Re-run one named stage even if completion output exists. "
            "May be supplied more than once."
        ),
    )

    parser.add_argument(
        "--force-all",
        action="store_true",
        help="Re-run every stage for selected batches.",
    )

    parser.add_argument(
        "--verify-existing-artifacts",
        action="store_true",
        help=(
            "Validate existing OCR JSON syntax before treating image_ocr "
            "as complete. Empty files are always treated as incomplete."
        ),
    )

    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help=(
            "Continue with later batches when one batch fails. "
            "Recommended for multi-batch runs."
        ),
    )

    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean_name(value: str) -> str:
    value = value.strip()

    if value.endswith(".csv"):
        value = value[:-4]

    return value


def batch_number_from_path(path: Path) -> int | None:
    import re

    match = re.search(
        r"_batch_(\d+)\.csv$",
        path.name,
        flags=re.IGNORECASE,
    )

    return int(match.group(1)) if match else None


def read_batch_metadata(batch_path: Path) -> dict[str, Any]:
    with batch_path.open(
        encoding="utf-8",
        newline="",
    ) as handle:
        rows = list(csv.DictReader(handle))

    if not rows:
        raise ValueError(f"Batch CSV is empty: {batch_path}")

    required_columns = {
        "document_id",
        "page",
        "recommended_route",
    }

    missing = required_columns - set(rows[0])

    if missing:
        raise ValueError(
            "Batch is missing required columns: "
            + ", ".join(sorted(missing))
        )

    document_ids = {
        str(row.get("document_id", "")).strip()
        for row in rows
        if str(row.get("document_id", "")).strip()
    }

    if len(document_ids) != 1:
        raise ValueError(
            "Batch must contain one document_id. Found: "
            + ", ".join(sorted(document_ids))
        )

    routes = {
        str(row.get("recommended_route", "")).strip()
        for row in rows
        if str(row.get("recommended_route", "")).strip()
    }

    if routes != {"table_extraction"}:
        raise ValueError(
            "This orchestrator accepts table_extraction batches only. "
            "Found routes: "
            + ", ".join(sorted(routes))
        )

    pages = sorted({
        int(row["page"])
        for row in rows
    })

    return {
        "document_id": next(iter(document_ids)),
        "pages": pages,
        "page_count": len(pages),
    }


def output_paths(
    output_root: Path,
    batch_path: Path,
) -> dict[str, Path]:
    batch_name = clean_name(batch_path.name)
    batch_root = output_root / batch_name

    return {
        "batch_root": batch_root,
        "rendered_images": batch_root / "rendered_images",
        "image_ocr_root": batch_root / "image_ocr",
        "detected_tables": (
            batch_root / "image_ocr" / "detected_tables"
        ),
        "visual_rows": batch_root / "visual_rows",
        "logical_rows": batch_root / "logical_rows",
        "review_queue": batch_root / "review_queue",
        "review_crops": batch_root / "review_queue" / "crops",
        "auto_review": batch_root / "auto_review",
        "decisions": batch_root / "decisions",
        "logs": batch_root / "logs",
        "status": batch_root / "pipeline_status.json",
        "manifest": batch_root / "pipeline_manifest.json",
    }


def is_nonempty_file(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def is_valid_json_file(
    path: Path,
    verify_contents: bool,
) -> bool:
    if not is_nonempty_file(path):
        return False

    if not verify_contents:
        return True

    try:
        json.loads(path.read_text(encoding="utf-8"))
        return True
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False


def expected_page_jsons_exist(
    directory: Path,
    pages: list[int],
    verify_contents: bool = False,
) -> bool:
    if not directory.is_dir():
        return False

    return all(
        is_valid_json_file(
            directory / f"page_{page:03d}.json",
            verify_contents=verify_contents,
        )
        for page in pages
    )


def is_stage_complete(
    stage: str,
    paths: dict[str, Path],
    pages: list[int],
    verify_existing_artifacts: bool = False,
) -> bool:
    checks = {
        "render": lambda: all(
            is_nonempty_file(
                paths["rendered_images"]
                / f"page_{page:03d}.png"
            )
            for page in pages
        ),
        "image_ocr": lambda: expected_page_jsons_exist(
            paths["detected_tables"],
            pages,
            verify_contents=verify_existing_artifacts,
        ),
        "visual_rows": lambda: is_nonempty_file(
            paths["visual_rows"]
            / "visual_table_rows.jsonl"
        ),
        "logical_rows": lambda: is_nonempty_file(
            paths["logical_rows"]
            / "logical_table_rows.jsonl"
        ),
        "review_queue": lambda: is_nonempty_file(
            paths["review_queue"]
            / "table_row_review_queue.jsonl"
        ),
        "review_crops": lambda: is_nonempty_file(
            paths["review_crops"]
            / "table_row_review_crops_manifest.json"
        ),
        "auto_review": lambda: is_nonempty_file(
            paths["auto_review"]
            / "table_row_auto_review.csv"
        ),
        "decision_export": lambda: is_nonempty_file(
            paths["decisions"]
            / "auto_review_export_summary.json"
        ),
    }

    return checks[stage]()


def run_command(
    command: list[str],
    log_path: Path,
) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)

    with log_path.open(
        "a",
        encoding="utf-8",
    ) as log:
        log.write("\n")
        log.write("=" * 100 + "\n")
        log.write(f"Started: {utc_now()}\n")
        log.write("Command:\n")
        log.write(" ".join(command) + "\n")
        log.write("=" * 100 + "\n")
        log.flush()

        completed = subprocess.run(
            command,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
        )

        log.write(f"\nFinished: {utc_now()}\n")
        log.write(
            f"Exit code: {completed.returncode}\n"
        )
        log.flush()

    if completed.returncode != 0:
        raise RuntimeError(
            "Command failed with exit code "
            f"{completed.returncode}. See log: {log_path}"
        )


def write_json(
    path: Path,
    payload: dict[str, Any],
) -> None:
    """Write control files atomically to survive interruption safely."""
    path.parent.mkdir(parents=True, exist_ok=True)
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


def build_stage_commands(
    args: argparse.Namespace,
    batch_path: Path,
    metadata: dict[str, Any],
    paths: dict[str, Path],
) -> dict[str, list[str]]:
    python = sys.executable

    render_command = [
        python,
        "src/render_batch_pages_highres.py",
        "--batch",
        str(batch_path),
        "--output-dir",
        str(paths["rendered_images"]),
        "--dpi",
        str(args.dpi),
    ]

    if args.pdf is not None:
        render_command.extend([
            "--pdf",
            str(args.pdf),
        ])

    image_ocr_command = [
        python,
        "src/run_table_region_extraction_batch.py",
        "--batch",
        str(batch_path),
        "--rendered-image-dir",
        str(paths["rendered_images"]),
        "--output-dir",
        str(paths["image_ocr_root"]),
        "--min-table-width-ratio",
        str(args.min_table_width_ratio),
        "--min-table-height-ratio",
        str(args.min_table_height_ratio),
    ]

    if args.copy_images:
        image_ocr_command.append("--copy-images")

    review_crop_command = [
        python,
        "src/create_table_row_review_crops.py",
        "--review-jsonl",
        str(
            paths["review_queue"]
            / "table_row_review_queue.jsonl"
        ),
        "--visual-rows-jsonl",
        str(
            paths["visual_rows"]
            / "visual_table_rows.jsonl"
        ),
        "--detected-tables-dir",
        str(paths["detected_tables"]),
        "--output-dir",
        str(paths["review_crops"]),
        "--padding-x",
        str(args.padding_x),
        "--padding-y",
        str(args.padding_y),
    ]

    if args.draw_boxes:
        review_crop_command.append("--draw-boxes")

    return {
        "render": render_command,
        "image_ocr": image_ocr_command,
        "visual_rows": [
            python,
            "src/export_visual_table_rows.py",
            "--input-dir",
            str(paths["detected_tables"]),
            "--output-dir",
            str(paths["visual_rows"]),
            "--source-policy",
            "crop_only",
            "--minimum-confidence",
            str(args.minimum_confidence),
        ],
        "logical_rows": [
            python,
            "src/build_logical_table_rows.py",
            "--input-csv",
            str(
                paths["visual_rows"]
                / "visual_table_rows.csv"
            ),
            "--output-dir",
            str(paths["logical_rows"]),
            "--maximum-gap",
            str(args.maximum_gap),
        ],
        "review_queue": [
            python,
            "src/build_table_row_review_queue.py",
            "--input-jsonl",
            str(
                paths["logical_rows"]
                / "logical_table_rows.jsonl"
            ),
            "--output-dir",
            str(paths["review_queue"]),
        ],
        "review_crops": review_crop_command,
        "auto_review": [
            python,
            "src/auto_review_table_rows.py",
            "--review-queue-csv",
            str(
                paths["review_queue"]
                / "table_row_review_queue.csv"
            ),
            "--crop-manifest-json",
            str(
                paths["review_crops"]
                / "table_row_review_crops_manifest.json"
            ),
            "--output-dir",
            str(paths["auto_review"]),
            "--accept-score",
            str(args.accept_score),
            "--provisional-score",
            str(args.provisional_score),
            "--minimum-confidence",
            str(args.auto_review_confidence),
        ],
        "decision_export": [
            python,
            "src/export_auto_reviewed_table_rows.py",
            "--input-csv",
            str(
                paths["auto_review"]
                / "table_row_auto_review.csv"
            ),
            "--output-dir",
            str(paths["decisions"]),
        ],
    }


def run_batch(
    args: argparse.Namespace,
    batch_path: Path,
) -> dict[str, Any]:
    metadata = read_batch_metadata(batch_path)
    paths = output_paths(args.output_root, batch_path)

    paths["batch_root"].mkdir(parents=True, exist_ok=True)
    paths["logs"].mkdir(parents=True, exist_ok=True)

    commands = build_stage_commands(
        args=args,
        batch_path=batch_path,
        metadata=metadata,
        paths=paths,
    )

    pipeline_manifest = {
        "created_at_utc": utc_now(),
        "batch_csv": str(batch_path),
        "document_id": metadata["document_id"],
        "page_count": metadata["page_count"],
        "pages": metadata["pages"],
        "settings": {
            "dpi": args.dpi,
            "min_table_width_ratio": (
                args.min_table_width_ratio
            ),
            "min_table_height_ratio": (
                args.min_table_height_ratio
            ),
            "minimum_confidence": args.minimum_confidence,
            "maximum_gap": args.maximum_gap,
            "accept_score": args.accept_score,
            "provisional_score": args.provisional_score,
            "auto_review_confidence": (
                args.auto_review_confidence
            ),
            "padding_x": args.padding_x,
            "padding_y": args.padding_y,
            "draw_boxes": args.draw_boxes,
            "copy_images": args.copy_images,
            "verify_existing_artifacts": (
                args.verify_existing_artifacts
            ),
        },
        "paths": {
            name: str(path)
            for name, path in paths.items()
        },
        "commands": commands,
    }

    write_json(paths["manifest"], pipeline_manifest)

    status: dict[str, Any] = {
        "batch_csv": str(batch_path),
        "document_id": metadata["document_id"],
        "page_count": metadata["page_count"],
        "started_at_utc": utc_now(),
        "status": "running",
        "stages": {},
    }

    write_json(paths["status"], status)

    forced_stages = set(args.force_stage)

    for stage in STAGES:
        is_forced = args.force_all or stage in forced_stages

        completed = is_stage_complete(
            stage=stage,
            paths=paths,
            pages=metadata["pages"],
            verify_existing_artifacts=(
                args.verify_existing_artifacts
            ),
        )

        if completed and not is_forced:
            status["stages"][stage] = {
                "status": "skipped_completed",
                "checked_at_utc": utc_now(),
            }
            write_json(paths["status"], status)

            print(
                f"[{batch_path.name}] {stage}: "
                "skipped (already complete)"
            )
            continue

        status["stages"][stage] = {
            "status": "running",
            "started_at_utc": utc_now(),
            "log_file": str(
                paths["logs"] / f"{stage}.log"
            ),
        }
        write_json(paths["status"], status)

        print(
            f"[{batch_path.name}] {stage}: running"
        )

        try:
            run_command(
                command=commands[stage],
                log_path=paths["logs"] / f"{stage}.log",
            )
        except Exception as error:
            status["stages"][stage] = {
                "status": "failed",
                "failed_at_utc": utc_now(),
                "error": str(error),
                "log_file": str(
                    paths["logs"] / f"{stage}.log"
                ),
            }
            status["status"] = "failed"
            status["finished_at_utc"] = utc_now()
            write_json(paths["status"], status)
            raise

        status["stages"][stage] = {
            "status": "completed",
            "finished_at_utc": utc_now(),
            "log_file": str(
                paths["logs"] / f"{stage}.log"
            ),
        }
        write_json(paths["status"], status)

        print(
            f"[{batch_path.name}] {stage}: complete"
        )

    status["status"] = "completed"
    status["finished_at_utc"] = utc_now()
    write_json(paths["status"], status)

    return {
        "batch_csv": str(batch_path),
        "batch_name": batch_path.name,
        "status": "completed",
        "output_root": str(paths["batch_root"]),
        "status_file": str(paths["status"]),
    }


def select_batches(
    args: argparse.Namespace,
) -> list[Path]:
    if args.batch is not None:
        if not args.batch.is_file():
            raise SystemExit(
                f"Batch CSV not found: {args.batch}"
            )
        return [args.batch]

    if args.batches_dir is None or not args.batches_dir.is_dir():
        raise SystemExit(
            f"Batches directory not found: {args.batches_dir}"
        )

    batch_paths = sorted(
        args.batches_dir.glob(
            "table_extraction_*_batch_*.csv"
        ),
        key=lambda path: (
            batch_number_from_path(path) or 0,
            path.name,
        ),
    )

    if not batch_paths:
        raise SystemExit(
            "No matching batch CSV files found in: "
            f"{args.batches_dir}"
        )

    selected = []

    for path in batch_paths:
        batch_number = batch_number_from_path(path)

        if batch_number is None:
            continue

        if (
            args.start_batch is not None
            and batch_number < args.start_batch
        ):
            continue

        if (
            args.stop_batch is not None
            and batch_number > args.stop_batch
        ):
            continue

        selected.append(path)

    if not selected:
        raise SystemExit(
            "No batches remain after applying start/stop filters."
        )

    return selected


def main() -> None:
    args = parse_args()

    if args.dpi < 72:
        raise SystemExit("--dpi must be at least 72.")

    if not 0.0 <= args.minimum_confidence <= 1.0:
        raise SystemExit(
            "--minimum-confidence must be between 0.0 and 1.0."
        )

    if not 0.0 <= args.auto_review_confidence <= 1.0:
        raise SystemExit(
            "--auto-review-confidence must be between 0.0 and 1.0."
        )

    if not 0 <= args.provisional_score <= args.accept_score <= 100:
        raise SystemExit(
            "Scores must satisfy: "
            "0 <= provisional-score <= accept-score <= 100."
        )

    if args.maximum_gap <= 0:
        raise SystemExit("--maximum-gap must be greater than zero.")

    if args.padding_x < 0 or args.padding_y < 0:
        raise SystemExit("Padding values cannot be negative.")

    args.output_root.mkdir(parents=True, exist_ok=True)

    batches = select_batches(args)

    print(
        "Universal table-batch pipeline starting."
    )
    print(f"Batches selected: {len(batches)}")
    print(f"Output root: {args.output_root}")
    print(f"Render DPI: {args.dpi}")

    results = []
    failures = []

    for batch_path in batches:
        print()
        print("=" * 100)
        print(f"Processing batch: {batch_path.name}")
        print("=" * 100)

        try:
            result = run_batch(
                args=args,
                batch_path=batch_path,
            )
            results.append(result)

            print(
                f"Completed batch: {batch_path.name}"
            )

        except Exception as error:
            failure = {
                "batch_csv": str(batch_path),
                "batch_name": batch_path.name,
                "status": "failed",
                "error": str(error),
                "failed_at_utc": utc_now(),
            }

            failures.append(failure)

            print(
                f"FAILED batch: {batch_path.name}\n"
                f"{error}"
            )

            if not args.continue_on_error:
                break

    run_summary = {
        "created_at_utc": utc_now(),
        "batches_selected": len(batches),
        "batches_completed": len(results),
        "batches_failed": len(failures),
        "results": results,
        "failures": failures,
    }

    summary_path = (
        args.output_root
        / "universal_table_batch_pipeline_summary.json"
    )

    write_json(summary_path, run_summary)

    print()
    print("=" * 100)
    print("Universal table-batch pipeline finished.")
    print(f"Batches completed: {len(results)}")
    print(f"Batches failed: {len(failures)}")
    print(f"Run summary: {summary_path}")

    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
