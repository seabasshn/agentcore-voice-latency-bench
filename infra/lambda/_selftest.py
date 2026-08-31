"""
_selftest.py — local logic validation for voice-bench Lambda handlers.

Run with:  python3 infra/lambda/_selftest.py

Tests that do NOT require AWS credentials:
  - check_eligibility:   full rule matrix
  - execute_cancellation: timing + step recording

Tests that require AWS / moto:
  - get_reservation:     skipped if moto unavailable
  - get_refund_method:   skipped if moto unavailable

Exit code 0 on pass, 1 on failure.
"""
import importlib.util
import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

HERE = Path(__file__).parent


def _load(name):
    """Import a handler module by filename from this directory."""
    path = HERE / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── check_eligibility ─────────────────────────────────────────────────────────

class TestCheckEligibility(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load("check_eligibility")

    def call(self, **kwargs):
        return self.mod.handler(kwargs, None)

    def test_already_traveled_ineligible(self):
        r = self.call(booking_id="B1", already_traveled=True, is_group_booking=False,
                      pax_count=1, has_checked_bags=False, linked_minor=False)
        self.assertFalse(r["eligible"])
        self.assertIn("already traveled", r["reason"])
        self.assertFalse(r["requires_split"])
        self.assertEqual(r["warnings"], [])
        self.assertGreater(r["_execution_ms"], 0)

    def test_eligible_no_warnings(self):
        r = self.call(booking_id="B2", already_traveled=False, is_group_booking=False,
                      pax_count=1, has_checked_bags=False, linked_minor=False)
        self.assertTrue(r["eligible"])
        self.assertEqual(r["warnings"], [])
        self.assertFalse(r["requires_split"])

    def test_group_requires_split(self):
        r = self.call(booking_id="B3", already_traveled=False, is_group_booking=True,
                      pax_count=3, has_checked_bags=False, linked_minor=False)
        self.assertTrue(r["requires_split"])
        self.assertIn("Group booking", r["warnings"][-1])

    def test_group_solo_no_split(self):
        r = self.call(booking_id="B4", already_traveled=False, is_group_booking=True,
                      pax_count=1, has_checked_bags=False, linked_minor=False)
        self.assertFalse(r["requires_split"])

    def test_all_warnings(self):
        r = self.call(booking_id="B5", already_traveled=False, is_group_booking=True,
                      pax_count=2, has_checked_bags=True, linked_minor=True)
        self.assertEqual(len(r["warnings"]), 3)
        texts = " ".join(r["warnings"])
        self.assertIn("minor", texts)
        self.assertIn("baggage", texts.lower())
        self.assertIn("split", texts.lower())

    def test_body_wrapped_input(self):
        payload = {"body": json.dumps({
            "booking_id": "B6", "already_traveled": False, "is_group_booking": False,
            "pax_count": 1, "has_checked_bags": False, "linked_minor": False
        })}
        r = self.mod.handler(payload, None)
        self.assertTrue(r["eligible"])

    def test_already_traveled_ignores_group_flags(self):
        r = self.call(booking_id="B7", already_traveled=True, is_group_booking=True,
                      pax_count=5, has_checked_bags=True, linked_minor=True)
        self.assertFalse(r["eligible"])
        # requires_split is still computed (no short-circuit on the split flag)
        self.assertIsInstance(r["requires_split"], bool)


# ── execute_cancellation ──────────────────────────────────────────────────────

class TestExecuteCancellation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load("execute_cancellation")

    def call(self, booking_id, steps):
        return self.mod.handler({"booking_id": booking_id, "steps": steps}, None)

    def test_empty_steps(self):
        r = self.call("BK-001", [])
        self.assertTrue(r["success"])
        self.assertEqual(r["steps_completed"], [])
        self.assertIsInstance(r["total_execution_ms"], int)

    def test_one_step_timing(self):
        t0 = time.perf_counter()
        r = self.call("BK-002", ["process_refund"])
        elapsed = (time.perf_counter() - t0) * 1000
        self.assertEqual(r["steps_completed"], ["process_refund"])
        self.assertGreaterEqual(r["total_execution_ms"], 150)
        self.assertGreaterEqual(elapsed, 140)  # wall-clock sanity

    def test_three_steps_timing(self):
        steps = ["split_booking", "remove_passenger", "process_refund"]
        r = self.call("BK-003", steps)
        self.assertEqual(r["steps_completed"], steps)
        self.assertGreaterEqual(r["total_execution_ms"], 450)

    def test_booking_id_propagated(self):
        r = self.call("BK-XYZ", ["process_refund"])
        self.assertEqual(r["booking_id"], "BK-XYZ")

    def test_body_wrapped_input(self):
        payload = {"body": json.dumps({
            "booking_id": "BK-W", "steps": ["split_booking"]
        })}
        r = self.mod.handler(payload, None)
        self.assertTrue(r["success"])
        self.assertGreaterEqual(r["total_execution_ms"], 150)


# ── get_reservation & get_refund_method (moto or skip) ────────────────────────

def _try_moto():
    """Returns True if moto is importable."""
    try:
        import moto  # noqa: F401
        return True
    except ImportError:
        return False


class TestGetReservationMoto(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not _try_moto():
            raise unittest.SkipTest("moto not installed — skipping DynamoDB tests")
        import moto
        import boto3 as _boto3
        cls.mock_ddb = moto.mock_aws()
        cls.mock_ddb.start()
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["RESERVATIONS_TABLE"] = "voice-bench-reservations"
        ddb = _boto3.resource("dynamodb", region_name="us-east-1")
        ddb.create_table(
            TableName="voice-bench-reservations",
            KeySchema=[{"AttributeName": "booking_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "booking_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        table = ddb.Table("voice-bench-reservations")
        table.put_item(Item={"booking_id": "TEST-001", "status": "confirmed",
                             "passenger": "Alice", "flight": "AA100"})
        cls.mod = _load("get_reservation")

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "mock_ddb"):
            cls.mock_ddb.stop()

    def test_found(self):
        r = self.mod.handler({"booking_id": "TEST-001"}, None)
        self.assertEqual(r["booking_id"], "TEST-001")
        self.assertEqual(r["status"], "confirmed")
        self.assertIn("_execution_ms", r)

    def test_not_found(self):
        r = self.mod.handler({"booking_id": "GHOST-999"}, None)
        self.assertFalse(r["found"])
        self.assertEqual(r["booking_id"], "GHOST-999")


class TestGetRefundMethodMoto(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not _try_moto():
            raise unittest.SkipTest("moto not installed — skipping DynamoDB tests")
        import moto
        import boto3 as _boto3
        cls.mock_ddb = moto.mock_aws()
        cls.mock_ddb.start()
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["REFUND_RULES_TABLE"] = "voice-bench-refund-rules"
        ddb = _boto3.resource("dynamodb", region_name="us-east-1")
        ddb.create_table(
            TableName="voice-bench-refund-rules",
            KeySchema=[
                {"AttributeName": "fare_type", "KeyType": "HASH"},
                {"AttributeName": "payment_method", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "fare_type", "AttributeType": "S"},
                {"AttributeName": "payment_method", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        table = ddb.Table("voice-bench-refund-rules")
        table.put_item(Item={
            "fare_type": "economy", "payment_method": "credit_card",
            "refund_method": "original_payment", "timeline": "7-10 business days",
            "transferable": False, "expiry_note": "N/A",
        })
        cls.mod = _load("get_refund_method")

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "mock_ddb"):
            cls.mock_ddb.stop()

    def test_found(self):
        r = self.mod.handler(
            {"fare_type": "economy", "payment_method": "credit_card"}, None
        )
        self.assertEqual(r["refund_method"], "original_payment")
        self.assertIn("_execution_ms", r)

    def test_not_found(self):
        r = self.mod.handler(
            {"fare_type": "first_class", "payment_method": "crypto"}, None
        )
        self.assertFalse(r["found"])


if __name__ == "__main__":
    print(f"Python {sys.version}")
    print(f"moto available: {_try_moto()}")
    print()
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    # Always run these (no AWS needed)
    suite.addTests(loader.loadTestsFromTestCase(TestCheckEligibility))
    suite.addTests(loader.loadTestsFromTestCase(TestExecuteCancellation))
    # Run DynamoDB tests (skipped automatically if moto missing)
    suite.addTests(loader.loadTestsFromTestCase(TestGetReservationMoto))
    suite.addTests(loader.loadTestsFromTestCase(TestGetRefundMethodMoto))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
