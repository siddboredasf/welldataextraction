from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

TAG_HEADERS = (
    "tag",
    "tag no",
    "tag number",
    "equipment tag",
    "equipment no",
    "equipment number",
    "equipment id",
    "asset id",
    "asset no",
    "instrument tag",
    "instrument no",
    "loop no",
    "loop number",
)

SERIAL_HEADERS = (
    "serial",
    "serial no",
    "serial number",
    "s/n",
    "s no",
    "sno",
)

MODEL_HEADERS = (
    "model",
    "model no",
    "model number",
    "device",
    "device no",
    "type",
    "part no",
    "part number",
)

EXCLUDED_HEADERS = (
    "certificate",
    "certificate no",
    "report",
    "report no",
    "drawing",
    "drawing no",
    "revision",
    "rev",
    "date",
    "range",
    "rating",
    "accuracy",
    "pressure",
    "position",
    "pos",
    "item",
    "line",
    "sr no",
    "s r no",
)

TAG_VALUE_PATTERN = re.compile(
    r"^(?:"
    r"\d{2,8}(?:[-_/]\d{1,8})+"
    r"|"
    r"(?=.*[A-Z])(?=.*\d)[A-Z0-9]+(?:[-_/][A-Z0-9]+)*"
    r")$",
    re.IGNORECASE,
)

SERIAL_VALUE_PATTERN = re.compile(
    r"^(?=.*[A-Z])(?=.*\d)[A-Z0-9]+(?:[-_/.][A-Z0-9]+)+$",
    re.IGNORECASE,
)

MODEL_VALUE_PATTERN = re.compile(
    r"^(?=.*[A-Z])(?=.*\d)[A-Z0-9]+(?:[-_/.][A-Z0-9]+)*$",
    re.IGNORECASE,
)


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalise_header(value: str) -> str:
    value = clean_text(value).lower()
    value = re.sub(r"[^a-z0-9/ ]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def bbox_tuple(line: dict[str, Any]) -> tuple[float, float, float, float]:
    bbox = line.get("bbox") or line.get("box")

    if isinstance(bbox, dict):
        return (
            float(bbox["x1"]),
            float(bbox["y1"]),
            float(bbox["x2"]),
            float(bbox["y2"]),
        )

    if isinstance(bbox, list) and len(bbox) == 4:
        return tuple(float(value) for value in bbox)

    raise ValueError(f"Unsupported OCR bbox: {bbox!r}")


def centre(line: dict[str, Any]) -> tuple[float, float]:
    x1, y1, x2, y2 = bbox_tuple(line)
    return ((x1 + x2) / 2, (y1 + y2) / 2)


def width(line: dict[str, Any]) -> float:
    x1, _, x2, _ = bbox_tuple(line)
    return x2 - x1


def height(line: dict[str, Any]) -> float:
    _, y1, _, y2 = bbox_tuple(line)
    return y2 - y1


def line_confidence(line: dict[str, Any]) -> float:
    return float(line.get("confidence", line.get("score", 0.0)) or 0.0)


def enrich_lines(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []

    for source in lines:
        text = clean_text(source.get("text", ""))
        if not text:
            continue

        try:
            x1, y1, x2, y2 = bbox_tuple(source)
        except (KeyError, TypeError, ValueError):
            continue

        result.append(
            {
                "text": text,
                "bbox": [x1, y1, x2, y2],
                "confidence": line_confidence(source),
            }
        )

    return result


def cluster_rows(
    lines: list[dict[str, Any]],
    y_tolerance: float | None = None,
) -> list[list[dict[str, Any]]]:
    """Group OCR tokens/lines into visual rows using vertical centres."""
    if not lines:
        return []

    ordered = sorted(lines, key=lambda line: centre(line)[1])

    if y_tolerance is None:
        heights = sorted(height(line) for line in ordered)
        median_height = heights[len(heights) // 2]
        y_tolerance = max(12.0, median_height * 0.75)

    rows: list[list[dict[str, Any]]] = []

    for line in ordered:
        _, line_y = centre(line)

        if not rows:
            rows.append([line])
            continue

        previous_row = rows[-1]
        previous_y = sum(centre(item)[1] for item in previous_row) / len(
            previous_row
        )

        if abs(line_y - previous_y) <= y_tolerance:
            previous_row.append(line)
        else:
            rows.append([line])

    for row in rows:
        row.sort(key=lambda line: centre(line)[0])

    return rows


def row_text(row: list[dict[str, Any]]) -> str:
    return clean_text(" ".join(line["text"] for line in row))


def row_bbox(row: list[dict[str, Any]]) -> list[float]:
    x1 = min(bbox_tuple(line)[0] for line in row)
    y1 = min(bbox_tuple(line)[1] for line in row)
    x2 = max(bbox_tuple(line)[2] for line in row)
    y2 = max(bbox_tuple(line)[3] for line in row)
    return [x1, y1, x2, y2]


def classify_header(header_text: str) -> str | None:
    value = normalise_header(header_text)

    if not value:
        return None

    if any(alias == value or alias in value for alias in TAG_HEADERS):
        return "equipment_tag"

    if any(alias == value or alias in value for alias in SERIAL_HEADERS):
        return "serial_number"

    if any(alias == value or alias in value for alias in MODEL_HEADERS):
        return "model_or_device"

    if any(alias == value or alias in value for alias in EXCLUDED_HEADERS):
        return "excluded"

    return None


def header_candidates(
    rows: list[list[dict[str, Any]]],
) -> list[tuple[int, list[dict[str, Any]]]]:
    """Return rows with at least one recognisable equipment-field header."""
    candidates = []

    for index, row in enumerate(rows):
        recognised = sum(
            classify_header(line["text"]) in {
                "equipment_tag",
                "serial_number",
                "model_or_device",
            }
            for line in row
        )

        if recognised:
            candidates.append((index, row))

    return candidates


def make_columns(
    header_row: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    columns = []

    for line in header_row:
        semantic = classify_header(line["text"])
        if semantic is None:
            continue

        x1, y1, x2, y2 = bbox_tuple(line)
        columns.append(
            {
                "semantic": semantic,
                "header_text": line["text"],
                "header_bbox": [x1, y1, x2, y2],
                "centre_x": (x1 + x2) / 2,
                "width": max(1.0, x2 - x1),
            }
        )

    return sorted(columns, key=lambda column: column["centre_x"])


def nearest_column(
    line: dict[str, Any],
    columns: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not columns:
        return None

    line_x, _ = centre(line)
    nearest = min(columns, key=lambda column: abs(line_x - column["centre_x"]))

    allowed_distance = max(nearest["width"] * 1.8, 120.0)

    if abs(line_x - nearest["centre_x"]) > allowed_distance:
        return None

    return nearest


def plausible_value(value: str, semantic: str) -> bool:
    compact = re.sub(r"\s+", "", value).upper()

    if len(compact) < 3 or len(compact) > 48:
        return False

    if semantic == "equipment_tag":
        return bool(TAG_VALUE_PATTERN.fullmatch(compact))

    if semantic == "serial_number":
        return bool(SERIAL_VALUE_PATTERN.fullmatch(compact))

    if semantic == "model_or_device":
        return bool(MODEL_VALUE_PATTERN.fullmatch(compact))

    return False


def merge_cells_in_column(
    row: list[dict[str, Any]],
    columns: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    cells: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for line in row:
        column = nearest_column(line, columns)
        if column is None:
            continue

        if column["semantic"] == "excluded":
            continue

        cells[column["semantic"]].append(line)

    for semantic in cells:
        cells[semantic].sort(key=lambda line: centre(line)[0])

    return cells


def combine_cell_lines(lines: list[dict[str, Any]]) -> dict[str, Any]:
    text = clean_text(" ".join(line["text"] for line in lines))
    x1 = min(bbox_tuple(line)[0] for line in lines)
    y1 = min(bbox_tuple(line)[1] for line in lines)
    x2 = max(bbox_tuple(line)[2] for line in lines)
    y2 = max(bbox_tuple(line)[3] for line in lines)
    confidence = sum(line["confidence"] for line in lines) / len(lines)

    return {
        "text": text,
        "bbox": [x1, y1, x2, y2],
        "confidence": confidence,
    }


def detect_header_tables(
    lines: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Infer simple header-led tables from OCR geometry.

    A table starts at a row containing recognised field headers. It ends
    before the next header row or after a substantial vertical gap.
    """
    prepared = enrich_lines(lines)
    rows = cluster_rows(prepared)
    candidates = header_candidates(rows)
    tables = []

    for candidate_position, (header_index, header_row) in enumerate(candidates):
        columns = make_columns(header_row)
        useful_columns = [
            column
            for column in columns
            if column["semantic"]
            in {"equipment_tag", "serial_number", "model_or_device"}
        ]

        if not useful_columns:
            continue

        next_header_index = (
            candidates[candidate_position + 1][0]
            if candidate_position + 1 < len(candidates)
            else len(rows)
        )

        data_rows = rows[header_index + 1 : next_header_index]
        header_y2 = row_bbox(header_row)[3]

        filtered_rows = []
        previous_y2 = header_y2

        for row in data_rows:
            _, row_y1, _, row_y2 = row_bbox(row)

            # A large whitespace gap commonly means table/footer boundary.
            if row_y1 - previous_y2 > 180:
                break

            previous_y2 = row_y2
            filtered_rows.append(row)

        if filtered_rows:
            tables.append(
                {
                    "header_row": header_row,
                    "header_bbox": row_bbox(header_row),
                    "columns": useful_columns,
                    "data_rows": filtered_rows,
                }
            )

    return tables


def extract_table_fields(
    lines: list[dict[str, Any]],
    page: int,
    source_kind: str = "layout_table",
) -> list[dict[str, Any]]:
    """Extract equipment tag, serial, and model values from header-led tables."""
    results = []
    seen: set[tuple[str, str, tuple[float, ...]]] = set()

    for table in detect_header_tables(lines):
        table_bbox = [
            min(
                table["header_bbox"][0],
                *(row_bbox(row)[0] for row in table["data_rows"]),
            ),
            table["header_bbox"][1],
            max(
                table["header_bbox"][2],
                *(row_bbox(row)[2] for row in table["data_rows"]),
            ),
            max(row_bbox(row)[3] for row in table["data_rows"]),
        ]

        for row_index, row in enumerate(table["data_rows"], start=1):
            cells = merge_cells_in_column(row, table["columns"])

            for semantic, cell_lines in cells.items():
                cell = combine_cell_lines(cell_lines)
                value = cell["text"]

                if not plausible_value(value, semantic):
                    continue

                if semantic == "equipment_tag":
                    identifier_kind = "equipment_tag_candidate"
                elif semantic == "serial_number":
                    identifier_kind = "serial_number_candidate"
                else:
                    identifier_kind = "model_or_device_candidate"

                key = (
                    identifier_kind,
                    value.upper(),
                    tuple(round(item, 1) for item in cell["bbox"]),
                )
                if key in seen:
                    continue
                seen.add(key)

                matching_column = next(
                    column
                    for column in table["columns"]
                    if column["semantic"] == semantic
                )

                results.append(
                    {
                        "raw_value": value,
                        "normalized_value": value.upper(),
                        "identifier_kind": identifier_kind,
                        "confidence": round(
                            min(1.0, 0.78 + cell["confidence"] * 0.18),
                            2,
                        ),
                        "status": "candidate",
                        "reason": "layout_table_header",
                        "evidence": {
                            "page": page,
                            "raw_value": value,
                            "bbox": [
                                round(item, 2) for item in cell["bbox"]
                            ],
                            "ocr_confidence": round(
                                cell["confidence"],
                                4,
                            ),
                            "source_kind": source_kind,
                            "table_bbox": [
                                round(item, 2) for item in table_bbox
                            ],
                            "header_text": matching_column["header_text"],
                            "header_bbox": [
                                round(item, 2)
                                for item in matching_column["header_bbox"]
                            ],
                            "row_index": row_index,
                        },
                    }
                )

    return results
