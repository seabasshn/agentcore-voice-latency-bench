"""Inline MCP tool schemas for the 4 Lambda targets of the voice-bench-tools gateway.

Each target = one Lambda (pointed at its :live PC alias) exposing one tool.
inputSchema follows JSON-Schema (type: object). booking_id-only fields are marked
required; the others are optional so the model can fill them from the reservation
record it retrieves first.
"""

ACCOUNT = "111122223333"
REGION = "us-east-1"


def lambda_alias_arn(fn: str) -> str:
    return f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:{fn}:live"


# (target_name, lambda_function, tool_name, description, inputSchema)
TOOLS = [
    {
        "target": "get_reservation",
        "lambda": "voice-bench-get-reservation",
        "name": "get_reservation",
        "description": "Retrieve a passenger's flight reservation by booking ID. Returns fare_type, payment_method, pax_count, and the booleans already_traveled, is_group_booking, has_checked_bags, linked_minor.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "booking_id": {"type": "string", "description": "The booking ID, e.g. BK-001"}
            },
            "required": ["booking_id"],
        },
    },
    {
        "target": "check_eligibility",
        "lambda": "voice-bench-check-eligibility",
        "name": "check_eligibility",
        "description": "Evaluate cancellation eligibility using the reservation's attributes. Pass the values returned by get_reservation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "booking_id": {"type": "string"},
                "already_traveled": {"type": "boolean"},
                "is_group_booking": {"type": "boolean"},
                "pax_count": {"type": "integer"},
                "has_checked_bags": {"type": "boolean"},
                "linked_minor": {"type": "boolean"},
            },
            "required": ["booking_id"],
        },
    },
    {
        "target": "get_refund_method",
        "lambda": "voice-bench-get-refund-method",
        "name": "get_refund_method",
        "description": "Look up the refund method, timeline, transferability and expiry for a given fare_type and payment_method.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "fare_type": {"type": "string", "description": "PREMIUM | STANDARD | ECONOMY | BASIC"},
                "payment_method": {"type": "string", "description": "CREDIT_CARD | VOUCHER | GIFT_CARD | POINTS"},
            },
            "required": ["fare_type", "payment_method"],
        },
    },
    {
        "target": "execute_cancellation",
        "lambda": "voice-bench-execute-cancellation",
        "name": "execute_cancellation",
        "description": "Execute the cancellation plan. steps is an ordered list from: split_booking, remove_passenger, process_refund.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "booking_id": {"type": "string"},
                "steps": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["booking_id", "steps"],
        },
    },
]
