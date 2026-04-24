"""
test_identity_resolution.py — Unit tests for identity_resolution.py

Focused on the fast-path send used for popup subscribers, which bypasses
the normal flow processor and therefore has to re-implement every piece
of the send contract itself (including tracking pixels).

Run:  python -m pytest tests/test_identity_resolution.py -v
"""

import os
import sys
from datetime import datetime, timedelta
from unittest.mock import patch

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)


class TestSendWelcomeStep1Immediately:
    """_send_welcome_step1_immediately is the fast path for popup subscribers.

    It must produce an email that is indistinguishable from one sent by the
    normal flow processor (app.py:_process_flow_enrollments). Any diff
    between the two paths will drift open-rate metrics, tracking, deliverability
    headers, etc.
    """

    def test_enqueued_html_contains_flow_open_tracking_pixel(
        self, in_memory_db, make_contact, make_template, make_flow
    ):
        """Regression: between 2026-04-09 and 2026-04-24, every popup welcome
        email went out without an open-tracking pixel. Welcome Step 1 open
        rate read as 0.0% for 2 straight weeks across 111 emails. The fix
        adds the pixel in _send_welcome_step1_immediately. This test fails
        if the pixel is ever dropped again.
        """
        from database import (
            EmailTemplate, Flow, FlowStep, FlowEnrollment,
            Contact, DeliveryQueue,
        )

        # Minimal fixture: welcome flow, step 1, contact, enrollment
        tpl = EmailTemplate.create(
            name="Welcome test",
            subject="Welcome {{first_name}}",
            html_body="<p>Hi {{first_name}}</p>",
            template_format="html",  # skip block renderer
            template_family="welcome",
        )
        flow = Flow.create(
            name="Welcome Series",
            trigger_type="contact_created",
            is_active=True,
            priority=5,
        )
        step = FlowStep.create(
            flow=flow, step_order=1, delay_hours=0,
            template=tpl, from_name="LDAS", from_email="hi@ldas.ca",
        )
        contact = make_contact(email="popup@example.com")
        enrollment = FlowEnrollment.create(
            flow=flow, contact=contact, current_step=1,
            next_send_at=datetime.now(), status="active",
        )

        # Stub discount_engine.generate_discount_code so we don't hit Shopify
        fake_discount = {"code": "TESTCODE", "value": "5",
                         "discount_type": "percentage",
                         "expires_at": datetime.now() + timedelta(days=10)}

        from identity_resolution import _send_welcome_step1_immediately

        with patch("discount_engine.generate_discount_code",
                   return_value=fake_discount):
            _send_welcome_step1_immediately(enrollment, flow, step, contact)

        # Exactly one DeliveryQueue row was created for this enrollment
        queued = list(DeliveryQueue.select().where(
            DeliveryQueue.enrollment_id == enrollment.id,
            DeliveryQueue.step_id == step.id,
        ))
        assert len(queued) == 1, (
            "Expected 1 queue entry for enrollment, got %d" % len(queued)
        )

        html = queued[0].html or ""

        # The regression we're guarding against
        assert "/track/flow-open/" in html, (
            "Flow welcome Step 1 HTML is missing the open-tracking pixel. "
            "Without /track/flow-open/<token>, opens cannot be recorded and "
            "welcome open rate will appear as 0%. Fix: re-add the pixel "
            "injection in identity_resolution._send_welcome_step1_immediately."
        )

        # And it must be a 1x1 img tag, not just a raw URL
        assert 'width="1"' in html and 'height="1"' in html, (
            "Open pixel must be a 1x1 img tag so clients actually request it"
        )

    def test_enqueued_html_contains_unsubscribe_url(
        self, in_memory_db, make_contact
    ):
        """Sanity: the unsubscribe URL must also be present for RFC 8058
        compliance + user trust. Same class of bug could drop it.
        """
        from database import (
            EmailTemplate, Flow, FlowStep, FlowEnrollment, DeliveryQueue,
        )

        tpl = EmailTemplate.create(
            name="Welcome test 2",
            subject="Welcome",
            html_body="<p>Hi</p>",
            template_format="html",
            template_family="welcome",
        )
        flow = Flow.create(
            name="Welcome Series 2",
            trigger_type="contact_created",
            is_active=True,
            priority=5,
        )
        step = FlowStep.create(
            flow=flow, step_order=1, delay_hours=0,
            template=tpl, from_name="LDAS", from_email="hi@ldas.ca",
        )
        contact = make_contact(email="popup2@example.com")
        enrollment = FlowEnrollment.create(
            flow=flow, contact=contact, current_step=1,
            next_send_at=datetime.now(), status="active",
        )

        from identity_resolution import _send_welcome_step1_immediately
        with patch("discount_engine.generate_discount_code", return_value=None):
            _send_welcome_step1_immediately(enrollment, flow, step, contact)

        dq = DeliveryQueue.get(
            (DeliveryQueue.enrollment_id == enrollment.id)
            & (DeliveryQueue.step_id == step.id)
        )
        # unsubscribe_url is a queue column, not necessarily in HTML
        assert dq.unsubscribe_url and "/unsubscribe/" in dq.unsubscribe_url, (
            "DeliveryQueue.unsubscribe_url must contain a signed unsub token"
        )
