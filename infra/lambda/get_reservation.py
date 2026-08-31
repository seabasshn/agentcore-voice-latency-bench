"""
voice-bench-get-reservation
Input:  {"booking_id": str}
Output: full reservation record + "_execution_ms" (float)
        or {"found": false, "booking_id": str, "_execution_ms": float}
"""
import json
import os
import time
from decimal import Decimal

import boto3

_TABLE_NAME = os.environ.get("RESERVATIONS_TABLE", "voice-bench-reservations")
_ddb = boto3.resource("dynamodb")
_table = _ddb.Table(_TABLE_NAME)


def _unwrap(event):
    """Accept raw dict, API-GW proxy, or body-wrapped JSON."""
    body = event.get("body")
    if isinstance(body, str):
        return json.loads(body)
    if isinstance(body, dict):
        return body
    return event


def _jsonify(obj):
    """Convert DynamoDB Decimal types to int/float for JSON safety."""
    if isinstance(obj, Decimal):
        f = float(obj)
        return int(f) if f == int(f) else f
    if isinstance(obj, dict):
        return {k: _jsonify(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonify(i) for i in obj]
    return obj


def handler(event, context):
    event = _unwrap(event)
    t0 = time.perf_counter()
    booking_id = event["booking_id"]
    resp = _table.get_item(Key={"booking_id": booking_id})
    ms = round((time.perf_counter() - t0) * 1000, 3)
    item = resp.get("Item")
    if not item:
        return {"found": False, "booking_id": booking_id, "_execution_ms": ms}
    record = _jsonify(dict(item))
    record["_execution_ms"] = ms
    return record
