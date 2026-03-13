"""Phase 4 Route Tests — verify all builder, telemetry, and block API routes."""
import sys, os, types, json, unittest, base64

# ── Mock Unix-only modules before importing app ──
fcntl_mock = types.ModuleType("fcntl")
fcntl_mock.flock = lambda *a, **k: None
fcntl_mock.LOCK_EX = 2
fcntl_mock.LOCK_NB = 4
sys.modules["fcntl"] = fcntl_mock

# Set auth credentials before importing app
os.environ["ADMIN_USERNAME"] = "admin"
os.environ["ADMIN_PASSWORD"] = "testpass"

# Add repo root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from database import db, init_db, EmailTemplate, Contact, AIRenderLog
from app import app

# Basic Auth header for all requests
AUTH = {"Authorization": "Basic " + base64.b64encode(b"admin:testpass").decode()}

SAMPLE_BLOCKS = [
    {"block_type": "hero", "content": {"headline": "Hello World", "subheadline": "Test"}},
    {"block_type": "text", "content": {"paragraphs": ["Body text here."]}},
    {"block_type": "cta", "content": {"button_text": "Click Me", "url": "https://example.com"}},
]


def _make_template(name="Test Template", family="welcome", blocks=None):
    """Create a blocks template and return it."""
    return EmailTemplate.create(
        name=name,
        subject="Subject {{first_name}}",
        html_body="<p>fallback</p>",
        template_format="blocks",
        blocks_json=json.dumps(blocks or SAMPLE_BLOCKS),
        template_family=family,
        ai_enabled=False,
    )


class Phase4RouteTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        db.init(":memory:")
        db.connect()
        init_db()
        app.config["TESTING"] = True
        cls.client = app.test_client()
        # Seed a contact
        cls.contact = Contact.create(
            email="test@example.com", first_name="Alice",
            last_name="Tester", subscribed=True,
        )

    @classmethod
    def tearDownClass(cls):
        db.close()

    # ── Page routes ──

    def test_new_blocks_page(self):
        r = self.client.get("/templates/new-blocks", headers=AUTH)
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Block Template", r.data)

    def test_edit_blocks_page(self):
        tpl = _make_template("Edit Page Test")
        r = self.client.get(f"/templates/{tpl.id}/edit-blocks", headers=AUTH)
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Edit Page Test", r.data)

    def test_templates_listing_shows_blocks_badge(self):
        _make_template("Badge Test")
        r = self.client.get("/templates", headers=AUTH)
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"BLOCKS", r.data)

    # ── Create/Save API ──

    def test_create_blocks_template(self):
        payload = {
            "name": "API Created",
            "subject": "New Subject",
            "preview_text": "Preview",
            "family": "welcome",
            "blocks": SAMPLE_BLOCKS,
            "ai_enabled": False,
        }
        r = self.client.post(
            "/api/templates/create-blocks",
            data=json.dumps(payload),
            content_type="application/json",
            headers=AUTH,
        )
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertTrue(data.get("success"), f"Create failed: {data}")
        self.assertIn("id", data)
        tpl = EmailTemplate.get_by_id(data["id"])
        self.assertEqual(tpl.template_format, "blocks")

    def test_save_blocks_template(self):
        tpl = _make_template("Save Test")
        payload = {
            "name": "Updated Name",
            "subject": "Updated Subject",
            "preview_text": "Updated preview",
            "family": "welcome",
            "blocks": SAMPLE_BLOCKS,
            "ai_enabled": True,
            "block_ai_overrides": {"0": True, "1": False},
        }
        r = self.client.post(
            f"/api/templates/{tpl.id}/save-blocks",
            data=json.dumps(payload),
            content_type="application/json",
            headers=AUTH,
        )
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertTrue(data.get("success"), f"Save failed: {data}")
        tpl_reloaded = EmailTemplate.get_by_id(tpl.id)
        self.assertEqual(tpl_reloaded.name, "Updated Name")
        self.assertTrue(tpl_reloaded.ai_enabled)

    def test_save_blocks_validation(self):
        tpl = _make_template("Validation Test")
        payload = {
            "name": "Bad Template",
            "subject": "Sub",
            "preview_text": "",
            "family": "welcome",
            "blocks": [
                {"block_type": "hero", "content": {"headline": "OK"}},
                {"block_type": "hero", "content": {"headline": "Dup hero"}},
                {"block_type": "hero", "content": {"headline": "Triple hero"}},
            ],
            "ai_enabled": False,
        }
        r = self.client.post(
            f"/api/templates/{tpl.id}/save-blocks",
            data=json.dumps(payload),
            content_type="application/json",
            headers=AUTH,
        )
        # Route should still return 200 (with errors/warnings in body)
        self.assertIn(r.status_code, [200, 400])

    # ── Preview routes ──

    def test_preview_blocks(self):
        tpl = _make_template("Preview Test")
        r = self.client.get(f"/api/templates/{tpl.id}/preview-blocks", headers=AUTH)
        self.assertEqual(r.status_code, 200)
        # Block content should render — check for the hero headline
        self.assertIn(b"Hello World", r.data)

    def test_preview_blocks_with_contact(self):
        tpl = _make_template("Contact Preview")
        # Create a fresh contact in the same DB context
        c = Contact.create(
            email="preview@test.com", first_name="Bob",
            last_name="Preview", subscribed=True,
        )
        r = self.client.get(
            f"/api/templates/{tpl.id}/preview-blocks?contact_id={c.id}",
            headers=AUTH,
        )
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Hello World", r.data)

    # ── Telemetry routes ──

    def test_telemetry_page(self):
        r = self.client.get("/telemetry", headers=AUTH)
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Telemetry", r.data)

    def test_telemetry_data_api(self):
        AIRenderLog.create(
            template_id=1, field_name="headline",
            generated_text="Test", fallback_used=False,
            render_ms=42, model_name="claude-sonnet",
        )
        r = self.client.get("/api/telemetry/data", headers=AUTH)
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertIn("ai_usage", data)
        self.assertGreaterEqual(data["ai_usage"]["total"], 1)


if __name__ == "__main__":
    unittest.main()
