#!/usr/bin/env python3
"""
Universal, evidence-first equipment extraction and metadata pipeline.

Flow
----
0. Profile full PDF, extract native text, classify pages, write intake outputs.
1. Select authoritative tag pages using OCR.
1B. Compare advisory intake classifications with tag-page selection.
2. Build authoritative tag-page batch from selected_pages.txt only.
3A. Run high-DPI generic table/OCR extraction with live terminal dashboard.
3B. Route selected pages with zero accepted table rows to dense OCR.
3C. Run dense OCR/key-value extraction on routed pages.
4. Optional legacy equipment-centric export; not required by the main flow.
5. Build unified review queue.
6A. Build a broad Step 6 metadata seed and select dense metadata pages.
6B. Run dense layout-aware generic field OCR.
6C. Locate admitted equipment tags on those pages.
6D. Propose local tag-to-field associations.
6E. Propose explicit tag-linked descriptive callouts.
7. Build narrow tag-evidence index, reviewed-register template, and field queue.
8. Optionally apply human-reviewed YAML decisions to a new reviewed export.

Safety model
------------
- Steps 0 and 1B are advisory and never change authoritative selected pages.
- Step 1 is sole writer of selected_pages.txt.
- Step 2 is sole writer of tag_pages_batch_001.csv.
- Generic metadata values remain proposals until separately reviewed.
- Decision YAML is optional and only creates a new reviewed export.
- Existing source OCR and original equipment register are never overwritten.
"""

from __future__ import annotations

import argparse
import csv
import os
import signal
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path


STEPS = (
    "0",
    "1",
    "1b",
    "2",
    "3a",
    "3b",
    "3c",
    "3d",
    "5",
    "6a",
    "6b",
    "6c",
    "6d",
    "6e",
    "6f",
    "6g",
    "7",
    "8",
)


class LivePipelineDashboard:
    """Read-only, filesystem-backed live terminal pipeline dashboard."""

    def __init__(
        self,
        document_id: str,
        output_root: Path,
        batch_name: str,
        interval_seconds: float,
    ) -> None:
        self.document_id = document_id
        self.output_root = output_root
        self.batch_name = batch_name
        self.interval_seconds = interval_seconds
        self.current_step = "starting"
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def batch_csv(self) -> Path:
        return self.output_root / "batches" / f"{self.batch_name}.csv"

    @property
    def tag_batch_dir(self) -> Path:
        return (
            self.output_root
            / "universal_table_pipeline_tags"
            / self.batch_name
        )

    def _count_csv_rows(self, path: Path | None) -> int:
        if path is None or not path.is_file() or path.stat().st_size == 0:
            return 0

        try:
            with path.open(encoding="utf-8", newline="") as handle:
                return sum(1 for _ in csv.DictReader(handle))
        except (OSError, UnicodeDecodeError, csv.Error):
            return 0

    def _count_text_lines(self, path: Path) -> int:
        if not path.is_file():
            return 0

        try:
            return sum(
                1
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
        except (OSError, UnicodeDecodeError):
            return 0

    def _count_files(self, directory: Path, pattern: str) -> int:
        if not directory.is_dir():
            return 0

        total = 0

        for path in directory.glob(pattern):
            try:
                if path.is_file() and path.stat().st_size > 0:
                    total += 1
            except OSError:
                continue

        return total

    def _accepted_table_rows_csv(self) -> Path:
        return (
            self.tag_batch_dir
            / "decisions"
            / "accepted"
            / "table_rows_auto_accepted.csv"
        )

    def _unresolved_table_rows_csv(self) -> Path:
        return (
            self.tag_batch_dir
            / "decisions"
            / "unresolved"
            / "table_rows_needing_review.csv"
        )

    def snapshot(self) -> dict[str, int]:
        review_dir = self.output_root / "review_queue"

        return {
            "tag_pages": self._count_csv_rows(self.batch_csv),
            "rendered": self._count_files(
                self.tag_batch_dir / "rendered_images",
                "page_*",
            ),
            "ocr_done": self._count_files(
                self.tag_batch_dir / "image_ocr" / "detected_tables",
                "page_*.json",
            ),
            "accepted_rows": self._count_csv_rows(
                self._accepted_table_rows_csv()
            ),
            "unresolved_rows": self._count_csv_rows(
                self._unresolved_table_rows_csv()
            ),
            "non_tabular_pages": self._count_text_lines(
                self.output_root / "non_tabular_tag_pages.txt"
            ),
            "initial_dense_fields": self._count_csv_rows(
                self.output_root / "non_tabular_tag_fields.csv"
            ),
            "dense_metadata_pages": self._count_text_lines(
                self.output_root / "dense_field_pages_from_evidence.txt"
            ),
            "dense_metadata_fields": self._count_csv_rows(
                self.output_root / "non_tabular_tag_fields_dense.csv"
            ),
            "dense_equipment_candidates": self._count_csv_rows(
                self.output_root
                / "equipment_centric"
                / "equipment_reconstruction_candidate_register.csv"
            ),
            "local_field_proposals": self._count_csv_rows(
                self.output_root
                / "equipment_field_evidence_local_regions.csv"
            ),
            "callout_proposals": self._count_csv_rows(
                self.output_root
                / "equipment_name_evidence_callouts.csv"
            ),
            "field_review": self._count_csv_rows(
                review_dir / "review_queue_equipment_fields.csv"
            ),
            "review_pages": self._count_csv_rows(
                review_dir / "review_queue_pages.csv"
            ),
            "review_table_rows": self._count_csv_rows(
                review_dir / "review_queue_table_rows.csv"
            ),
            "review_fields": self._count_csv_rows(
                review_dir / "review_queue_fields.csv"
            ),
        }

    def print_snapshot(self, final: bool = False) -> None:
        values = self.snapshot()
        total = values["tag_pages"]
        done = values["ocr_done"]
        remaining = max(0, total - done)

        title = (
            "FINAL PIPELINE DASHBOARD"
            if final
            else "LIVE PIPELINE DASHBOARD"
        )

        print(
            "\n"
            + "=" * 84
            + f"\n{title} | document={self.document_id} | "
            + f"step={self.current_step}"
            + "\n"
            + "-" * 84
            + f"\nTag pages selected       : {total}"
            + f"\nRendered page images     : {values['rendered']}/{total}"
            + f"\nOCR + detection complete : {done}/{total}"
            + f"\nOCR pages remaining      : {remaining}"
            + f"\nAccepted table rows      : {values['accepted_rows']}"
            + f"\nUnresolved table rows    : {values['unresolved_rows']}"
            + f"\nNon-tabular pages (3B)   : {values['non_tabular_pages']}"
            + f"\nDense fields (3C)        : {values['initial_dense_fields']}"
            + f"\nDense metadata pages (6A)      : {values['dense_metadata_pages']}"
            + f"\nDense metadata fields (6B)     : {values['dense_metadata_fields']}"
            + f"\nDense equipment candidates (6C): "
            + f"{values['dense_equipment_candidates']}"
            + f"\nLocal field proposals    : {values['local_field_proposals']}"
            + f"\nCallout name proposals   : {values['callout_proposals']}"
            + f"\nField review items (7)   : {values['field_review']}"
            + f"\nUnified review queue (5) : "
            + f"pages={values['review_pages']}, "
            + f"tables={values['review_table_rows']}, "
            + f"fields={values['review_fields']}"
            + "\n"
            + "=" * 84,
            flush=True,
        )

    def _loop(self) -> None:
        while not self._stop_event.wait(self.interval_seconds):
            self.print_snapshot()

    def start(self, step_name: str) -> None:
        self.current_step = step_name

        if self.interval_seconds <= 0:
            return

        self.stop()
        self._stop_event.clear()
        self.print_snapshot()

        self._thread = threading.Thread(
            target=self._loop,
            name="pipeline-dashboard",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

        if self._thread is not None:
            self._thread.join(
                timeout=max(1.0, self.interval_seconds + 1.0)
            )
            self._thread = None


def derive_document_id(pdf_path: Path) -> str:
    value = "".join(
        character if character.isalnum() or character in "-_."
        else "_"
        for character in pdf_path.stem
    ).strip("_")

    return value or "document"


def normalise_step(value: str) -> str:
    step = value.strip().lower()

    aliases = {
        "0": "0",
        "1": "1",
        "1b": "1b",
        "2": "2",
        "3": "3a",
        "3a": "3a",
        "3b": "3b",
        "3c": "3c",
        "3d": "3d",
        "measurement": "3d",
        "measurements": "3d",
        "5": "5",
        "6": "6a",
        "6a": "6a",
        "6b": "6b",
        "6c": "6c",
        "6d": "6d",
        "6e": "6e",
        "6f": "6f",
        "6g": "6g",
        "7": "7",
        "8": "8",
    }

    if step not in aliases:
        raise argparse.ArgumentTypeError(
            "Step must be one of: " + ", ".join(STEPS)
        )

    return aliases[step]


def selected_steps(
    start_step: str,
    stop_after_step: str,
) -> tuple[str, ...]:
    start_index = STEPS.index(start_step)
    stop_index = STEPS.index(stop_after_step)

    if stop_index < start_index:
        raise SystemExit(
            "--stop-after-step must be the same as or after --start-step."
        )

    return STEPS[start_index : stop_index + 1]


def must_exist(path: Path, description: str) -> None:
    if not path.is_file():
        raise SystemExit(f"{description}\nMissing: {path}")


def terminate_process_group(
    process: subprocess.Popen,
    grace_seconds: float = 10.0,
) -> None:
    """Stop a child process and all descendants in its process group."""
    if process.poll() is not None:
        return

    try:
        process_group_id = os.getpgid(process.pid)
        os.killpg(process_group_id, signal.SIGTERM)
    except ProcessLookupError:
        return

    deadline = time.monotonic() + grace_seconds

    while process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.2)

    if process.poll() is None:
        try:
            os.killpg(process_group_id, signal.SIGKILL)
        except ProcessLookupError:
            pass


def run_command(
    label: str,
    command: list[str],
    dashboard: LivePipelineDashboard | None = None,
    dashboard_step: str | None = None,
) -> None:
    print()
    print("=" * 100)
    print(label)
    print("-" * 100)
    print("Command:")
    print(" ".join(command))
    print("=" * 100)

    if dashboard is not None and dashboard_step is not None:
        dashboard.start(dashboard_step)

    process = subprocess.Popen(
        command,
        start_new_session=True,
    )

    try:
        returncode = process.wait()
    except KeyboardInterrupt:
        print(
            "\nInterrupt received; stopping pipeline process group...",
            flush=True,
        )
        terminate_process_group(process)
        raise
    finally:
        if dashboard is not None and dashboard_step is not None:
            dashboard.stop()
            dashboard.print_snapshot()

    if returncode != 0:
        raise subprocess.CalledProcessError(returncode, command)


def read_csv(path: Path | None) -> list[dict[str, str]]:
    if path is None or not path.is_file() or path.stat().st_size == 0:
        return []

    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def choose_equipment_tag_source(
    equipment_dir: Path,
    legacy_register_csv: Path,
) -> Path:
    """
    Prefer evidence-controlled reconstruction intake when it exists.

    The intake may contain accepted contextual tag candidates that have not
    yet been expanded into the legacy equipment register. The legacy register
    remains the fallback and remains the source for legacy reviewed exports.
    """
    intake_csv = equipment_dir / "equipment_reconstruction_intake.csv"

    if read_csv(intake_csv):
        print(
            "[metadata] Tag source: reconstruction intake "
            f"({intake_csv})"
        )
        return intake_csv

    must_exist(
        legacy_register_csv,
        "Neither reconstruction intake nor equipment register is available. "
        "Run Step 6C dense equipment reconstruction first.",
    )

    print(
        "[metadata] Tag source: legacy equipment register "
        f"({legacy_register_csv})"
    )
    return legacy_register_csv


def run_document_intake(
    pdf_path: Path,
    document_id: str,
    intake_dir: Path,
    taxonomy_config: Path,
) -> None:
    """Run advisory whole-document profiling and page classification."""
    profile_dir = intake_dir / "profile"
    native_text_dir = intake_dir / "native_text"
    classification_dir = intake_dir / "classification"

    profile_inventory = profile_dir / "page_inventory.csv"
    native_text_index = native_text_dir / "page_text_index.csv"
    classification_csv = classification_dir / "page_classification.csv"
    processing_queue_csv = classification_dir / "processing_queue.csv"
    manifest = intake_dir / "intake_manifest.json"

    expected = [
        profile_inventory,
        native_text_index,
        classification_csv,
        processing_queue_csv,
        manifest,
    ]

    if all(path.is_file() for path in expected):
        print("[0] Document intake: skipped (already complete).")
        return

    must_exist(taxonomy_config, "Taxonomy configuration is unavailable.")
    intake_dir.mkdir(parents=True, exist_ok=True)

    run_command(
        "[0A] Profile full PDF structure",
        [
            sys.executable,
            "src/profile_document.py",
            "--pdf",
            str(pdf_path),
            "--document-id",
            document_id,
            "--output-dir",
            str(profile_dir),
        ],
    )

    run_command(
        "[0B] Extract native PDF text",
        [
            sys.executable,
            "src/extract_native_text.py",
            "--pdf",
            str(pdf_path),
            "--document-id",
            document_id,
            "--output-dir",
            str(native_text_dir),
        ],
    )

    run_command(
        "[0C] Classify PDF pages",
        [
            sys.executable,
            "src/classify_document_pages.py",
            "--page-index",
            str(native_text_index),
            "--text-dir",
            str(native_text_dir / "pages"),
            "--taxonomy-config",
            str(taxonomy_config),
            "--document-id",
            document_id,
            "--output-dir",
            str(classification_dir),
        ],
    )

    run_command(
        "[0D] Write intake manifest",
        [
            sys.executable,
            "src/build_document_intake_manifest.py",
            "--pdf",
            str(pdf_path),
            "--document-id",
            document_id,
            "--taxonomy-config",
            str(taxonomy_config),
            "--profile-inventory-csv",
            str(profile_inventory),
            "--native-text-index-csv",
            str(native_text_index),
            "--classification-csv",
            str(classification_csv),
            "--processing-queue-csv",
            str(processing_queue_csv),
            "--output-dir",
            str(intake_dir),
        ],
    )


def run_tag_selection(
    pdf_path: Path,
    thumbnail_dir: Path,
    selection_dir: Path,
    scan_dpi: int,
) -> None:
    run_command(
        "[1] Select authoritative tag pages",
        [
            sys.executable,
            "src/select_tag_pages.py",
            "--pdf",
            str(pdf_path),
            "--thumbnail-dir",
            str(thumbnail_dir),
            "--output-dir",
            str(selection_dir),
            "--scan-dpi",
            str(scan_dpi),
        ],
    )


def compare_intake_to_tag_selection(
    document_id: str,
    intake_dir: Path,
    selection_dir: Path,
    batches_dir: Path,
    minimum_score: int,
) -> None:
    """Write advisory comparison outputs without changing selected pages."""
    classification_csv = (
        intake_dir / "classification" / "page_classification.csv"
    )
    profile_inventory_csv = intake_dir / "profile" / "page_inventory.csv"
    selected_pages = selection_dir / "selected_pages.txt"
    weak_pages = selection_dir / "review_candidate_pages.txt"
    comparison_csv = intake_dir / "tag_selection_comparison.csv"
    supplemental_batch = (
        batches_dir / "intake_supplemental_review_batch_001.csv"
    )

    if comparison_csv.is_file() and supplemental_batch.is_file():
        print("[1B] Intake/tag comparison: skipped (already complete).")
        return

    for path, description in (
        (classification_csv, "Page classification is unavailable."),
        (profile_inventory_csv, "Page inventory is unavailable."),
        (selected_pages, "Authoritative selected pages are unavailable."),
    ):
        must_exist(path, description)

    command = [
        sys.executable,
        "src/compare_intake_to_tag_selection.py",
        "--classification-csv",
        str(classification_csv),
        "--profile-inventory-csv",
        str(profile_inventory_csv),
        "--selected-pages",
        str(selected_pages),
        "--document-id",
        document_id,
        "--output-dir",
        str(intake_dir),
        "--supplemental-batch-csv",
        str(supplemental_batch),
        "--minimum-score",
        str(minimum_score),
    ]

    if weak_pages.is_file():
        command.extend([
            "--review-candidate-pages",
            str(weak_pages),
        ])

    run_command("[1B] Compare intake and tag selection", command)


def build_tag_batch_csv(
    selection_dir: Path,
    document_id: str,
    batches_dir: Path,
    batch_name: str,
) -> Path:
    """The sole writer of the authoritative tag-page batch."""
    selected_pages = selection_dir / "selected_pages.txt"
    must_exist(selected_pages, "Selected tag pages are unavailable.")

    pages = sorted({
        int(line.strip())
        for line in selected_pages.read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip().isdigit()
    })

    if not pages:
        raise SystemExit(f"No pages found in: {selected_pages}")

    batches_dir.mkdir(parents=True, exist_ok=True)
    batch_csv = batches_dir / f"{batch_name}.csv"

    with batch_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "document_id",
                "page",
                "recommended_route",
                "reason",
            ],
        )
        writer.writeheader()

        for page in pages:
            writer.writerow({
                "document_id": document_id,
                "page": page,
                "recommended_route": "table_extraction",
                "reason": "tag_page_selected",
            })

    print(f"[2] Tag batch pages: {len(pages)}")
    print(f"[2] Output: {batch_csv}")

    return batch_csv


def existing_tag_batch_csv(
    batches_dir: Path,
    batch_name: str,
) -> Path:
    batch_csv = batches_dir / f"{batch_name}.csv"
    must_exist(
        batch_csv,
        "Tag-page batch is unavailable. Run Step 2 or earlier first.",
    )
    return batch_csv


def table_batch_dir(
    table_output_root: Path,
    batch_name: str,
) -> Path:
    exact = table_output_root / batch_name

    if exact.is_dir():
        return exact

    matches = sorted(
        path
        for path in table_output_root.glob("tag_pages_batch_*")
        if path.is_dir()
    )

    if len(matches) == 1:
        return matches[0]

    raise SystemExit(
        "Could not identify table extraction batch directory under:\n"
        f"{table_output_root}\n"
        f"Expected: {exact}"
    )


def accepted_table_rows_csv(
    table_output_root: Path,
    batch_name: str,
) -> Path:
    path = (
        table_batch_dir(table_output_root, batch_name)
        / "decisions"
        / "accepted"
        / "table_rows_auto_accepted.csv"
    )
    must_exist(path, "Accepted table-row output is unavailable.")
    return path


def unresolved_table_rows_csv(
    table_output_root: Path,
    batch_name: str,
) -> Path | None:
    path = (
        table_batch_dir(table_output_root, batch_name)
        / "decisions"
        / "unresolved"
        / "table_rows_needing_review.csv"
    )
    return path if path.is_file() else None


def run_table_pipeline(
    batch_csv: Path,
    table_output_root: Path,
    source_pdf: Path,
    ocr_dpi: int,
    dashboard: LivePipelineDashboard,
) -> None:
    run_command(
        "[3A] Run generic high-DPI table/OCR batch",
        [
            sys.executable,
            "src/run_universal_table_batch_pipeline.py",
            "--batch",
            str(batch_csv),
            "--output-root",
            str(table_output_root),
            "--pdf",
            str(source_pdf),
            "--dpi",
            str(ocr_dpi),
            "--min-table-width-ratio",
            "0.30",
            "--min-table-height-ratio",
            "0.12",
            "--minimum-confidence",
            "0.70",
            "--maximum-gap",
            "180",
            "--accept-score",
            "70",
            "--provisional-score",
            "45",
            "--auto-review-confidence",
            "0.80",
            "--padding-x",
            "100",
            "--padding-y",
            "100",
        ],
        dashboard=dashboard,
        dashboard_step="3A — table/OCR extraction",
    )


def run_measurement_table_pipeline(
    document_id: str,
    ocr_json_dir: Path,
    measurement_dir: Path,
    dashboard: LivePipelineDashboard,
    measurement_pages_list: Path | None = None,
    completed_calibration_csv: Path | None = None,
) -> None:
    """Reconstruct directional measurement records from generic table OCR."""
    measurement_dir.mkdir(parents=True, exist_ok=True)

    cells_csv = measurement_dir / "universal_table_cells.csv"
    header_slots_csv = measurement_dir / "directional_header_slots.csv"
    header_slots_review_csv = (
        measurement_dir / "directional_header_slot_review.csv"
    )
    canonical_schema_csv = (
        measurement_dir / "canonical_directional_header_schema.csv"
    )
    raw_observations_csv = measurement_dir / "calibration_observations.csv"
    assigned_observations_csv = (
        measurement_dir / "calibration_observations_assigned.csv"
    )
    assignment_review_csv = (
        measurement_dir / "calibration_observation_assignment_review.csv"
    )

    run_command(
        "[3D1] Export coordinate-aware table cells",
        [
            sys.executable,
            "src/export_universal_table_cells.py",
            "--document-id",
            document_id,
            "--ocr-json-dir",
            str(ocr_json_dir),
            *(
                [
                    "--pages-list",
                    str(measurement_pages_list),
                ]
                if measurement_pages_list is not None
                else []
            ),
            "--output-csv",
            str(cells_csv),
        ],
        dashboard=dashboard,
        dashboard_step="3D1 — table-cell export",
    )

    run_command(
        "[3D2] Detect directional measurement series",
        [
            sys.executable,
            "src/detect_measurement_series.py",
            "--document-id",
            document_id,
            "--cells-csv",
            str(cells_csv),
            "--output-dir",
            str(measurement_dir),
        ],
        dashboard=dashboard,
        dashboard_step="3D2 — measurement-series detection",
    )

    run_command(
        "[3D3] Discover directional header slots",
        [
            sys.executable,
            "src/discover_directional_header_slots.py",
            "--cells-csv",
            str(cells_csv),
            "--output-csv",
            str(header_slots_csv),
            "--review-csv",
            str(header_slots_review_csv),
        ],
        dashboard=dashboard,
        dashboard_step="3D3 — directional header slots",
    )

    run_command(
        "[3D4] Reconcile canonical directional header schema",
        [
            sys.executable,
            "src/reconcile_directional_header_slots.py",
            "--header-slots-csv",
            str(header_slots_csv),
            "--output-dir",
            str(measurement_dir),
        ],
        dashboard=dashboard,
        dashboard_step="3D4 — canonical header schema",
    )

    must_exist(
        canonical_schema_csv,
        "Canonical directional schema is unavailable after Step 3D4.",
    )
    must_exist(
        raw_observations_csv,
        "Raw directional observations are unavailable after Step 3D2.",
    )

    if not read_csv(canonical_schema_csv):
        print(
            "[3D5] No canonical directional header slots were promoted; "
            "tag-anchored reconstruction skipped."
        )
        return

    run_command(
        "[3D5] Assign observations to canonical header slots",
        [
            sys.executable,
            "src/assign_observations_to_canonical_slots.py",
            "--observations-csv",
            str(raw_observations_csv),
            "--schema-csv",
            str(canonical_schema_csv),
            "--output-csv",
            str(assigned_observations_csv),
            "--review-csv",
            str(assignment_review_csv),
        ],
        dashboard=dashboard,
        dashboard_step="3D5 — canonical observation assignment",
    )

    run_command(
        "[3D6] Rebuild tag-anchored measurements from canonical slots",
        [
            sys.executable,
            "src/rebuild_calibration_using_canonical_schema.py",
            "--document-id",
            document_id,
            "--cells-csv",
            str(cells_csv),
            "--schema-csv",
            str(canonical_schema_csv),
            "--output-dir",
            str(measurement_dir),
        ],
        dashboard=dashboard,
        dashboard_step="3D6 — canonical measurement reconstruction",
    )

    secondary_dir = measurement_dir / "secondary_resolution"
    secondary_resolutions_csv = (
        secondary_dir / "calibration_secondary_resolutions.csv"
    )
    reconciled_csv = (
        measurement_dir / "calibration_observations_reconciled.csv"
    )
    reconciliation_audit_csv = (
        measurement_dir
        / "calibration_observations_reconciliation_audit.csv"
    )

    if completed_calibration_csv is None:
        print(
            "[3D7] Completed calibration CSV was not supplied; "
            "secondary resolution and reconciliation skipped."
        )
        return

    must_exist(
        completed_calibration_csv,
        "Completed calibration CSV is unavailable for [3D7].",
    )

    run_command(
        "[3D7] Resolve canonical review gaps from completed layer",
        [
            sys.executable,
            "src/resolve_calibration_review_from_completed_layer.py",
            "--review-csv",
            str(
                measurement_dir
                / "calibration_canonical_slot_review.csv"
            ),
            "--completed-csv",
            str(completed_calibration_csv),
            "--output-dir",
            str(secondary_dir),
        ],
        dashboard=dashboard,
        dashboard_step="3D7 — secondary calibration resolution",
    )

    run_command(
        "[3D8] Reconcile primary and secondary observations",
        [
            sys.executable,
            "src/reconcile_calibration_observations.py",
            "--primary-csv",
            str(
                measurement_dir
                / "calibration_observations_canonical.csv"
            ),
            "--review-csv",
            str(
                measurement_dir
                / "calibration_canonical_slot_review.csv"
            ),
            "--secondary-csv",
            str(secondary_resolutions_csv),
            "--output-csv",
            str(reconciled_csv),
            "--audit-csv",
            str(reconciliation_audit_csv),
        ],
        dashboard=dashboard,
        dashboard_step="3D8 — reconciled calibration observations",
    )

    must_exist(
        reconciled_csv,
        "Reconciled calibration observations are unavailable after [3D8].",
    )
    must_exist(
        reconciliation_audit_csv,
        "Calibration reconciliation audit is unavailable after [3D8].",
    )



def identify_non_tabular_tag_pages(
    selected_pages_file: Path,
    accepted_rows_csv: Path,
    output_file: Path,
) -> None:
    """Route selected pages with zero accepted rows to dense OCR."""
    must_exist(selected_pages_file, "Selected tag pages are unavailable.")
    must_exist(accepted_rows_csv, "Accepted table rows are unavailable.")

    selected_pages = sorted({
        int(line.strip())
        for line in selected_pages_file.read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip().isdigit()
    })

    counts: dict[int, int] = {}

    with accepted_rows_csv.open(
        encoding="utf-8",
        newline="",
    ) as handle:
        for row in csv.DictReader(handle):
            value = str(row.get("page", "")).strip()

            if value.isdigit():
                page = int(value)
                counts[page] = counts.get(page, 0) + 1

    routed = [
        page
        for page in selected_pages
        if counts.get(page, 0) == 0
    ]

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(
        "".join(f"{page}\n" for page in routed),
        encoding="utf-8",
    )

    print(f"[3B] Selected tag pages: {len(selected_pages)}")
    print(f"[3B] Pages with zero accepted rows: {len(routed)}")
    print(f"[3B] Output: {output_file}")


def run_dense_field_extraction(
    pdf_path: Path,
    pages_file: Path,
    output_csv: Path,
    ocr_dpi: int,
    label: str,
    all_ocr_lines_csv: Path | None = None,
    dashboard: LivePipelineDashboard | None = None,
    dashboard_step: str | None = None,
) -> None:
    must_exist(pages_file, "Dense-OCR page list is unavailable.")

    pages = [
        line.strip()
        for line in pages_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    output_csv.parent.mkdir(parents=True, exist_ok=True)

    if not pages:
        output_csv.write_text(
            ",".join([
                "page",
                "block_id",
                "field_label",
                "field_label_normalized",
                "field_value",
                "raw_line",
                "bbox_x1",
                "bbox_y1",
                "bbox_x2",
                "bbox_y2",
                "ocr_confidence",
                "source_kind",
            ]) + "\n",
            encoding="utf-8",
        )
        print(f"{label}: no pages; wrote empty field CSV.")
        return

    run_command(
        label,
        [
            sys.executable,
            "src/extract_fields_non_tabular_tag_pages.py",
            "--pdf",
            str(pdf_path),
            "--pages-list",
            str(pages_file),
            "--output-csv",
            str(output_csv),
            *(
                [
                    "--all-ocr-lines-csv",
                    str(all_ocr_lines_csv),
                ]
                if all_ocr_lines_csv is not None
                else []
            ),
            "--ocr-dpi",
            str(ocr_dpi),
        ],
        dashboard=dashboard,
        dashboard_step=dashboard_step,
    )


def build_equipment_centric_records(
    document_id: str,
    table_output_root: Path,
    batch_name: str,
    non_tabular_fields_csv: Path,
    equipment_output_dir: Path,
) -> None:
    accepted_csv = accepted_table_rows_csv(
        table_output_root,
        batch_name,
    )
    unresolved_csv = unresolved_table_rows_csv(
        table_output_root,
        batch_name,
    )

    batch_dir = table_batch_dir(table_output_root, batch_name)
    ocr_json_dir = batch_dir / "image_ocr" / "detected_tables"

    if not ocr_json_dir.is_dir():
        raise SystemExit(
            "OCR JSON directory is unavailable:\n"
            f"{ocr_json_dir}"
        )

    must_exist(
        non_tabular_fields_csv,
        "Initial dense non-tabular field output is unavailable.",
    )

    equipment_output_dir.mkdir(parents=True, exist_ok=True)

    command = [
        sys.executable,
        "src/export_equipment_centric_data.py",
        "--document-id",
        document_id,
        "--ocr-json-dir",
        str(ocr_json_dir),
        "--accepted-csv",
        str(accepted_csv),
        "--dense-fields-csv",
        str(non_tabular_fields_csv),
        "--output-dir",
        str(equipment_output_dir),
    ]

    if unresolved_csv is not None:
        command.extend([
            "--unresolved-csv",
            str(unresolved_csv),
        ])

    run_command(
        "[4A] Build equipment-centric records and exports",
        command,
    )

    run_command(
        "[4B] Build broad equipment-tag candidate inventory",
        [
            sys.executable,
            "src/build_equipment_tag_candidate_inventory.py",
            "--document-id",
            document_id,
            "--ocr-json-dir",
            str(ocr_json_dir),
            "--output-dir",
            str(equipment_output_dir),
        ],
    )


def build_review_queue(
    selection_dir: Path,
    table_output_root: Path,
    batch_name: str,
    non_tabular_fields_csv: Path,
    equipment_output_dir: Path,
    review_dir: Path,
) -> None:
    """Build the original unified review queue and copy equipment artifacts."""
    review_dir.mkdir(parents=True, exist_ok=True)

    weak_pages_file = selection_dir / "review_candidate_pages.txt"
    weak_pages = sorted({
        int(line.strip())
        for line in weak_pages_file.read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip().isdigit()
    }) if weak_pages_file.is_file() else []

    unresolved_rows = read_csv(
        unresolved_table_rows_csv(table_output_root, batch_name)
    )
    field_rows = read_csv(non_tabular_fields_csv)

    fields_by_page: dict[int, list[dict[str, str]]] = {}

    for row in field_rows:
        page_value = str(row.get("page", "")).strip()

        if page_value.isdigit():
            fields_by_page.setdefault(int(page_value), []).append(row)

    sparse_pages = sorted(
        page
        for page, rows in fields_by_page.items()
        if len(rows) <= 2
    )

    page_rows = [
        {
            "page": page,
            "review_reason": "weak_tag_evidence",
            "source": str(weak_pages_file),
        }
        for page in weak_pages
    ] + [
        {
            "page": page,
            "review_reason": "sparse_non_tabular_fields",
            "source": str(non_tabular_fields_csv),
        }
        for page in sparse_pages
    ]

    pages_csv = review_dir / "review_queue_pages.csv"
    table_csv = review_dir / "review_queue_table_rows.csv"
    fields_csv = review_dir / "review_queue_fields.csv"
    summary_txt = review_dir / "review_queue_summary.txt"
    review_xlsx = review_dir / "review_queue.xlsx"

    with pages_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["page", "review_reason", "source"],
        )
        writer.writeheader()
        writer.writerows(page_rows)

    table_columns = (
        list(unresolved_rows[0].keys())
        if unresolved_rows
        else [
            "page",
            "table_index",
            "logical_row_number",
            "record_text",
            "review_reasons",
        ]
    )

    with table_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=table_columns,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(unresolved_rows)

    sparse_rows = [
        row
        for page in sparse_pages
        for row in fields_by_page[page]
    ]

    field_columns = (
        list(sparse_rows[0].keys())
        if sparse_rows
        else [
            "page",
            "block_id",
            "field_label",
            "field_value",
            "raw_line",
        ]
    )

    with fields_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=field_columns,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(sparse_rows)

    with summary_txt.open("w", encoding="utf-8") as handle:
        handle.write("Universal equipment extraction review queue\n")
        handle.write("==========================================\n\n")
        handle.write(f"Weak tag pages: {len(weak_pages)}\n")
        handle.write(f"Unresolved table rows: {len(unresolved_rows)}\n")
        handle.write(f"Sparse field pages: {len(sparse_pages)}\n")
        handle.write(f"Sparse field rows: {len(sparse_rows)}\n")

    try:
        import pandas as pd

        with pd.ExcelWriter(review_xlsx, engine="openpyxl") as writer:
            pd.read_csv(pages_csv).to_excel(
                writer,
                sheet_name="pages",
                index=False,
            )
            pd.read_csv(table_csv).to_excel(
                writer,
                sheet_name="table_rows",
                index=False,
            )
            pd.read_csv(fields_csv).to_excel(
                writer,
                sheet_name="fields",
                index=False,
            )
    except Exception as error:
        print(f"[5] Review Excel export skipped: {error}")

    equipment_review = equipment_output_dir / "equipment_review_queue.csv"
    if equipment_review.is_file():
        shutil.copy2(
            equipment_review,
            review_dir / "review_queue_equipment.csv",
        )

    candidate_csv = equipment_output_dir / "tag_candidates.csv"
    candidate_rows = read_csv(candidate_csv)
    candidate_review_rows = [
        row
        for row in candidate_rows
        if row.get("candidate_type") == "possible_equipment_tag"
    ]
    candidate_output = review_dir / "review_queue_tag_candidates.csv"

    candidate_columns = (
        list(candidate_review_rows[0].keys())
        if candidate_review_rows
        else [
            "candidate",
            "candidate_type",
            "best_candidate_score",
            "best_admission_recommendation",
            "source_pages",
            "best_source_text",
        ]
    )

    with candidate_output.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=candidate_columns,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(candidate_review_rows)

    print(f"[5] Weak tag pages: {len(weak_pages)}")
    print(f"[5] Unresolved table rows: {len(unresolved_rows)}")
    print(f"[5] Sparse field pages: {len(sparse_pages)}")
    print(f"[5] Equipment review copied: {equipment_review.is_file()}")
    print(f"[5] Tag candidates needing review: {len(candidate_review_rows)}")
    print(f"[5] Review folder: {review_dir}")



def write_empty_csv(path: Path, fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Universal evidence-first equipment extraction with intake, "
            "live dashboard, checkpoints, review queues, and optional "
            "reviewed metadata export."
        )
    )

    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)

    parser.add_argument(
        "--document-id",
        default=None,
        help="Optional document ID; defaults to normalized PDF filename.",
    )

    parser.add_argument(
        "--taxonomy-config",
        type=Path,
        default=Path("config/page_taxonomy.yaml"),
        help="Page taxonomy YAML used by advisory intake Step 0.",
    )

    parser.add_argument(
        "--intake-supplemental-minimum-score",
        type=int,
        default=100,
        help="Minimum advisory intake score for supplemental pages.",
    )

    parser.add_argument(
        "--batch-name",
        default="tag_pages_batch_001",
        help="Authoritative tag-page batch CSV stem.",
    )

    parser.add_argument(
        "--scan-dpi",
        type=int,
        default=160,
        help="Low-resolution tag-selection DPI; default 160.",
    )

    parser.add_argument(
        "--ocr-dpi",
        type=int,
        default=300,
        help="High-resolution OCR DPI; default 300.",
    )

    parser.add_argument(
        "--local-radius-px",
        type=float,
        default=900.0,
        help="Maximum tag-to-field local-region distance; default 900.",
    )

    parser.add_argument(
        "--dashboard-interval",
        type=float,
        default=5.0,
        help="Seconds between terminal dashboard updates; 0 disables it.",
    )

    parser.add_argument(
        "--measurement-pages-list",
        type=Path,
        default=None,
        help=(
            "Optional one-based page list used only by Step 3D measurement "
            "table reconstruction. Defaults to all detected-table OCR pages."
        ),
    )

    parser.add_argument(
        "--completed-calibration-csv",
        type=Path,
        default=None,
        help=(
            "Optional completed calibration CSV used to resolve canonical "
            "measurement gaps."
        ),
    )

    parser.add_argument(
        "--decisions-yaml",
        type=Path,
        default=None,
        help=(
            "Optional human-reviewed YAML. Step 8 applies it only to a "
            "new reviewed register export."
        ),
    )

    parser.add_argument(
        "--provisional-register",
        required=False,
        default=None,
        type=Path,
        help="Wide provisional linked equipment register.",
    )

    parser.add_argument(
        "--metadata-review-policy",
        type=Path,
        default=Path("config/metadata_review_policy.json"),
        help="JSON policy used by Step 7 metadata review queue.",
    )

    parser.add_argument(
        "--start-step",
        type=normalise_step,
        default="0",
    )

    parser.add_argument(
        "--stop-after-step",
        type=normalise_step,
        default="8",
    )

    args = parser.parse_args()

    if not args.pdf.is_file():
        raise SystemExit(f"PDF not found: {args.pdf}")

    if args.scan_dpi < 72:
        raise SystemExit("--scan-dpi must be at least 72.")

    if args.ocr_dpi < 150:
        raise SystemExit("--ocr-dpi must be at least 150.")

    if args.local_radius_px <= 0:
        raise SystemExit("--local-radius-px must be greater than zero.")

    if args.dashboard_interval < 0:
        raise SystemExit("--dashboard-interval cannot be negative.")

    requested = selected_steps(
        args.start_step,
        args.stop_after_step,
    )

    document_id = args.document_id or derive_document_id(args.pdf)
    root = args.output_root
    root.mkdir(parents=True, exist_ok=True)

    dashboard = LivePipelineDashboard(
        document_id=document_id,
        output_root=root,
        batch_name=args.batch_name,
        interval_seconds=args.dashboard_interval,
    )

    intake_dir = root / "document_intake"
    selection_dir = root / "tag_page_selection"
    batches_dir = root / "batches"
    table_root = root / "universal_table_pipeline_tags"
    equipment_dir = root / "equipment_centric"
    measurement_dir = root / "measurement_tables"
    review_dir = root / "review_queue"

    selected_pages = selection_dir / "selected_pages.txt"
    batch_csv = batches_dir / f"{args.batch_name}.csv"
    non_tabular_pages = root / "non_tabular_tag_pages.txt"
    non_tabular_fields = root / "non_tabular_tag_fields.csv"

    provisional_register = args.provisional_register
    metadata_review_policy = args.metadata_review_policy
    tag_source_csv: Path | None = None

    step6_seed_csv = equipment_dir / "step6_metadata_seed.csv"
    dense_pages = root / "dense_field_pages_from_evidence.txt"
    dense_fields = root / "non_tabular_tag_fields_dense.csv"
    dense_all_lines = root / "dense_metadata_all_ocr_lines.csv"
    tag_locations = root / "admitted_tag_locations_dense.csv"
    local_field_evidence = (
        root / "equipment_field_evidence_local_regions.csv"
    )
    callout_name_evidence = root / "equipment_name_evidence_callouts.csv"

    tag_index_csv = root / "equipment_tag_evidence_index.csv"
    tag_index_xlsx = root / "equipment_tag_evidence_index.xlsx"
    reviewed_register_csv = root / "equipment_register_reviewed.csv"
    reviewed_register_xlsx = root / "equipment_register_reviewed.xlsx"
    field_review_csv = review_dir / "review_queue_equipment_fields.csv"
    field_review_xlsx = review_dir / "review_queue_equipment_fields.xlsx"

    reviewed_with_names_csv = (
        equipment_dir / "equipment_register_reviewed_with_names.csv"
    )
    reviewed_with_names_xlsx = (
        equipment_dir / "equipment_register_reviewed_with_names.xlsx"
    )

    dynamic_metadata = (
        equipment_dir / "dynamic_equipment_metadata.csv"
    )
    provisional_vs_evidence = (
        equipment_dir / "provisional_vs_evidence.csv"
    )
    provisional_review_queue = (
        equipment_dir / "provisional_review_queue.csv"
    )

    print("Universal equipment extraction and metadata pipeline")
    print(f"Document ID: {document_id}")
    print(f"PDF: {args.pdf}")
    print(f"Output root: {root}")
    print(f"Steps: {', '.join(requested)}")

    try:
        if "0" in requested:
            run_document_intake(
                pdf_path=args.pdf,
                document_id=document_id,
                intake_dir=intake_dir,
                taxonomy_config=args.taxonomy_config,
            )

        if "1" in requested:
            run_tag_selection(
                pdf_path=args.pdf,
                thumbnail_dir=root / f"thumbnails_{args.scan_dpi}dpi",
                selection_dir=selection_dir,
                scan_dpi=args.scan_dpi,
            )

        if "1b" in requested:
            compare_intake_to_tag_selection(
                document_id=document_id,
                intake_dir=intake_dir,
                selection_dir=selection_dir,
                batches_dir=batches_dir,
                minimum_score=args.intake_supplemental_minimum_score,
            )

        if "2" in requested:
            batch_csv = build_tag_batch_csv(
                selection_dir=selection_dir,
                document_id=document_id,
                batches_dir=batches_dir,
                batch_name=args.batch_name,
            )
        elif any(
            step in requested
            for step in (
                "3a", "3b", "3c", "3d", "5",
                "6a", "6b", "6c", "6d", "6e", "6f", "6g",
                "7", "8",
            )
        ):
            batch_csv = existing_tag_batch_csv(
                batches_dir=batches_dir,
                batch_name=args.batch_name,
            )

        if "3a" in requested:
            run_table_pipeline(
                batch_csv=batch_csv,
                table_output_root=table_root,
                source_pdf=args.pdf,
                ocr_dpi=args.ocr_dpi,
                dashboard=dashboard,
            )

        if "3b" in requested:
            identify_non_tabular_tag_pages(
                selected_pages_file=selected_pages,
                accepted_rows_csv=accepted_table_rows_csv(
                    table_root,
                    args.batch_name,
                ),
                output_file=non_tabular_pages,
            )

        if "3c" in requested:
            run_dense_field_extraction(
                pdf_path=args.pdf,
                pages_file=non_tabular_pages,
                output_csv=non_tabular_fields,
                ocr_dpi=args.ocr_dpi,
                label="[3C] Run dense OCR on zero-table-evidence pages",
                dashboard=dashboard,
                dashboard_step="3C — initial dense field OCR",
            )

        if "3d" in requested:
            batch_dir = table_batch_dir(table_root, args.batch_name)
            ocr_json_dir = batch_dir / "image_ocr" / "detected_tables"

            if not ocr_json_dir.is_dir():
                raise SystemExit(
                    "Detected-table OCR JSON directory is unavailable. "
                    "Run Step 3A first.\n"
                    f"Missing: {ocr_json_dir}"
                )

            run_measurement_table_pipeline(
                document_id=document_id,
                ocr_json_dir=ocr_json_dir,
                measurement_dir=measurement_dir,
                dashboard=dashboard,
                measurement_pages_list=args.measurement_pages_list,
                completed_calibration_csv=args.completed_calibration_csv,
            )

        if "5" in requested:
            build_review_queue(
                selection_dir=selection_dir,
                table_output_root=table_root,
                batch_name=args.batch_name,
                non_tabular_fields_csv=non_tabular_fields,
                equipment_output_dir=equipment_dir,
                review_dir=review_dir,
            )

        if "6a" in requested:
            must_exist(
                selected_pages,
                "Selected tag pages are unavailable. Run Step 1 first.",
            )

            dense_pages.parent.mkdir(parents=True, exist_ok=True)
            dense_pages.write_text(
                selected_pages.read_text(encoding="utf-8"),
                encoding="utf-8",
            )

            selected_count = sum(
                1
                for line in dense_pages.read_text(
                    encoding="utf-8"
                ).splitlines()
                if line.strip()
            )

            print(
                "[6A] Dense metadata pages selected from "
                f"authoritative tag pages: {selected_count}"
            )
            print(f"[6A] Output: {dense_pages}")

            tag_source_csv = step6_seed_csv

        if "6b" in requested:
            run_dense_field_extraction(
                pdf_path=args.pdf,
                pages_file=dense_pages,
                output_csv=dense_fields,
                ocr_dpi=args.ocr_dpi,
                label="[6B] Run dense layout-aware metadata OCR",
                all_ocr_lines_csv=dense_all_lines,
                dashboard=dashboard,
                dashboard_step="6B — dense metadata OCR",
            )

        recovered_tag_locations = root / "equipment_centric" / "recovered_dense_table_tag_locations.csv"
        dense_metadata_evidence = root / "equipment_centric" / "universal_dense_metadata_evidence.csv"
        multipage_metadata_evidence = root / "equipment_centric" / "multipage_dense_metadata_evidence.csv"
        scored_evidence = root / "equipment_centric" / "scored_multipage_dense_evidence.csv"
        scored_review_queue = root / "equipment_centric" / "scored_multipage_dense_review_queue.csv"
        dynamic_registry = root / "equipment_centric" / "dynamic_equipment_registry.csv"
        dynamic_metadata = root / "equipment_centric" / "dynamic_equipment_metadata.csv"
        dynamic_review = root / "equipment_centric" / "dynamic_equipment_review_queue.csv"
        dense_detected_tables_dir = (
            table_root
            / args.batch_name
            / "image_ocr"
            / "detected_tables"
        )

        dense_candidate_records = equipment_dir / "equipment_reconstruction_candidate_register.csv"
        dense_expanded_records = equipment_dir / "equipment_register_discovery_expanded.csv"
        dense_linked_evidence = equipment_dir / "universal_dense_metadata_evidence.csv"
        dense_inherited_records = equipment_dir / "multipage_dense_metadata_records.csv"
        dense_scored_records = equipment_dir / "scored_dense_equipment_records.csv"
        dense_record_review = equipment_dir / "dense_equipment_review_queue.csv"

        if "6c" in requested:
            must_exist(dense_fields, "Dense field output is unavailable. Run Step 6B first.")
            must_exist(dense_all_lines, "Full dense OCR output is unavailable. Run Step 6B first.")
            run_command(
                "[6C] Reconstruct dense equipment records",
                [
                    sys.executable,
                    "src/reconstruct_step6_dense_equipment_records.py",
                    "--document-id",
                    document_id,
                    "--dense-fields-csv",
                    str(dense_fields),
                    "--all-ocr-lines-csv",
                    str(dense_all_lines),
                    "--ocr-json-dir",
                    str(dense_detected_tables_dir),
                    "--pages-list",
                    str(dense_pages),
                    "--pdf",
                    str(args.pdf),
                    "--output-dir",
                    str(equipment_dir),
                ],
                dashboard=dashboard, dashboard_step="6C — dense equipment record reconstruction",
            )

        if "6d" in requested:
            must_exist(dense_candidate_records, "6C candidate records are unavailable. Run Step 6C first.")
            run_command(
                "[6D] Link dense equipment record evidence",
                [sys.executable, "src/link_dense_equipment_record_evidence.py",
                 "--records", str(dense_candidate_records), "--metadata", str(dense_fields),
                 "--all-ocr-lines", str(dense_all_lines), "--output", str(dense_linked_evidence)],
                dashboard=dashboard, dashboard_step="6D — dense record evidence linking",
            )

        if "6e" in requested:
            must_exist(dense_candidate_records, "6C candidate records are unavailable. Run Step 6C first.")
            must_exist(dense_linked_evidence, "6D evidence is unavailable. Run Step 6D first.")
            run_command(
                "[6E] Inherit shared dense fields",
                [sys.executable, "src/inherit_dense_equipment_fields.py",
                 "--records", str(dense_candidate_records), "--evidence", str(dense_linked_evidence),
                 "--output", str(dense_inherited_records)],
                dashboard=dashboard, dashboard_step="6E — dense field inheritance",
            )

        if "6f" in requested:
            must_exist(dense_inherited_records, "6E enriched records are unavailable. Run Step 6E first.")
            run_command(
                "[6F] Score dense equipment records",
                [
                    sys.executable,
                    "src/score_dense_equipment_records.py",
                    "--input",
                    str(dense_inherited_records),
                    "--selected",
                    str(dense_scored_records),
                    "--review",
                    str(dense_record_review),
                ],
                dashboard=dashboard,
                dashboard_step="6F — score dense equipment records",
            )

        if "6g" in requested:
            must_exist(
                dense_linked_evidence,
                "Long-form dense equipment evidence is unavailable. "
                "Run Step 6D first.",
            )
            run_command(
                "[6G] Promote long-form dense equipment evidence",
                [
                    sys.executable,
                    "src/promote_dynamic_equipment_evidence.py",
                    "--input",
                    str(dense_linked_evidence),
                    "--registry",
                    str(dynamic_registry),
                    "--metadata",
                    str(dynamic_metadata),
                    "--review",
                    str(dynamic_review),
                ],
                dashboard=dashboard,
                dashboard_step="6G — promote dense field evidence",
            )

        if "7" in requested:
            must_exist(
                dynamic_metadata,
                "Dynamic metadata evidence is unavailable. Run Step 6G first.",
            )
            if provisional_register is not None:
                must_exist(
                    provisional_register,
                    "Supplied provisional linked equipment register is unavailable.",
                )
            must_exist(
                metadata_review_policy,
                "Metadata review policy is unavailable.",
            )

            evidence_patterns = equipment_dir / "evidence_pattern_queue.csv"
            metadata_review_queue = equipment_dir / "metadata_review_queue.csv"
            metadata_auto_accept = equipment_dir / "metadata_auto_accept.csv"
            metadata_review_summary = equipment_dir / "metadata_review_summary.csv"
            evidence_vs_provisional = equipment_dir / "evidence_vs_provisional.csv"
            provisional_vs_evidence = equipment_dir / "provisional_vs_evidence.csv"
            provisional_review_queue = equipment_dir / "provisional_review_queue.csv"
            resolved_metadata = equipment_dir / "provisional_resolved_metadata.csv"
            final_registry = equipment_dir / "final_equipment_registry.csv"
            validated_final_registry = equipment_dir / "final_equipment_registry_validated.csv"

            run_command(
                "[7A] Build evidence-pattern queue",
                [sys.executable, "src/build_evidence_pattern_queue.py",
                 "--input", str(dynamic_metadata), "--output", str(evidence_patterns)],
            )

            run_command(
                "[7B] Build grouped metadata review queue",
                [sys.executable, "src/build_review_queue.py",
                 "--input", str(dynamic_metadata), "--policy", str(metadata_review_policy),
                 "--queue", str(metadata_review_queue), "--auto", str(metadata_auto_accept),
                 "--summary", str(metadata_review_summary)],
            )

            if provisional_register is None:
                print(
                    "[7C–7E] No provisional register supplied; "
                    "comparison stages skipped."
                )
                write_empty_csv(
                    evidence_vs_provisional,
                    [
                        "equipment_tag",
                        "field_name",
                        "field_value",
                        "provisional_value",
                        "provisional_comparison",
                        "recommended_action",
                    ],
                )
                write_empty_csv(
                    provisional_vs_evidence,
                    [
                        "equipment_tag",
                        "field_name",
                        "provisional_value",
                        "evidence_values",
                        "status",
                        "recommended_action",
                    ],
                )
                write_empty_csv(
                    provisional_review_queue,
                    [
                        "equipment_tag",
                        "field_name",
                        "provisional_value",
                        "evidence_values",
                        "review_status",
                        "recommended_action",
                    ],
                )
            else:
                run_command(
                    "[7C] Compare evidence with provisional register",
                    [sys.executable, "src/compare_evidence_to_provisional.py",
                     "--evidence", str(dynamic_metadata), "--provisional", str(provisional_register),
                     "--output", str(evidence_vs_provisional)],
                )
                run_command(
                    "[7D] Compare provisional fields with evidence",
                    [sys.executable, "src/compare_provisional_register.py",
                     "--provisional", str(provisional_register), "--evidence", str(dynamic_metadata),
                     "--output", str(provisional_vs_evidence)],
                )
                run_command(
                    "[7E] Build provisional metadata review queue",
                    [sys.executable, "src/build_provisional_review_queue.py",
                     "--provisional", str(provisional_register), "--evidence", str(dynamic_metadata),
                     "--output", str(provisional_review_queue)],
                )

            if provisional_register is None:
                run_command(
                    "[7F] Build extracted equipment registry",
                    [sys.executable, "src/build_extracted_equipment_registry.py",
                     "--input", str(dynamic_registry),
                     "--output", str(final_registry),
                     "--auto", str(metadata_auto_accept),
                     "--queue", str(metadata_review_queue)],
                )
            elif not resolved_metadata.is_file():
                print(
                    "[7F] Final registry skipped: provisional_resolved_metadata.csv "
                    "is unavailable."
                )
            else:
                run_command(
                    "[7F] Build final equipment registry",
                    [sys.executable, "src/build_final_registry.py",
                     "--provisional", str(provisional_register), "--resolved", str(resolved_metadata),
                     "--output", str(final_registry)],
                )

            if final_registry.is_file():
                run_command(
                    "[7G] Validate final equipment registry",
                    [sys.executable, "src/validate_final_registry.py",
                     "--input", str(final_registry), "--output", str(validated_final_registry)],
                )

        if "8" in requested:
            if args.decisions_yaml is None:
                print(
                    "[8] No --decisions-yaml supplied; "
                    "skipped reviewed-name export."
                )
            else:
                must_exist(
                    args.decisions_yaml,
                    "Supplied decision YAML is unavailable.",
                )
                validated_final_registry = (
                    equipment_dir
                    / "final_equipment_registry_validated.csv"
                )
                must_exist(
                    validated_final_registry,
                    "Validated final equipment registry is unavailable. "
                    "Run Step 7 first.",
                )

                reviewed_final_csv = (
                    equipment_dir
                    / "final_equipment_registry_reviewed.csv"
                )
                reviewed_final_xlsx = (
                    equipment_dir
                    / "final_equipment_registry_reviewed.xlsx"
                )

                run_command(
                    "[8] Apply human-reviewed decisions to final registry",
                    [
                        sys.executable,
                        "src/export_reviewed_equipment_register.py",
                        "--input-csv",
                        str(validated_final_registry),
                        "--decisions-yaml",
                        str(args.decisions_yaml),
                        "--output-csv",
                        str(reviewed_final_csv),
                        "--output-xlsx",
                        str(reviewed_final_xlsx),
                    ],
                )

        if "8" in requested:
            validated_final_registry = (
                equipment_dir
                / "final_equipment_registry_validated.csv"
            )

            measurement_candidates = [
                measurement_dir
                / "calibration_observations_reconciled.csv",
                measurement_dir
                / "calibration_observations_canonical.csv",
                measurement_dir
                / "calibration_observations_assigned.csv",
                measurement_dir
                / "calibration_observations.csv",
            ]

            measurements_csv = next(
                (
                    candidate
                    for candidate in measurement_candidates
                    if candidate.is_file()
                ),
                None,
            )

            if measurements_csv is None:
                raise SystemExit(
                    "No extracted measurement output is available. "
                    "Run Step 3D first."
                )

            final_workbook = (
                equipment_dir
                / f"{document_id}_final_equipment_package.xlsx"
            )

            must_exist(
                validated_final_registry,
                "Validated final equipment registry is unavailable. "
                "Run Step 7 first.",
            )
            must_exist(
                measurements_csv,
                "Final measurement output is unavailable. "
                "Run Step 3D first.",
            )
            must_exist(
                dynamic_metadata,
                "Dynamic metadata output is unavailable. "
                "Run Step 6G first.",
            )
            must_exist(
                provisional_vs_evidence,
                "Provisional comparison output is unavailable. "
                "Run Step 7D first.",
            )
            must_exist(
                provisional_review_queue,
                "Provisional review queue is unavailable. "
                "Run Step 7E first.",
            )

            run_command(
                "[8B] Build final equipment and measurement workbook",
                [
                    sys.executable,
                    "src/build_final_equipment_workbook.py",
                    "--registry",
                    str(validated_final_registry),
                    "--measurements",
                    str(measurements_csv),
                    "--metadata",
                    str(dynamic_metadata),
                    "--comparison",
                    str(provisional_vs_evidence),
                    "--review",
                    str(provisional_review_queue),
                    "--output",
                    str(final_workbook),
                ],
            )

    finally:
        dashboard.current_step = "complete"
        dashboard.stop()
        dashboard.print_snapshot(final=True)

    print()
    print("Pipeline checkpoint run complete.")
    print(f"Completed requested steps: {', '.join(requested)}")
    print("Original source/OCR/register files were not overwritten.")


if __name__ == "__main__":
    main()
