"""
Test fixtures for MailEngineHub end-to-end scenario tests.

Uses an in-memory SQLite database so tests never touch production data
and never call SES. SystemConfig defaults to shadow mode.
"""

import os
import sys
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

# Add project root to path
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)


@pytest.fixture(autouse=True)
def in_memory_db():
    """Swap the Peewee database to :memory: for every test.
    Creates all tables fresh, tears down after each test.
    """
    import database as db_module
    from peewee import SqliteDatabase

    # Create in-memory database
    test_db = SqliteDatabase(":memory:", pragmas={"foreign_keys": 1})

    # Monkey-patch the module-level db object
    original_db = db_module.db
    db_module.db = test_db

    # Rebind all models to the test database
    models = []
    for name in dir(db_module):
        obj = getattr(db_module, name)
        if isinstance(obj, type) and hasattr(obj, '_meta') and hasattr(obj._meta, 'database'):
            if obj.__name__ != 'BaseModel':
                obj._meta.database = test_db
                models.append(obj)

    # Also rebind BaseModel so any new queries use test_db
    db_module.BaseModel._meta.database = test_db

    test_db.connect()
    test_db.create_tables(models, safe=True)

    # Ensure SystemConfig singleton exists (shadow mode by default)
    try:
        db_module.SystemConfig.create(id=1, delivery_mode="shadow", updated_at=datetime.now())
    except Exception:
        pass

    # Ensure WarmupConfig singleton exists
    try:
        db_module.WarmupConfig.create(
            id=1, is_active=False, current_phase=1,
            emails_sent_today=0, last_reset_date="",
        )
    except Exception:
        pass

    yield test_db

    test_db.close()

    # Restore original database bindings
    db_module.db = original_db
    db_module.BaseModel._meta.database = original_db
    for model in models:
        model._meta.database = original_db


@pytest.fixture
def make_contact():
    """Factory to create test contacts."""
    from database import Contact
    _counter = [0]

    def _make(email=None, subscribed=True, first_name="Test", last_name="User", **kw):
        _counter[0] += 1
        if email is None:
            email = "test%d@example.com" % _counter[0]
        return Contact.create(
            email=email, subscribed=subscribed,
            first_name=first_name, last_name=last_name,
            **kw
        )
    return _make


@pytest.fixture
def make_template():
    """Factory to create test email templates."""
    from database import EmailTemplate
    _counter = [0]

    def _make(name=None, subject="Test Subject {{first_name}}", html="<p>Hello {{first_name}}</p>"):
        _counter[0] += 1
        if name is None:
            name = "Test Template %d" % _counter[0]
        return EmailTemplate.create(name=name, subject=subject, html_body=html)
    return _make


@pytest.fixture
def make_flow(make_template):
    """Factory to create test flows with steps."""
    from database import Flow, FlowStep

    def _make(trigger_type, trigger_value="", steps=1, priority=5):
        flow = Flow.create(
            name="Test %s Flow" % trigger_type,
            trigger_type=trigger_type,
            trigger_value=trigger_value,
            is_active=True,
            priority=priority,
        )
        for i in range(steps):
            template = make_template()
            FlowStep.create(
                flow=flow, step_order=i + 1, delay_hours=0,
                template=template, from_name="LDAS Electronics",
                from_email="test@ldas.ca",
            )
        return flow
    return _make


@pytest.fixture
def mock_ses():
    """Mock send_campaign_email to always succeed without calling SES."""
    with patch("email_sender.send_campaign_email") as mock:
        mock.return_value = (True, None, "mock-msg-id-12345")
        yield mock
