#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PAIR_TAG_RE = re.compile(
    r"\b(?P<area>\d{3,4})\s*-\s*(?P<class>[A-Z]{1,3})\s*-\s*"
    r"(?P<first>\d{3,5})\s*/\s*(?P<second>\d{3,5})\b",
    re.IGNORECASE,
)
FULL_TAG_RE = re.compile(
    r"\b(?P<area>\d{3,4})\s*-\s*(?P<class>[A-Z]{1,3})\s*-\s*"
    r"(?P<number>\d{3,5})\b",
    re.IGNORECASE,
)
SERIAL_RE = re.compile(
    r"\b\d{2,6}\s*-\s*[A-Z]{1,5}\s*-\s*\d{2,8}\b",
    re.IGNORECASE,
)
JOB_RE = re.compile(
    r"\b\d{2,5}\s*-\s*\d{2,6}\b",
    re.IGNORECASE,
)
DRAWING_RE = re.compile(
    r"\b[A-Z][A-Z0-9]{1,12}"
    r"(?:\s*/\s*[A-Z0-9][A-Z0-9-]{0,15}){2,}\b"
    r"|\b[A-Z]{2,12}-\d{3,6}-[A-Z]{2,12}-\d{3,8}\b",
    re.IGNORECASE,
)
REV_RE = re.compile(
    r"\bREV(?:ISION)?\.?\s*[:#]?\s*(?P<rev>[A-Z]?\d{1,3})\b",
    re.IGNORECASE,
)

CANONICAL_SOURCE_KIND = "detected_table_crop"

LABEL_RE = re.compile(
    r"\b(?:CLIENT|PROJECT|EQUIPMENT(?:\s+NAME)?|"
    r"(?:EQUIPMENT|VESSEL)\s+TAG|TAG|SERIAL|JOB|"
    r"DRG|DWG|DRAWING|DOCUMENT|DOC(?:UMENT)?|"
    r"VENDOR|MANUFACTURER|MFR|MODEL|REV(?:ISION)?)\b",
    re.IGNORECASE,
)

NOISE_VALUES = {
    "",
    "TAG",
    "TAG #",
    "EQUIPMENT",
    "EQUIPMENT:",
    "PROJECT",
    "PROJECT:",
    "CLIENT",
    "CLIENT:",
    "JOB",
    "JOB #",
    "JOB NO",
    "DRG",
    "DRG #",
    "DWG",
    "DRAWING",
    "DRAWING:",
    "REV",
    "REVISION",
    "DOCUMENT",
    "MODEL",
    "TYPE",
    "SCALE",
    "SIZE",
    "SHEET",
    "DATE",
    "DESCRIPTION",
    "CLIENT DOCS",
    "CLIENT REP",
    "FOR CLIENT",
    "FOR MANUFACTURER",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create quality-controlled equipment register from saved OCR JSON."
        )
    )
    parser.add_argument("--document-id", required=True)
    parser.add_argument("--ocr-json-dir", type=Path, required=True)
    parser.add_argument("--accepted-csv", type=Path)
    parser.add_argument("--unresolved-csv", type=Path)
    parser.add_argument("--dense-fields-csv", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip(" \t\r\n:;,.()[]{}")


def upper(value: str) -> str:
    return clean(value).upper()


def normalize(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "", upper(value))


def clean_tag(area: str, kind: str, number: str) -> str:
    return f"{area}-{kind.upper()}-{number.zfill(3)}"


def extract_tags(
    text: str,
    require_equipment_context: bool = False,
) -> list[str]:
    text = upper(text)

    if require_equipment_context and not re.search(
        r"\\b(?:EQUIPMENT|VESSEL)\\s+(?:TAG|NO\\.?)\\b"
        r"|\\b(?:EQUIPMENT|VESSEL)\\s*:"
        r"|\\bTAG\\s*(?:NO\\.?|NUMBER|#)?\\s*[:#]",
        text,
        re.IGNORECASE,
    ):
        return []

    tags: list[str] = []

    for match in PAIR_TAG_RE.finditer(text):
        tags.append(
            clean_tag(
                match.group("area"),
                match.group("class"),
                match.group("first"),
            )
        )
        tags.append(
            clean_tag(
                match.group("area"),
                match.group("class"),
                match.group("second"),
            )
        )

    for match in FULL_TAG_RE.finditer(text):
        tags.append(
            clean_tag(
                match.group("area"),
                match.group("class"),
                match.group("number"),
            )
        )

    return list(dict.fromkeys(tags))


def strip_tag_suffix(value: str) -> str:
    value = PAIR_TAG_RE.sub("", value)
    value = FULL_TAG_RE.sub("", value)
    value = re.sub(r"\(\s*\)", "", value)
    return clean(value)


def valid_phrase(value: str, minimum_words: int = 1) -> bool:
    value = clean(value)
    value_upper = upper(value)

    if value_upper in NOISE_VALUES:
        return False

    if len(value) < 3 or len(value) > 100:
        return False

    words = re.findall(r"[A-Z]{2,}", value_upper)
    return len(words) >= minimum_words


def value_after_colon(text: str, label_pattern: str) -> str:
    match = re.search(
        rf"\b{label_pattern}\b\s*(?:NO\.?|NUMBER|#)?\s*[:#]\s*(?P<value>.+)$",
        text,
        re.IGNORECASE,
    )
    return clean(match.group("value")) if match else ""


def values_for_line(text: str) -> dict[str, list[str]]:
    values: dict[str, list[str]] = defaultdict(list)
    content = clean(text)
    content_upper = upper(content)

    client = value_after_colon(content, r"CLIENT")
    if valid_phrase(client):
        values["client"].append(client)

    project = value_after_colon(content, r"PROJECT")
    if valid_phrase(project):
        values["project"].append(project)

    equipment = value_after_colon(
        content,
        r"EQUIPMENT(?:\s+NAME)?",
    )
    if equipment:
        equipment = strip_tag_suffix(equipment)
        if valid_phrase(equipment, minimum_words=2):
            values["equipment_name"].append(equipment)

    tags = extract_tags(content)
    if tags and (
        re.search(
            r"\b(?:EQUIPMENT|VESSEL)\s+TAG\b|\bTAG\s*(?:NO\.?|NUMBER|#)?",
            content_upper,
        )
        or content_upper.startswith("EQUIPMENT:")
    ):
        values["equipment_tag"].extend(tags)

    if "SERIAL" in content_upper or "S/N" in content_upper:
        for serial in SERIAL_RE.findall(content_upper):
            serial = re.sub(r"\s+", "", serial)
            if serial not in NOISE_VALUES:
                values["serial_number"].append(serial)

    if re.search(r"\bJOB\b", content_upper):
        for job in JOB_RE.findall(content_upper):
            job = re.sub(r"\s+", "", job)
            if job not in values["serial_number"]:
                values["job_number"].append(job)

    if re.search(r"\b(?:DRG|DWG|DRAWING)\b", content_upper):
        drawing_value = value_after_colon(
            content,
            r"(?:DRG|DWG|DRAWING)",
        )

        for drawing in DRAWING_RE.findall(drawing_value.upper()):
            drawing = re.sub(r"\s+", "", drawing)
            if len(drawing) >= 8:
                values["drawing_number"].append(drawing)

        revision = REV_RE.search(content_upper)
        if revision:
            values["drawing_revision"].append(
                revision.group("rev")
            )

    if re.search(r"\b(?:VENDOR|MANUFACTURER|MFR)\b", content_upper):
        vendor = value_after_colon(
            content,
            r"(?:VENDOR|MANUFACTURER|MFR)",
        )
        if valid_phrase(vendor):
            values["manufacturer"].append(vendor)

    if re.search(r"\bMODEL\b", content_upper):
        model = value_after_colon(content, r"MODEL")
        if model and len(model) <= 60:
            values["model_number"].append(model)

    revision = REV_RE.search(content_upper)
    if revision:
        values["revision"].append(revision.group("rev"))

    return {
        field: list(dict.fromkeys(items))
        for field, items in values.items()
    }


def csv_rows(path: Path | None, status: str) -> list[dict[str, str]]:
    if not path or not path.is_file():
        return []

    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    for row in rows:
        row["_source_status"] = status

    return rows


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
    sheets: dict[str, tuple[list[dict[str, Any]], list[str]]],
) -> str:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font
    except ImportError:
        return "not_created_openpyxl_unavailable"

    workbook = Workbook()
    workbook.remove(workbook.active)

    for title, (rows, columns) in sheets.items():
        sheet = workbook.create_sheet(title[:31])
        sheet.append(columns)

        for cell in sheet[1]:
            cell.font = Font(bold=True)

        for row in rows:
            sheet.append(
                [
                    json.dumps(value, ensure_ascii=False)
                    if isinstance(value, (dict, list))
                    else value
                    for value in (
                        row.get(column, "")
                        for column in columns
                    )
                ]
            )

        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions

        for cells in sheet.columns:
            letter = cells[0].column_letter
            width = max(
                len(str(cell.value or ""))
                for cell in cells[:300]
            )
            sheet.column_dimensions[letter].width = min(
                max(width + 2, 12),
                55,
            )

    workbook.save(path)
    return "created"


def candidates_for_scope(
    candidates: list[dict[str, Any]],
    scope: str,
) -> list[str]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for item in candidates:
        if item.get("relationship_scope") == scope:
            grouped[item["field_value"]].append(item)

    ranked = sorted(
        grouped.items(),
        key=lambda pair: (
            len(pair[1]),
            max(float(item["confidence"]) for item in pair[1]),
            -min(int(item["page"]) for item in pair[1]),
        ),
        reverse=True,
    )

    return [value for value, _ in ranked]


def canonical_value(
    candidates: list[dict[str, Any]],
) -> tuple[str, list[str]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for item in candidates:
        grouped[item["field_value"]].append(item)

    ranked = sorted(
        grouped.items(),
        key=lambda pair: (
            len(pair[1]),
            max(float(item["confidence"]) for item in pair[1]),
            -min(int(item["page"]) for item in pair[1]),
        ),
        reverse=True,
    )

    if not ranked:
        return "", []

    return ranked[0][0], [value for value, _ in ranked]


def main() -> None:
    args = parse_args()

    if not args.ocr_json_dir.is_dir():
        raise SystemExit(
            f"OCR JSON directory not found: {args.ocr_json_dir}"
        )

    json_files = sorted(args.ocr_json_dir.glob("page_*.json"))
    if not json_files:
        raise SystemExit(
            f"No page_*.json files found: {args.ocr_json_dir}"
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)

    raw_lines: list[dict[str, Any]] = []
    canonical_lines: list[dict[str, Any]] = []
    page_tags: dict[int, set[str]] = defaultdict(set)
    group_sources: list[dict[str, Any]] = []

    for json_path in json_files:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        page = int(payload.get("page") or 0)

        for table in payload.get("tables") or []:
            table_index = int(table.get("table_index") or 0)

            for line in table.get("lines") or []:
                text = clean(str(line.get("text", "")))
                if not text:
                    continue

                kind = str(line.get("source_kind", ""))
                confidence = float(line.get("confidence") or 0.0)
                bbox = line.get("bbox") or {}
                tags = extract_tags(
                    text,
                    require_equipment_context=True,
                )

                raw = {
                    "document_id": args.document_id,
                    "page": page,
                    "table_index": table_index,
                    "text": text,
                    "confidence": round(confidence, 6),
                    "bbox": json.dumps(bbox, ensure_ascii=False),
                    "source_kind": kind,
                    "source_file": str(json_path),
                    "detected_tags": " | ".join(tags),
                }
                raw_lines.append(raw)

                if kind != CANONICAL_SOURCE_KIND:
                    continue

                fields = values_for_line(text)
                explicit_line_tags = fields.get(
                    "equipment_tag",
                    [],
                )

                if explicit_line_tags:
                    page_tags[page].update(explicit_line_tags)

                    if len(explicit_line_tags) > 1:
                        group_sources.append(
                            {
                                "page": page,
                                "tags": sorted(explicit_line_tags),
                                "source_text": text,
                                "confidence": confidence,
                            }
                        )

                canonical_lines.append(
                    {
                        **raw,
                        "fields": fields,
                        "explicit_line_tags": explicit_line_tags,
                    }
                )

    evidence_rows: list[dict[str, Any]] = []
    related_rows: list[dict[str, Any]] = []
    counter = 0

    for line in canonical_lines:
        explicit_tags = line["explicit_line_tags"]
        line_tags = explicit_tags

        if not line_tags and len(page_tags[line["page"]]) <= 2:
            line_tags = sorted(page_tags[line["page"]])
            linkage = (
                "page_context_validated_tag"
                if line_tags
                else "unlinked"
            )
        else:
            linkage = (
                "explicit_validated_tag"
                if explicit_tags
                else (
                    "embedded_tag_in_full_crop_line"
                    if line_tags
                    else "unlinked"
                )
            )

        for field_name, values in line["fields"].items():
            for value in values:
                counter += 1
                evidence_rows.append(
                    {
                        "evidence_id": f"E-{counter:06d}",
                        "document_id": args.document_id,
                        "page": line["page"],
                        "table_index": line["table_index"],
                        "field_name": field_name,
                        "field_value": value,
                        "normalized_value": normalize(value),
                        "confidence": line["confidence"],
                        "linked_tags": " | ".join(line_tags),
                        "relationship_scope": (
                            "group_shared"
                            if len(line_tags) > 1
                            else (
                                "asset_specific"
                                if len(line_tags) == 1
                                else "unlinked"
                            )
                        ),
                        "linkage_method": linkage,
                        "raw_text": line["text"],
                        "bbox": line["bbox"],
                        "source_kind": line["source_kind"],
                        "source_file": line["source_file"],
                    }
                )

        content_upper = upper(line["text"])
        if any(
            marker in content_upper
            for marker in (
                "INSPECTION",
                "ACT:#",
                "ACTIVITY",
                "STANDARD",
                "H - HOLD",
                "R - REVIEW",
                "W - WITNESS",
                "M - MONITOR",
                "CERTIFICATE",
                "ASME",
                "API ",
                "NACE",
                "EN ",
            )
        ):
            related_rows.append(
                {
                    "related_record_id": (
                        f"FULL-CROP-{len(related_rows)+1:06d}"
                    ),
                    "document_id": args.document_id,
                    "page": line["page"],
                    "table_index": line["table_index"],
                    "related_record_type": (
                        "full_crop_related_text"
                    ),
                    "linked_tags": " | ".join(line_tags),
                    "linkage_method": linkage,
                    "confidence": line["confidence"],
                    "record_text": line["text"],
                    "bbox": line["bbox"],
                    "source_file": line["source_file"],
                }
            )

    for row in (
        csv_rows(args.accepted_csv, "accepted_candidate")
        + csv_rows(args.unresolved_csv, "unresolved_candidate")
    ):
        text = clean(row.get("record_text", ""))
        if not text:
            continue

        page = int(row.get("page") or 0)
        upper_text = upper(text)

        if not any(
            marker in upper_text
            for marker in (
                "INSPECTION",
                "ACT:#",
                "ACTIVITY",
                "STANDARD",
                "H - HOLD",
                "R - REVIEW",
                "W - WITNESS",
                "M - MONITOR",
                "CERTIFICATE",
                "ASME",
                "API ",
                "NACE",
                "EN ",
            )
        ):
            continue

        tags = extract_tags(text) or sorted(page_tags.get(page, set()))

        related_rows.append(
            {
                "related_record_id": row.get(
                    "review_id",
                    f"CANDIDATE-{len(related_rows)+1:06d}",
                ),
                "document_id": args.document_id,
                "page": page,
                "table_index": row.get("table_index", ""),
                "related_record_type": row["_source_status"],
                "linked_tags": " | ".join(tags),
                "linkage_method": (
                    "candidate_explicit_tag"
                    if extract_tags(text)
                    else (
                        "candidate_page_context_tag"
                        if tags
                        else "unlinked"
                    )
                ),
                "confidence": row.get("average_confidence", ""),
                "record_text": text,
                "bbox": row.get("row_bbox", ""),
                "source_file": row.get("review_crop", ""),
            }
        )

    if args.dense_fields_csv and args.dense_fields_csv.is_file():
        with args.dense_fields_csv.open(
            encoding="utf-8-sig",
            newline="",
        ) as handle:
            for row in csv.DictReader(handle):
                page = int(row.get("page") or 0)
                text = clean(
                    " ".join(
                        item
                        for item in (
                            row.get("field_label", ""),
                            row.get("field_value", ""),
                            row.get("raw_line", ""),
                        )
                        if item
                    )
                )

                if not text:
                    continue

                related_rows.append(
                    {
                        "related_record_id": (
                            f"DENSE-{page:03d}-{len(related_rows)+1:04d}"
                        ),
                        "document_id": args.document_id,
                        "page": page,
                        "table_index": "",
                        "related_record_type": "dense_field",
                        "linked_tags": " | ".join(
                            sorted(page_tags.get(page, set()))
                        ),
                        "linkage_method": (
                            "page_context_validated_tag"
                            if page_tags.get(page)
                            else "unlinked"
                        ),
                        "confidence": "",
                        "record_text": text,
                        "bbox": "",
                        "source_file": str(args.dense_fields_csv),
                    }
                )

    deduped: dict[tuple[str, str, str, int], dict[str, Any]] = {}

    for row in evidence_rows:
        key = (
            row["field_name"],
            row["normalized_value"],
            row["linked_tags"],
            int(row["page"]),
        )
        existing = deduped.get(key)

        if existing is None or float(row["confidence"]) > float(
            existing["confidence"]
        ):
            deduped[key] = row

    evidence_rows = sorted(
        deduped.values(),
        key=lambda row: (
            row["linked_tags"],
            row["field_name"],
            row["field_value"],
            int(row["page"]),
        ),
    )

    tags = sorted(
        {
            tag
            for row in evidence_rows
            for tag in row["linked_tags"].split(" | ")
            if tag
        }
        | {
            tag
            for tags_on_page in page_tags.values()
            for tag in tags_on_page
        }
    )

    evidence_by_tag: dict[str, list[dict[str, Any]]] = defaultdict(list)
    related_by_tag: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for row in evidence_rows:
        for tag in filter(None, row["linked_tags"].split(" | ")):
            evidence_by_tag[tag].append(row)

    for row in related_rows:
        for tag in filter(None, row["linked_tags"].split(" | ")):
            related_by_tag[tag].append(row)

    group_by_tag: dict[str, str] = {}
    for group in group_sources:
        group_id = (
            f"{args.document_id}:group:"
            + "__".join(group["tags"])
        )
        for tag in group["tags"]:
            group_by_tag[tag] = group_id

    register_rows: list[dict[str, Any]] = []
    tag_rows: list[dict[str, Any]] = []
    review_rows: list[dict[str, Any]] = []
    assets: list[dict[str, Any]] = []

    fields = (
        "equipment_name",
        "client",
        "project",
        "manufacturer",
        "serial_number",
        "job_number",
        "drawing_number",
        "drawing_revision",
        "model_number",
        "revision",
    )

    for tag in tags:
        tag_evidence = evidence_by_tag.get(tag, [])
        field_map: dict[str, list[dict[str, Any]]] = defaultdict(list)

        for row in tag_evidence:
            field_map[row["field_name"]].append(row)

        canonical: dict[str, str] = {}
        candidates: dict[str, list[str]] = {}

        for field_name in fields:
            best, options = canonical_value(
                field_map.get(field_name, [])
            )
            canonical[field_name] = best
            candidates[field_name] = options

            if len(options) > 1:
                review_rows.append(
                    {
                        "equipment_id": (
                            f"{args.document_id}:asset:{tag}"
                        ),
                        "equipment_tag": tag,
                        "issue_type": "multiple_clean_candidates",
                        "field_name": field_name,
                        "candidate_values": " | ".join(options),
                        "source_pages": " | ".join(
                            str(page)
                            for page in sorted(
                                {
                                    int(row["page"])
                                    for row in field_map[field_name]
                                }
                            )
                        ),
                        "reason": (
                            "Multiple values passed full-crop "
                            "line-level validation."
                        ),
                        "recommended_action": (
                            "Confirm preferred value using linked evidence."
                        ),
                    }
                )

        source_pages = sorted(
            {int(row["page"]) for row in tag_evidence}
        )

        asset_specific_serials = candidates_for_scope(
            field_map.get("serial_number", []),
            "asset_specific",
        )
        shared_group_serials = candidates_for_scope(
            field_map.get("serial_number", []),
            "group_shared",
        )
        asset_specific_drawings = candidates_for_scope(
            field_map.get("drawing_number", []),
            "asset_specific",
        )
        shared_group_drawings = candidates_for_scope(
            field_map.get("drawing_number", []),
            "group_shared",
        )
        asset_specific_drawing_revisions = candidates_for_scope(
            field_map.get("drawing_revision", []),
            "asset_specific",
        )
        shared_group_drawing_revisions = candidates_for_scope(
            field_map.get("drawing_revision", []),
            "group_shared",
        )

        if not canonical["equipment_name"]:
            review_rows.append(
                {
                    "equipment_id": (
                        f"{args.document_id}:asset:{tag}"
                    ),
                    "equipment_tag": tag,
                    "issue_type": "missing_equipment_name",
                    "field_name": "equipment_name",
                    "candidate_values": "",
                    "source_pages": " | ".join(
                        str(page) for page in source_pages
                    ),
                    "reason": (
                        "No complete equipment name found in "
                        "a validated full-crop OCR line."
                    ),
                    "recommended_action": (
                        "Inspect linked source pages and raw evidence."
                    ),
                }
            )

        group_id = group_by_tag.get(tag, "")
        review_status = (
            "needs_review"
            if any(item["equipment_tag"] == tag for item in review_rows)
            else "ready_for_validation"
        )

        asset = {
            "equipment_id": f"{args.document_id}:asset:{tag}",
            "equipment_group_id": group_id,
            "equipment_tag": tag,
            "equipment_name": canonical["equipment_name"],
            "client": canonical["client"],
            "project": canonical["project"],
            "manufacturer": canonical["manufacturer"],
            "asset_serial_numbers": asset_specific_serials,
            "shared_group_serial_numbers": shared_group_serials,
            "serial_numbers": (
                asset_specific_serials
                if asset_specific_serials
                else shared_group_serials
            ),
            "job_numbers": candidates["job_number"],
            "asset_drawing_numbers": asset_specific_drawings,
            "shared_group_drawing_numbers": shared_group_drawings,
            "drawing_numbers": (
                asset_specific_drawings
                if asset_specific_drawings
                else shared_group_drawings
            ),
            "asset_drawing_revisions": (
                asset_specific_drawing_revisions
            ),
            "shared_group_drawing_revisions": (
                shared_group_drawing_revisions
            ),
            "drawing_revisions": (
                asset_specific_drawing_revisions
                if asset_specific_drawing_revisions
                else shared_group_drawing_revisions
            ),
            "model_numbers": candidates["model_number"],
            "revisions": candidates["revision"],
            "source_pages": source_pages,
            "evidence_count": len(tag_evidence),
            "related_record_count": len(related_by_tag.get(tag, [])),
            "review_status": review_status,
            "field_candidates": candidates,
        }
        assets.append(asset)

        register_rows.append(
            {
                "equipment_id": asset["equipment_id"],
                "equipment_group_id": asset["equipment_group_id"],
                "equipment_tag": asset["equipment_tag"],
                "equipment_name": asset["equipment_name"],
                "client": asset["client"],
                "project": asset["project"],
                "manufacturer": asset["manufacturer"],
                "asset_serial_numbers": " | ".join(
                    asset["asset_serial_numbers"]
                ),
                "shared_group_serial_numbers": " | ".join(
                    asset["shared_group_serial_numbers"]
                ),
                "serial_numbers": " | ".join(asset["serial_numbers"]),
                "job_numbers": " | ".join(asset["job_numbers"]),
                "asset_drawing_numbers": " | ".join(
                    asset["asset_drawing_numbers"]
                ),
                "shared_group_drawing_numbers": " | ".join(
                    asset["shared_group_drawing_numbers"]
                ),
                "drawing_numbers": " | ".join(
                    asset["drawing_numbers"]
                ),
                "asset_drawing_revisions": " | ".join(
                    asset["asset_drawing_revisions"]
                ),
                "shared_group_drawing_revisions": " | ".join(
                    asset["shared_group_drawing_revisions"]
                ),
                "drawing_revisions": " | ".join(
                    asset["drawing_revisions"]
                ),
                "model_numbers": " | ".join(asset["model_numbers"]),
                "revisions": " | ".join(asset["revisions"]),
                "source_pages": " | ".join(
                    str(page) for page in asset["source_pages"]
                ),
                "source_page_count": len(asset["source_pages"]),
                "evidence_count": asset["evidence_count"],
                "related_record_count": asset[
                    "related_record_count"
                ],
                "review_status": asset["review_status"],
            }
        )

        tag_rows.append(
            {
                "equipment_id": asset["equipment_id"],
                "equipment_group_id": asset["equipment_group_id"],
                "equipment_tag": tag,
                "equipment_name": asset["equipment_name"],
                "source_pages": " | ".join(
                    str(page) for page in asset["source_pages"]
                ),
                "evidence_count": asset["evidence_count"],
                "review_status": asset["review_status"],
            }
        )

    unlinked_raw = [
        row
        for row in raw_lines
        if not row["detected_tags"]
    ]

    register_columns = [
        "equipment_id",
        "equipment_group_id",
        "equipment_tag",
        "equipment_name",
        "client",
        "project",
        "manufacturer",
        "asset_serial_numbers",
        "shared_group_serial_numbers",
        "serial_numbers",
        "job_numbers",
        "asset_drawing_numbers",
        "shared_group_drawing_numbers",
        "drawing_numbers",
        "asset_drawing_revisions",
        "shared_group_drawing_revisions",
        "drawing_revisions",
        "model_numbers",
        "revisions",
        "source_pages",
        "source_page_count",
        "evidence_count",
        "related_record_count",
        "review_status",
    ]
    tag_columns = [
        "equipment_id",
        "equipment_group_id",
        "equipment_tag",
        "equipment_name",
        "source_pages",
        "evidence_count",
        "review_status",
    ]
    evidence_columns = [
        "evidence_id",
        "document_id",
        "page",
        "table_index",
        "field_name",
        "field_value",
        "normalized_value",
        "confidence",
        "linked_tags",
        "relationship_scope",
        "linkage_method",
        "raw_text",
        "bbox",
        "source_kind",
        "source_file",
    ]
    related_columns = [
        "related_record_id",
        "document_id",
        "page",
        "table_index",
        "related_record_type",
        "linked_tags",
        "linkage_method",
        "confidence",
        "record_text",
        "bbox",
        "source_file",
    ]
    raw_columns = [
        "document_id",
        "page",
        "table_index",
        "text",
        "confidence",
        "bbox",
        "source_kind",
        "source_file",
        "detected_tags",
    ]
    review_columns = [
        "equipment_id",
        "equipment_tag",
        "issue_type",
        "field_name",
        "candidate_values",
        "source_pages",
        "reason",
        "recommended_action",
    ]

    write_csv(
        args.output_dir / "equipment_register.csv",
        register_rows,
        register_columns,
    )
    write_csv(
        args.output_dir / "equipment_tags.csv",
        tag_rows,
        tag_columns,
    )
    write_csv(
        args.output_dir / "equipment_metadata_evidence.csv",
        evidence_rows,
        evidence_columns,
    )
    write_csv(
        args.output_dir / "equipment_related_records.csv",
        related_rows,
        related_columns,
    )
    write_csv(
        args.output_dir / "unlinked_evidence.csv",
        unlinked_raw,
        raw_columns,
    )
    write_csv(
        args.output_dir / "equipment_review_queue.csv",
        review_rows,
        review_columns,
    )

    export = {
        "document_id": args.document_id,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "equipment_assets": assets,
        "equipment_groups": group_sources,
        "summary": {
            "ocr_json_pages": len(json_files),
            "raw_ocr_lines": len(raw_lines),
            "canonical_full_crop_lines": len(canonical_lines),
            "equipment_assets": len(assets),
            "metadata_evidence": len(evidence_rows),
            "related_records": len(related_rows),
            "unlinked_raw_lines": len(unlinked_raw),
            "review_queue_records": len(review_rows),
        },
    }

    (args.output_dir / "equipment_register.json").write_text(
        json.dumps(export, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    workbook_status = write_xlsx(
        args.output_dir / "equipment_register.xlsx",
        {
            "Equipment Register": (register_rows, register_columns),
            "Equipment Tags": (tag_rows, tag_columns),
            "Metadata Evidence": (
                evidence_rows,
                evidence_columns,
            ),
            "Related Records": (
                related_rows,
                related_columns,
            ),
            "Unlinked Evidence": (
                unlinked_raw,
                raw_columns,
            ),
            "Review Queue": (review_rows, review_columns),
        },
    )

    summary = {
        "document_id": args.document_id,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "ocr_json_pages": len(json_files),
        "raw_ocr_lines": len(raw_lines),
        "canonical_full_crop_lines": len(canonical_lines),
        "equipment_assets": len(assets),
        "metadata_evidence": len(evidence_rows),
        "related_records": len(related_rows),
        "unlinked_raw_lines": len(unlinked_raw),
        "review_queue_records": len(review_rows),
        "workbook": workbook_status,
        "output_dir": str(args.output_dir),
    }

    (
        args.output_dir / "equipment_export_summary.json"
    ).write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print("V3 equipment-centric export complete.")
    for key, value in summary.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
