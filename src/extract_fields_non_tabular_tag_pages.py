#!/usr/bin/env python3
"""
Universal dense OCR + layout-block key/value extractor.

Input:
- A PDF.
- A text file containing one-based PDF page numbers, one page per line.

Output:
- A CSV of generic field evidence with page coordinates, OCR confidence, and
  layout-derived block IDs.

Important design choices:
- No document IDs, equipment tags, vendors, page ranges, or fixed schemas.
- OCR blocks are created from geometry, not from document-specific wording.
- Candidate fields are emitted only from generic label/value patterns.
- This script does not assign a field to an asset; a later association stage
  makes that decision only when an admitted tag is in the same local block.
"""

from __future__ import annotations

import argparse
import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

try:
    import fitz
except ImportError as error:
    raise SystemExit(
        "PyMuPDF is required. Install it in the project environment."
    ) from error

try:
    import pytesseract
    from pytesseract import Output
except ImportError as error:
    raise SystemExit(
        "pytesseract is required. Install it in the project environment."
    ) from error

try:
    from PIL import Image
except ImportError as error:
    raise SystemExit(
        "Pillow is required. Install it in the project environment."
    ) from error


OUTPUT_COLUMNS = [
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
]

# Labels are deliberately generic: they recognize field-like text, not a
# document-specific equipment schema. Later stages map labels to a schema.
MAX_LABEL_CHARS = 70
MAX_VALUE_CHARS = 500
MIN_WORD_CONFIDENCE = 20.0
MIN_LINE_CONFIDENCE = 35.0
BLOCK_VERTICAL_GAP = 85
BLOCK_HORIZONTAL_RESET = 220
MIN_LABEL_WORDS = 1
MAX_LABEL_WORDS = 8

COLON_SPLIT_RE = re.compile(
    r"^\s*(?P<label>[^:]{1,70}?)\s*:\s*(?P<value>.+?)\s*$"
)

DASH_SPLIT_RE = re.compile(
    r"^\s*(?P<label>[A-Za-z][A-Za-z0-9 /().#&_-]{0,68}?)"
    r"\s{2,}(?P<value>\S.+?)\s*$"
)

LABEL_LIKE_RE = re.compile(
    r"^[A-Za-z][A-Za-z0-9 /().#&_-]{0,69}$"
)

SECTION_HEADING_RE = re.compile(
    r"^[A-Z][A-Z0-9 /().#&_-]{2,80}$"
)


@dataclass(frozen=True)
class OcrLine:
    text: str
    x1: int
    y1: int
    x2: int
    y2: int
    confidence: float

    @property
    def height(self) -> int:
        return max(1, self.y2 - self.y1)


def normalise_label(value: str) -> str:
    value = str(value or "").lower().strip()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def clean_text(value: str) -> str:
    value = str(value or "").replace("\u00a0", " ").strip()
    value = re.sub(r"\s+", " ", value)
    return value


def parse_pages(path: Path) -> list[int]:
    pages: set[int] = set()

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        value = raw_line.strip()

        if not value or value.startswith("#"):
            continue

        if not value.isdigit():
            raise SystemExit(
                f"Invalid page number {value!r} in pages list: {path}"
            )

        page = int(value)

        if page < 1:
            raise SystemExit(
                f"Page numbers must be one-based positive integers: {path}"
            )

        pages.add(page)

    return sorted(pages)


def render_page(pdf: fitz.Document, page_number: int, dpi: int) -> Image.Image:
    page = pdf.load_page(page_number - 1)
    scale = dpi / 72.0
    pixmap = page.get_pixmap(
        matrix=fitz.Matrix(scale, scale),
        alpha=False,
    )
    mode = "RGB" if pixmap.n >= 3 else "L"
    return Image.frombytes(
        mode,
        [pixmap.width, pixmap.height],
        pixmap.samples,
    )


def grouped_ocr_lines(image: Image.Image) -> list[OcrLine]:
    data = pytesseract.image_to_data(
        image,
        output_type=Output.DICT,
        config="--oem 3 --psm 11",
    )

    line_words: dict[tuple[int, int, int, int], list[tuple[str, int, int, int, int, float]]] = {}

    count = len(data.get("text", []))

    for index in range(count):
        text = clean_text(data["text"][index])

        if not text:
            continue

        try:
            confidence = float(data["conf"][index])
        except (TypeError, ValueError):
            continue

        if confidence < MIN_WORD_CONFIDENCE:
            continue

        left = int(data["left"][index])
        top = int(data["top"][index])
        width = int(data["width"][index])
        height = int(data["height"][index])

        if width <= 0 or height <= 0:
            continue

        key = (
            int(data["block_num"][index]),
            int(data["par_num"][index]),
            int(data["line_num"][index]),
            int(data["page_num"][index]),
        )

        line_words.setdefault(key, []).append(
            (
                text,
                left,
                top,
                left + width,
                top + height,
                confidence,
            )
        )

    lines: list[OcrLine] = []

    for words in line_words.values():
        words.sort(key=lambda item: (item[2], item[1]))

        text = clean_text(" ".join(word[0] for word in words))
        confidence = sum(word[5] for word in words) / len(words)

        if not text or confidence < MIN_LINE_CONFIDENCE:
            continue

        lines.append(
            OcrLine(
                text=text,
                x1=min(word[1] for word in words),
                y1=min(word[2] for word in words),
                x2=max(word[3] for word in words),
                y2=max(word[4] for word in words),
                confidence=confidence,
            )
        )

    return sorted(lines, key=lambda line: (line.y1, line.x1))


def is_heading(line: OcrLine) -> bool:
    text = clean_text(line.text)

    if ":" in text:
        return False

    if len(text) > 80 or len(text) < 3:
        return False

    letters = [char for char in text if char.isalpha()]

    if not letters:
        return False

    uppercase_ratio = sum(char.isupper() for char in letters) / len(letters)

    return bool(
        SECTION_HEADING_RE.match(text)
        and uppercase_ratio >= 0.75
    )


def build_blocks(lines: list[OcrLine]) -> list[list[OcrLine]]:
    """
    Create document-agnostic local blocks from OCR geometry.

    A new block begins when a line has a large vertical separation, a large
    left-position reset after a prior paragraph, or looks like a section
    heading. This is deliberately conservative: uncertain grouping is better
    than page-wide association.
    """
    if not lines:
        return []

    blocks: list[list[OcrLine]] = []
    current: list[OcrLine] = []
    previous: OcrLine | None = None

    for line in lines:
        new_block = False

        if previous is None:
            new_block = True
        else:
            vertical_gap = line.y1 - previous.y2
            x_reset = previous.x1 - line.x1

            dynamic_gap = max(
                BLOCK_VERTICAL_GAP,
                int(max(previous.height, line.height) * 3.5),
            )

            if vertical_gap > dynamic_gap:
                new_block = True

            if x_reset > BLOCK_HORIZONTAL_RESET and vertical_gap > 10:
                new_block = True

            if is_heading(line):
                new_block = True

        if new_block:
            if current:
                blocks.append(current)
            current = [line]
        else:
            current.append(line)

        previous = line

    if current:
        blocks.append(current)

    return blocks


def split_field(line: OcrLine) -> tuple[str, str] | None:
    text = clean_text(line.text)

    if not text or len(text) > MAX_VALUE_CHARS:
        return None

    match = COLON_SPLIT_RE.match(text)

    if match:
        label = clean_text(match.group("label"))
        value = clean_text(match.group("value"))

        if valid_label(label) and valid_value(value):
            return label, value

    match = DASH_SPLIT_RE.match(text)

    if match:
        label = clean_text(match.group("label"))
        value = clean_text(match.group("value"))

        if valid_label(label) and valid_value(value):
            return label, value

    return None


def valid_label(value: str) -> bool:
    if not value or len(value) > MAX_LABEL_CHARS:
        return False

    if not LABEL_LIKE_RE.match(value):
        return False

    words = value.split()

    return MIN_LABEL_WORDS <= len(words) <= MAX_LABEL_WORDS


def valid_value(value: str) -> bool:
    if not value or len(value) > MAX_VALUE_CHARS:
        return False

    if value.endswith(":"):
        return False

    return any(char.isalnum() for char in value)


def rows_for_page(page_number: int, image: Image.Image) -> Iterable[dict[str, str]]:
    lines = grouped_ocr_lines(image)
    blocks = build_blocks(lines)

    for block_number, block_lines in enumerate(blocks, start=1):
        block_id = f"page_{page_number:03d}_block_{block_number:03d}"

        for line in block_lines:
            parsed = split_field(line)

            if parsed is None:
                continue

            label, value = parsed

            yield {
                "page": str(page_number),
                "block_id": block_id,
                "field_label": label,
                "field_label_normalized": normalise_label(label),
                "field_value": value,
                "raw_line": line.text,
                "bbox_x1": str(line.x1),
                "bbox_y1": str(line.y1),
                "bbox_x2": str(line.x2),
                "bbox_y2": str(line.y2),
                "ocr_confidence": f"{line.confidence:.6f}",
                "source_kind": "dense_page_ocr_key_value",
            }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Extract generic key/value field evidence from selected PDF pages "
            "using dense OCR and geometry-derived local blocks."
        )
    )
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--pages-list", required=True, type=Path)
    parser.add_argument("--output-csv", required=True, type=Path)
    parser.add_argument(
        "--all-ocr-lines-csv",
        type=Path,
        default=None,
        help="Optional CSV containing every OCR line, not only key/value rows.",
    )
    parser.add_argument(
        "--ocr-dpi",
        type=int,
        default=300,
        help="Render DPI for dense OCR; default: 300.",
    )
    args = parser.parse_args()

    if not args.pdf.is_file():
        raise SystemExit(f"PDF not found: {args.pdf}")

    if not args.pages_list.is_file():
        raise SystemExit(f"Pages list not found: {args.pages_list}")

    if args.ocr_dpi < 150:
        raise SystemExit("--ocr-dpi must be at least 150.")

    pages = parse_pages(args.pages_list)
    args.output_csv.parent.mkdir(parents=True, exist_ok=True)

    with fitz.open(args.pdf) as pdf:
        page_count = pdf.page_count

        invalid_pages = [page for page in pages if page > page_count]

        if invalid_pages:
            raise SystemExit(
                "Pages list contains pages beyond the PDF length "
                f"({page_count}): {invalid_pages}"
            )

        all_handle = None
        all_writer = None

        if args.all_ocr_lines_csv is not None:
            args.all_ocr_lines_csv.parent.mkdir(parents=True, exist_ok=True)
            all_handle = args.all_ocr_lines_csv.open(
                "w",
                encoding="utf-8",
                newline="",
            )
            all_fields = [
                "page",
                "raw_line",
                "bbox_x1",
                "bbox_y1",
                "bbox_x2",
                "bbox_y2",
                "ocr_confidence",
            ]
            all_writer = csv.DictWriter(all_handle, fieldnames=all_fields)
            all_writer.writeheader()

        try:
            with args.output_csv.open(
                "w",
                encoding="utf-8",
                newline="",
            ) as handle:
                writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS)
                writer.writeheader()

                field_count = 0

                for index, page_number in enumerate(pages, start=1):
                    image = render_page(pdf, page_number, args.ocr_dpi)
                    lines = grouped_ocr_lines(image)
                    rows = list(rows_for_page(page_number, image))

                    if all_writer is not None:
                        all_writer.writerows({
                            "page": str(page_number),
                            "raw_line": line.text,
                            "bbox_x1": str(line.x1),
                            "bbox_y1": str(line.y1),
                            "bbox_x2": str(line.x2),
                            "bbox_y2": str(line.y2),
                            "ocr_confidence": f"{line.confidence:.6f}",
                        } for line in lines)

                    writer.writerows(rows)
                    field_count += len(rows)

                    print(
                        f"[{index}/{len(pages)}] page {page_number}: "
                        f"{len(rows)} field-like rows"
                    )
        finally:
            if all_handle is not None:
                all_handle.close()

    print()
    print(f"Pages processed: {len(pages)}")
    print(f"Field-like rows written: {field_count}")
    print(f"Output: {args.output_csv}")


if __name__ == "__main__":
    main()
