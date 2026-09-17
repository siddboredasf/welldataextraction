#!/usr/bin/env python3
"""
Step 6C: reconstruct equipment candidates from dense certificate/table OCR.

Primary source:
    Step 3A generic detected-table OCR JSON lines, including bbox/confidence.

Secondary sources:
    Step 6B dense OCR field rows and all OCR lines.

This stage does not read or use Step 3D measurement outputs or 3D tag seeds.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path


OUTPUT_FIELDS = [
    "equipment_tag",
    "serial_number",
    "model",
    "equipment_description",
    "range_or_rating",
    "set_point",
    "declaration_reference",
    "order_acceptance_number",
    "purchase_order",
    "test_certificate_reference",
    "source_page",
    "linked_source_pages",
    "link_basis",
    "field_inheritance_note",
    "record_status",
    "review_status",
    "raw_ocr_tag",
    "tag_ocr_confidence",
    "source_record_id",
    "source_document_id",
    "source_table_index",
    "source_text",
    "source_bbox",
    "extraction_method",
    "review_reason",
    "overlaps_3d_tag",
]

DIRECT_SERIAL_TAG_RE = re.compile(
    r"""
    (?:ser(?:ial)?\.?\s*(?:no|number)\.?)
    \s*[:#-]+\s*
    (?P<serial>\d{4,})
    \s*
    tag
    \s*[:#-]?\s*
    (?P<tag>[A-Z0-9-]{5,})
    """,
    re.IGNORECASE | re.VERBOSE,
)

SERIAL_ONLY_RE = re.compile(
    r"""
    (?:ser(?:ial)?\.?\s*(?:no|number)\.?)
    \s*[:#-]?\s*
    (?P<serial>\d{4,})
    """,
    re.IGNORECASE | re.VERBOSE,
)

TAG_FRAGMENT_RE = re.compile(
    r"(?<![A-Z0-9])(?P<tag>\d{3,8}[A-Z]{1,10}\d{2,8})(?![A-Z0-9])",
    re.IGNORECASE,
)

SET_POINT_RE = re.compile(
    r"""
    (?:set\s*[12]|setpoint)
    \s*[:#-]?\s*
    (?P<value>\d+(?:\.\d+)?\s*BAR)
    """,
    re.IGNORECASE | re.VERBOSE,
)

RANGE_RE = re.compile(
    r"""
    (?P<value>
        \d+(?:\.\d+)?
        \s*(?:/|-|TO)\s*
        \d+(?:\.\d+)?
        \s*BAR
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

MODEL_RE = re.compile(
    r"\b(?P<model>MAH[A-Z0-9]{3,})\b",
    re.IGNORECASE,
)

PURCHASE_ORDER_RE = re.compile(
    r"""
    (?:
        vs\.?\s*ord\.?\s*no\.?
        |purchase\s+ord(?:er|\.?)?
        |purchase\s+order
    )
    \s*[:#-]?\s*
    (?P<value>[A-Z0-9][A-Z0-9/._-]+)
    """,
    re.IGNORECASE | re.VERBOSE,
)

DECLARATION_RE = re.compile(
    r"""
    (?:n[°o]\.?\s*:\s*|declaration(?:\s+ref(?:erence)?)?\s*[:#-]?)
    (?P<value>DC\s*[-.]?\s*\d{3,})
    """,
    re.IGNORECASE | re.VERBOSE,
)

CERTIFICATE_RE = re.compile(
    r"\b(?P<value>CA\s*[- ]?\s*\d{4,})\b",
    re.IGNORECASE,
)


def clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip(" ;|,\t")


def normalise_page(value: object) -> str:
    text = clean(value)

    if text.isdigit():
        return str(int(text))

    return ""


def normalise_tag(value: str) -> str:
    value = re.sub(r"[^A-Z0-9-]", "", value.upper())

    # Retain OCR output as the candidate identity unless the repair is
    # mechanically safe. Do not convert O/I/L ambiguities silently.
    return value


def parse_confidence(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def confidence_text(value: object) -> str:
    numeric = parse_confidence(value)

    if numeric <= 0:
        return ""

    return f"{numeric:.6f}"


def bbox_text(line: dict) -> str:
    bbox = line.get("bbox", {})

    if isinstance(bbox, dict):
        values = {
            key: bbox.get(key)
            for key in ("x1", "y1", "x2", "y2")
        }

        if any(value is not None for value in values.values()):
            return json.dumps(values, separators=(",", ":"))

    values = {
        key: line.get(key)
        for key in ("bbox_x1", "bbox_y1", "bbox_x2", "bbox_y2")
    }

    if any(clean(value) for value in values.values()):
        return json.dumps(values, separators=(",", ":"))

    return clean(line.get("bbox"))


def line_bbox(line: dict) -> tuple[float, float, float, float]:
    bbox = line.get("bbox", {})

    if not isinstance(bbox, dict):
        bbox = line

    try:
        return (
            float(bbox.get("x1", 0.0)),
            float(bbox.get("y1", 0.0)),
            float(bbox.get("x2", 0.0)),
            float(bbox.get("y2", 0.0)),
        )
    except (TypeError, ValueError):
        return (0.0, 0.0, 0.0, 0.0)


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []

    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def read_selected_pages(path: Path) -> set[str]:
    if not path.is_file():
        return set()

    return {
        normalise_page(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if normalise_page(line)
    }


def extract_json_lines(
    ocr_json_dir: Path,
    selected_pages: set[str],
) -> dict[str, list[dict]]:
    lines_by_page: dict[str, list[dict]] = defaultdict(list)

    for path in sorted(ocr_json_dir.glob("page_*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue

        page = normalise_page(payload.get("page"))

        if page not in selected_pages:
            continue

        for table in payload.get("tables", []):
            table_index = table.get("table_index", "")

            for line in table.get("lines", []):
                item = dict(line)
                item["page"] = page
                item["table_index"] = str(table_index)
                item["source_file"] = str(path)
                item["text"] = clean(item.get("text"))

                if item["text"]:
                    lines_by_page[page].append(item)

    for page, lines in lines_by_page.items():
        lines.sort(
            key=lambda line: (
                line_bbox(line)[1],
                line_bbox(line)[0],
            )
        )

    return lines_by_page


def read_dense_lines(
    path: Path,
    selected_pages: set[str],
) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = defaultdict(list)

    for row in read_csv(path):
        page = normalise_page(
            row.get("page")
            or row.get("source_page")
            or row.get("page_number")
        )

        if page not in selected_pages:
            continue

        text = clean(
            row.get("raw_line")
            or row.get("source_text")
            or row.get("field_value")
        )

        if not text:
            continue

        result[page].append(
            {
                "text": text,
                "confidence": row.get("ocr_confidence")
                or row.get("confidence")
                or "",
                "bbox": {
                    "x1": row.get("bbox_x1"),
                    "y1": row.get("bbox_y1"),
                    "x2": row.get("bbox_x2"),
                    "y2": row.get("bbox_y2"),
                },
                "table_index": row.get("table_index", ""),
                "source_kind": row.get("source_kind", "dense_ocr_line"),
                "source_file": str(path),
                "page": page,
            }
        )

    return result


def metadata_candidate_score(
    value: str,
    confidence: float,
    *,
    complete: bool,
    label_present: bool,
) -> float:
    """Prefer complete, labelled, high-confidence OCR values."""

    return (
        (10000.0 if complete else 0.0)
        + (1000.0 if label_present else 0.0)
        + (len(value) * 10.0)
        + (confidence * 100.0)
    )


def best_candidate(
    candidates: list[tuple[str, float, bool, bool]],
) -> str:
    """Return the strongest OCR candidate for one metadata field."""

    if not candidates:
        return ""

    ranked = sorted(
        candidates,
        key=lambda item: metadata_candidate_score(
            item[0],
            item[1],
            complete=item[2],
            label_present=item[3],
        ),
        reverse=True,
    )

    return ranked[0][0]


def page_profile(lines: list[dict]) -> dict[str, str]:
    """Extract the strongest shared certificate metadata per page."""

    profile = {
        "model": "",
        "equipment_description": "",
        "range_or_rating": "",
        "declaration_reference": "",
        "order_acceptance_number": "",
        "purchase_order": "",
        "test_certificate_reference": "",
    }

    models: list[tuple[str, float, bool, bool]] = []
    descriptions: list[tuple[str, float, bool, bool]] = []
    ranges: list[tuple[str, float, bool, bool]] = []
    declarations: list[tuple[str, float, bool, bool]] = []
    purchase_orders: list[tuple[str, float, bool, bool]] = []
    certificates: list[tuple[str, float, bool, bool]] = []

    for line in lines:
        line_text = clean(line.get("text"))
        line_upper = line_text.upper()
        line_confidence = parse_confidence(line.get("confidence"))

        if not line_text:
            continue

        model_match = MODEL_RE.search(line_text)
        if model_match:
            value = clean(model_match.group("model")).upper()
            models.append((
                value,
                line_confidence,
                bool(re.fullmatch(r"MAH[A-Z0-9]{3,}", value)),
                True,
            ))

        if "EX-PROOF PR. SWITCH SERIES MA" in line_upper:
            descriptions.append((
                "EX-PROOF PR. SWITCH SERIES MA",
                line_confidence,
                True,
                True,
            ))

        for match in RANGE_RE.finditer(line_text):
            value = clean(match.group("value")).upper()
            complete = bool(
                re.fullmatch(
                    r"\d+(?:\.\d+)?\s*(?:/|-|TO)\s*"
                    r"\d+(?:\.\d+)?\s*BAR",
                    value,
                    re.IGNORECASE,
                )
            )
            ranges.append((
                value,
                line_confidence,
                complete,
                "BAR" in line_upper,
            ))

        declaration_match = DECLARATION_RE.search(line_text)
        if declaration_match:
            value = clean(
                declaration_match.group("value")
            ).replace(" ", "")
            complete = bool(
                re.fullmatch(r"DC[-.]?\d{5}", value)
            )
            declarations.append((
                value,
                line_confidence,
                complete,
                True,
            ))

        purchase_match = PURCHASE_ORDER_RE.search(line_text)
        if purchase_match:
            value = clean(purchase_match.group("value"))
            complete = bool(
                re.fullmatch(
                    r"WO\d{6}/\d{2}/[A-Z]{2}",
                    value,
                    re.IGNORECASE,
                )
            )
            purchase_orders.append((
                value,
                line_confidence,
                complete,
                True,
            ))

        certificate_match = CERTIFICATE_RE.search(line_text)
        if certificate_match:
            value = clean(
                certificate_match.group("value")
            ).replace(" ", "")
            complete = bool(
                re.fullmatch(r"CA-?\d{5,}", value)
            )
            certificates.append((
                value,
                line_confidence,
                complete,
                True,
            ))

    profile["model"] = best_candidate(models)
    profile["equipment_description"] = best_candidate(descriptions)
    profile["range_or_rating"] = best_candidate(ranges)
    profile["declaration_reference"] = best_candidate(declarations)
    profile["purchase_order"] = best_candidate(purchase_orders)
    profile["test_certificate_reference"] = best_candidate(certificates)

    declaration = profile["declaration_reference"]

    if declaration:
        match = re.search(r"(\d{5})$", declaration)

        if match:
            profile["order_acceptance_number"] = match.group(1)

    return profile


def merge_best(
    record: dict[str, object],
    field: str,
    value: str,
) -> None:
    value = clean(value)

    if not value:
        return

    current = clean(record.get(field))

    if not current or len(value) > len(current):
        record[field] = value


def create_record(
    *,
    document_id: str,
    tag: str,
    serial: str,
    page: str,
    line: dict,
    sequence: int,
) -> dict[str, object]:
    return {
        "equipment_tag": tag,
        "serial_number": serial,
        "model": "",
        "equipment_description": "",
        "range_or_rating": "",
        "set_point": "",
        "declaration_reference": "",
        "order_acceptance_number": "",
        "purchase_order": "",
        "test_certificate_reference": "",
        "source_page": page,
        "_pages": {page},
        "linked_source_pages": "",
        "link_basis": "direct serial/tag line from Step 3A generic OCR",
        "field_inheritance_note": "",
        "record_status": "candidate",
        "review_status": "",
        "raw_ocr_tag": tag,
        "tag_ocr_confidence": confidence_text(line.get("confidence")),
        "source_record_id": f"DENSE-{sequence:06d}",
        "source_document_id": document_id,
        "source_table_index": clean(line.get("table_index")),
        "source_text": clean(line.get("text")),
        "source_bbox": bbox_text(line),
        "extraction_method": "step3a_detected_table_json_direct_serial_tag",
        "review_reason": "",
        "overlaps_3d_tag": "unknown",
    }


def candidate_score(
    *,
    match_method: str,
    tag: str,
    confidence: float,
    vertical_distance: float,
) -> float:
    """Rank direct row evidence above overlapping-tile artefacts."""

    direct_bonus = (
        10000.0
        if match_method == "direct_same_line"
        else 0.0
    )

    complete_tag_bonus = (
        1000.0
        if len(tag) >= 10
        else 0.0
    )

    length_bonus = len(tag) * 10.0
    confidence_bonus = confidence * 100.0
    distance_penalty = vertical_distance / 10.0

    return (
        direct_bonus
        + complete_tag_bonus
        + length_bonus
        + confidence_bonus
        - distance_penalty
    )


def collect_serial_tag_candidates(
    *,
    page: str,
    lines: list[dict],
) -> list[dict[str, object]]:
    """Collect all candidates before selecting one candidate per serial."""

    candidates: list[dict[str, object]] = []

    for line in lines:
        line_text = clean(line.get("text"))
        direct = DIRECT_SERIAL_TAG_RE.search(line_text)

        if direct:
            tag = normalise_tag(direct.group("tag"))
            serial = clean(direct.group("serial"))

            if tag and serial:
                candidates.append({
                    "page": page,
                    "serial": serial,
                    "tag": tag,
                    "line": line,
                    "match_method": "direct_same_line",
                    "vertical_distance": 0.0,
                    "score": candidate_score(
                        match_method="direct_same_line",
                        tag=tag,
                        confidence=parse_confidence(
                            line.get("confidence")
                        ),
                        vertical_distance=0.0,
                    ),
                })

            continue

        serial_only = SERIAL_ONLY_RE.search(line_text)

        if not serial_only:
            continue

        serial = clean(serial_only.group("serial"))
        x1, y1, x2, y2 = line_bbox(line)

        # Only evaluate tag fragments on the same physical certificate row.
        # The previous loose row grouping crossed into adjacent records.
        row_height = max(1.0, y2 - y1)
        y_tolerance = max(26.0, row_height * 1.15)

        nearby = []

        for neighbour in lines:
            nx1, ny1, nx2, ny2 = line_bbox(neighbour)

            if neighbour is line:
                continue

            if abs(ny1 - y1) > y_tolerance:
                continue

            # Tag is expected immediately to the right of the serial/tag
            # label cell. Avoid text from later rows or far-left headers.
            if nx1 < x2 - 30.0:
                continue

            nearby.append(neighbour)

        nearby.sort(
            key=lambda item: (
                line_bbox(item)[0],
                abs(line_bbox(item)[1] - y1),
            )
        )

        for neighbour in nearby:
            tag_match = TAG_FRAGMENT_RE.search(
                clean(neighbour.get("text"))
            )

            if not tag_match:
                continue

            tag = normalise_tag(tag_match.group("tag"))

            if not tag:
                continue

            nx1, ny1, nx2, ny2 = line_bbox(neighbour)
            vertical_distance = abs(ny1 - y1)

            candidates.append({
                "page": page,
                "serial": serial,
                "tag": tag,
                "line": line,
                "match_method": "split_same_row",
                "vertical_distance": vertical_distance,
                "score": candidate_score(
                    match_method="split_same_row",
                    tag=tag,
                    confidence=min(
                        parse_confidence(line.get("confidence")),
                        parse_confidence(
                            neighbour.get("confidence")
                        ),
                    ),
                    vertical_distance=vertical_distance,
                ),
                "tag_line": neighbour,
            })

            # The nearest valid tag fragment is the only split-row candidate
            # for this serial line.
            break

    return candidates


def select_serial_candidates(
    candidates: list[dict[str, object]],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Choose one strongest candidate for each page/serial pair."""

    by_serial: dict[tuple[str, str], list[dict[str, object]]] = {}

    for candidate in candidates:
        key = (
            str(candidate["page"]),
            str(candidate["serial"]),
        )
        by_serial.setdefault(key, []).append(candidate)

    selected: list[dict[str, object]] = []
    rejected: list[dict[str, object]] = []

    for key, group in by_serial.items():
        ranked = sorted(
            group,
            key=lambda candidate: (
                float(candidate["score"]),
                len(str(candidate["tag"])),
                parse_confidence(
                    candidate["line"].get("confidence")
                ),
            ),
            reverse=True,
        )

        winner = ranked[0]
        selected.append(winner)

        for candidate in ranked[1:]:
            rejected.append({
                "page": key[0],
                "serial_number": key[1],
                "selected_tag": winner["tag"],
                "selected_match_method": winner["match_method"],
                "selected_score": f"{winner['score']:.3f}",
                "discarded_tag": candidate["tag"],
                "discarded_match_method": candidate["match_method"],
                "discarded_score": f"{candidate['score']:.3f}",
                "discarded_source_text": clean(
                    candidate["line"].get("text")
                ),
                "reason": (
                    "Lower-ranked candidate for the same "
                    "page/serial pair."
                ),
            })

    return selected, rejected


def direct_records_from_page(
    *,
    document_id: str,
    page: str,
    lines: list[dict],
    sequence_start: int,
) -> tuple[list[dict[str, object]], list[dict[str, object]], int]:
    """Return one selected record per page/serial pair plus audit rows."""

    candidates = collect_serial_tag_candidates(
        page=page,
        lines=lines,
    )

    selected, rejected = select_serial_candidates(candidates)

    records: list[dict[str, object]] = []
    sequence = sequence_start

    for candidate in selected:
        record = create_record(
            document_id=document_id,
            tag=str(candidate["tag"]),
            serial=str(candidate["serial"]),
            page=page,
            line=candidate["line"],
            sequence=sequence,
        )

        record["extraction_method"] = (
            "step3a_detected_table_json_"
            + str(candidate["match_method"])
        )

        if candidate["match_method"] == "split_same_row":
            record["source_text"] = (
                clean(candidate["line"].get("text"))
                + " || "
                + clean(
                    candidate.get("tag_line", {}).get("text")
                )
            )

        records.append(record)
        sequence += 1

    return records, rejected, sequence


def assign_row_setpoint(
    record: dict[str, object],
    lines: list[dict],
) -> None:
    source_bbox = record.get("source_bbox", "")

    try:
        source = json.loads(source_bbox)
        source_y = float(source.get("y1", 0.0))
    except (ValueError, TypeError, json.JSONDecodeError):
        source_y = 0.0

    candidates: list[tuple[float, str]] = []

    for line in lines:
        match = SET_POINT_RE.search(clean(line.get("text")))

        if not match:
            continue

        _, line_y, _, _ = line_bbox(line)
        distance = abs(line_y - source_y)

        if distance <= 120.0:
            candidates.append(
                (distance, clean(match.group("value")).upper())
            )

    if candidates:
        candidates.sort(key=lambda item: item[0])
        merge_best(record, "set_point", candidates[0][1])


def enrich_record_from_page_profile(
    record: dict[str, object],
    profile: dict[str, str],
    lines: list[dict],
) -> None:
    for field, value in profile.items():
        merge_best(record, field, value)

    assign_row_setpoint(record, lines)

    if (
        record.get("model")
        or record.get("equipment_description")
        or record.get("purchase_order")
        or record.get("declaration_reference")
        or record.get("test_certificate_reference")
    ):
        record["field_inheritance_note"] = (
            "Shared certificate-page metadata retained with "
            "same-page provenance; review before final acceptance."
        )


def finalise(record: dict[str, object]) -> dict[str, str]:
    pages = sorted(
        int(value)
        for value in record.pop("_pages", set())
        if str(value).isdigit()
    )

    record["linked_source_pages"] = json.dumps(pages)

    missing = [
        field
        for field in (
            "model",
            "equipment_description",
            "range_or_rating",
            "set_point",
        )
        if not clean(record.get(field))
    ]

    reasons = []

    if missing:
        reasons.append("missing " + ", ".join(missing))

    if (
        clean(record.get("range_or_rating"))
        and not re.search(
            r"\d+(?:\.\d+)?\s*(?:/|-|TO)\s*\d+",
            clean(record["range_or_rating"]),
            re.IGNORECASE,
        )
    ):
        reasons.append("range lower limit not captured")

    record["review_reason"] = "; ".join(reasons)
    record["review_status"] = (
        "Candidate — complete core fields"
        if not reasons
        else "Review required: " + "; ".join(reasons)
    )
    record["record_status"] = (
        "candidate"
        if clean(record.get("equipment_tag"))
        and clean(record.get("serial_number"))
        else "needs_review"
    )

    return {
        field: clean(record.get(field))
        for field in OUTPUT_FIELDS
    }


def write_csv(
    path: Path,
    rows: list[dict[str, str]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument("--document-id", required=True)
    parser.add_argument("--dense-fields-csv", required=True, type=Path)
    parser.add_argument("--all-ocr-lines-csv", required=True, type=Path)
    parser.add_argument("--ocr-json-dir", required=True, type=Path)
    parser.add_argument("--pages-list", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)

    # Retained only because the runner historically supplies it.
    parser.add_argument("--pdf", required=False, type=Path)

    args = parser.parse_args()

    selected_pages = read_selected_pages(args.pages_list)

    if not selected_pages:
        raise SystemExit("No selected dense metadata pages found.")

    if not args.ocr_json_dir.is_dir():
        raise SystemExit(
            "Detected-table OCR JSON directory not found:\n"
            f"{args.ocr_json_dir}"
        )

    json_lines_by_page = extract_json_lines(
        args.ocr_json_dir,
        selected_pages,
    )

    dense_lines_by_page = read_dense_lines(
        args.all_ocr_lines_csv,
        selected_pages,
    )

    records: dict[tuple[str, str], dict[str, object]] = {}
    pairing_review_rows: list[dict[str, str]] = []
    sequence = 1

    for page in sorted(
        json_lines_by_page,
        key=lambda value: int(value),
    ):
        json_lines = json_lines_by_page[page]
        dense_lines = dense_lines_by_page.get(page, [])

        # JSON is authoritative for direct serial/tag geometry. Dense OCR is
        # supplemental context only.
        profile = page_profile(json_lines + dense_lines)

        page_records, page_pairing_review, sequence = (
            direct_records_from_page(
                document_id=args.document_id,
                page=page,
                lines=json_lines,
                sequence_start=sequence,
            )
        )

        pairing_review_rows.extend(page_pairing_review)

        for record in page_records:
            key = (
                normalise_tag(str(record["equipment_tag"])),
                clean(record["serial_number"]),
            )

            existing = records.get(key)

            if existing is None:
                records[key] = record
                existing = record
            else:
                existing["_pages"].update(record["_pages"])

            enrich_record_from_page_profile(
                existing,
                profile,
                json_lines,
            )

    output_rows = [
        finalise(record)
        for record in records.values()
    ]

    output_rows.sort(
        key=lambda row: (
            int(row["source_page"])
            if row["source_page"].isdigit()
            else 999999,
            row["equipment_tag"],
            row["serial_number"],
        )
    )

    candidate_register = (
        args.output_dir
        / "equipment_reconstruction_candidate_register.csv"
    )
    reconstruction_intake = (
        args.output_dir
        / "equipment_reconstruction_intake.csv"
    )
    expanded_register = (
        args.output_dir
        / "equipment_register_discovery_expanded.csv"
    )

    write_csv(candidate_register, output_rows)
    write_csv(reconstruction_intake, output_rows)
    write_csv(expanded_register, output_rows)

    pairing_review = (
        args.output_dir
        / "dense_serial_tag_pairing_review.csv"
    )

    pairing_fields = [
        "page",
        "serial_number",
        "selected_tag",
        "selected_match_method",
        "selected_score",
        "discarded_tag",
        "discarded_match_method",
        "discarded_score",
        "discarded_source_text",
        "reason",
    ]

    with pairing_review.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=pairing_fields,
        )
        writer.writeheader()
        writer.writerows(pairing_review_rows)

    print(
        "Dense field rows available:",
        len(read_csv(args.dense_fields_csv)),
    )
    print(
        "Full dense OCR lines available:",
        sum(len(lines) for lines in dense_lines_by_page.values()),
    )
    print(
        "Detected-table JSON lines read:",
        sum(len(lines) for lines in json_lines_by_page.values()),
    )
    print("Direct dense equipment candidates:", len(output_rows))
    print("Discarded same-serial alternatives:", len(pairing_review_rows))
    print(f"Candidate register: {candidate_register}")
    print(f"Pairing review: {pairing_review}")


if __name__ == "__main__":
    main()
