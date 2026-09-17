#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


IDENTIFIER_RE = re.compile(
    r"\b[A-Z0-9]{2,8}(?:\s*-\s*[A-Z0-9]{1,12}){2,6}\b",
    re.IGNORECASE,
)

PAIR_TAG_RE = re.compile(
    r"\b(?P<area>\d{3,4})\s*-\s*(?P<class>[A-Z]{1,4})\s*-\s*"
    r"(?P<first>\d{3,5})\s*/\s*(?P<second>\d{3,5})\b",
    re.IGNORECASE,
)

FULL_TAG_RE = re.compile(
    r"\b(?P<area>\d{3,4})\s*-\s*(?P<class>[A-Z]{1,4})\s*-\s*"
    r"(?P<number>\d{3,5})\b",
    re.IGNORECASE,
)

SERIAL_RE = re.compile(
    r"\b\d{2,6}\s*-\s*[A-Z]{1,5}\s*-\s*\d{2,8}\b",
    re.IGNORECASE,
)

JOB_LABEL_RE = re.compile(
    r"\b(?:GMMOS\s+)?JOB(?:\s+NO\.?|\s+NUMBER|#)?\s*[:#]?",
    re.IGNORECASE,
)

DRAWING_LABEL_RE = re.compile(
    r"\b(?:DRG|DWG|DRAWING)(?:\s+NO\.?|\s+NUMBER|#)?\s*[:#]?",
    re.IGNORECASE,
)

DOCUMENT_LABEL_RE = re.compile(
    r"\b(?:DOC(?:UMENT)?(?:\s+NO\.?|\s+NUMBER|\s+REF(?:ERENCE)?)?|"
    r"QCP|WPS|WPQR|ITP|MDR|P&ID)\b",
    re.IGNORECASE,
)

EXPLICIT_TAG_LABEL_RE = re.compile(
    r"\b(?:EQUIPMENT|VESSEL|PUMP|COMPRESSOR|MOTOR|SKID|PACKAGE)"
    r"\s+(?:TAG|NO\.?|NUMBER)\b"
    r"|\bTAG\s*(?:NO\.?|NUMBER|#)?\s*[:#]"
    r"|\bEQUIPMENT\s*:",
    re.IGNORECASE,
)

EQUIPMENT_TERMS = {
    "EQUIPMENT",
    "VESSEL",
    "RECEIVER",
    "PUMP",
    "COMPRESSOR",
    "MOTOR",
    "TANK",
    "FILTER",
    "SEPARATOR",
    "EXCHANGER",
    "HEATER",
    "COOLER",
    "VALVE",
    "SKID",
    "PACKAGE",
    "BLOWER",
    "FAN",
    "GENERATOR",
    "TURBINE",
    "INSTRUMENT",
    "AIR",
}

TITLE_BLOCK_TERMS = {
    "SCALE",
    "SHEET",
    "DRAWN",
    "CHECKED",
    "APPROVED",
    "APPD",
    "DWN",
    "REVISION",
    "DOCUMENT NUMBER",
    "PURCHASER",
    "CLIENT REP",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build a broad, reviewable equipment-tag candidate inventory "
            "from saved OCR JSON."
        )
    )
    parser.add_argument("--document-id", required=True)
    parser.add_argument("--ocr-json-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip(" \t\r\n:;,.()[]{}")


def upper(value: str) -> str:
    return clean(value).upper()


def normalize_identifier(value: str) -> str:
    return re.sub(r"\s+", "", upper(value))


def normalize_tag(
    area: str,
    tag_class: str,
    number: str,
) -> str:
    return f"{area}-{tag_class.upper()}-{number.zfill(3)}"


def tag_matches(text: str) -> list[str]:
    candidates: list[str] = []

    for match in PAIR_TAG_RE.finditer(upper(text)):
        candidates.append(
            normalize_tag(
                match.group("area"),
                match.group("class"),
                match.group("first"),
            )
        )
        candidates.append(
            normalize_tag(
                match.group("area"),
                match.group("class"),
                match.group("second"),
            )
        )

    for match in FULL_TAG_RE.finditer(upper(text)):
        candidates.append(
            normalize_tag(
                match.group("area"),
                match.group("class"),
                match.group("number"),
            )
        )

    return list(dict.fromkeys(candidates))


def nearby_terms(text: str) -> list[str]:
    content = upper(text)
    return sorted(
        term
        for term in EQUIPMENT_TERMS
        if term in content
    )


def classify_candidate(
    candidate: str,
    text: str,
) -> tuple[str, int, str]:
    content = upper(text)
    candidate_upper = upper(candidate)
    score = 0

    explicit_tag_context = bool(
        EXPLICIT_TAG_LABEL_RE.search(content)
    )
    equipment_context = nearby_terms(content)
    title_block_hits = sum(
        term in content
        for term in TITLE_BLOCK_TERMS
    )

    if (
        SERIAL_RE.fullmatch(candidate_upper)
        and re.search(r"\\bSERIAL\\b|\\bS/N\\b", content)
    ):
        return (
            "serial_number",
            -8,
            "exclude_from_asset_admission",
        )

    if (
        DRAWING_LABEL_RE.search(content)
        or candidate_upper.startswith(("RFT-", "GMMOS-", "IN-"))
    ):
        return (
            "drawing_or_document_reference",
            -7,
            "exclude_from_asset_admission",
        )

    if (
        DOCUMENT_LABEL_RE.search(content)
        and (
            "SPECIFICATION" in content
            or "PROCEDURE" in content
            or "DOCUMENT" in content
        )
    ):
        return (
            "document_or_procedure_reference",
            -6,
            "exclude_from_asset_admission",
        )

    if (
        FULL_TAG_RE.fullmatch(candidate_upper)
        and EXPLICIT_TAG_LABEL_RE.search(content)
    ):
        return (
            "likely_equipment_tag",
            12,
            "admit_to_asset_candidate_queue",
        )

    if (
        FULL_TAG_RE.fullmatch(candidate_upper)
        and equipment_context
    ):
        return (
            "possible_equipment_tag",
            6 + min(3, len(equipment_context)),
            "review_for_asset_admission",
        )

    if DRAWING_LABEL_RE.search(content):
        return (
            "drawing_or_document_reference",
            -7,
            "exclude_from_asset_admission",
        )

    if DOCUMENT_LABEL_RE.search(content):
        return (
            "document_or_procedure_reference",
            -6,
            "exclude_from_asset_admission",
        )

    if JOB_LABEL_RE.search(content):
        return (
            "job_number_or_reference",
            -5,
            "exclude_from_asset_admission",
        )

    if candidate_upper.startswith(("RFT-", "GMMOS-", "IN-")):
        return (
            "likely_reference_identifier",
            -4,
            "review_as_reference",
        )

    if explicit_tag_context:
        score += 8

    if equipment_context:
        score += min(4, len(equipment_context))

    if PAIR_TAG_RE.search(content):
        score += 3

    if title_block_hits >= 3:
        score -= 5

    if FULL_TAG_RE.fullmatch(candidate_upper):
        score += 2
    else:
        score -= 2

    if score >= 10:
        return (
            "likely_equipment_tag",
            score,
            "admit_to_asset_candidate_queue",
        )

    if score >= 5:
        return (
            "possible_equipment_tag",
            score,
            "review_for_asset_admission",
        )

    return (
        "ambiguous_identifier",
        score,
        "review_only",
    )


def write_csv(
    path: Path,
    rows: list[dict[str, Any]],
    columns: list[str],
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=columns,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)


def write_xlsx(
    path: Path,
    rows: list[dict[str, Any]],
    columns: list[str],
) -> str:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font
    except ImportError:
        return "not_created_openpyxl_unavailable"

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Tag Candidates"
    sheet.append(columns)

    for cell in sheet[1]:
        cell.font = Font(bold=True)

    for row in rows:
        sheet.append(
            [
                row.get(column, "")
                for column in columns
            ]
        )

    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions

    for cells in sheet.columns:
        letter = cells[0].column_letter
        width = max(
            len(str(cell.value or ""))
            for cell in cells[:400]
        )
        sheet.column_dimensions[letter].width = min(
            max(width + 2, 12),
            65,
        )

    workbook.save(path)
    return "created"


def main() -> None:
    args = parse_args()

    if not args.ocr_json_dir.is_dir():
        raise SystemExit(
            f"OCR JSON directory not found: {args.ocr_json_dir}"
        )

    json_files = sorted(args.ocr_json_dir.glob("page_*.json"))
    if not json_files:
        raise SystemExit(
            f"No OCR page JSON files found in: {args.ocr_json_dir}"
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)

    occurrences: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int, str, str]] = set()

    for json_path in json_files:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        page = int(payload.get("page") or 0)

        for table in payload.get("tables") or []:
            table_index = int(table.get("table_index") or 0)

            for line in table.get("lines") or []:
                text = clean(str(line.get("text", "")))
                if not text:
                    continue

                candidates = tag_matches(text)

                for raw_identifier in IDENTIFIER_RE.findall(
                    upper(text)
                ):
                    normalized_id = normalize_identifier(raw_identifier)

                    if normalized_id not in candidates:
                        candidates.append(normalized_id)

                for candidate in candidates:
                    candidate = clean(candidate)
                    if not candidate:
                        continue

                    kind, score, recommendation = classify_candidate(
                        candidate,
                        text,
                    )

                    source_kind = str(
                        line.get("source_kind", "")
                    )
                    key = (
                        candidate,
                        page,
                        table_index,
                        source_kind,
                        upper(text),
                    )

                    if key in seen:
                        continue

                    seen.add(key)

                    occurrences.append(
                        {
                            "document_id": args.document_id,
                            "candidate": candidate,
                            "candidate_normalized": normalize_identifier(
                                candidate
                            ),
                            "candidate_type": kind,
                            "candidate_score": score,
                            "admission_recommendation": recommendation,
                            "source_page": page,
                            "table_index": table_index,
                            "source_kind": source_kind,
                            "ocr_confidence": round(
                                float(line.get("confidence") or 0.0),
                                6,
                            ),
                            "explicit_label_context": (
                                "yes"
                                if EXPLICIT_TAG_LABEL_RE.search(
                                    upper(text)
                                )
                                else "no"
                            ),
                            "nearby_equipment_terms": " | ".join(
                                nearby_terms(text)
                            ),
                            "source_text": text,
                            "source_file": str(json_path),
                        }
                    )

    by_candidate: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for row in occurrences:
        by_candidate[row["candidate_normalized"]].append(row)

    inventory_rows: list[dict[str, Any]] = []

    for candidate_key, rows in by_candidate.items():
        rows.sort(
            key=lambda row: (
                row["candidate_score"],
                row["ocr_confidence"],
            ),
            reverse=True,
        )

        best = rows[0]
        pages = sorted({row["source_page"] for row in rows})
        kinds = Counter(row["candidate_type"] for row in rows)
        recommendations = Counter(
            row["admission_recommendation"]
            for row in rows
        )

        inventory_rows.append(
            {
                "document_id": args.document_id,
                "candidate": best["candidate"],
                "candidate_normalized": candidate_key,
                "candidate_type": best["candidate_type"],
                "best_candidate_score": best["candidate_score"],
                "best_admission_recommendation": (
                    best["admission_recommendation"]
                ),
                "occurrence_count": len(rows),
                "source_page_count": len(pages),
                "source_pages": " | ".join(
                    str(page) for page in pages
                ),
                "best_ocr_confidence": best["ocr_confidence"],
                "explicit_label_context_seen": (
                    "yes"
                    if any(
                        row["explicit_label_context"] == "yes"
                        for row in rows
                    )
                    else "no"
                ),
                "nearby_equipment_terms": (
                    best["nearby_equipment_terms"]
                ),
                "candidate_type_distribution": " | ".join(
                    f"{kind}:{count}"
                    for kind, count in kinds.most_common()
                ),
                "recommendation_distribution": " | ".join(
                    f"{recommendation}:{count}"
                    for recommendation, count in recommendations.most_common()
                ),
                "best_source_page": best["source_page"],
                "best_table_index": best["table_index"],
                "best_source_kind": best["source_kind"],
                "best_source_text": best["source_text"],
                "best_source_file": best["source_file"],
            }
        )

    inventory_rows.sort(
        key=lambda row: (
            {
                "likely_equipment_tag": 0,
                "possible_equipment_tag": 1,
                "ambiguous_identifier": 2,
                "likely_reference_identifier": 3,
                "serial_number": 4,
                "job_number_or_reference": 5,
                "drawing_or_document_reference": 6,
                "document_or_procedure_reference": 7,
            }.get(row["candidate_type"], 99),
            -int(row["best_candidate_score"]),
            -int(row["occurrence_count"]),
            row["candidate"],
        )
    )

    columns = [
        "document_id",
        "candidate",
        "candidate_normalized",
        "candidate_type",
        "best_candidate_score",
        "best_admission_recommendation",
        "occurrence_count",
        "source_page_count",
        "source_pages",
        "best_ocr_confidence",
        "explicit_label_context_seen",
        "nearby_equipment_terms",
        "candidate_type_distribution",
        "recommendation_distribution",
        "best_source_page",
        "best_table_index",
        "best_source_kind",
        "best_source_text",
        "best_source_file",
    ]

    csv_path = args.output_dir / "tag_candidates.csv"
    xlsx_path = args.output_dir / "tag_candidates.xlsx"
    summary_path = args.output_dir / "tag_candidate_summary.json"

    write_csv(csv_path, inventory_rows, columns)
    workbook_status = write_xlsx(
        xlsx_path,
        inventory_rows,
        columns,
    )

    summary = {
        "document_id": args.document_id,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "ocr_json_pages": len(json_files),
        "candidate_occurrences": len(occurrences),
        "unique_candidates": len(inventory_rows),
        "candidate_types": dict(
            Counter(
                row["candidate_type"]
                for row in inventory_rows
            )
        ),
        "admission_recommendations": dict(
            Counter(
                row["best_admission_recommendation"]
                for row in inventory_rows
            )
        ),
        "workbook": workbook_status,
        "csv": str(csv_path),
        "xlsx": str(xlsx_path),
    }

    summary_path.write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8",
    )

    print("Equipment tag-candidate inventory complete.")
    for key, value in summary.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
