"""
voice-bench-check-eligibility
Input:  {"booking_id": str, "already_traveled": bool, "is_group_booking": bool,
          "pax_count": int, "has_checked_bags": bool, "linked_minor": bool}
Output: {"eligible": bool, "reason": str, "requires_split": bool,
          "warnings": [str], "_execution_ms": float}
"""
import json
import time


def _unwrap(event):
    body = event.get("body")
    if isinstance(body, str):
        return json.loads(body)
    if isinstance(body, dict):
        return body
    return event


def handler(event, context):
    event = _unwrap(event)
    t0 = time.perf_counter()

    already_traveled = bool(event.get("already_traveled", False))
    is_group = bool(event.get("is_group_booking", False))
    pax_count = int(event.get("pax_count", 1))
    has_checked_bags = bool(event.get("has_checked_bags", False))
    linked_minor = bool(event.get("linked_minor", False))

    if already_traveled:
        eligible = False
        reason = "Ticket already traveled; not eligible for cancellation."
    else:
        eligible = True
        reason = "Eligible for cancellation."

    requires_split = is_group and pax_count > 1

    warnings = []
    if linked_minor:
        warnings.append("Linked minor: guardian confirmation required.")
    if has_checked_bags:
        warnings.append("Checked baggage present: refund of baggage fees may differ.")
    if requires_split:
        warnings.append("Group booking: booking must be split before cancellation.")

    ms = round((time.perf_counter() - t0) * 1000, 3)
    return {
        "eligible": eligible,
        "reason": reason,
        "requires_split": requires_split,
        "warnings": warnings,
        "_execution_ms": ms,
    }
