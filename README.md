# Well Document Extraction

A provenance-first pipeline for extracting equipment records, equipment metadata, and calibration or measurement observations from vendor manuals and similar engineering PDFs.

The pipeline preserves source-linked evidence, reconstructs equipment records from dense OCR/table evidence, applies policy-driven metadata decisions, and creates a final Excel package for review and handoff.

## What It Produces

For each processed document, the pipeline can produce:

- A validated equipment registry CSV
- A long-form metadata-evidence CSV linked to equipment records
- Canonical calibration or measurement observations
- Metadata review decisions and review-queue artifacts
- A final Excel workbook combining equipment, measurements, metadata evidence, lineage, comparisons, and review outputs

Primary final deliverable:

```text
data/outputs/<document_id>/equipment_centric/
<document_id>_final_equipment_package.xlsx
```

## Requirements

- Python 3.11 or newer
- Project virtual environment at `.venv`
- Source PDFs under `data/raw/`
- OCR/PDF/Excel dependencies installed in `.venv`
- Active metadata policy at `config/metadata_review_policy.json`

The pipeline uses Python tooling including PDF/OCR libraries, OpenPyXL, Pandas, and PyYAML.

## Environment

Activate the virtual environment if needed:

```bash
source .venv/bin/activate
```

All commands below use `.venv/bin/python`, so activation is optional.

To create a virtual environment:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

Install the repository dependency set using the project requirements or environment definition.

## Inputs

Minimum input:

```text
data/raw/<document>.pdf
```

Optional inputs:

- `--provisional-register <path>`: existing equipment register for comparison
- `--completed-calibration-csv <path>`: completed calibration layer for reconciliation
- `--decisions-yaml <path>`: human-approved equipment-name decisions
- `--metadata-review-policy <path>`: alternate metadata decision policy

## Pipeline Stages

Active orchestration entry point:

```text
src/run_universal_equipment_metadata_pipeline.py
```

Supported stages:

```text
0 → 1 → 1b → 2 → 3a → 3b → 3c → 3d → 5 → 6a → 6b → 6c → 6d → 6e → 6f → 6g → 7 → 8
```

Step 4 is retired and intentionally not selectable.

| Stage | Purpose |
|---|---|
| `0` | Profile PDF structure, extract native text, classify pages, and write intake manifest |
| `1` | Select authoritative tag/equipment pages |
| `1b` | Compare intake signals with selected tag pages |
| `2` | Create the tag-page batch manifest |
| `3a` | Run high-DPI generic table/OCR batch extraction |
| `3b` | Identify selected pages with no accepted table evidence |
| `3c` | Run dense OCR on pages routed by 3B |
| `3d` | Reconstruct directional calibration/measurement observations |
| `5` | Build applicable review artifacts |
| `6a` | Select pages for dense metadata extraction |
| `6b` | Extract dense metadata fields and full OCR lines |
| `6c` | Reconstruct dense equipment records from tag and serial evidence |
| `6d` | Link metadata evidence to reconstructed equipment records |
| `6e` | Inherit shared dense record fields |
| `6f` | Score dense equipment records and write any record-level review items |
| `6g` | Create dynamic equipment registry and metadata evidence outputs |
| `7` | Make metadata decisions, build final registry, and validate it |
| `8` | Create final equipment-and-measurement Excel package |

## Full Run

Replace the PDF name and document ID as needed.

```bash
.venv/bin/python \
  src/run_universal_equipment_metadata_pipeline.py \
  --pdf data/raw/vendor_manual_001.pdf \
  --output-root data/outputs/vendor_manual_001 \
  --document-id vendor_manual_001 \
  --start-step 0 \
  --stop-after-step 8
```

All generated artifacts are written below `--output-root`. The pipeline does not overwrite the source PDF or an external provisional register.

## Recommended Checkpoint Runs

For development, recovery, or long documents, use checkpoints.

### Intake through OCR/table checkpoint

```bash
.venv/bin/python \
  src/run_universal_equipment_metadata_pipeline.py \
  --pdf data/raw/vendor_manual_001.pdf \
  --output-root data/outputs/vendor_manual_001 \
  --document-id vendor_manual_001 \
  --start-step 0 \
  --stop-after-step 3a
```

### Downstream page evidence and measurements

```bash
.venv/bin/python \
  src/run_universal_equipment_metadata_pipeline.py \
  --pdf data/raw/vendor_manual_001.pdf \
  --output-root data/outputs/vendor_manual_001 \
  --document-id vendor_manual_001 \
  --start-step 3b \
  --stop-after-step 3d
```

### Equipment metadata through validated registry

```bash
.venv/bin/python \
  src/run_universal_equipment_metadata_pipeline.py \
  --pdf data/raw/vendor_manual_001.pdf \
  --output-root data/outputs/vendor_manual_001 \
  --document-id vendor_manual_001 \
  --start-step 5 \
  --stop-after-step 7
```

### Final Excel package

```bash
.venv/bin/python \
  src/run_universal_equipment_metadata_pipeline.py \
  --pdf data/raw/vendor_manual_001.pdf \
  --output-root data/outputs/vendor_manual_001 \
  --document-id vendor_manual_001 \
  --start-step 8 \
  --stop-after-step 8
```

## Reusing OCR Checkpoints

Step 3A is the expensive high-DPI rendering/OCR/table-extraction stage. To test downstream work in a new output folder without repeating Step 3A, copy only the Step 2 selection checkpoint and Step 3A artifacts.

```bash
SOURCE=data/outputs/vendor_manual_001
TARGET=data/outputs/vendor_manual_001_downstream_test

rm -rf "$TARGET"

mkdir -p \
  "$TARGET/tag_page_selection" \
  "$TARGET/batches" \
  "$TARGET/universal_table_pipeline_tags"

cp -Rp \
  "$SOURCE/tag_page_selection/." \
  "$TARGET/tag_page_selection/"

cp -p \
  "$SOURCE/batches/tag_pages_batch_001.csv" \
  "$TARGET/batches/"

cp -Rp \
  "$SOURCE/universal_table_pipeline_tags/tag_pages_batch_001" \
  "$TARGET/universal_table_pipeline_tags/"
```

Then run fresh downstream processing:

```bash
.venv/bin/python \
  src/run_universal_equipment_metadata_pipeline.py \
  --pdf data/raw/vendor_manual_001.pdf \
  --output-root data/outputs/vendor_manual_001_downstream_test \
  --document-id vendor_manual_001 \
  --start-step 3b \
  --stop-after-step 3d
```

```bash
.venv/bin/python \
  src/run_universal_equipment_metadata_pipeline.py \
  --pdf data/raw/vendor_manual_001.pdf \
  --output-root data/outputs/vendor_manual_001_downstream_test \
  --document-id vendor_manual_001 \
  --start-step 5 \
  --stop-after-step 8
```

Do not copy `equipment_centric/`, `measurement_tables/`, review outputs, or final workbooks into a reproducibility test folder.

## Output Layout

Typical output root:

```text
data/outputs/<document_id>/
├── tag_page_selection/
├── batches/
├── universal_table_pipeline_tags/
├── measurement_tables/
├── equipment_centric/
├── review_queue/
├── non_tabular_tag_pages.txt
├── non_tabular_tag_fields.csv
├── non_tabular_tag_fields_dense.csv
├── dense_field_pages_from_evidence.txt
└── dense_metadata_all_ocr_lines.csv
```

Important equipment outputs:

```text
equipment_centric/
├── equipment_reconstruction_candidate_register.csv
├── universal_dense_metadata_evidence.csv
├── dynamic_equipment_registry.csv
├── dynamic_equipment_metadata.csv
├── metadata_auto_accept.csv
├── metadata_review_queue.csv
├── metadata_review_summary.csv
├── final_equipment_registry.csv
├── final_equipment_registry_validated.csv
└── <document_id>_final_equipment_package.xlsx
```

Important measurement outputs:

```text
measurement_tables/
├── universal_table_cells.csv
├── calibration_observations.csv
├── calibration_observations_assigned.csv
├── calibration_observations_canonical.csv
├── calibration_canonical_slot_review.csv
└── canonical_directional_header_schema.csv
```

Step 8 selects the first available measurement output in this order:

```text
calibration_observations_reconciled.csv
calibration_observations_canonical.csv
calibration_observations_assigned.csv
calibration_observations.csv
```

## Metadata Decisions

Step 7 uses:

```text
config/metadata_review_policy.json
```

The policy controls:

- identity fields, such as `serial_number`
- risk/priority fields, such as `order_acceptance_number`
- evidence-tier rank
- auto-accept confidence threshold
- minimum evidence tier for auto-acceptance
- maximum candidate-value count

A risk field is not automatically forced to manual review. It receives elevated priority only if it fails the configured auto-accept conditions.

Example validated outcome:

```text
153 input evidence rows
17 identity-field rows
136 non-identity metadata auto-accept candidates
0 grouped metadata review tasks
17 auto-accepted equipment records
```

Final registry status behavior:

- Queue task exists for an equipment ID: `field_level_review_required`
- Auto-accepted metadata exists and no queue task exists: `auto_accepted`
- No final decision exists: preserve upstream state and retain review-required validation

## Optional Human Decisions

### Equipment name decisions

The decisions YAML is optional. Step 8 applies only decisions with:

```text
decision: accept_equipment_name
```

Example:

```yaml
decisions:
  5001PSH0111:
    decision: accept_equipment_name
    equipment_name: Ex-proof pressure switch
    confidence: 1.0
    evidence_pages:
      - 72
```

Run with reviewed names:

```bash
.venv/bin/python \
  src/run_universal_equipment_metadata_pipeline.py \
  --pdf data/raw/vendor_manual_001.pdf \
  --output-root data/outputs/vendor_manual_001 \
  --document-id vendor_manual_001 \
  --decisions-yaml config/reviewed_equipment_names.yaml \
  --start-step 8 \
  --stop-after-step 8
```

Do not create a decisions YAML merely to repeat auto-accepted values. Use it only for genuine human approvals, corrections, or overrides.

### Provisional register comparison

To compare extracted evidence against an existing equipment register:

```bash
.venv/bin/python \
  src/run_universal_equipment_metadata_pipeline.py \
  --pdf data/raw/vendor_manual_001.pdf \
  --output-root data/outputs/vendor_manual_001 \
  --document-id vendor_manual_001 \
  --provisional-register data/reference/provisional_equipment_register.csv \
  --start-step 7 \
  --stop-after-step 8
```

Without a provisional register, Step 7 writes header-only comparison/review placeholders. This is expected and allows the pipeline to create an extracted-only final package.

### Calibration reconciliation

Provide a completed calibration layer to enable Step 3D secondary resolution and reconciliation:

```bash
.venv/bin/python \
  src/run_universal_equipment_metadata_pipeline.py \
  --pdf data/raw/vendor_manual_001.pdf \
  --output-root data/outputs/vendor_manual_001 \
  --document-id vendor_manual_001 \
  --completed-calibration-csv data/reference/completed_calibration.csv \
  --start-step 3d \
  --stop-after-step 3d
```

## Inspecting Data

### Final registry status counts

```bash
.venv/bin/python - <<'PY'
import csv
from collections import Counter
from pathlib import Path

path = Path(
    "data/outputs/vendor_manual_001/"
    "equipment_centric/final_equipment_registry_validated.csv"
)

with path.open(encoding="utf-8-sig", newline="") as handle:
    rows = list(csv.DictReader(handle))

print("Registry rows:", len(rows))
print("review_status:", Counter(
    row.get("review_status", "") for row in rows
))
print("validation_status:", Counter(
    row.get("validation_status", "") for row in rows
))
PY
```

### Metadata review state

```bash
wc -l \
  data/outputs/vendor_manual_001/equipment_centric/metadata_review_queue.csv \
  data/outputs/vendor_manual_001/equipment_centric/metadata_auto_accept.csv
```

A header-only queue has one line:

```text
1 metadata_review_queue.csv
```

Readable policy summary:

```bash
column -s, -t \
  data/outputs/vendor_manual_001/equipment_centric/metadata_review_summary.csv \
  | less -S
```

### View the registry and evidence

```bash
column -s, -t \
  data/outputs/vendor_manual_001/equipment_centric/\
final_equipment_registry_validated.csv \
  | less -S
```

```bash
column -s, -t \
  data/outputs/vendor_manual_001/equipment_centric/\
dynamic_equipment_metadata.csv \
  | less -S
```

### Inspect field provenance

```bash
.venv/bin/python - <<'PY'
import csv
from pathlib import Path

path = Path(
    "data/outputs/vendor_manual_001/"
    "equipment_centric/dynamic_equipment_metadata.csv"
)

field_name = "order_acceptance_number"

with path.open(encoding="utf-8-sig", newline="") as handle:
    rows = list(csv.DictReader(handle))

for row in rows:
    if row.get("field_name") == field_name:
        print({
            "equipment_id": row.get("equipment_id"),
            "equipment_tag": row.get("equipment_tag"),
            "serial_number": row.get("serial_number"),
            "field_name": row.get("field_name"),
            "field_value": row.get("field_value") or row.get("value"),
            "evidence_tier": row.get("evidence_tier"),
            "confidence": row.get("confidence"),
            "source_page": row.get("source_page") or row.get("source_pages"),
            "source_text": row.get("source_text"),
        })
PY
```

Change `field_name` to inspect another field.

### Inspect final workbook sheets

```bash
.venv/bin/python - <<'PY'
from pathlib import Path
from openpyxl import load_workbook

path = Path(
    "data/outputs/vendor_manual_001/"
    "equipment_centric/vendor_manual_001_final_equipment_package.xlsx"
)

book = load_workbook(path, read_only=True, data_only=True)

for name in book.sheetnames:
    sheet = book[name]
    print(
        f"{name}: "
        f"{sheet.max_row - 1} data rows, "
        f"{sheet.max_column} columns"
    )
PY
```

Expected sheets:

```text
README
Equipment Registry
Measurement Readings
Metadata Evidence
Provisional Comparison
Review Queue
Lineage
```

## Final Validation

Use this after a complete run. It verifies expected final artifacts exist, reads row counts, checks final statuses, and confirms the workbook is non-empty.

```bash
.venv/bin/python - <<'PY'
import csv
from collections import Counter
from pathlib import Path
from openpyxl import load_workbook

root = Path("data/outputs/vendor_manual_001")
equipment = root / "equipment_centric"
measurements = root / "measurement_tables"

registry = equipment / "final_equipment_registry_validated.csv"
metadata = equipment / "dynamic_equipment_metadata.csv"
auto = equipment / "metadata_auto_accept.csv"
queue = equipment / "metadata_review_queue.csv"
observations = measurements / "calibration_observations_canonical.csv"
workbook_path = equipment / "vendor_manual_001_final_equipment_package.xlsx"

required = [
    registry,
    metadata,
    auto,
    queue,
    observations,
    workbook_path,
]

missing = [path for path in required if not path.is_file()]

if missing:
    raise SystemExit(
        "Missing final artifacts:\n"
        + "\n".join(f"- {path}" for path in missing)
    )

def read_rows(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))

registry_rows = read_rows(registry)
metadata_rows = read_rows(metadata)
auto_rows = read_rows(auto)
queue_rows = read_rows(queue)
measurement_rows = read_rows(observations)

book = load_workbook(workbook_path, read_only=True, data_only=True)

print("Registry rows:", len(registry_rows))
print("Metadata evidence rows:", len(metadata_rows))
print("Metadata auto-accept rows:", len(auto_rows))
print("Metadata review tasks:", len(queue_rows))
print("Canonical measurement rows:", len(measurement_rows))
print("Workbook bytes:", workbook_path.stat().st_size)
print("Review statuses:", Counter(
    row.get("review_status", "") for row in registry_rows
))
print("Validation statuses:", Counter(
    row.get("validation_status", "") for row in registry_rows
))
print("Workbook sheets:", ", ".join(book.sheetnames))

if not registry_rows:
    raise SystemExit("Final registry contains no records.")

if workbook_path.stat().st_size == 0:
    raise SystemExit("Final workbook is empty.")

print("PASS: required final artifacts are present and non-empty.")
PY
```

Counts vary by document. Check for consistency, evidence provenance, decision state, review queues, and expected workbook sheets rather than assuming one document's counts apply to all runs.

## Troubleshooting

### Selected tag pages are unavailable

Step 3B requires:

```text
tag_page_selection/selected_pages.txt
```

Run the selection stages first, or copy the complete `tag_page_selection/` checkpoint when testing downstream stages.

### No extracted measurement output is available

Step 8 requires a measurement CSV from Step 3D:

```bash
.venv/bin/python \
  src/run_universal_equipment_metadata_pipeline.py \
  --pdf data/raw/vendor_manual_001.pdf \
  --output-root data/outputs/vendor_manual_001 \
  --document-id vendor_manual_001 \
  --start-step 3d \
  --stop-after-step 3d
```

### Validated final equipment registry is unavailable

Run Step 7:

```bash
.venv/bin/python \
  src/run_universal_equipment_metadata_pipeline.py \
  --pdf data/raw/vendor_manual_001.pdf \
  --output-root data/outputs/vendor_manual_001 \
  --document-id vendor_manual_001 \
  --start-step 7 \
  --stop-after-step 7
```

### Registry says review required but the metadata queue is empty

The registry must be built after Step 7 decisions. The active runner passes both:

```text
metadata_auto_accept.csv
metadata_review_queue.csv
```

to `build_extracted_equipment_registry.py`, allowing final status to resolve to `auto_accepted` when appropriate.

### Step 3D reports missing-slot review rows

This indicates incomplete coverage for one or more expected canonical measurement header slots. It does not automatically invalidate equipment identity or metadata.

Inspect:

```text
measurement_tables/calibration_canonical_slot_review.csv
measurement_tables/calibration_observation_assignment_review.csv
```

If a completed calibration layer exists, use `--completed-calibration-csv` to enable reconciliation.

### Step 8 skips reviewed-name export

This is expected if `--decisions-yaml` is omitted. The final equipment-and-measurement workbook is still built.

### PyMuPDF `fitz` deprecation warning

A warning recommending `import pymupdf` rather than `import fitz` is non-fatal. Plan an API migration, but do not treat the warning alone as output failure.

## Workspace Conventions

```text
src/               Active pipeline source code
config/            Active policies and configurations
data/raw/          Source documents
data/outputs/      Generated document outputs
archive/           Retired patches, prototypes, and snapshots
workspace_audit/   Cleanup manifests, inventories, and validation notes
```

Keep reusable Step 2 and Step 3A checkpoints when downstream reruns may be needed. Keep final CSV evidence, review artifacts, and final workbooks together for auditability.

## Before Handoff

1. Confirm `final_equipment_registry_validated.csv` exists and has records.
2. Confirm `metadata_review_queue.csv` is empty or has been reviewed.
3. Inspect measurement review artifacts for unresolved missing-slot issues.
4. Confirm the final Excel workbook exists and opens.
5. Keep the source-linked CSV evidence, review outputs, and workbook together.
6. Record source PDF version, pipeline commit/version, policy/config files, and run date.

## Advanced Runner Options

Most runs need only `--pdf`, `--output-root`, and optionally `--document-id`. The following controls are available for tuned, selective, or reproducible runs.

| Option | Purpose | Default |
|---|---|---|
| `--taxonomy-config <path>` | YAML page-taxonomy configuration used by advisory intake Step 0 | Runner default |
| `--intake-supplemental-minimum-score <number>` | Minimum advisory intake score for including supplemental pages | Runner default |
| `--batch-name <name>` | Stem/name of the authoritative tag-page batch CSV | Runner default |
| `--scan-dpi <integer>` | Low-resolution DPI used during tag-page selection | `160` |
| `--ocr-dpi <integer>` | High-resolution DPI used for OCR/table extraction | `300` |
| `--local-radius-px <integer>` | Maximum local tag-to-field association distance in pixels | `900` |
| `--dashboard-interval <seconds>` | Terminal dashboard refresh interval; use `0` to disable | Runner default |
| `--measurement-pages-list <path>` | Optional one-based page list used only by Step 3D measurement reconstruction | All detected-table OCR pages |

Example tuned run:

```bash
.venv/bin/python \
  src/run_universal_equipment_metadata_pipeline.py \
  --pdf data/raw/vendor_manual_001.pdf \
  --output-root data/outputs/vendor_manual_001 \
  --document-id vendor_manual_001 \
  --scan-dpi 160 \
  --ocr-dpi 300 \
  --local-radius-px 900 \
  --dashboard-interval 10 \
  --start-step 0 \
  --stop-after-step 8
```

Use a dedicated measurement page list only when you have independently identified the pages that contain calibration/measurement tables:

```bash
.venv/bin/python \
  src/run_universal_equipment_metadata_pipeline.py \
  --pdf data/raw/vendor_manual_001.pdf \
  --output-root data/outputs/vendor_manual_001 \
  --document-id vendor_manual_001 \
  --measurement-pages-list data/reference/measurement_pages.txt \
  --start-step 3d \
  --stop-after-step 3d
```

The measurement page list must contain one-based PDF page numbers, typically one page number per line.
