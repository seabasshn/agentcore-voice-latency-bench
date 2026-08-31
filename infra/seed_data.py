#!/usr/bin/env python3
"""
Idempotent seed script for voice-bench DynamoDB tables.
Uses put_item so it is safe to re-run at any time.

Usage:
    AWS_PROFILE=voice-bench python3 infra/seed_data.py
"""

import os
import boto3
from boto3.dynamodb.conditions import Attr  # noqa: F401  (not used but kept for future)

# ---------------------------------------------------------------------------
# Session / resource setup
# ---------------------------------------------------------------------------

PROFILE = os.environ.get("AWS_PROFILE", "voice-bench")
REGION = "us-east-1"

session = boto3.Session(profile_name=PROFILE, region_name=REGION)
dynamodb = session.resource("dynamodb")

RESERVATIONS_TABLE = "voice-bench-reservations"
REFUND_RULES_TABLE = "voice-bench-refund-rules"

# ---------------------------------------------------------------------------
# Refund-rules matrix
#
# Keyed by payment_method.  The `timeline` key is overridden per fare_type
# below — see FARE_TIMELINES.
#
# Rationale:
#   CREDIT_CARD  → original payment instrument is traceable; refund directly.
#   VOUCHER      → non-cash; issue travel credit valid 12 months.
#   GIFT_CARD    → transferable product; transferable credit valid 24 months.
#   POINTS       → loyalty currency; return immediately to loyalty account.
# ---------------------------------------------------------------------------

PAYMENT_METHOD_RULES = {
    "CREDIT_CARD": {
        "refund_method": "ORIGINAL_PAYMENT",
        "transferable": False,
        "expiry_note": "N/A",
        # base timeline overridden per fare_type below
        "_base_timeline": "{fare_timeline}",
    },
    "VOUCHER": {
        "refund_method": "TRAVEL_CREDIT",
        "transferable": False,
        "expiry_note": "12 months",
        "_base_timeline": "{fare_timeline}",
    },
    "GIFT_CARD": {
        "refund_method": "TRANSFERABLE_CREDIT",
        "transferable": True,
        "expiry_note": "24 months",
        "_base_timeline": "{fare_timeline}",
    },
    "POINTS": {
        "refund_method": "LOYALTY_POINTS",
        "transferable": False,
        "expiry_note": "per loyalty program terms",
        # Points are returned to the loyalty account immediately regardless of fare
        "_base_timeline": "immediate",
    },
}

# Timeline varies by fare class (PREMIUM fastest → BASIC slowest).
# For POINTS the timeline is always "immediate" (set in PAYMENT_METHOD_RULES).
FARE_TIMELINES = {
    "PREMIUM": "1-2 business days",
    "STANDARD": "3-5 business days",
    "ECONOMY": "5-7 business days",
    "BASIC": "7-10 business days",
}

FARE_TYPES = ["PREMIUM", "STANDARD", "ECONOMY", "BASIC"]
PAYMENT_METHODS = ["CREDIT_CARD", "VOUCHER", "GIFT_CARD", "POINTS"]


def build_refund_rules():
    """Return the 16 refund-rule records (4 fare × 4 payment)."""
    rules = []
    for fare in FARE_TYPES:
        for pm in PAYMENT_METHODS:
            base = PAYMENT_METHOD_RULES[pm]
            timeline = (
                "immediate"
                if base["_base_timeline"] == "immediate"
                else FARE_TIMELINES[fare]
            )
            rules.append(
                {
                    "fare_type": fare,
                    "payment_method": pm,
                    "refund_method": base["refund_method"],
                    "timeline": timeline,
                    "transferable": base["transferable"],
                    "expiry_note": base["expiry_note"],
                }
            )
    return rules


# ---------------------------------------------------------------------------
# Fixed reservations (BK-001..BK-005) — benchmark depends on these exactly.
# ---------------------------------------------------------------------------

FIXED_RESERVATIONS = [
    {
        # BK-001: clean eligible — primary test booking
        "booking_id": "BK-001",
        "passenger_name": "Alice Martin",
        "flight_date": "2026-09-15",
        "fare_type": "STANDARD",
        "payment_method": "CREDIT_CARD",
        "pax_count": 1,
        "has_checked_bags": False,
        "already_traveled": False,
        "is_group_booking": False,
        "linked_minor": False,
    },
    {
        # BK-002: ineligible — passenger has already traveled
        "booking_id": "BK-002",
        "passenger_name": "Bob Chen",
        "flight_date": "2026-08-10",
        "fare_type": "ECONOMY",
        "payment_method": "GIFT_CARD",
        "pax_count": 1,
        "has_checked_bags": False,
        "already_traveled": True,
        "is_group_booking": False,
        "linked_minor": False,
    },
    {
        # BK-003: group booking — requires split
        "booking_id": "BK-003",
        "passenger_name": "Carol Reyes",
        "flight_date": "2026-10-05",
        "fare_type": "BASIC",
        "payment_method": "VOUCHER",
        "pax_count": 4,
        "has_checked_bags": False,
        "already_traveled": False,
        "is_group_booking": True,
        "linked_minor": False,
    },
    {
        # BK-004: minor linked — guardian confirmation needed
        "booking_id": "BK-004",
        "passenger_name": "David Park",
        "flight_date": "2026-11-20",
        "fare_type": "PREMIUM",
        "payment_method": "POINTS",
        "pax_count": 2,
        "has_checked_bags": False,
        "already_traveled": False,
        "is_group_booking": False,
        "linked_minor": True,
    },
    {
        # BK-005: checked bags present — warning only, still eligible
        "booking_id": "BK-005",
        "passenger_name": "Emma Wilson",
        "flight_date": "2026-09-22",
        "fare_type": "STANDARD",
        "payment_method": "CREDIT_CARD",
        "pax_count": 1,
        "has_checked_bags": True,
        "already_traveled": False,
        "is_group_booking": False,
        "linked_minor": False,
    },
]

# ---------------------------------------------------------------------------
# Generate deterministic BK-006..BK-050 (45 records).
#
# Strategy: iterate over all 16 (fare × payment) combos first (covers every
# rule permutation), then cycle through them again to fill to 45.
# The four booleans are toggled deterministically by index so that every
# permutation of {has_checked_bags, already_traveled, is_group_booking,
# linked_minor} appears across the 45 records.
# ---------------------------------------------------------------------------

_PASSENGER_NAMES = [
    "Fiona Adams", "George Baker", "Hannah Clark", "Ivan Davis", "Julia Evans",
    "Kevin Foster", "Laura Green", "Marcus Hill", "Nina Ingram", "Oscar Jones",
    "Paula King", "Quinn Lee", "Rachel Moore", "Samuel Nash", "Tara Owens",
    "Uma Pierce", "Victor Quinn", "Wendy Ross", "Xavier Scott", "Yara Thomas",
    "Zachary Upton", "Abigail Vance", "Bryan Ward", "Chloe Xavier", "Derek Young",
    "Elise Zimmerman", "Frank Arnold", "Grace Bishop", "Henry Carter", "Iris Dixon",
    "Jack Ellison", "Karen Franklin", "Leo Graham", "Maya Hardy", "Neil Irving",
    "Olivia Johnson", "Peter Kimura", "Quinn Lewis", "Rosa Miller", "Sean Newton",
    "Tina O'Brien", "Ulrich Peters", "Vera Quinn", "Walter Reed", "Xenia Stone",
]

# 16 (fare, payment) combos in deterministic order
_ALL_COMBOS = [(f, p) for f in FARE_TYPES for p in PAYMENT_METHODS]

# Flight dates: one per week starting 2026-09-01 to keep them all in the future
_BASE_DATE_YEAR = 2026
_BASE_DATE_MONTH = 9
_BASE_DATE_DAY = 1


def _flight_date(idx: int) -> str:
    """Return a future ISO date, incrementing by ~1 week per index (mod 52)."""
    weeks_offset = idx % 52
    # Simple arithmetic without datetime to keep this dependency-free
    # Start: 2026-09-01 + 7*weeks_offset days
    base_day_of_year = 244  # day-of-year for 2026-09-01 (approx)
    total_days = base_day_of_year + weeks_offset * 7
    # Approximate month/day from day-of-year (good enough for test data)
    months = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]  # 2026 is not a leap year
    remaining = total_days % 365 or 365
    m = 1
    for days_in_month in months[1:]:
        if remaining <= days_in_month:
            break
        remaining -= days_in_month
        m += 1
    year = 2026 + (total_days - 1) // 365
    return f"{year:04d}-{m:02d}-{remaining:02d}"


def build_generated_reservations():
    """Build BK-006..BK-050 deterministically (no randomness)."""
    records = []
    for i in range(45):
        booking_num = i + 6  # BK-006 .. BK-050
        booking_id = f"BK-{booking_num:03d}"

        combo_idx = i % 16
        fare_type, payment_method = _ALL_COMBOS[combo_idx]

        # Toggle booleans by bit-position of i to cover all permutations
        has_checked_bags = bool((i >> 0) & 1)
        already_traveled = bool((i >> 1) & 1)
        is_group_booking = bool((i >> 2) & 1)
        linked_minor = bool((i >> 3) & 1)

        # Pax count: 1 for solo, 2-4 for group or linked_minor bookings
        if is_group_booking:
            pax_count = 2 + (i % 3)  # 2, 3, or 4
        elif linked_minor:
            pax_count = 2
        else:
            pax_count = 1

        records.append(
            {
                "booking_id": booking_id,
                "passenger_name": _PASSENGER_NAMES[i % len(_PASSENGER_NAMES)],
                "flight_date": _flight_date(i),
                "fare_type": fare_type,
                "payment_method": payment_method,
                "pax_count": pax_count,
                "has_checked_bags": has_checked_bags,
                "already_traveled": already_traveled,
                "is_group_booking": is_group_booking,
                "linked_minor": linked_minor,
            }
        )
    return records


# ---------------------------------------------------------------------------
# Seed functions
# ---------------------------------------------------------------------------


def seed_reservations(table):
    all_records = FIXED_RESERVATIONS + build_generated_reservations()
    for record in all_records:
        # DynamoDB requires Decimal for numbers but booleans are native
        item = {k: v for k, v in record.items()}
        # Convert int pax_count to plain int (boto3 handles it fine)
        table.put_item(Item=item)
    return len(all_records)


def seed_refund_rules(table):
    rules = build_refund_rules()
    for rule in rules:
        table.put_item(Item=rule)
    return len(rules)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    print(f"Using AWS profile: {PROFILE}, region: {REGION}")

    res_table = dynamodb.Table(RESERVATIONS_TABLE)
    rule_table = dynamodb.Table(REFUND_RULES_TABLE)

    print(f"Seeding {RESERVATIONS_TABLE} ...")
    n_res = seed_reservations(res_table)
    print(f"  -> {n_res} reservation records written (idempotent put_item)")

    print(f"Seeding {REFUND_RULES_TABLE} ...")
    n_rules = seed_refund_rules(rule_table)
    print(f"  -> {n_rules} refund-rule records written (idempotent put_item)")

    print("Seed complete.")
    print(f"  Reservations : {n_res}")
    print(f"  Refund rules : {n_rules}")


if __name__ == "__main__":
    main()
