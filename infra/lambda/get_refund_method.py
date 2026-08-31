"""
voice-bench-get-refund-method
Input:  {"fare_type": str, "payment_method": str}
Output: {"refund_method": ..., "timeline": ..., "transferable": ...,
          "expiry_note": ..., "_execution_ms": float}
        or {"found": false, "fare_type": str, "payment_method": str, "_execution_ms": float}
"""
import json
import os
import time
from decimal import Decimal

import boto3

_TABLE_NAME = os.environ.get("REFUND_RULES_TABLE", "voice-bench-refund-rules")
_ddb = boto3.resource("dynamodb")
_table = _ddb.Table(_TABLE_NAME)


def _unwrap(event):
    body = event.get("body")
    if isinstance(body, str):
        return json.loads(body)
    if isinstance(body, dict):
        return body
    return event


def _jsonify(obj):
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
    fare_type = event["fare_type"]
    payment_method = event["payment_method"]
    resp = _table.get_item(
        Key={"fare_type": fare_type, "payment_method": payment_method}
    )
    ms = round((time.perf_counter() - t0) * 1000, 3)
    item = resp.get("Item")
    if not item:
        return {
            "found": False,
            "fare_type": fare_type,
            "payment_method": payment_method,
            "_execution_ms": ms,
        }
    record = _jsonify(dict(item))
    record["_execution_ms"] = ms
    return record
