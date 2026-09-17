#!/usr/bin/env python3
"""
Reconcile directional numeric header slots across table pages.

This generic stage promotes header tracks supported by multiple pages and
suppresses page-local OCR/body bleed artifacts. It does not assume a number
of slots or any specific nominal values.

Promotion signals:
- track recurs across pages;
- numeric labels agree across those pages;
- labels form an increasing x-ordered sequence.
"""

from __future__ import annotations

import argparse
import csv
import re
from collections import Counter, defaultdict
from pathlib import Path


CONSENSUS_COLUMNS = [
    "direction",
    "canonical_slot",
    "canonical_header_value",
    "canonical_x_centre",
    "support_pages",
    "support_observations",
    "pages_present",
    "value_agreement",
    "promotion_status",
]

PAGE_MAPPING_COLUMNS = [
    "page",
    "table_id",
    "direction",
    "local_slot",
    "local_header_value",
    "local_x_centre",
    "canonical_slot",
    "canonical_header_value",
    "distance_to_canonical",
    "mapping_status",
]


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def f(value: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def numeric(value: str) -> float | None:
    try:
        return float(clean(value).replace(",", "."))
    except ValueError:
        return None


def median(values: list[float]) -> float:
    if not values:
        return 0.0

    values = sorted(values)
    middle = len(values) // 2

    if len(values) % 2:
        return values[middle]

    return (values[middle - 1] + values[middle]) / 2


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(
    path: Path,
    columns: list[str],
    rows: list[dict[str, str]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=columns,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)


def cluster_tracks(
    rows: list[dict[str, str]],
    tolerance: float,
) -> list[list[dict[str, str]]]:
    groups: list[list[dict[str, str]]] = []

    for row in sorted(
        rows,
        key=lambda item: f(item.get("header_x_centre", "")),
    ):
        centre = f(row.get("header_x_centre", ""))

        if not groups:
            groups.append([row])
            continue

        group_centre = sum(
            f(item.get("header_x_centre", ""))
            for item in groups[-1]
        ) / len(groups[-1])

        if abs(centre - group_centre) <= tolerance:
            groups[-1].append(row)
        else:
            groups.append([row])

    return groups


def monotonic_subsequence(
    tracks: list[dict[str, object]],
) -> list[dict[str, object]]:
    """
    Keep the longest x-ordered subsequence with strictly increasing numeric
    header values. This removes duplicate and body-bleed header candidates.
    """
    if not tracks:
        return []

    ordered = sorted(
        tracks,
        key=lambda item: float(item["x_centre"]),
    )

    lengths = [1] * len(ordered)
    previous = [-1] * len(ordered)

    for index in range(len(ordered)):
        value = float(ordered[index]["header_value"])

        for earlier in range(index):
            earlier_value = float(ordered[earlier]["header_value"])

            if earlier_value < value and lengths[earlier] + 1 > lengths[index]:
                lengths[index] = lengths[earlier] + 1
                previous[index] = earlier

    best = max(range(len(ordered)), key=lambda item: lengths[item])
    selected = []

    while best >= 0:
        selected.append(ordered[best])
        best = previous[best]

    return list(reversed(selected))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--header-slots-csv", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--x-tolerance",
        type=float,
        default=100.0,
        help="Maximum x distance for cross-page header-track clustering.",
    )
    parser.add_argument(
        "--minimum-page-support",
        type=int,
        default=2,
        help="Minimum distinct pages required to promote a canonical track.",
    )
    args = parser.parse_args()

    rows = read_csv(args.header_slots_csv)

    by_direction: dict[str, list[dict[str, str]]] = defaultdict(list)
    pages_by_direction: dict[str, set[str]] = defaultdict(set)

    for row in rows:
        direction = clean(row.get("direction", ""))
        page = clean(row.get("page", ""))

        if not direction or not page:
            continue

        if numeric(row.get("header_value", "")) is None:
            continue

        by_direction[direction].append(row)
        pages_by_direction[direction].add(page)

    consensus_rows = []
    mapping_rows = []
    canonical_by_direction: dict[
        str,
        list[dict[str, object]],
    ] = {}

    for direction, direction_rows in sorted(by_direction.items()):
        clusters = cluster_tracks(
            direction_rows,
            args.x_tolerance,
        )

        candidate_tracks = []

        for cluster in clusters:
            pages = sorted({
                row.get("page", "")
                for row in cluster
            })

            values = [
                numeric(row.get("header_value", ""))
                for row in cluster
            ]
            values = [value for value in values if value is not None]

            if not values:
                continue

            value_counts = Counter(
                round(value, 3)
                for value in values
            )
            canonical_value, agreement_count = value_counts.most_common(1)[0]

            candidate_tracks.append({
                "x_centre": sum(
                    f(row.get("header_x_centre", ""))
                    for row in cluster
                ) / len(cluster),
                "header_value": canonical_value,
                "page_count": len(pages),
                "observation_count": len(cluster),
                "pages": pages,
                "agreement": agreement_count / len(cluster),
            })

        supported = [
            track
            for track in candidate_tracks
            if int(track["page_count"]) >= args.minimum_page_support
        ]

        selected = monotonic_subsequence(supported)
        canonical_by_direction[direction] = selected

        for slot, track in enumerate(selected, start=1):
            consensus_rows.append({
                "direction": direction,
                "canonical_slot": str(slot),
                "canonical_header_value": f"{track['header_value']:g}",
                "canonical_x_centre": f"{track['x_centre']:.2f}",
                "support_pages": str(track["page_count"]),
                "support_observations": str(track["observation_count"]),
                "pages_present": ",".join(track["pages"]),
                "value_agreement": f"{track['agreement']:.3f}",
                "promotion_status": "canonical",
            })

    for row in rows:
        direction = clean(row.get("direction", ""))
        centre = f(row.get("header_x_centre", ""))
        value = numeric(row.get("header_value", ""))
        tracks = canonical_by_direction.get(direction, [])

        if value is None or not tracks:
            mapping_rows.append({
                "page": row.get("page", ""),
                "table_id": row.get("table_id", ""),
                "direction": direction,
                "local_slot": row.get("slot_index", ""),
                "local_header_value": row.get("header_value", ""),
                "local_x_centre": row.get("header_x_centre", ""),
                "canonical_slot": "",
                "canonical_header_value": "",
                "distance_to_canonical": "",
                "mapping_status": "no_canonical_match",
            })
            continue

        index, track = min(
            enumerate(tracks, start=1),
            key=lambda item: abs(
                centre - float(item[1]["x_centre"])
            ),
        )

        distance = abs(centre - float(track["x_centre"]))
        value_matches = abs(value - float(track["header_value"])) <= 1.0

        if distance <= args.x_tolerance and value_matches:
            status = "matched_canonical_slot"
        elif distance <= args.x_tolerance:
            status = "x_matches_value_disagrees"
        else:
            status = "outside_canonical_track"

        mapping_rows.append({
            "page": row.get("page", ""),
            "table_id": row.get("table_id", ""),
            "direction": direction,
            "local_slot": row.get("slot_index", ""),
            "local_header_value": row.get("header_value", ""),
            "local_x_centre": row.get("header_x_centre", ""),
            "canonical_slot": str(index),
            "canonical_header_value": f"{track['header_value']:g}",
            "distance_to_canonical": f"{distance:.2f}",
            "mapping_status": status,
        })

    write_csv(
        args.output_dir / "canonical_directional_header_schema.csv",
        CONSENSUS_COLUMNS,
        consensus_rows,
    )
    write_csv(
        args.output_dir / "directional_header_slot_mapping.csv",
        PAGE_MAPPING_COLUMNS,
        mapping_rows,
    )

    print(f"Input header-slot rows: {len(rows)}")
    print(
        "Directions reconciled: "
        f"{len(canonical_by_direction)}"
    )

    for direction, tracks in sorted(canonical_by_direction.items()):
        print(
            f"{direction}: "
            f"{len(tracks)} canonical monotonic header slots"
        )

    print(f"Output directory: {args.output_dir}")


if __name__ == "__main__":
    main()
