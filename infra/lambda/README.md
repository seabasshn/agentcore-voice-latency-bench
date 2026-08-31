# voice-agent-latency-bench: Lambda Tool Handlers

Four ARM64/Graviton Lambda functions used as tool targets in the voice-agent
latency benchmark. Each function is deployed via `infra/lambda-tools.yaml`.

## Function contracts

### voice-bench-get-reservation
- **Input**: `{"booking_id": "string"}`
- **Behavior**: DynamoDB `GetItem` on `voice-bench-reservations` (PK `booking_id`).
- **Output (found)**: Full reservation record with all fields deserialized to
  native Python types (booleans, ints, strings) plus `"_execution_ms": float`.
- **Output (not found)**: `{"found": false, "booking_id": "...", "_execution_ms": float}`

### voice-bench-check-eligibility
- **Input**: `{"booking_id": "string", "already_traveled": bool,
  "is_group_booking": bool, "pax_count": int,
  "has_checked_bags": bool, "linked_minor": bool}`
- **Behavior**: Deterministic rule engine — no DynamoDB calls.
  - `already_traveled=true` → `eligible=false`, reason "Ticket already traveled; not eligible for cancellation."
  - else → `eligible=true`, reason "Eligible for cancellation."
  - `requires_split = is_group_booking AND pax_count > 1`
  - `warnings`: appends messages for `linked_minor`, `has_checked_bags`, `requires_split`
- **Output**: `{"eligible": bool, "reason": str, "requires_split": bool,
  "warnings": [...], "_execution_ms": float}`

### voice-bench-get-refund-method
- **Input**: `{"fare_type": "string", "payment_method": "string"}`
- **Behavior**: DynamoDB `GetItem` on `voice-bench-refund-rules`
  (PK `fare_type`, SK `payment_method`). Returns the rule record or a
  not-found response.
- **Output (found)**: Record fields (`refund_method`, `timeline`, `transferable`,
  `expiry_note`, …) plus `"_execution_ms": float`.
- **Output (not found)**: `{"found": false, "fare_type": "...",
  "payment_method": "...", "_execution_ms": float}`

### voice-bench-execute-cancellation
- **Input**: `{"booking_id": "string",
  "steps": ["split_booking","remove_passenger","process_refund"]}`
- **Behavior**: Iterates steps; each step `time.sleep(0.15)` then records
  completion. Total latency ≥ 150 ms × number of steps.
- **Output**: `{"success": true, "booking_id": "...",
  "steps_completed": [...], "total_execution_ms": int}`

## Input unwrapping

All handlers accept three input shapes:
1. **Direct tool call** — event is the arguments dict.
2. **API-Gateway proxy** — event has `"body": "<JSON string>"`, unwrapped automatically.
3. **Nested body dict** — event has `"body": {...}`, also unwrapped.

## Provisioned Concurrency wiring

Each function has two deployment resources in `lambda-tools.yaml`:

| Resource type | Logical ID pattern | Purpose |
|---|---|---|
| `AWS::Lambda::Function` | `*Function` | The function itself; `$LATEST` has **no PC** — used for cold-start baseline (T2 in the benchmark). |
| `AWS::Lambda::Version` | `*Version` | Publishes `$LATEST` as an immutable numbered version. |
| `AWS::Lambda::Alias` | `*Alias` (name: `live`) | Points at the published version with **PC = 5**. Invoke via `function:live` qualifier for warm/provisioned latency (T1). |

To invoke the provisioned alias from the benchmark harness:
```python
client.invoke(FunctionName="voice-bench-get-reservation:live", ...)
```
To invoke `$LATEST` (cold-start baseline):
```python
client.invoke(FunctionName="voice-bench-get-reservation", ...)
```

## Table names (env vars)

| Env var | Default | Description |
|---|---|---|
| `RESERVATIONS_TABLE` | `voice-bench-reservations` | Reservation records (PK `booking_id`) |
| `REFUND_RULES_TABLE` | `voice-bench-refund-rules` | Refund rules (PK `fare_type`, SK `payment_method`) |

## Sync note: standalone files vs. inline ZipFile

The standalone `.py` files in this directory and the `ZipFile` blocks in
`lambda-tools.yaml` contain equivalent logic (same function, same imports).
The standalone `.py` files are the authoritative source. After editing a
handler, update the corresponding `ZipFile` block in `lambda-tools.yaml`.
`infra/build_lambda_template.py` can regenerate the template from the .py files.
