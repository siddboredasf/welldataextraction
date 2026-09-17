#!/usr/bin/env python3
"""High-recall, rotation-aware page selector for vendor manuals.

The selector deliberately has no document-specific page allowlists. Every page
is evaluated from its OCR evidence. It is a queueing filter for likely
equipment/instrument tag pages. A document number, serial number, or a
tag-shaped OCR fragment is not sufficient by itself to select a page.
"""

import argparse
import csv
import json
import re
from pathlib import Path

import pymupdf
import pytesseract
from PIL import Image, ImageOps


DEFAULT_SCAN_DPI = 160
ROTATIONS = (0, 90, 180, 270)
TESSERACT_CONFIGS = (
    "--oem 3 --psm 3",
    "--oem 3 --psm 6",
    "--oem 3 --psm 11",
    "--oem 3 --psm 12",
)


ISA_FUNCTIONS = (
    "XV|HV|MOV|SDV|ESDV|BDV|PSV|PCV|FCV|LCV|TCV|"
    "PT|PI|PIC|PIT|PC|PS|PSH|PSHH|PSL|PSLL|"
    "TT|TI|TIC|TIT|TC|TS|TSH|TSHH|TSL|TSLL|"
    "FT|FI|FIC|FIT|FC|FS|FSH|FSHH|FSL|FSLL|"
    "LT|LI|LIC|LIT|LC|LS|LSH|LSHH|LSL|LSLL|"
    "AT|AI|AIC|AIT|AC|AS|ZT|ZI|ZIC|ZIT|ZC|ZS|"
    "YT|YI|YIC|YIT|YC|YS|TE|FE|PE|LE|AE|VE|WE"
)


ISA_TAG_RE = re.compile(
    rf"(?<![A-Z0-9])(?:{ISA_FUNCTIONS})\s*[-–—]?\s*\d{{1,8}}(?:[A-Z]{{1,4}})?(?![A-Z0-9])",
    re.IGNORECASE,
)


COMPACT_INSTRUMENT_TAG_RE = re.compile(
    rf"(?<![A-Z0-9])\d{{3,8}}\s*(?:{ISA_FUNCTIONS})\s*\d{{1,8}}(?:[A-Z]{{1,4}})?(?![A-Z0-9])",
    re.IGNORECASE,
)


BROAD_COMPACT_CANDIDATE_RE = re.compile(
    r"(?<![A-Z0-9])\d{3,8}\s*[A-Z]{2,8}\s*\d{2,8}(?:[A-Z]{1,4})?(?![A-Z0-9])",
    re.IGNORECASE,
)


SERIAL_OR_REFERENCE_RE = re.compile(
    r"(?<!\d)\d{3,8}\s*[-–—/]\s*\d{1,8}(?!\d)"
)


TAG_FIELD_RE = re.compile(
    r"\b(?:tag|instrument\s+tag|equipment\s+tag|loop\s+tag|item\s+tag|tag\s+no)\b",
    re.IGNORECASE,
)


SERIAL_FIELD_RE = re.compile(
    r"\b(?:serial|serial\s+no|serial\s+number|s/n|sno)\b",
    re.IGNORECASE,
)


CERTIFICATE_RE = re.compile(
    r"\b(?:final\s+test\s+certificate|declaration\s+of\s+conformity|"
    r"calibration\s+certificate|test\s+certificate|inspection\s+certificate|"
    r"certificate\s+of\s+conformity|certificate|declaration)\b",
    re.IGNORECASE,
)


EQUIPMENT_CONTEXT_RE = re.compile(
    r"\b(?:equipment|instrument(?:s)?|valve|actuator|transmitter|controller|"
    r"switch|gauge|detector|sensor|meter|analyser|analyzer|solenoid|"
    r"control\s+valve|pressure|temperature|flow|level|process\s+connection|"
    r"set\s*point|proof\s+pressure|range|manufacturer|model|type|serial)\b",
    re.IGNORECASE,
)


TABLE_CONTEXT_RE = re.compile(
    r"\b(?:table|equipment\s+list|instrument\s+data|data\s+sheet|"
    r"document\s+register|document\s+number|drawing|spare\s+parts?|"
    r"part\s+number|quantity|description|model|range|set\s*point|"
    r"terminal|wiring|schematic|pneumatic|hydraulic|packing|storage)\b",
    re.IGNORECASE,
)


def derive_document_id(pdf_path: Path) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", pdf_path.stem).strip("_")
    return value or "vendor_manual"


def normalize_text(text: str) -> str:
    text = (text or "").upper()
    text = text.replace("–", "-").replace("—", "-")
    text = text.replace("|", " ").replace("_", " ")
    return re.sub(r"\s+", " ", text).strip()


def normalise_match(value: str) -> str:
    return re.sub(r"\s+", "", value).replace("–", "-").replace("—", "-")


def render_thumbnail(
    page: pymupdf.Page,
    page_number: int,
    directory: Path,
    dpi: int,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    output_path = directory / f"page_{page_number:03d}.png"

    scale = dpi / 72.0
    pixmap = page.get_pixmap(
        matrix=pymupdf.Matrix(scale, scale),
        alpha=False,
    )
    pixmap.save(str(output_path))
    return output_path


def prepare_image(image: Image.Image) -> Image.Image:
    return ImageOps.autocontrast(ImageOps.grayscale(image))


def rotate_image(image: Image.Image, rotation: int) -> Image.Image:
    if rotation == 0:
        return image

    return image.rotate(rotation, expand=True)


def ocr_image(image: Image.Image) -> str:
    outputs = []

    for config in TESSERACT_CONFIGS:
        text = pytesseract.image_to_string(
            image,
            lang="eng",
            config=config,
        )

        if text.strip():
            outputs.append(text)

    return "\n".join(outputs)


def nearby_context(
    text: str,
    start: int,
    end: int,
    pattern: re.Pattern,
    window: int = 220,
) -> bool:
    lower = max(0, start - window)
    upper = min(len(text), end + window)
    return bool(pattern.search(text[lower:upper]))


def unique_append(values: list[str], value: str) -> bool:
    if value in values:
        return False

    values.append(value)
    return True


def analyse_rotation(text: str, rotation: int) -> dict:
    normalized = normalize_text(text)

    result = {
        "rotation": rotation,
        "text": normalized,
        "equipment_score": 0,
        "review_score": 0,
        "serial_score": 0,
        "tags": [],
        "serials": [],
        "reasons": [],
    }

    has_tag_field = bool(TAG_FIELD_RE.search(normalized))
    has_serial_field = bool(SERIAL_FIELD_RE.search(normalized))
    has_certificate = bool(CERTIFICATE_RE.search(normalized))
    has_equipment_context = bool(EQUIPMENT_CONTEXT_RE.search(normalized))
    has_table_context = bool(TABLE_CONTEXT_RE.search(normalized))

    table_matches = TABLE_CONTEXT_RE.findall(normalized)
    table_context_count = len({item.upper() for item in table_matches})

    for pattern_name, pattern in (
        ("compact_instrument_tag", COMPACT_INSTRUMENT_TAG_RE),
        ("isa_instrument_tag", ISA_TAG_RE),
    ):
        for match in pattern.finditer(normalized):
            raw_tag = match.group(0)
            tag = normalise_match(raw_tag)

            near_tag_field = nearby_context(
                normalized,
                match.start(),
                match.end(),
                TAG_FIELD_RE,
            )

            near_equipment_context = nearby_context(
                normalized,
                match.start(),
                match.end(),
                EQUIPMENT_CONTEXT_RE,
            )

            separated = bool(re.search(r"[-–—]", raw_tag))
            long_identifier = len(tag) >= 7
            compact = pattern_name == "compact_instrument_tag"
            is_short_tag = len(tag) <= 5

            # Examples of likely false positives from OCR are ZS-1, FS5,
            # AS4, AI5, and YT7. Short patterns must have strong nearby
            # tag/equipment evidence. Longer tags may be accepted where
            # they are hyphenated, compact, or supported by local context.
            accepted = (
                compact
                or (
                    not is_short_tag
                    and (
                        separated
                        or long_identifier
                        or near_tag_field
                        or near_equipment_context
                    )
                )
                or (
                    is_short_tag
                    and (
                        near_tag_field
                        or near_equipment_context
                    )
                )
            )

            if not accepted:
                result["reasons"].append(
                    f"short_tag_rejected_without_context:{tag}"
                )
                continue

            if unique_append(result["tags"], tag):
                result["equipment_score"] += 5
                result["reasons"].append(
                    f"{pattern_name}:{tag}"
                )

            if near_tag_field:
                result["equipment_score"] += 5
                result["reasons"].append(
                    f"tag_field_near:{tag}"
                )

            if near_equipment_context:
                result["equipment_score"] += 2
                result["reasons"].append(
                    f"equipment_context_near:{tag}"
                )

    for match in BROAD_COMPACT_CANDIDATE_RE.finditer(normalized):
        candidate = normalise_match(match.group(0))

        if (
            candidate not in result["tags"]
            and nearby_context(
                normalized,
                match.start(),
                match.end(),
                TAG_FIELD_RE,
            )
        ):
            result["tags"].append(candidate)
            result["equipment_score"] += 3
            result["reasons"].append(
                f"broad_candidate_in_tag_context:{candidate}"
            )

    for match in SERIAL_OR_REFERENCE_RE.finditer(normalized):
        serial = normalise_match(match.group(0))

        if unique_append(result["serials"], serial):
            result["serial_score"] += 1
            result["reasons"].append(
                f"serial_or_reference:{serial}"
            )

    if has_tag_field:
        result["review_score"] += 3
        result["reasons"].append("tag_field_present")

    if has_serial_field:
        result["review_score"] += 2
        result["reasons"].append("serial_field_present")

    if has_serial_field and has_tag_field:
        result["review_score"] += 3
        result["reasons"].append(
            "serial_and_tag_fields_present"
        )

    if has_certificate:
        result["review_score"] += 3
        result["reasons"].append(
            "certificate_or_declaration_present"
        )

    if has_equipment_context:
        result["review_score"] += 1
        result["reasons"].append(
            "equipment_context_present"
        )

    if has_table_context:
        result["review_score"] += 1
        result["reasons"].append(
            "table_or_document_context_present"
        )

    if table_context_count >= 3:
        result["review_score"] += 2
        result["reasons"].append(
            f"table_context_terms:{table_context_count}"
        )

    result["tags"].sort()
    result["serials"].sort()

    return result


def merge_rotation_results(
    page_number: int,
    thumbnail_path: Path,
    rotations: list[dict],
) -> dict:
    tags = sorted({
        tag
        for item in rotations
        for tag in item["tags"]
    })

    serials = sorted({
        serial
        for item in rotations
        for serial in item["serials"]
    })

    equipment_score = max(
        (item["equipment_score"] for item in rotations),
        default=0,
    )

    review_score = max(
        (item["review_score"] for item in rotations),
        default=0,
    )

    serial_score = max(
        (item["serial_score"] for item in rotations),
        default=0,
    )

    reasons = []

    for item in rotations:
        for reason in item["reasons"]:
            labelled = f"{item['rotation']}deg:{reason}"

            if labelled not in reasons:
                reasons.append(labelled)

    # A strong orientation must contain a tag with nearby evidence.
    # A regex match on its own is not enough.
    strong_rotations = [
        item["rotation"]
        for item in rotations
        if item["tags"]
        and (
            any(
                "tag_field_near:" in reason
                for reason in item["reasons"]
            )
            or any(
                "equipment_context_near:" in reason
                for reason in item["reasons"]
            )
        )
    ]

    review_rotations = [
        item["rotation"]
        for item in rotations
        if item["review_score"] >= 5
    ]

    has_tag_field = any(
        "tag_field_present" in reason
        or "tag_field_near:" in reason
        for reason in reasons
    )

    has_certificate = any(
        "certificate_or_declaration_present" in reason
        for reason in reasons
    )

    has_table_context = any(
        "table_or_document_context_present" in reason
        or "table_context_terms:" in reason
        for reason in reasons
    )

    has_equipment_context = any(
        "equipment_context_present" in reason
        or "equipment_context_near:" in reason
        for reason in reasons
    )

    has_serials = bool(serials)

    # Strict selection:
    # - A page must contain a detected tag with nearby tag/equipment context,
    #   OR a Tag field plus clear equipment/certificate context.
    # - Serial numbers, drawing numbers, document numbers, contents pages,
    #   title pages, and broad table context alone cannot select a page.
    credible_tag_rotations = [
        item
        for item in rotations
        if item["tags"]
        and (
            any(
                "tag_field_near:" in reason
                for reason in item["reasons"]
            )
            or any(
                "equipment_context_near:" in reason
                for reason in item["reasons"]
            )
        )
    ]

    strong_context_rotations = [
        item
        for item in rotations
        if any(
            "tag_field_present" in reason
            for reason in item["reasons"]
        )
        and any(
            "equipment_context_present" in reason
            or "certificate_or_declaration_present" in reason
            for reason in item["reasons"]
        )
    ]

    if credible_tag_rotations:
        selected = True
        decision = "tag_detected_with_context"

    elif (
        strong_context_rotations
        and has_tag_field
        and has_equipment_context
    ):
        selected = True
        decision = "tag_field_with_equipment_context"

    else:
        selected = False

        if tags:
            decision = "tag_like_text_without_supporting_context"

        elif has_serials:
            decision = "serial_or_document_reference_only"

        elif has_table_context:
            decision = "document_or_table_context_only"

        else:
            decision = "no_tag_evidence"

    return {
        "page": page_number,
        "selected": selected,
        "decision": decision,
        "equipment_score": equipment_score,
        "review_score": review_score,
        "serial_score": serial_score,
        "tags": tags,
        "serials": serials,
        "strong_rotations": strong_rotations,
        "review_rotations": review_rotations,
        "reasons": reasons,
        "thumbnail": str(thumbnail_path),
        "token_count_by_rotation": {
            str(item["rotation"]): len(item["text"].split())
            for item in rotations
        },
    }


def write_reports(
    records: list[dict],
    output_dir: Path,
    pdf_path: Path,
    scan_dpi: int,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    selected = [
        record
        for record in records
        if record["selected"]
    ]

    strong = [
        record
        for record in selected
        if record["decision"] == "tag_detected_with_context"
    ]

    review = [
        record
        for record in selected
        if record["decision"] != "tag_detected_with_context"
    ]

    report = {
        "document_id": derive_document_id(pdf_path),
        "source_pdf": str(pdf_path),
        "scan_dpi": scan_dpi,
        "rotations_checked": list(ROTATIONS),
        "tesseract_configs": list(TESSERACT_CONFIGS),
        "selection_mode": "strict_evidence_based_tag_queue",
        "hard_coded_page_allowlist": False,
        "total_pages": len(records),
        "selected_pages": len(selected),
        "strong_tag_pages": len(strong),
        "context_review_pages": len(review),
        "records": records,
    }

    report_path = output_dir / "tag_selection_report.json"
    report_path.write_text(
        json.dumps(report, indent=2),
        encoding="utf-8",
    )

    columns = [
        "page",
        "selected",
        "decision",
        "equipment_score",
        "review_score",
        "serial_score",
        "tags",
        "serials",
        "strong_rotations",
        "review_rotations",
        "reasons",
        "thumbnail",
    ]

    csv_path = output_dir / "tag_selection_report.csv"

    with csv_path.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=columns,
        )

        writer.writeheader()

        for record in records:
            writer.writerow({
                "page": record["page"],
                "selected": record["selected"],
                "decision": record["decision"],
                "equipment_score": record["equipment_score"],
                "review_score": record["review_score"],
                "serial_score": record["serial_score"],
                "tags": " | ".join(record["tags"]),
                "serials": " | ".join(record["serials"]),
                "strong_rotations": " | ".join(
                    map(str, record["strong_rotations"])
                ),
                "review_rotations": " | ".join(
                    map(str, record["review_rotations"])
                ),
                "reasons": " | ".join(record["reasons"]),
                "thumbnail": record["thumbnail"],
            })

    outputs = (
        ("selected_pages.txt", selected),
        ("strong_tag_pages.txt", strong),
        ("review_candidate_pages.txt", review),
    )

    for filename, records_to_write in outputs:
        file_path = output_dir / filename

        with file_path.open("w", encoding="utf-8") as handle:
            for record in records_to_write:
                handle.write(f"{record['page']:03d}\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Strict, evidence-based, rotation-aware "
            "vendor-manual tag page selector."
        )
    )

    parser.add_argument(
        "--pdf",
        type=Path,
        required=True,
        help="Source PDF.",
    )

    parser.add_argument(
        "--thumbnail-dir",
        type=Path,
        help="Thumbnail directory.",
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Selection-report directory.",
    )

    parser.add_argument(
        "--scan-dpi",
        type=int,
        default=DEFAULT_SCAN_DPI,
        help=f"Thumbnail DPI; default: {DEFAULT_SCAN_DPI}.",
    )

    parser.add_argument(
        "--force-render",
        action="store_true",
        help="Always regenerate thumbnails.",
    )

    args = parser.parse_args()

    if not args.pdf.is_file():
        raise SystemExit(f"PDF not found: {args.pdf}")

    if args.scan_dpi < 72:
        raise SystemExit("--scan-dpi must be at least 72.")

    document_id = derive_document_id(args.pdf)

    thumbnail_dir = (
        args.thumbnail_dir
        or Path("outputs")
        / document_id
        / f"thumbnails_{args.scan_dpi}dpi"
    )

    output_dir = (
        args.output_dir
        or Path("outputs")
        / document_id
        / "tag_page_selection"
    )

    document = pymupdf.open(args.pdf)
    records = []

    for page_index, page in enumerate(document):
        page_number = page_index + 1

        thumbnail_path = (
            thumbnail_dir
            / f"page_{page_number:03d}.png"
        )

        if args.force_render or not thumbnail_path.exists():
            thumbnail_path = render_thumbnail(
                page,
                page_number,
                thumbnail_dir,
                args.scan_dpi,
            )

        with Image.open(thumbnail_path) as source:
            original = prepare_image(source)
            rotation_results = []

            for rotation in ROTATIONS:
                rotated = rotate_image(original, rotation)
                text = ocr_image(rotated)

                rotation_results.append(
                    analyse_rotation(text, rotation)
                )

        record = merge_rotation_results(
            page_number,
            thumbnail_path,
            rotation_results,
        )

        records.append(record)

        print(
            f"page {page_number:03d} | "
            f"selected={record['selected']} | "
            f"{record['decision']} | "
            f"rotations="
            f"{record['strong_rotations'] or record['review_rotations'] or '-'} | "
            f"tags={', '.join(record['tags']) or '-'}"
        )

    document.close()

    write_reports(
        records,
        output_dir,
        args.pdf,
        args.scan_dpi,
    )

    print("\nCompleted strict evidence-based tag-page selection")
    print(f"Document: {args.pdf}")
    print(f"Pages processed: {len(records)}")
    print(
        "Pages selected for high-DPI OCR: "
        f"{sum(record['selected'] for record in records)}"
    )
    print(f"Rotations checked: {', '.join(map(str, ROTATIONS))}")
    print(f"Output directory: {output_dir}")


if __name__ == "__main__":
    main()