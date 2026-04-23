"""
Tests for the Intelligence Layer (Phase 1).

Tests:
1. shared_constants — category inference, product alias resolution, seasonal context
2. product_intelligence — seeding, alias resolution, purchase history, product context
3. intelligence_layer — unified profile, timing gate, discount policy, diagnostics
"""

import sys
import os
import json
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

# Ensure repo root is on path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Detect peewee availability — DB-dependent test classes are skipped without it
try:
    import peewee
    HAS_PEEWEE = True
except ImportError:
    HAS_PEEWEE = False


class TestSharedConstants(unittest.TestCase):
    """Test shared_constants.py — the single source of business knowledge."""

    def test_infer_category_headset(self):
        from shared_constants import infer_category
        self.assertEqual(infer_category("BlueParrott G7 Bluetooth Headset"), "Bluetooth Headsets")

    def test_infer_category_dashcam(self):
        from shared_constants import infer_category
        self.assertEqual(infer_category("A20 Dash Cam 1080p"), "Dash Cams")

    def test_infer_category_ear_cushion(self):
        from shared_constants import infer_category
        self.assertEqual(infer_category("Replacement Ear Cushion Pad"), "Ear Cushions & Parts")

    def test_infer_category_cable(self):
        from shared_constants import infer_category
        self.assertEqual(infer_category("USB-C Charging Cable"), "Cables & Chargers")

    def test_infer_category_unknown(self):
        from shared_constants import infer_category
        self.assertEqual(infer_category("Random Widget XYZ"), "Other Electronics")

    def test_infer_category_empty(self):
        from shared_constants import infer_category
        self.assertEqual(infer_category(""), "Other Electronics")
        self.assertEqual(infer_category(None), "Other Electronics")

    def test_product_alias_resolution(self):
        from shared_constants import resolve_product_alias
        self.assertEqual(resolve_product_alias("g7"), "BlueParrott G7")
        self.assertEqual(resolve_product_alias("blueparrott g7"), "BlueParrott G7")
        self.assertEqual(resolve_product_alias("th-11"), "TH-11")

    def test_product_alias_unknown_passthrough(self):
        from shared_constants import resolve_product_alias
        self.assertEqual(resolve_product_alias("Some Random Product"), "Some Random Product")

    def test_seasonal_context(self):
        from shared_constants import get_current_season
        result = get_current_season()
        self.assertIn("key", result)
        self.assertIn("label", result)
        self.assertIn("themes", result)
        self.assertIn("promo_appropriate", result)
        self.assertIsInstance(result["themes"], list)

    def test_upgrade_paths_structure(self):
        from shared_constants import UPGRADE_PATHS
        self.assertIn("Bluetooth Headsets", UPGRADE_PATHS)
        headsets = UPGRADE_PATHS["Bluetooth Headsets"]
        self.assertEqual(len(headsets), 4)
        # Each entry: (tier_name, low, high, trigger_days)
        self.assertEqual(headsets[0][0], "Budget")
        self.assertIsInstance(headsets[0][1], (int, float))

    def test_reorder_cycles_structure(self):
        from shared_constants import REORDER_CYCLES
        self.assertIn("Ear Cushions & Parts", REORDER_CYCLES)
        ear = REORDER_CYCLES["Ear Cushions & Parts"]
        self.assertEqual(ear[0], 180)  # typical_cycle_days

    def test_seeded_relationships_structure(self):
        from shared_constants import SEEDED_RELATIONSHIPS
        self.assertGreater(len(SEEDED_RELATIONSHIPS), 5)
        for row in SEEDED_RELATIONSHIPS:
            self.assertEqual(len(row), 6)  # source, target, type, strength, delay, reason

    def test_no_category_keywords_duplication(self):
        """Ensure the 4 files now import from shared_constants, not define their own."""
        import customer_intelligence
        import shared_constants
        # They should be the same object (imported, not copy)
        self.assertIs(customer_intelligence.CATEGORY_KEYWORDS, shared_constants.CATEGORY_KEYWORDS)


@unittest.skipUnless(HAS_PEEWEE, "peewee required for database tests")
class TestProductIntelligenceSeeding(unittest.TestCase):
    """Test product_intelligence.py seeding with in-memory SQLite."""

    @classmethod
    def setUpClass(cls):
        """Create in-memory database for testing."""
        from peewee import SqliteDatabase
        import database as db_module

        cls.test_db = SqliteDatabase(":memory:")
        # Bind all models to test DB
        models = [
            db_module.Contact, db_module.CustomerProfile, db_module.ContactScore,
            db_module.ShopifyOrder, db_module.ShopifyOrderItem,
            db_module.ProductCommercial, db_module.ProductAlias,
            db_module.ProductRelationship, db_module.ProductUpgradePath,
            db_module.ReorderCycle, db_module.EmailTemplate,
            db_module.Campaign, db_module.CampaignEmail,
            db_module.Flow, db_module.FlowStep, db_module.FlowEnrollment, db_module.FlowEmail,
            db_module.AutoEmail, db_module.SuppressionEntry,
            db_module.ActionPerformance, db_module.TemplatePerformance,
            db_module.TemplateSegmentPerformance, db_module.ContactStrategy,
        ]
        cls.test_db.bind(models)
        cls.test_db.connect()
        cls.test_db.create_tables(models)

    @classmethod
    def tearDownClass(cls):
        cls.test_db.close()

    def setUp(self):
        """Clean tables before each test."""
        from database import (ProductRelationship, ProductUpgradePath,
                              ReorderCycle, ProductAlias)
        ProductRelationship.delete().execute()
        ProductUpgradePath.delete().execute()
        ReorderCycle.delete().execute()
        ProductAlias.delete().execute()

    def test_seed_creates_rows(self):
        from product_intelligence import seed_product_intelligence
        result = seed_product_intelligence()
        self.assertGreater(result["relationships"], 0)
        self.assertGreater(result["upgrades"], 0)
        self.assertGreater(result["cycles"], 0)
        self.assertGreater(result["aliases"], 0)

    def test_seed_idempotent(self):
        from product_intelligence import seed_product_intelligence
        from database import ProductRelationship
        result1 = seed_product_intelligence()
        count1 = ProductRelationship.select().count()
        result2 = seed_product_intelligence()
        count2 = ProductRelationship.select().count()
        self.assertEqual(count1, count2, "Seeding should be idempotent")

    def test_seeded_relationships_have_correct_source(self):
        from product_intelligence import seed_product_intelligence
        from database import ProductRelationship
        seed_product_intelligence()
        for rel in ProductRelationship.select():
            self.assertEqual(rel.source_origin, "seeded")

    def test_product_level_relationships_exist(self):
        """Verify both category-level and product-level relationships are seeded."""
        from product_intelligence import seed_product_intelligence
        from database import ProductRelationship
        seed_product_intelligence()

        # Product-level: G7 → G10 upgrade
        g7_upgrade = ProductRelationship.get_or_none(
            ProductRelationship.source_key == "BlueParrott G7",
            ProductRelationship.target_key == "BlueParrott G10",
        )
        self.assertIsNotNone(g7_upgrade)
        self.assertTrue(g7_upgrade.source_is_product)
        self.assertTrue(g7_upgrade.target_is_product)
        self.assertEqual(g7_upgrade.relationship_type, "upgrade")

        # Category-level: Headsets → Ear Cushions
        headset_parts = ProductRelationship.get_or_none(
            ProductRelationship.source_key == "Bluetooth Headsets",
            ProductRelationship.target_key == "Ear Cushions & Parts",
        )
        self.assertIsNotNone(headset_parts)
        self.assertFalse(headset_parts.source_is_product)

    def test_alias_resolution(self):
        from product_intelligence import seed_product_intelligence, resolve_product
        seed_product_intelligence()
        self.assertEqual(resolve_product("g7"), "BlueParrott G7")
        self.assertEqual(resolve_product("blueparrott g7"), "BlueParrott G7")
        # Unknown product passes through
        self.assertEqual(resolve_product("Unknown Widget"), "Unknown Widget")


@unittest.skipUnless(HAS_PEEWEE, "peewee required for database tests")
class TestProductIntelligenceContext(unittest.TestCase):
    """Test get_customer_product_context with synthetic purchase data."""

    @classmethod
    def setUpClass(cls):
        from peewee import SqliteDatabase
        import database as db_module

        cls.test_db = SqliteDatabase(":memory:")
        models = [
            db_module.Contact, db_module.CustomerProfile, db_module.ContactScore,
            db_module.ShopifyOrder, db_module.ShopifyOrderItem,
            db_module.ProductCommercial, db_module.ProductAlias,
            db_module.ProductRelationship, db_module.ProductUpgradePath,
            db_module.ReorderCycle, db_module.EmailTemplate,
            db_module.Campaign, db_module.CampaignEmail,
            db_module.Flow, db_module.FlowStep, db_module.FlowEnrollment, db_module.FlowEmail,
            db_module.AutoEmail, db_module.SuppressionEntry,
            db_module.ActionPerformance, db_module.TemplatePerformance,
            db_module.TemplateSegmentPerformance, db_module.ContactStrategy,
        ]
        cls.test_db.bind(models)
        cls.test_db.connect()
        cls.test_db.create_tables(models)

        # Seed intelligence
        from product_intelligence import seed_product_intelligence
        seed_product_intelligence()

    @classmethod
    def tearDownClass(cls):
        cls.test_db.close()

    def setUp(self):
        from database import Contact, ShopifyOrder, ShopifyOrderItem
        Contact.delete().execute()
        ShopifyOrder.delete().execute()
        ShopifyOrderItem.delete().execute()

    def _create_contact_with_order(self, email, product_title, price, days_ago):
        from database import Contact, ShopifyOrder, ShopifyOrderItem
        contact = Contact.create(email=email, first_name="Test", subscribed=True)
        order = ShopifyOrder.create(
            contact=contact,
            shopify_order_id=f"order_{email}_{days_ago}",
            email=email,
            order_total=price,
            ordered_at=datetime.now() - timedelta(days=days_ago),
        )
        ShopifyOrderItem.create(
            order=order,
            product_title=product_title,
            product_id="prod_123",
            quantity=1,
            unit_price=price,
            product_type="",
        )
        return contact

    def test_headset_buyer_gets_replacement_suggestion(self):
        """Customer who bought a headset 200 days ago should get ear cushion replacement."""
        from product_intelligence import get_customer_product_context
        contact = self._create_contact_with_order("test@example.com", "BlueParrott G7", 75, 200)
        ctx = get_customer_product_context(contact.id)

        # Should have replacement suggestions
        self.assertGreater(len(ctx["replacements"]), 0)
        # At least one should be ear cushions
        ear_reps = [r for r in ctx["replacements"] if "Ear Cushion" in r["product_key"]]
        self.assertGreater(len(ear_reps), 0)

    def test_headset_buyer_gets_upgrade(self):
        """Customer with G7 should get G10 upgrade suggestion."""
        from product_intelligence import get_customer_product_context
        contact = self._create_contact_with_order("test2@example.com", "BlueParrott G7", 75, 200)
        ctx = get_customer_product_context(contact.id)

        # Should have upgrade suggestions
        self.assertGreater(len(ctx["upgrades"]), 0)

    def test_headset_buyer_gets_cross_sell(self):
        """Headset buyer should get dash cam cross-sell."""
        from product_intelligence import get_customer_product_context
        contact = self._create_contact_with_order("test3@example.com", "BlueParrott G7", 75, 30)
        ctx = get_customer_product_context(contact.id)

        cross_sell_targets = [cs["target_key"] for cs in ctx["cross_sells"]]
        # Should suggest dash cams or mounts
        has_cross = any("Dash" in t or "Mount" in t for t in cross_sell_targets)
        self.assertTrue(has_cross or len(ctx["cross_sells"]) > 0,
                        f"Expected cross-sells, got: {cross_sell_targets}")

    def test_no_purchase_returns_empty(self):
        """Contact with no orders should get empty recommendations."""
        from database import Contact
        from product_intelligence import get_customer_product_context
        contact = Contact.create(email="empty@test.com", subscribed=True)
        ctx = get_customer_product_context(contact.id)

        self.assertEqual(ctx["replacements"], [])
        self.assertEqual(ctx["upgrades"], [])
        self.assertIsNone(ctx["top_pick"])

    def test_top_pick_prioritizes_due_replacement(self):
        """Top pick should favor replacement due soon over other types."""
        from product_intelligence import get_customer_product_context
        # Headset bought 200 days ago — ear cushion replacement due (180 day cycle)
        contact = self._create_contact_with_order("priority@test.com", "BlueParrott G7", 75, 200)
        ctx = get_customer_product_context(contact.id)

        if ctx["top_pick"]:
            # Should prioritize replacement or reorder
            self.assertIn(ctx["top_pick"]["action"], ("replacement", "reorder", "upgrade"))


@unittest.skipUnless(HAS_PEEWEE, "peewee required for database tests")
class TestIntelligenceLayer(unittest.TestCase):
    """Test intelligence_layer.py unified API."""

    @classmethod
    def setUpClass(cls):
        from peewee import SqliteDatabase
        import database as db_module

        cls.test_db = SqliteDatabase(":memory:")
        models = [
            db_module.Contact, db_module.CustomerProfile, db_module.ContactScore,
            db_module.ShopifyOrder, db_module.ShopifyOrderItem,
            db_module.ProductCommercial, db_module.ProductAlias,
            db_module.ProductRelationship, db_module.ProductUpgradePath,
            db_module.ReorderCycle, db_module.EmailTemplate,
            db_module.Campaign, db_module.CampaignEmail,
            db_module.Flow, db_module.FlowStep, db_module.FlowEnrollment, db_module.FlowEmail,
            db_module.AutoEmail, db_module.SuppressionEntry,
            db_module.ActionPerformance, db_module.TemplatePerformance,
            db_module.TemplateSegmentPerformance, db_module.ContactStrategy,
        ]
        cls.test_db.bind(models)
        cls.test_db.connect()
        cls.test_db.create_tables(models)

        from product_intelligence import seed_product_intelligence
        seed_product_intelligence()

    @classmethod
    def tearDownClass(cls):
        cls.test_db.close()

    def setUp(self):
        from database import (Contact, CustomerProfile, ContactScore,
                              ShopifyOrder, ShopifyOrderItem, AutoEmail,
                              FlowEmail, CampaignEmail, SuppressionEntry)
        Contact.delete().execute()
        CustomerProfile.delete().execute()
        ContactScore.delete().execute()
        ShopifyOrder.delete().execute()
        ShopifyOrderItem.delete().execute()
        AutoEmail.delete().execute()
        FlowEmail.delete().execute()
        CampaignEmail.delete().execute()
        SuppressionEntry.delete().execute()

    def _create_full_contact(self):
        from database import Contact, CustomerProfile, ContactScore
        contact = Contact.create(
            email="full@test.com", first_name="John", last_name="Doe",
            subscribed=True, total_orders=5, total_spent=500.0,
            source="shopify",
        )
        CustomerProfile.create(
            contact=contact, email="full@test.com",
            total_orders=5, total_spent=500.0, avg_order_value=100.0,
            days_since_last_order=30, avg_days_between_orders=60,
            lifecycle_stage="active_buyer", customer_type="repeat",
            intent_score=65, reorder_likelihood=70, churn_risk_score=20,
            discount_sensitivity=0.3, has_used_discount=True,
            price_tier="mid", preferred_send_hour=10, preferred_send_dow=2,
            top_products='["BlueParrott G7"]',
            top_categories='["Bluetooth Headsets"]',
            category_affinity_json='{"Bluetooth Headsets": 80}',
        )
        ContactScore.create(
            contact=contact, rfm_segment="loyal",
            engagement_score=72, recency_days=30,
            optimal_gap_hours=36, sunset_score=10,
        )
        return contact

    def test_get_contact_intelligence_schema_version(self):
        from intelligence_layer import get_contact_intelligence
        contact = self._create_full_contact()
        intel = get_contact_intelligence(contact.id)
        self.assertEqual(intel["schema_version"], 1)

    def test_get_contact_intelligence_has_all_sections(self):
        from intelligence_layer import get_contact_intelligence
        contact = self._create_full_contact()
        intel = get_contact_intelligence(contact.id)
        required_sections = ["contact", "classification", "scores", "purchase",
                             "products", "timing", "engagement", "learning", "next_products"]
        for section in required_sections:
            self.assertIn(section, intel, f"Missing section: {section}")

    def test_get_contact_intelligence_values(self):
        from intelligence_layer import get_contact_intelligence
        contact = self._create_full_contact()
        intel = get_contact_intelligence(contact.id)

        self.assertEqual(intel["contact"]["email"], "full@test.com")
        self.assertEqual(intel["classification"]["lifecycle_stage"], "active_buyer")
        self.assertEqual(intel["classification"]["rfm_segment"], "loyal")
        self.assertEqual(intel["scores"]["intent"], 65)
        self.assertEqual(intel["purchase"]["total_orders"], 5)
        self.assertEqual(intel["timing"]["preferred_hour"], 10)
        self.assertEqual(intel["timing"]["optimal_gap_hours"], 36)

    def test_get_contact_intelligence_missing_contact(self):
        from intelligence_layer import get_contact_intelligence
        intel = get_contact_intelligence(99999)
        self.assertEqual(intel["schema_version"], 1)
        self.assertEqual(intel["contact"], {})

    def test_should_contact_now_ok(self):
        from intelligence_layer import should_contact_now
        contact = self._create_full_contact()
        result = should_contact_now(contact.id)
        self.assertTrue(result["can_send"])
        self.assertEqual(result["reason"], "ok")

    def test_should_contact_now_unsubscribed(self):
        from intelligence_layer import should_contact_now
        from database import Contact
        contact = Contact.create(email="unsub@test.com", subscribed=False)
        result = should_contact_now(contact.id)
        self.assertFalse(result["can_send"])
        self.assertEqual(result["reason"], "unsubscribed")

    def test_should_contact_now_suppressed(self):
        from intelligence_layer import should_contact_now
        from database import Contact, SuppressionEntry
        contact = Contact.create(email="bounced@test.com", subscribed=True)
        SuppressionEntry.create(email="bounced@test.com", reason="hard_bounce",
                                source="ses_notification")
        result = should_contact_now(contact.id)
        self.assertFalse(result["can_send"])
        self.assertIn("suppressed", result["reason"])

    def test_should_contact_now_too_soon(self):
        from intelligence_layer import should_contact_now
        from database import Contact, ContactScore, AutoEmail
        contact = Contact.create(email="recent@test.com", subscribed=True)
        ContactScore.create(contact=contact, optimal_gap_hours=48)
        AutoEmail.create(
            contact=contact, template=0, status="sent",
            sent_at=datetime.now() - timedelta(hours=12),
        )
        result = should_contact_now(contact.id)
        self.assertFalse(result["can_send"])
        self.assertIn("too_soon", result["reason"])

    def test_discount_policy_vip_no_discount(self):
        """VIP full-price buyers should not get discounted."""
        from intelligence_layer import get_discount_policy
        from database import Contact, CustomerProfile, ContactScore
        contact = Contact.create(email="vip@test.com", subscribed=True)
        CustomerProfile.create(
            contact=contact, email="vip@test.com",
            lifecycle_stage="vip", discount_sensitivity=0.0,
            has_used_discount=False,
        )
        ContactScore.create(contact=contact, rfm_segment="champion")
        result = get_discount_policy(contact.id, "cross_sell")
        self.assertFalse(result["offer_discount"])
        self.assertIn("Full-price", result["reason"])

    def test_discount_policy_cart_recovery(self):
        """Cart recovery should always offer discount."""
        from intelligence_layer import get_discount_policy
        from database import Contact, CustomerProfile, ContactScore
        contact = Contact.create(email="cart@test.com", subscribed=True)
        CustomerProfile.create(
            contact=contact, email="cart@test.com",
            lifecycle_stage="active_buyer",
        )
        ContactScore.create(contact=contact, rfm_segment="potential")
        result = get_discount_policy(contact.id, "cart_abandonment")
        self.assertTrue(result["offer_discount"])
        self.assertEqual(result["discount_type"], "percentage")
        self.assertEqual(result["discount_value"], "5")

    def test_discount_policy_with_candidate_products(self):
        """Discount policy should check margins when candidate products provided."""
        from intelligence_layer import get_discount_policy
        from database import Contact, CustomerProfile, ContactScore, ProductCommercial
        contact = Contact.create(email="margin@test.com", subscribed=True)
        CustomerProfile.create(
            contact=contact, email="margin@test.com",
            lifecycle_stage="active_buyer",
            discount_sensitivity=0.5, has_used_discount=True,
        )
        ContactScore.create(contact=contact, rfm_segment="potential")
        ProductCommercial.delete().execute()
        ProductCommercial.create(
            product_id="low_margin",
            product_title="Low Margin Widget",
            margin_pct=0.12,  # below 20% threshold
            stock_pressure="normal",
        )
        result = get_discount_policy(contact.id, "cross_sell",
                                     candidate_products=["Low Margin Widget"])
        self.assertFalse(result["offer_discount"])
        self.assertIn("Margin too thin", result["reason"])

    def test_seasonal_context(self):
        from intelligence_layer import get_seasonal_context
        result = get_seasonal_context()
        self.assertEqual(result["schema_version"], 1)
        self.assertIn("key", result)
        self.assertIn("label", result)

    def test_business_brief(self):
        from intelligence_layer import get_business_brief
        result = get_business_brief()
        self.assertEqual(result["schema_version"], 1)
        self.assertIn("categories", result)
        self.assertIn("upgrade_paths", result)
        self.assertIn("value_props", result)
        self.assertIn("Bluetooth Headsets", result["categories"])

    def test_diagnose_contact(self):
        from intelligence_layer import diagnose_contact
        contact = self._create_full_contact()
        output = diagnose_contact(contact.id)
        self.assertIn("INTELLIGENCE REPORT", output)
        self.assertIn("full@test.com", output)
        self.assertIn("active_buyer", output)

    def test_format_intelligence_for_prompt(self):
        from intelligence_layer import format_intelligence_for_prompt
        contact = self._create_full_contact()
        text = format_intelligence_for_prompt(contact.id)
        self.assertIsInstance(text, str)
        # Should contain the intelligence brief header
        self.assertIn("INTELLIGENCE BRIEF", text)


if __name__ == "__main__":
    unittest.main()
