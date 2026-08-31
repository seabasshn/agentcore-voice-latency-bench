"""
voice-bench-execute-cancellation
Input:  {"booking_id": str,
          "steps": ["split_booking","remove_passenger","process_refund"]}
Output: {"success": true, "booking_id": str,
          "steps_completed": [...], "total_execution_ms": int}
Each step sleeps 150 ms to simulate downstream work (total >= 150ms * n_steps).
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
    booking_id = event.get("booking_id", "")
    steps = event.get("steps", [])
    completed = []
    for step in steps:
        time.sleep(0.15)
        completed.append(step)
    total_ms = int((time.perf_counter() - t0) * 1000)
    return {
        "success": True,
        "booking_id": booking_id,
        "steps_completed": completed,
        "total_execution_ms": total_ms,
    }
