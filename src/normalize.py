from __future__ import annotations

import re
from datetime import datetime


TAG_PATTERN = re.compile(r"^\d{4}PS(?:H|L|LL)\d{4}$")
SERIAL_PATTERN = re.compile(r"^\d{6}$")
RANGE_PATTERN = re.compile(
    r"(?P<lower>\d+(?:\.\d+)?)\s*(?:\+|TO|-)\s*"
    r"(?P<upper>\d+(?:\.\d+)?)\s*BAR",
    re.IGNORECASE,
)
PRESSURE_PATTERN = re.compile(
    r"^(?P<value>\d+(?:\.\d+)?)\s*BAR?$",
    re.IGNORECASE,
)


def normalize_tag(value: str) -> str:
    return re.sub(r"[\s_-]+", "", value.upper())


def is_valid_tag(value: str) -> bool:
    return bool(TAG_PATTERN.fullmatch(normalize_tag(value)))


def normalize_serial(value: str) -> str:
    return re.sub(r"\D", "", value)


def is_valid_serial(value: str) -> bool:
    return bool(SERIAL_PATTERN.fullmatch(normalize_serial(value)))


def parse_pressure_bar(value: str) -> float | None:
    match = PRESSURE_PATTERN.fullmatch(value.strip())

    if not match:
        return None

    return float(match.group("value"))


def parse_range_bar(value: str) -> dict[str, float | str] | None:
    normalized = value.upper().replace("*", "+")

    match = RANGE_PATTERN.search(normalized)

    if not match:
        return None

    lower = float(match.group("lower"))
    upper = float(match.group("upper"))

    if upper < lower:
        return None

    return {
        "lower_bar": lower,
        "upper_bar": upper,
        "unit": "bar",
    }


def parse_date(value: str) -> str | None:
    for pattern in ("%d-%m-%y", "%d-%m-%Y", "%d/%m/%y", "%d/%m/%Y"):
        try:
            return datetime.strptime(value.strip(), pattern).date().isoformat()
        except ValueError:
            continue

    return None
