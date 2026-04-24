"""
test_discount_engine.py — Unit tests for discount_engine + AM↔discount wiring.

Tests:
  1. TestDiscountStrategies — DISCOUNT_STRATEGIES shape + required keys
  2. TestAMDiscountMappingInvariant — every AM action's discount purpose
     must have a matching DISCOUNT_STRATEGIES entry (regression guard for
     the 'Unknown discount purpose: cross_sell' bug)

Run:  python -m pytest tests/test_discount_engine.py -v
"""

import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)


class TestDiscountStrategies:
    """DISCOUNT_STRATEGIES dict invariants."""

    def test_all_strategies_have_required_keys(self):
        """Every strategy must define type, value, expires_hours, prefix."""
        from discount_engine import DISCOUNT_STRATEGIES
        required = {"type", "value", "expires_hours", "prefix"}
        for purpose, strategy in DISCOUNT_STRATEGIES.items():
            missing = required - set(strategy.keys())
            assert not missing, "strategy %r missing keys: %s" % (purpose, missing)

    def test_all_types_are_supported(self):
        """Only 'percentage' and 'free_shipping' are supported by Shopify price rules."""
        from discount_engine import DISCOUNT_STRATEGIES
        supported = {"percentage", "free_shipping"}
        for purpose, strategy in DISCOUNT_STRATEGIES.items():
            assert strategy["type"] in supported, (
                "strategy %r has unsupported type %r" % (purpose, strategy["type"])
            )

    def test_cross_sell_strategy_exists(self):
        """Regression: cross_sell was missing, causing 7 'Unknown discount purpose:
        cross_sell' warnings per 4 days at the nightly AM batch — emails went
        out without a discount code attached.
        """
        from discount_engine import DISCOUNT_STRATEGIES
        assert "cross_sell" in DISCOUNT_STRATEGIES, (
            "cross_sell strategy is required — am_runtime.AM_ACTION_TO_DISCOUNT_PURPOSE "
            "maps the 'cross_sell' action to this purpose"
        )
        s = DISCOUNT_STRATEGIES["cross_sell"]
        assert s["type"] == "percentage"
        assert s["value"] == "5"
        assert s["expires_hours"] == 168  # 7 days
        assert s["prefix"] == "XSELL"


class TestAMDiscountMappingInvariant:
    """The AM layer maps action types to discount purposes. Every non-None
    mapping must resolve to a real DISCOUNT_STRATEGIES entry, otherwise
    generate_discount_code returns None and emails ship without a code.
    """

    def test_every_am_action_discount_purpose_resolves(self):
        """Regression guard: prevent future mapping drift between
        am_runtime.AM_ACTION_TO_DISCOUNT_PURPOSE and
        discount_engine.DISCOUNT_STRATEGIES.
        """
        from am_runtime import AM_ACTION_TO_DISCOUNT_PURPOSE
        from discount_engine import DISCOUNT_STRATEGIES

        for action, purpose in AM_ACTION_TO_DISCOUNT_PURPOSE.items():
            if purpose is None:
                continue  # action intentionally has no discount (e.g., education)
            assert purpose in DISCOUNT_STRATEGIES, (
                "AM action %r maps to discount purpose %r but that purpose is not "
                "in DISCOUNT_STRATEGIES — emails would ship without a code" % (action, purpose)
            )


class TestRetryDB:
    """retry_db is the shared DB-lock retry helper used by discount_engine and
    profit_engine. It must retry on SQLite 'database is locked' / 'busy'
    errors with backoff, give up after max_retries, and not retry on other
    errors.
    """

    def test_succeeds_on_first_try(self):
        from discount_engine import retry_db
        calls = [0]

        def _op():
            calls[0] += 1
            return "ok"

        assert retry_db(_op) == "ok"
        assert calls[0] == 1

    def test_retries_on_database_locked(self):
        from discount_engine import retry_db
        from peewee import OperationalError

        calls = [0]

        def _op():
            calls[0] += 1
            if calls[0] < 3:
                raise OperationalError("database is locked")
            return "recovered"

        # base_delay tiny so test is fast
        result = retry_db(_op, max_retries=5, base_delay=0.001)
        assert result == "recovered"
        assert calls[0] == 3  # 2 failures + 1 success

    def test_gives_up_after_max_retries(self):
        from discount_engine import retry_db
        from peewee import OperationalError
        import pytest as _pt

        calls = [0]

        def _op():
            calls[0] += 1
            raise OperationalError("database is locked")

        with _pt.raises(OperationalError):
            retry_db(_op, max_retries=2, base_delay=0.001)
        # max_retries=2 → 3 total attempts (initial + 2 retries)
        assert calls[0] == 3

    def test_does_not_retry_on_non_lock_operational_error(self):
        from discount_engine import retry_db
        from peewee import OperationalError
        import pytest as _pt

        calls = [0]

        def _op():
            calls[0] += 1
            raise OperationalError("no such column: foo")  # schema error, not lock

        with _pt.raises(OperationalError):
            retry_db(_op, max_retries=5, base_delay=0.001)
        assert calls[0] == 1  # no retries

    def test_propagates_non_db_exceptions_immediately(self):
        from discount_engine import retry_db
        import pytest as _pt

        calls = [0]

        def _op():
            calls[0] += 1
            raise ValueError("oops")

        with _pt.raises(ValueError):
            retry_db(_op, max_retries=5, base_delay=0.001)
        assert calls[0] == 1  # no retries

    def test_backcompat_underscore_name_still_works(self):
        """_retry_db alias preserved for any pre-rename callers."""
        from discount_engine import _retry_db, retry_db
        assert _retry_db is retry_db


class TestProfitEngineUsesRetry:
    """profit_engine imports retry_db and uses it around ProductCommercial writes."""

    def test_profit_engine_imports_retry_db(self):
        """Static guarantee that profit_engine depends on retry_db so a lock
        error during the nightly run doesn't kill the whole scoring pass.
        """
        import profit_engine
        assert hasattr(profit_engine, "retry_db"), (
            "profit_engine must import retry_db from discount_engine"
        )
