"""
Tests for the Knowledge Base Auto-Enrichment feature.
Task 1: Database Models — ScrapeSource, ScrapeLog, RejectionLog, KnowledgeEntry.is_rejected
"""

import pytest
from datetime import datetime


class TestDatabaseModels:

    def test_scrape_source_create(self, in_memory_db):
        """Create a ScrapeSource and verify all fields."""
        from database import ScrapeSource

        source = ScrapeSource.create(
            source_type="web",
            source_name="Competitor Blog",
            url="https://example.com/blog",
            scrape_frequency="daily",
            is_active=True,
            last_scraped_at=None,
            config_json='{"depth": 2}',
        )

        fetched = ScrapeSource.get_by_id(source.id)
        assert fetched.source_type == "web"
        assert fetched.source_name == "Competitor Blog"
        assert fetched.url == "https://example.com/blog"
        assert fetched.scrape_frequency == "daily"
        assert fetched.is_active is True
        assert fetched.last_scraped_at is None
        assert fetched.config_json == '{"depth": 2}'
        assert fetched.created_at is not None

    def test_scrape_log_create(self, in_memory_db):
        """Create a ScrapeLog with FK to ScrapeSource; verify items_errored field exists."""
        from database import ScrapeSource, ScrapeLog

        source = ScrapeSource.create(
            source_type="shopify",
            source_name="Product Feed",
            url="https://mystore.myshopify.com",
        )

        log = ScrapeLog.create(
            source=source,
            status="ok",
            items_found=50,
            items_staged=40,
            items_skipped=8,
            items_errored=2,
            error_message="",
        )

        fetched = ScrapeLog.get_by_id(log.id)
        assert fetched.source_id == source.id
        assert fetched.status == "ok"
        assert fetched.items_found == 50
        assert fetched.items_staged == 40
        assert fetched.items_skipped == 8
        assert fetched.items_errored == 2
        assert fetched.error_message == ""
        assert fetched.started_at is not None

    def test_rejection_log_create(self, in_memory_db):
        """Create a RejectionLog and verify content_hash field exists."""
        from database import ScrapeSource, RejectionLog

        source = ScrapeSource.create(
            source_type="amazon",
            source_name="Amazon Reviews",
            url="https://amazon.com/product/123",
        )

        rejection = RejectionLog.create(
            original_entry_type="testimonial",
            source=source,
            title="Low quality review",
            content_snippet="This product is ok I guess.",
            source_url="https://amazon.com/product/123/review/abc",
            content_hash="abc123def456",
        )

        fetched = RejectionLog.get_by_id(rejection.id)
        assert fetched.original_entry_type == "testimonial"
        assert fetched.source_id == source.id
        assert fetched.title == "Low quality review"
        assert fetched.content_snippet == "This product is ok I guess."
        assert fetched.source_url == "https://amazon.com/product/123/review/abc"
        assert fetched.content_hash == "abc123def456"
        assert fetched.created_at is not None

    def test_knowledge_entry_is_rejected_field(self, in_memory_db):
        """Create a KnowledgeEntry, set is_rejected=True, verify it persists."""
        from database import KnowledgeEntry

        entry = KnowledgeEntry.create(
            entry_type="faq",
            title="How do I return an item?",
            content="You can return items within 30 days of purchase.",
            is_active=True,
            is_rejected=False,
        )

        assert entry.is_rejected is False

        # Update to rejected
        KnowledgeEntry.update(is_rejected=True).where(
            KnowledgeEntry.id == entry.id
        ).execute()

        fetched = KnowledgeEntry.get_by_id(entry.id)
        assert fetched.is_rejected is True
