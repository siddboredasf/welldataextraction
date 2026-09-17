#!/usr/bin/env python3
"""
Reconstruct logical calibration rows from coordinate-aware table OCR.

This detector:
- identifies tables with rising/falling directional measurement headers;
- derives rising and falling x-bands from those headers;
- groups nearby OCR fragments into logical horizontal rows;
- emits one calibration-row candidate per recovered row identifier;
- exports directional numeric observations by column geometry;
- validates expected row counts and numeric suffix coverage when requested.

It does not hard-code a document ID, page range, vendor, tag prefix, unit,
range, or expected count. Expected count is supplied at runtime only when the
source document is known to contain a fixed number of calibration rows.
"""

from __future__ import annotations

import argparse
import csv
import re
from collections import Counter, defaultdict
from pathlib import Path


ROW_COLUMNS = [
    "calibration_row_id",
    "document_id",
    "page",
    "table_id",
    "source_row_indices",
    "position_candidate",
    "series_identifier",
    "range_min",
    "range_max",
    "unit_candidate",
    "header_evidence",
    "row_confidence",
    "row_status",
    "row_bbox",
    "source_kind",
]

OBSERVATION_COLUMNS = [
    "observation_id",
    "calibration_row_id",
    "document_id",
    "page",
    "table_id",
    "source_row_indices",
    "series_identifier",
    "position_candidate",
    "direction",
    "value",
    "unit_candidate",
    "ocr_confidence",
    "bbox",
    "column_band",
    "source_kind",
    "observation_status",
]

NON_DIRECTIONAL_COLUMNS = [
    "evidence_id",
    "calibration_row_id",
    "document_id",
    "page",
    "table_id",
    "source_row_indices",
    "series_identifier",
    "position_candidate",
    "value",
    "bbox",
    "ocr_confidence",
    "x_centre",
    "classification",
    "source_kind",
]

VALIDATION_COLUMNS = [
    "validation_type",
    "expected_value",
    "actual_value",
    "status",
    "details",
]


DIRECTION_TERMS = {
    "rising": "rising",
    "falling": "falling",
    "increasing": "increasing",
    "decreasing": "decreasing",
    "ascending": "increasing",
    "descending": "decreasing",
}

MEASUREMENT_TERMS = {
    "pressure",
    "reading",
    "measurement",
    "measured",
    "observed",
    "actual",
    "indicated",
    "value",
    "test",
    "calibration",
    "deviation",
    "error",
}

UNITS = {
    "bar",
    "mbar",
    "kpa",
    "mpa",
    "psi",
    "pa",
    "v",
    "mv",
    "kv",
    "a",
    "ma",
    "hz",
    "rpm",
    "°c",
    "°f",
}

NUMBER_RE = re.compile(r"[-+]?\d+(?:[.,]\d+)?")
PURE_NUMBER_RE = re.compile(r"^[-+]?\d+(?:[.,]\d+)?$")
RANGE_RE = re.compile(
    r"(?<!\d)"
    r"([-+]?\d+(?:[.,]\d+)?)"
    r"\s*(?:to|-|–|—)\s*"
    r"([-+]?\d+(?:[.,]\d+)?)"
    r"\s*([a-zA-Z°/%]+)?",
    flags=re.IGNORECASE,
)

TAG_CANDIDATE_RE = re.compile(
    r"\b([A-Z0-9]+(?:[-_/][A-Z0-9]+)+)\b",
    flags=re.IGNORECASE,
)


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def normalized(value: str) -> str:
    return clean(value).lower()


def safe_float(value: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def safe_int(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def parse_number(value: str) -> float | None:
    match = NUMBER_RE.fullmatch(clean(value).replace(",", "."))

    if not match:
        return None

    try:
        return float(match.group(0))
    except ValueError:
        return None


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(
    path: Path,
    fieldnames: list[str],
    rows: list[dict[str, str]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fieldnames,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)


def x_centre(cell: dict[str, str]) -> float:
    return (
        safe_float(cell.get("x1", ""))
        + safe_float(cell.get("x2", ""))
    ) / 2


def y_centre(cell: dict[str, str]) -> float:
    return (
        safe_float(cell.get("y1", ""))
        + safe_float(cell.get("y2", ""))
    ) / 2


def cell_width(cell: dict[str, str]) -> float:
    return max(
        1.0,
        safe_float(cell.get("x2", ""))
        - safe_float(cell.get("x1", "")),
    )


def cell_height(cell: dict[str, str]) -> float:
    return max(
        1.0,
        safe_float(cell.get("y2", ""))
        - safe_float(cell.get("y1", "")),
    )


def confidence(cell: dict[str, str]) -> float:
    return safe_float(cell.get("ocr_confidence", ""))


def direction_from_text(text: str) -> str:
    content = normalized(text)

    for term, direction in DIRECTION_TERMS.items():
        if re.search(rf"\b{re.escape(term)}\b", content):
            return direction

    return ""


def measurement_terms(text: str) -> list[str]:
    content = normalized(text)

    return sorted(
        term
        for term in MEASUREMENT_TERMS
        if re.search(rf"\b{re.escape(term)}\b", content)
    )


def infer_unit(text: str) -> str:
    content = normalized(text)

    for unit in sorted(UNITS, key=len, reverse=True):
        if re.search(
            rf"(?<![a-zA-Z]){re.escape(unit)}(?![a-zA-Z])",
            content,
        ):
            return unit

    return ""


def infer_range(text: str) -> tuple[str, str, str]:
    for match in RANGE_RE.finditer(text):
        low_raw, high_raw, unit = match.groups()

        try:
            low = float(low_raw.replace(",", "."))
            high = float(high_raw.replace(",", "."))
        except ValueError:
            continue

        if high <= low:
            continue

        return f"{low:g}", f"{high:g}", clean(unit or "")

    return "", "", ""


def group_by_table(
    cells: list[dict[str, str]],
) -> dict[tuple[str, str], list[dict[str, str]]]:
    tables: dict[
        tuple[str, str],
        list[dict[str, str]],
    ] = defaultdict(list)

    for cell in cells:
        page = clean(cell.get("page", ""))
        table_id = clean(cell.get("table_id", ""))

        if page and table_id:
            tables[(page, table_id)].append(cell)

    return tables


def deduplicate_cells(
    cells: list[dict[str, str]],
) -> list[dict[str, str]]:
    """
    Suppress crop/tile OCR duplicates while retaining higher-confidence text.
    """
    output: list[dict[str, str]] = []

    for cell in sorted(
        cells,
        key=lambda item: (
            -confidence(item),
            y_centre(item),
            x_centre(item),
        ),
    ):
        text = normalized(cell.get("text", ""))

        if not text:
            continue

        duplicate = False

        for existing in output:
            if normalized(existing.get("text", "")) != text:
                continue

            if (
                abs(x_centre(cell) - x_centre(existing)) <= 110
                and abs(y_centre(cell) - y_centre(existing)) <= 35
            ):
                duplicate = True
                break

        if not duplicate:
            output.append(cell)

    return sorted(output, key=lambda item: (y_centre(item), x_centre(item)))


def header_context(
    cells: list[dict[str, str]],
) -> dict[str, object] | None:
    """
    Detect directional table context from the upper visible table region.
    """
    if not cells:
        return None

    top_y = min(y_centre(cell) for cell in cells)
    candidate_headers = [
        cell
        for cell in cells
        if y_centre(cell) <= top_y + 520
    ]

    header_text = " ".join(
        clean(cell.get("text", ""))
        for cell in candidate_headers
    )

    directions = sorted({
        direction_from_text(cell.get("text", ""))
        for cell in candidate_headers
        if direction_from_text(cell.get("text", ""))
    })

    terms = measurement_terms(header_text)

    if len(directions) < 2 or not terms:
        return None

    range_min, range_max, range_unit = infer_range(header_text)
    unit = infer_unit(header_text) or range_unit

    evidence = [
        f"directions={','.join(directions)}",
        f"measurement_terms={','.join(terms)}",
    ]

    if range_min and range_max:
        evidence.append(f"range={range_min} to {range_max}")

    if unit:
        evidence.append(f"unit={unit}")

    return {
        "header_cells": candidate_headers,
        "directions": directions,
        "range_min": range_min,
        "range_max": range_max,
        "unit": unit,
        "evidence": "; ".join(evidence),
    }


def directional_units(
    header_cells: list[dict[str, str]],
    fallback_unit: str,
) -> dict[str, str]:
    """
    Resolve each directional unit from its own header fragment.

    This avoids assigning a secondary unit from general range text such as
    '0 to 160 Bar / Psi' when the actual directional column header says
    'Rising Pressure (Bar)' or 'Falling Pressure (Bar)'.
    """
    found: dict[str, list[str]] = defaultdict(list)

    for cell in header_cells:
        direction = direction_from_text(cell.get("text", ""))

        if not direction:
            continue

        unit = infer_unit(cell.get("text", ""))

        if unit:
            found[direction].append(unit)

    output = {}

    for direction in DIRECTION_TERMS.values():
        values = found.get(direction, [])

        if values:
            output[direction] = Counter(values).most_common(1)[0][0]
        else:
            output[direction] = fallback_unit

    return output


def directional_bands(
    header_cells: list[dict[str, str]],
) -> dict[str, tuple[float, float]]:
    """
    Derive one bounding x-band per direction from all matching header OCR
    fragments. A controlled margin accommodates header text that does not
    cover every measurement sub-column.
    """
    by_direction: dict[str, list[tuple[float, float]]] = defaultdict(list)

    for cell in header_cells:
        direction = direction_from_text(cell.get("text", ""))

        if not direction:
            continue

        if not measurement_terms(cell.get("text", "")):
            continue

        start = safe_float(cell.get("x1", ""))
        end = safe_float(cell.get("x2", ""))

        if end <= start:
            continue

        margin = max(70.0, (end - start) * 0.30)

        by_direction[direction].append((
            max(0.0, start - margin),
            end + margin,
        ))

    output: dict[str, tuple[float, float]] = {}

    for direction, intervals in by_direction.items():
        output[direction] = (
            min(start for start, _ in intervals),
            max(end for _, end in intervals),
        )

    return output


def is_in_band(
    value: float,
    band: tuple[float, float],
) -> bool:
    return band[0] <= value <= band[1]


def logical_row_groups(
    body_cells: list[dict[str, str]],
) -> list[list[dict[str, str]]]:
    """
    Build logical rows primarily from the exporter-assigned row_index.

    The prior version grouped all cells only by y-centre, which could merge
    adjacent physical table rows when overlapping OCR tiles were vertically
    offset. row_index is the primary anchor; only rows with the same index are
    combined here.

    Any fragment that belongs to a neighboring visual line remains separate,
    which is safer than contaminating one calibration record with another.
    """
    by_row_index: dict[int, list[dict[str, str]]] = defaultdict(list)

    for cell in body_cells:
        index = safe_int(cell.get("row_index", ""))

        if index <= 0:
            continue

        by_row_index[index].append(cell)

    groups = []

    for index in sorted(by_row_index):
        group = by_row_index[index]

        group.sort(key=lambda item: (x_centre(item), y_centre(item)))
        groups.append(group)

    return groups

def row_text(cells: list[dict[str, str]]) -> str:
    return " ".join(
        clean(cell.get("text", ""))
        for cell in sorted(cells, key=x_centre)
    )


def row_bbox(cells: list[dict[str, str]]) -> str:
    if not cells:
        return "[]"

    return (
        "["
        f"{min(safe_float(cell.get('x1', '')) for cell in cells):.2f}, "
        f"{min(safe_float(cell.get('y1', '')) for cell in cells):.2f}, "
        f"{max(safe_float(cell.get('x2', '')) for cell in cells):.2f}, "
        f"{max(safe_float(cell.get('y2', '')) for cell in cells):.2f}"
        "]"
    )


def cell_bbox(cell: dict[str, str]) -> str:
    return (
        "["
        f"{safe_float(cell.get('x1', '')):.2f}, "
        f"{safe_float(cell.get('y1', '')):.2f}, "
        f"{safe_float(cell.get('x2', '')):.2f}, "
        f"{safe_float(cell.get('y2', '')):.2f}"
        "]"
    )


def source_row_indices(cells: list[dict[str, str]]) -> str:
    values = sorted({
        safe_int(cell.get("row_index", ""))
        for cell in cells
        if safe_int(cell.get("row_index", "")) > 0
    })

    return ",".join(str(value) for value in values)


def tag_identifier(cells: list[dict[str, str]]) -> str:
    """
    Find tag-like text while rejecting decimal numeric measurement values.

    Examples accepted:
      3656-17
      PT-101
      ABC/123
    Examples rejected:
      20.5
      40.0
      0
    """
    for cell in sorted(cells, key=x_centre):
        text = clean(cell.get("text", ""))

        for match in TAG_CANDIDATE_RE.finditer(text):
            candidate = clean(match.group(1))

            if PURE_NUMBER_RE.fullmatch(candidate):
                continue

            if re.fullmatch(r"\d+\.\d+", candidate):
                continue

            return candidate

    return ""


def position_candidate(
    cells: list[dict[str, str]],
    left_boundary: float,
) -> str:
    """
    Position values are only accepted from the left-side non-directional zone.
    """
    candidates = []

    for cell in cells:
        if x_centre(cell) >= left_boundary:
            continue

        value = parse_number(cell.get("text", ""))

        if value is None:
            continue

        if value.is_integer() and 0 < value <= 10000:
            candidates.append((x_centre(cell), int(value)))

    if not candidates:
        return ""

    candidates.sort()

    return str(candidates[0][1])


def deduplicate_directional_cells(
    cells: list[dict[str, str]],
) -> list[dict[str, str]]:
    """
    Keep one OCR numeric cell per approximate physical position.

    Duplicate tile/crop OCR often produces the same value close in x but at
    slightly different y positions. Since this function is called inside one
    logical table row and one direction band, x proximity is the main signal.
    """
    selected: list[dict[str, str]] = []

    for cell in sorted(
        cells,
        key=lambda item: (
            x_centre(item),
            -confidence(item),
            y_centre(item),
        ),
    ):
        value = parse_number(cell.get("text", ""))

        if value is None:
            continue

        duplicate_index = None

        for index, existing in enumerate(selected):
            existing_value = parse_number(existing.get("text", ""))

            if existing_value is None:
                continue

            same_value = abs(value - existing_value) <= 0.001
            close_x = abs(x_centre(cell) - x_centre(existing)) <= 90
            close_y = abs(y_centre(cell) - y_centre(existing)) <= 75

            if same_value and close_x and close_y:
                duplicate_index = index
                break

        if duplicate_index is None:
            selected.append(cell)
        elif confidence(cell) > confidence(selected[duplicate_index]):
            selected[duplicate_index] = cell

    return sorted(selected, key=x_centre)


def directional_numeric_cells(
    cells: list[dict[str, str]],
    bands: dict[str, tuple[float, float]],
) -> dict[str, list[dict[str, str]]]:
    output: dict[str, list[dict[str, str]]] = defaultdict(list)

    for cell in cells:
        if parse_number(cell.get("text", "")) is None:
            continue

        centre = x_centre(cell)
        matched = [
            direction
            for direction, band in bands.items()
            if is_in_band(centre, band)
        ]

        if len(matched) == 1:
            output[matched[0]].append(cell)

    for direction, values in output.items():
        output[direction] = deduplicate_directional_cells(values)

    return output


def non_directional_numeric_cells(
    cells: list[dict[str, str]],
    bands: dict[str, tuple[float, float]],
) -> list[dict[str, str]]:
    output = []

    for cell in cells:
        if parse_number(cell.get("text", "")) is None:
            continue

        centre = x_centre(cell)

        if not any(is_in_band(centre, band) for band in bands.values()):
            output.append(cell)

    return sorted(output, key=x_centre)


def row_confidence(
    identifier: str,
    directional: dict[str, list[dict[str, str]]],
) -> float:
    score = 0.45

    if identifier:
        score += 0.20

    if directional.get("rising"):
        score += 0.15

    if directional.get("falling"):
        score += 0.15

    return min(score, 0.95)


def suffix_number(identifier: str) -> int | None:
    match = re.search(r"(\d+)$", identifier)

    if not match:
        return None

    return int(match.group(1))


def validation_rows(
    calibration_rows: list[dict[str, str]],
    expected_row_count: int | None,
) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []

    actual = len(calibration_rows)

    if expected_row_count is not None:
        output.append({
            "validation_type": "calibration_row_count",
            "expected_value": str(expected_row_count),
            "actual_value": str(actual),
            "status": "pass" if actual == expected_row_count else "fail",
            "details": (
                "Count of recovered logical calibration rows with "
                "non-numeric identifiers."
            ),
        })

    identifiers = [
        row["series_identifier"]
        for row in calibration_rows
        if row.get("series_identifier", "")
    ]

    duplicates = sorted(
        identifier
        for identifier, count in Counter(identifiers).items()
        if count > 1
    )

    output.append({
        "validation_type": "duplicate_identifiers",
        "expected_value": "0",
        "actual_value": str(len(duplicates)),
        "status": "pass" if not duplicates else "fail",
        "details": ",".join(duplicates) or "none",
    })

    suffixes = [
        suffix_number(identifier)
        for identifier in identifiers
    ]
    suffixes = [
        suffix
        for suffix in suffixes
        if suffix is not None
    ]

    if suffixes:
        observed = set(suffixes)
        minimum = min(observed)
        maximum = max(observed)
        missing = [
            value
            for value in range(minimum, maximum + 1)
            if value not in observed
        ]

        output.append({
            "validation_type": "identifier_suffix_gaps",
            "expected_value": f"{minimum}..{maximum}",
            "actual_value": str(len(missing)),
            "status": "pass" if not missing else "fail",
            "details": (
                ",".join(str(value) for value in missing)
                if missing
                else "none"
            ),
        })

    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--document-id", required=True)
    parser.add_argument("--cells-csv", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--expected-row-count",
        type=int,
        default=None,
        help="Optional source-specific validation count.",
    )
    args = parser.parse_args()

    cells = read_csv(args.cells_csv)
    tables = group_by_table(cells)

    row_outputs: list[dict[str, str]] = []
    observation_outputs: list[dict[str, str]] = []
    non_directional_outputs: list[dict[str, str]] = []

    table_count = 0
    calibration_row_number = 0
    observation_number = 0
    evidence_number = 0

    for (page, table_id), raw_cells in sorted(
        tables.items(),
        key=lambda item: (safe_int(item[0][0]), item[0][1]),
    ):
        table_cells = deduplicate_cells(raw_cells)
        context = header_context(table_cells)

        if context is None:
            continue

        bands = directional_bands(context["header_cells"])
        units_by_direction = directional_units(
            context["header_cells"],
            str(context["unit"]),
        )

        if len(bands) < 2:
            continue

        table_count += 1

        header_bottom = max(
            safe_float(cell.get("y2", ""))
            for cell in context["header_cells"]
        )

        body_cells = [
            cell
            for cell in table_cells
            if y_centre(cell) > header_bottom + 18
        ]

        groups = logical_row_groups(body_cells)

        left_boundary = min(
            start
            for start, _ in bands.values()
        )

        for group in groups:
            identifier = tag_identifier(group)
            directional = directional_numeric_cells(group, bands)
            non_directional = non_directional_numeric_cells(group, bands)

            if not identifier:
                continue

            if not directional.get("rising") and not directional.get("falling"):
                continue

            calibration_row_number += 1
            row_id = (
                f"{args.document_id}:calibration:"
                f"p{page}:"
                f"{table_id.split(':')[-1]}:"
                f"{calibration_row_number:03d}"
            )

            position = position_candidate(group, left_boundary)
            row_score = row_confidence(identifier, directional)

            row_outputs.append({
                "calibration_row_id": row_id,
                "document_id": args.document_id,
                "page": page,
                "table_id": table_id,
                "source_row_indices": source_row_indices(group),
                "position_candidate": position,
                "series_identifier": identifier,
                "range_min": str(context["range_min"]),
                "range_max": str(context["range_max"]),
                "unit_candidate": str(context["unit"]),
                "header_evidence": str(context["evidence"]),
                "row_confidence": f"{row_score:.2f}",
                "row_status": "candidate",
                "row_bbox": row_bbox(group),
                "source_kind": "generic_cell_grid_column_geometry",
            })

            for direction, directional_cells in directional.items():
                band = bands[direction]

                for cell in directional_cells:
                    value = parse_number(cell.get("text", ""))

                    if value is None:
                        continue

                    observation_number += 1

                    observation_outputs.append({
                        "observation_id": (
                            f"{row_id}:"
                            f"{direction}:"
                            f"{observation_number:05d}"
                        ),
                        "calibration_row_id": row_id,
                        "document_id": args.document_id,
                        "page": page,
                        "table_id": table_id,
                        "source_row_indices": source_row_indices(group),
                        "series_identifier": identifier,
                        "position_candidate": position,
                        "direction": direction,
                        "value": f"{value:g}",
                        "unit_candidate": units_by_direction.get(
                            direction,
                            str(context["unit"]),
                        ),
                        "ocr_confidence": f"{confidence(cell):.6f}",
                        "bbox": cell_bbox(cell),
                        "column_band": (
                            f"[{band[0]:.2f}, {band[1]:.2f}]"
                        ),
                        "source_kind": "generic_cell_grid_column_geometry",
                        "observation_status": "candidate",
                    })

            for cell in non_directional:
                value = parse_number(cell.get("text", ""))

                if value is None:
                    continue

                evidence_number += 1

                non_directional_outputs.append({
                    "evidence_id": f"{row_id}:other:{evidence_number:05d}",
                    "calibration_row_id": row_id,
                    "document_id": args.document_id,
                    "page": page,
                    "table_id": table_id,
                    "source_row_indices": source_row_indices(group),
                    "series_identifier": identifier,
                    "position_candidate": position,
                    "value": f"{value:g}",
                    "bbox": cell_bbox(cell),
                    "ocr_confidence": f"{confidence(cell):.6f}",
                    "x_centre": f"{x_centre(cell):.2f}",
                    "classification": "non_directional_numeric",
                    "source_kind": "generic_cell_grid_column_geometry",
                })

    validations = validation_rows(
        row_outputs,
        args.expected_row_count,
    )

    write_csv(
        args.output_dir / "calibration_rows.csv",
        ROW_COLUMNS,
        row_outputs,
    )
    write_csv(
        args.output_dir / "calibration_observations.csv",
        OBSERVATION_COLUMNS,
        observation_outputs,
    )
    write_csv(
        args.output_dir / "calibration_non_directional_numeric.csv",
        NON_DIRECTIONAL_COLUMNS,
        non_directional_outputs,
    )
    write_csv(
        args.output_dir / "calibration_row_validation.csv",
        VALIDATION_COLUMNS,
        validations,
    )

    print(f"Cell rows read: {len(cells)}")
    print(f"Tables evaluated: {len(tables)}")
    print(f"Directional calibration tables: {table_count}")
    print(f"Recovered calibration rows: {len(row_outputs)}")
    print(f"Directional observations: {len(observation_outputs)}")
    print(f"Non-directional numeric evidence: {len(non_directional_outputs)}")

    if args.expected_row_count is not None:
        print(f"Expected calibration rows: {args.expected_row_count}")

    print(f"Output directory: {args.output_dir}")


if __name__ == "__main__":
    main()
