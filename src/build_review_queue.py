#!/usr/bin/env python3

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path


QUEUE_FIELDS = [
    "review_id",
    "priority",
    "priority_score",
    "equipment_id",
    "equipment_tag",
    "serial_number",
    "field_name",
    "candidate_value",
    "candidate_count",
    "evidence_count",
    "best_evidence_tier",
    "best_confidence",
    "source_pages",
    "evidence_ids",
    "source_texts",
    "recommended_action",
    "reason",
    "review_status",
    "reviewed_value",
    "reviewer",
    "review_notes",
]

AUTO_FIELDS = [
    "equipment_id",
    "equipment_tag",
    "serial_number",
    "field_name",
    "candidate_value",
    "decision",
    "reason",
    "evidence_ids",
    "source_pages",
]

SUMMARY_FIELDS = [
    "equipment_id",
    "equipment_tag",
    "serial_number",
    "review_fields",
    "high_priority_fields",
    "candidate_groups",
    "recommended_action",
]


def clean(value):
    return " ".join(str(value or "").split()).strip()


def norm(value):
    return clean(value).casefold()


def number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def make_review_id(equipment_id, field_name):
    raw = f"{equipment_id}|{field_name}".encode("utf-8")
    return "REV-" + hashlib.sha1(raw).hexdigest()[:12].upper()


def parse_args():
    parser = argparse.ArgumentParser(
        description="Build a grouped, configurable evidence review queue."
    )

    parser.add_argument(
        "--input",
        required=True,
        type=Path,
        help="Long-form metadata evidence CSV.",
    )

    parser.add_argument(
        "--policy",
        required=True,
        type=Path,
        help="JSON review policy.",
    )

    parser.add_argument(
        "--queue",
        required=True,
        type=Path,
        help="Output CSV for grouped review tasks.",
    )

    parser.add_argument(
        "--auto",
        required=True,
        type=Path,
        help="Output CSV for automatically accepted candidates.",
    )

    parser.add_argument(
        "--summary",
        required=True,
        type=Path,
        help="Output CSV for equipment-level summaries.",
    )

    return parser.parse_args()


def read_csv(path):
    if not path.is_file():
        raise SystemExit(f"Input file not found: {path}")

    with path.open(
        encoding="utf-8-sig",
        newline="",
    ) as handle:
        return list(csv.DictReader(handle))


def write_csv(path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fields,
        )
        writer.writeheader()
        writer.writerows(rows)


def tier_score(row, tier_rank):
    return tier_rank.get(
        clean(row.get("evidence_tier")),
        0,
    )


def evidence_score(row, tier_rank):
    return (
        tier_score(row, tier_rank),
        number(row.get("confidence")),
        number(row.get("selection_score")),
    )


def priority_for(
    field_name,
    candidate_count,
    best_tier,
    confidence,
    risk_fields,
    high_threshold,
    medium_threshold,
):
    score = 0

    if field_name in risk_fields:
        score += 25

    if candidate_count > 1:
        score += 45

    if best_tier >= 3:
        score += 20
    elif best_tier == 2:
        score += 10
    else:
        score += 5

    if confidence < 0.85:
        score += 25
    elif confidence < 0.95:
        score += 10

    if score >= high_threshold:
        return "high", min(score, 100)

    if score >= medium_threshold:
        return "medium", min(score, 100)

    return "low", min(score, 100)


def main():
    args = parse_args()
    policy = json.loads(args.policy.read_text(encoding="utf-8"))

    identity_fields = set(
        policy.get("identity_fields", [])
    )
    risk_fields = set(
        policy.get("risk_fields", [])
    )
    tier_rank = policy.get("tier_rank", {})
    auto_policy = policy.get("auto_accept", {})
    priority_policy = policy.get("priority_thresholds", {})

    max_candidates = int(
        auto_policy.get("max_candidate_values", 1)
    )
    minimum_tier = int(
        auto_policy.get("minimum_tier_rank", 2)
    )
    minimum_confidence = float(
        auto_policy.get("minimum_confidence", 0.98)
    )

    high_threshold = float(
        priority_policy.get("high", 70)
    )
    medium_threshold = float(
        priority_policy.get("medium", 40)
    )

    rows = read_csv(args.input)
    groups = defaultdict(list)

    for row in rows:
        equipment_id = clean(row.get("equipment_id"))
        field_name = clean(row.get("field_name"))
        field_value = clean(row.get("field_value"))

        if not equipment_id:
            continue

        if not field_name or not field_value:
            continue

        if field_name in identity_fields:
            continue

        groups[(equipment_id, field_name)].append(row)

    queue_rows = []
    auto_rows = []
    summary = defaultdict(list)

    for (equipment_id, field_name), candidates in sorted(groups.items()):
        by_value = defaultdict(list)

        for row in candidates:
            by_value[norm(row.get("field_value"))].append(row)

        ranked = sorted(
            by_value.items(),
            key=lambda item: max(
                evidence_score(row, tier_rank)
                for row in item[1]
            ),
            reverse=True,
        )

        best_rows = ranked[0][1]
        best_row = max(
            best_rows,
            key=lambda row: evidence_score(row, tier_rank),
        )

        candidate_count = len(ranked)
        best_tier = tier_score(best_row, tier_rank)
        best_confidence = number(best_row.get("confidence"))

        all_rows = [
            row
            for value_rows in by_value.values()
            for row in value_rows
        ]

        pages = list(dict.fromkeys(
            clean(row.get("source_page"))
            for row in all_rows
            if clean(row.get("source_page"))
        ))

        evidence_ids = list(dict.fromkeys(
            clean(row.get("evidence_id"))
            for row in all_rows
            if clean(row.get("evidence_id"))
        ))

        source_texts = list(dict.fromkeys(
            clean(row.get("source_text"))
            for row in all_rows
            if clean(row.get("source_text"))
        ))

        tag = clean(best_row.get("equipment_tag"))
        serial = clean(best_row.get("serial_number"))
        value = clean(best_row.get("field_value"))

        priority, priority_score = priority_for(
            field_name,
            candidate_count,
            best_tier,
            best_confidence,
            risk_fields,
            high_threshold,
            medium_threshold,
        )

        can_auto_accept = (
            candidate_count <= max_candidates
            and best_tier >= minimum_tier
            and best_confidence >= minimum_confidence
        )

        if can_auto_accept:
            auto_rows.append({
                "equipment_id": equipment_id,
                "equipment_tag": tag,
                "serial_number": serial,
                "field_name": field_name,
                "candidate_value": value,
                "decision": "auto_accept_candidate",
                "reason": (
                    "single candidate met configured tier "
                    "and confidence thresholds"
                ),
                "evidence_ids": "|".join(evidence_ids),
                "source_pages": "|".join(pages),
            })

            summary[(equipment_id, tag, serial)].append({
                "status": "auto",
                "priority": priority,
            })
            continue

        if candidate_count > 1:
            reason = "conflicting candidate values"
            action = "compare_candidates"
        elif best_tier < minimum_tier:
            reason = "evidence tier below auto-accept threshold"
            action = "review_source_context"
        elif best_confidence < minimum_confidence:
            reason = "confidence below auto-accept threshold"
            action = "review_source_context"
        else:
            reason = "candidate requires validation"
            action = "review_source_context"

        queue_rows.append({
            "review_id": make_review_id(
                equipment_id,
                field_name,
            ),
            "priority": priority,
            "priority_score": priority_score,
            "equipment_id": equipment_id,
            "equipment_tag": tag,
            "serial_number": serial,
            "field_name": field_name,
            "candidate_value": value,
            "candidate_count": candidate_count,
            "evidence_count": len(all_rows),
            "best_evidence_tier": best_row.get(
                "evidence_tier",
                "",
            ),
            "best_confidence": best_confidence,
            "source_pages": "|".join(pages),
            "evidence_ids": "|".join(evidence_ids),
            "source_texts": " || ".join(source_texts),
            "recommended_action": action,
            "reason": reason,
            "review_status": "pending",
            "reviewed_value": "",
            "reviewer": "",
            "review_notes": "",
        })

        summary[(equipment_id, tag, serial)].append({
            "status": "review",
            "priority": priority,
        })

    queue_rows.sort(
        key=lambda row: (
            {"high": 0, "medium": 1, "low": 2}[
                row["priority"]
            ],
            -float(row["priority_score"]),
            row["equipment_tag"],
            row["field_name"],
        )
    )

    write_csv(args.queue, QUEUE_FIELDS, queue_rows)
    write_csv(args.auto, AUTO_FIELDS, auto_rows)

    summary_rows = []

    for (equipment_id, tag, serial), items in sorted(summary.items()):
        high_count = sum(
            item["priority"] == "high"
            for item in items
        )
        review_count = sum(
            item["status"] == "review"
            for item in items
        )

        summary_rows.append({
            "equipment_id": equipment_id,
            "equipment_tag": tag,
            "serial_number": serial,
            "review_fields": len(items),
            "high_priority_fields": high_count,
            "candidate_groups": review_count,
            "recommended_action": (
                "review_high_priority_first"
                if high_count
                else "review_when_available"
            ),
        })

    write_csv(args.summary, SUMMARY_FIELDS, summary_rows)

    print(f"Input evidence rows: {len(rows)}")
    print(f"Grouped review tasks: {len(queue_rows)}")
    print(f"Auto-accept candidates: {len(auto_rows)}")
    print(f"Equipment summaries: {len(summary_rows)}")
    print(f"Queue: {args.queue}")
    print(f"Auto decisions: {args.auto}")
    print(f"Summary: {args.summary}")


if __name__ == "__main__":
    main()
