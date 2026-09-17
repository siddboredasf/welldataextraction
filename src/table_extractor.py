#!/usr/bin/env python3
"""Universal table-extraction adapter.

This module provides one stable public function:

    extract_tables(text: str, page_number: int | None = None)
        -> list[dict]

It first attempts to use the repository's existing table-region extraction
implementation. If that implementation exposes a different API or cannot be
loaded, the adapter falls back to conservative plain-text table detection.

The adapter is intentionally separate from run_table_extraction.py so the
batch runner remains stable while the underlying extractor evolves.
"""

from __future__ import annotations

import importlib
import inspect
import re
from typing import Any, Callable


def _normalise_cell(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalise_table(
    table: Any,
    page_number: int | None = None,
    table_index: int = 1,
) -> dict[str, Any]:
    """Convert common extractor return shapes into one JSON-safe structure."""

    if isinstance(table, dict):
        result = dict(table)

        if "rows" not in result:
            for key in ("data", "table", "cells", "records"):
                if key in result:
                    result["rows"] = result.pop(key)
                    break

        if "rows" not in result:
            result["rows"] = []

        result["rows"] = [
            [
                _normalise_cell(cell)
                for cell in row
            ]
            if isinstance(row, (list, tuple))
            else [_normalise_cell(row)]
            for row in result["rows"]
        ]

        result.setdefault(
            "table_id",
            (
                f"page_{page_number:03d}_table_{table_index:02d}"
                if page_number is not None
                else f"table_{table_index:02d}"
            ),
        )

        return result

    if isinstance(table, (list, tuple)):
        rows = []

        for row in table:
            if isinstance(row, (list, tuple)):
                rows.append([
                    _normalise_cell(cell)
                    for cell in row
                ])
            else:
                rows.append([_normalise_cell(row)])

        return {
            "table_id": (
                f"page_{page_number:03d}_table_{table_index:02d}"
                if page_number is not None
                else f"table_{table_index:02d}"
            ),
            "headers": rows[0] if rows else [],
            "rows": rows[1:] if rows else [],
        }

    return {
        "table_id": (
            f"page_{page_number:03d}_table_{table_index:02d}"
            if page_number is not None
            else f"table_{table_index:02d}"
        ),
        "headers": [],
        "rows": [[_normalise_cell(table)]],
    }


def _find_callable(module: Any) -> Callable[..., Any] | None:
    """Find a likely table-extraction callable in an existing module."""

    preferred_names = [
        "extract_tables",
        "extract_table_regions",
        "extract_regions",
        "detect_tables",
        "parse_tables",
        "run",
    ]

    for name in preferred_names:
        candidate = getattr(module, name, None)

        if callable(candidate):
            return candidate

    return None


def _call_existing_extractor(
    text: str,
    page_number: int | None = None,
) -> list[dict[str, Any]] | None:
    """Try existing project extractors without assuming one exact API."""

    module_names = [
        "extract_table_regions",
        "reconstruct_table_rows",
        "certificate_table_rows",
        "table_layout",
    ]

    for module_name in module_names:
        try:
            module = importlib.import_module(module_name)
        except Exception:
            continue

        extractor = _find_callable(module)

        if extractor is None:
            continue

        try:
            signature = inspect.signature(extractor)
            parameters = signature.parameters

            kwargs: dict[str, Any] = {}

            if "text" in parameters:
                kwargs["text"] = text
            elif "page_text" in parameters:
                kwargs["page_text"] = text
            elif "content" in parameters:
                kwargs["content"] = text

            if page_number is not None:
                if "page_number" in parameters:
                    kwargs["page_number"] = page_number
                elif "page" in parameters:
                    kwargs["page"] = page_number

            if kwargs:
                raw_result = extractor(**kwargs)
            else:
                positional_count = len([
                    parameter
                    for parameter in parameters.values()
                    if parameter.kind
                    in (
                        inspect.Parameter.POSITIONAL_ONLY,
                        inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    )
                    and parameter.default is inspect.Parameter.empty
                ])

                if positional_count >= 2 and page_number is not None:
                    raw_result = extractor(text, page_number)
                else:
                    raw_result = extractor(text)

        except Exception:
            continue

        if raw_result is None:
            continue

        if isinstance(raw_result, dict):
            if "tables" in raw_result:
                raw_tables = raw_result["tables"]
            else:
                raw_tables = [raw_result]
        elif isinstance(raw_result, (list, tuple)):
            raw_tables = raw_result
        else:
            raw_tables = [raw_result]

        return [
            _normalise_table(
                table,
                page_number=page_number,
                table_index=index,
            )
            for index, table in enumerate(raw_tables, start=1)
        ]

    return None


def _looks_like_table_line(line: str) -> bool:
    stripped = line.strip()

    if not stripped:
        return False

    if "|" in stripped:
        return len([part for part in stripped.split("|") if part.strip()]) >= 2

    if "\t" in stripped:
        return len([part for part in stripped.split("\t") if part.strip()]) >= 2

    if re.search(r"\s{2,}", stripped):
        return len(re.split(r"\s{2,}", stripped)) >= 2

    return False


def _split_table_line(line: str) -> list[str]:
    stripped = line.strip()

    if "|" in stripped:
        parts = stripped.split("|")
    elif "\t" in stripped:
        parts = stripped.split("\t")
    else:
        parts = re.split(r"\s{2,}", stripped)

    return [
        _normalise_cell(part)
        for part in parts
        if _normalise_cell(part)
    ]


def _fallback_text_tables(
    text: str,
    page_number: int | None = None,
) -> list[dict[str, Any]]:
    """Conservative fallback for visibly delimited native-text tables."""

    lines = text.splitlines()
    table_groups: list[list[list[str]]] = []
    current: list[list[str]] = []

    for line in lines:
        if _looks_like_table_line(line):
            current.append(_split_table_line(line))
            continue

        if current:
            if len(current) >= 2:
                table_groups.append(current)
            current = []

    if current and len(current) >= 2:
        table_groups.append(current)

    tables = []

    for index, rows in enumerate(table_groups, start=1):
        tables.append({
            "table_id": (
                f"page_{page_number:03d}_table_{index:02d}"
                if page_number is not None
                else f"table_{index:02d}"
            ),
            "source": "native_text_fallback",
            "headers": rows[0],
            "rows": rows[1:],
            "row_count": max(len(rows) - 1, 0),
            "column_count": max(len(row) for row in rows),
        })

    return tables


def extract_tables(
    text: str,
    page_number: int | None = None,
) -> list[dict[str, Any]]:
    """Extract and normalize tables from page text."""

    if not text.strip():
        return []

    existing_result = _call_existing_extractor(
        text=text,
        page_number=page_number,
    )

    if existing_result is not None:
        return existing_result

    return _fallback_text_tables(
        text=text,
        page_number=page_number,
    )
