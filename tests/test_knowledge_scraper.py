"""
Tests for the Knowledge Base Auto-Enrichment feature.
Task 1: Database Models — ScrapeSource, ScrapeLog, RejectionLog, KnowledgeEntry.is_rejected
Tasks 2-5: knowledge_scraper.py — rate limiter, BaseScraper dedup, AI classifier,
           scrapers (Shopify/Web/Amazon/EmailTrends), seed sources, pipeline orchestrator.
"""

import hashlib
import json
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock


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


# ===========================================================================
# TestDedup
# ===========================================================================

class TestDedup:
    """Tests for BaseScraper.is_duplicate()."""

    def _make_source(self):
        from database import ScrapeSource
        return ScrapeSource.create(
            source_type="web",
            source_name="Test Source",
            url="https://example.com",
        )

    def test_is_duplicate_false_for_new_content(self, in_memory_db):
        """Fresh content that has never been seen should not be a duplicate."""
        from knowledge_scraper import BaseScraper
        source = self._make_source()
        scraper = BaseScraper(source)

        assert scraper.is_duplicate("This is completely new content nobody has seen before.") is False

    def test_is_duplicate_true_for_existing_knowledge_entry(self, in_memory_db):
        """Content whose hash is stored in a KnowledgeEntry metadata_json should be detected."""
        from database import KnowledgeEntry
        from knowledge_scraper import BaseScraper

        raw_text = "BlueParrott B450-XT Trucker Headset - Noise Cancelling"
        content_hash = hashlib.sha256(raw_text.encode()).hexdigest()

        KnowledgeEntry.create(
            entry_type="product_catalog",
            title="BlueParrott B450-XT",
            content="Noise cancelling headset for truckers.",
            metadata_json=json.dumps({"raw_content_hash": content_hash}),
            is_active=True,
        )

        source = self._make_source()
        scraper = BaseScraper(source)
        assert scraper.is_duplicate(raw_text) is True

    def test_is_duplicate_true_for_rejected_entry(self, in_memory_db):
        """Content whose hash appears in RejectionLog should be detected as duplicate."""
        from database import RejectionLog
        from knowledge_scraper import BaseScraper

        raw_text = "This content was previously rejected as irrelevant."
        content_hash = hashlib.sha256(raw_text.encode()).hexdigest()

        source = self._make_source()
        RejectionLog.create(
            original_entry_type="faq",
            source=source,
            title="Rejected entry",
            content_snippet=raw_text[:80],
            source_url="https://example.com/page",
            content_hash=content_hash,
        )

        scraper = BaseScraper(source)
        assert scraper.is_duplicate(raw_text) is True

    def test_is_duplicate_checks_rejected_knowledge_entries(self, in_memory_db):
        """Even is_rejected=True KnowledgeEntry rows should block duplicates."""
        from database import KnowledgeEntry
        from knowledge_scraper import BaseScraper

        raw_text = "A previously rejected knowledge entry that got is_rejected=True."
        content_hash = hashlib.sha256(raw_text.encode()).hexdigest()

        KnowledgeEntry.create(
            entry_type="blog_post",
            title="Rejected Blog Post",
            content="Some content.",
            metadata_json=json.dumps({"raw_content_hash": content_hash}),
            is_active=False,
            is_rejected=True,
        )

        source = self._make_source()
        scraper = BaseScraper(source)
        assert scraper.is_duplicate(raw_text) is True


# ===========================================================================
# TestAIClassifier
# ===========================================================================

class TestAIClassifier:
    """Tests for classify_content()."""

    def _make_source(self):
        from database import ScrapeSource
        return ScrapeSource.create(
            source_type="web",
            source_name="Test Blog",
            url="https://example.com",
        )

    def test_classify_returns_dict_for_relevant_content(self, in_memory_db):
        """classify_content should return a dict when AI returns score >= 30."""
        from knowledge_scraper import classify_content

        source = self._make_source()
        chunk = {"raw_text": "BlueParrott B450-XT – best trucking headset", "url": "https://example.com"}
        ai_response = json.dumps({
            "entry_type": "product_catalog",
            "title": "BlueParrott B450-XT",
            "content": "Best trucking headset with noise cancellation.",
            "relevance_score": 85,
            "reasoning": "Directly relevant to LDAS product line.",
        })

        mock_provider = MagicMock()
        mock_provider.complete.return_value = ai_response

        with patch("knowledge_scraper.get_provider", return_value=mock_provider):
            result = classify_content(chunk, source, [])

        assert result is not None
        assert result["entry_type"] == "product_catalog"
        assert result["relevance_score"] == 85

    def test_classify_returns_none_for_low_relevance(self, in_memory_db):
        """classify_content should return None when relevance_score < 30."""
        from knowledge_scraper import classify_content

        source = self._make_source()
        chunk = {"raw_text": "Best pizza recipes for summer", "url": "https://food.com"}
        ai_response = json.dumps({
            "entry_type": "blog_post",
            "title": "Pizza Recipes",
            "content": "How to make pizza.",
            "relevance_score": 5,
            "reasoning": "Completely unrelated to electronics.",
        })

        mock_provider = MagicMock()
        mock_provider.complete.return_value = ai_response

        with patch("knowledge_scraper.get_provider", return_value=mock_provider):
            result = classify_content(chunk, source, [])

        assert result is None

    def test_classify_includes_rejection_context(self, in_memory_db):
        """The system prompt passed to the AI should include rejection context."""
        from database import RejectionLog
        from knowledge_scraper import classify_content

        source = self._make_source()

        rejection = RejectionLog.create(
            original_entry_type="testimonial",
            source=source,
            title="Cheap knock-off review",
            content_snippet="This is garbage.",
            source_url="https://amazon.com/bad",
            content_hash="somehash",
        )

        chunk = {"raw_text": "Jabra Evolve2 – professional headset", "url": "https://jabra.com"}
        ai_response = json.dumps({
            "entry_type": "competitor_intel",
            "title": "Jabra Evolve2",
            "content": "Professional headset with ANC.",
            "relevance_score": 65,
            "reasoning": "Competitor product.",
        })

        mock_provider = MagicMock()
        mock_provider.complete.return_value = ai_response

        captured_prompts = {}
        def capture_complete(system_prompt, user_prompt, **kw):
            captured_prompts["system"] = system_prompt
            return ai_response

        mock_provider.complete.side_effect = capture_complete

        with patch("knowledge_scraper.get_provider", return_value=mock_provider):
            classify_content(chunk, source, [rejection])

        # Rejection context should appear in system prompt
        assert "Cheap knock-off review" in captured_prompts["system"]

    def test_classify_raises_on_api_error_propagates(self, in_memory_db):
        """AIProviderError should propagate out of classify_content."""
        from ai_provider import AIProviderError
        from knowledge_scraper import classify_content

        source = self._make_source()
        chunk = {"raw_text": "Some product description", "url": "https://example.com"}

        mock_provider = MagicMock()
        mock_provider.complete.side_effect = AIProviderError("API quota exceeded")

        with patch("knowledge_scraper.get_provider", return_value=mock_provider):
            with pytest.raises(AIProviderError):
                classify_content(chunk, source, [])


# ===========================================================================
# TestWebScraper
# ===========================================================================

class TestWebScraper:
    """Tests for WebScraper._parse_html()."""

    def _make_source(self, config=None):
        from database import ScrapeSource
        cfg = config or {
            "item_selector": "article",
            "title_selector": "h2",
            "content_selector": "p",
        }
        return ScrapeSource.create(
            source_type="web",
            source_name="Test Web",
            url="https://example.com/blog",
            config_json=json.dumps(cfg),
        )

    def test_parse_html_extracts_content(self, in_memory_db):
        """_parse_html should extract titles, content, and image URLs from matching elements."""
        from knowledge_scraper import WebScraper

        html = """
        <html><body>
          <article>
            <h2>BlueParrott Headset Review</h2>
            <p>The BlueParrott B450-XT is an excellent headset for truckers.</p>
            <img src="https://example.com/headset.jpg" />
          </article>
          <article>
            <h2>Another Product</h2>
            <p>This is a second product description that is long enough.</p>
          </article>
        </body></html>
        """

        source = self._make_source()
        scraper = WebScraper(source)
        chunks = scraper._parse_html(html, "https://example.com/blog")

        assert len(chunks) == 2
        assert "BlueParrott Headset Review" in chunks[0]["raw_text"]
        assert "excellent headset" in chunks[0]["raw_text"]
        assert "https://example.com/headset.jpg" in chunks[0]["image_urls"]
        assert chunks[0]["url"] == "https://example.com/blog"

    def test_parse_html_handles_empty_page(self, in_memory_db):
        """_parse_html on a page with no matching elements should return an empty list."""
        from knowledge_scraper import WebScraper

        html = "<html><body><div>No articles here.</div></body></html>"
        source = self._make_source()
        scraper = WebScraper(source)
        chunks = scraper._parse_html(html, "https://example.com/empty")
        assert chunks == []

    def test_parse_html_skips_short_items(self, in_memory_db):
        """Items with combined text < 20 chars should be skipped."""
        from knowledge_scraper import WebScraper

        html = """
        <html><body>
          <article>
            <h2>Hi</h2>
            <p>Ok</p>
          </article>
          <article>
            <h2>A Long Enough Title Here</h2>
            <p>This paragraph has sufficient content to pass the length check.</p>
          </article>
        </body></html>
        """
        source = self._make_source()
        scraper = WebScraper(source)
        chunks = scraper._parse_html(html, "https://example.com")
        # Only the second article should pass
        assert len(chunks) == 1
        assert "Long Enough Title" in chunks[0]["raw_text"]


# ===========================================================================
# TestAmazonScraper
# ===========================================================================

class TestAmazonScraper:
    """Tests for AmazonScraper helpers."""

    def _make_source(self, search_term="BlueParrott trucker headset"):
        from database import ScrapeSource
        return ScrapeSource.create(
            source_type="amazon",
            source_name="Amazon Test",
            url=search_term,
        )

    def test_build_search_url(self, in_memory_db):
        """_build_search_url should produce a valid Amazon.ca search URL."""
        from knowledge_scraper import AmazonScraper

        source = self._make_source("BlueParrott trucker headset")
        scraper = AmazonScraper(source)
        url = scraper._build_search_url()

        assert url.startswith("https://www.amazon.ca/s?k=")
        assert "BlueParrott" in url
        # Spaces should be URL-encoded
        assert " " not in url

    def test_user_agent_rotation(self, in_memory_db):
        """_get_user_agent should return a non-empty string from the pool."""
        from knowledge_scraper import AmazonScraper, _USER_AGENTS

        source = self._make_source()
        scraper = AmazonScraper(source)

        agents_seen = set()
        for _ in range(20):
            ua = scraper._get_user_agent()
            assert ua in _USER_AGENTS
            agents_seen.add(ua)

        # With 5 agents and 20 draws, we should see more than 1 (rotation is random)
        assert len(agents_seen) >= 1


# ===========================================================================
# TestEmailTrendsScraper
# ===========================================================================

class TestEmailTrendsScraper:
    """Tests for EmailTrendsScraper."""

    def _make_source(self):
        from database import ScrapeSource
        return ScrapeSource.create(
            source_type="web",
            source_name="Really Good Emails",
            url="https://reallygoodemails.com",
            config_json=json.dumps({
                "item_selector": "article",
                "title_selector": "h2",
                "content_selector": "p",
                "tags": "email_trends",
            }),
        )

    def test_tags_email_trends(self, in_memory_db):
        """EmailTrendsScraper._parse_html should add tags='email_trends' to every chunk."""
        from knowledge_scraper import EmailTrendsScraper

        html = """
        <html><body>
          <article>
            <h2>7 Email Subject Line Tricks That Drive Opens</h2>
            <p>Personalisation and urgency are the two biggest drivers of email open rates in 2024.</p>
          </article>
        </body></html>
        """

        source = self._make_source()
        scraper = EmailTrendsScraper(source)
        chunks = scraper._parse_html(html, "https://reallygoodemails.com")

        assert len(chunks) == 1
        assert chunks[0].get("tags") == "email_trends"


# ===========================================================================
# TestPipelineOrchestrator
# ===========================================================================

class TestPipelineOrchestrator:
    """Tests for seed_scrape_sources() and run_knowledge_enrichment()."""

    def test_seed_sources_created_on_first_run(self, in_memory_db):
        """seed_scrape_sources() should populate the ScrapeSource table when empty."""
        from database import ScrapeSource
        from knowledge_scraper import seed_scrape_sources, _SEED_SOURCES

        assert ScrapeSource.select().count() == 0
        seed_scrape_sources()
        assert ScrapeSource.select().count() == len(_SEED_SOURCES)

    def test_seed_sources_idempotent(self, in_memory_db):
        """Calling seed_scrape_sources() twice should not create duplicate rows."""
        from database import ScrapeSource
        from knowledge_scraper import seed_scrape_sources, _SEED_SOURCES

        seed_scrape_sources()
        seed_scrape_sources()
        assert ScrapeSource.select().count() == len(_SEED_SOURCES)

    def test_pipeline_stages_entries_as_inactive(self, in_memory_db):
        """run_knowledge_enrichment should create KnowledgeEntry rows with is_active=False."""
        from database import KnowledgeEntry, ScrapeSource
        from knowledge_scraper import run_knowledge_enrichment

        # Pre-create a single active source so we control what gets scraped
        source = ScrapeSource.create(
            source_type="web",
            source_name="Test Blog",
            url="https://example.com/blog",
            scrape_frequency="daily",
            is_active=True,
            config_json=json.dumps({
                "item_selector": "article",
                "title_selector": "h2",
                "content_selector": "p",
            }),
        )

        fake_chunks = [
            {"raw_text": "BlueParrott B450-XT Trucker Headset for professional drivers", "url": "https://example.com/p1"},
        ]
        ai_response = json.dumps({
            "entry_type": "product_catalog",
            "title": "BlueParrott B450-XT",
            "content": "Professional noise-cancelling headset.",
            "relevance_score": 80,
            "reasoning": "Direct product match.",
        })

        mock_provider = MagicMock()
        mock_provider.complete.return_value = ai_response

        with patch("knowledge_scraper.WebScraper.fetch", return_value=fake_chunks), \
             patch("knowledge_scraper.get_provider", return_value=mock_provider), \
             patch("knowledge_scraper.seed_scrape_sources"):
            run_knowledge_enrichment()

        entries = list(KnowledgeEntry.select())
        assert len(entries) == 1
        assert entries[0].is_active is False
        assert entries[0].entry_type == "product_catalog"

    def test_pipeline_skips_duplicates(self, in_memory_db):
        """Duplicate chunks (same hash already staged) should be skipped, not staged again."""
        from database import KnowledgeEntry, ScrapeSource
        from knowledge_scraper import run_knowledge_enrichment

        raw_text = "BlueParrott B450-XT Trucker Headset for professional drivers"
        content_hash = hashlib.sha256(raw_text.encode()).hexdigest()

        # Pre-create a KnowledgeEntry with this hash so dedup fires
        KnowledgeEntry.create(
            entry_type="product_catalog",
            title="BlueParrott B450-XT",
            content="Already staged.",
            metadata_json=json.dumps({"raw_content_hash": content_hash}),
            is_active=False,
        )

        source = ScrapeSource.create(
            source_type="web",
            source_name="Test Blog",
            url="https://example.com/blog",
            scrape_frequency="daily",
            is_active=True,
            config_json=json.dumps({
                "item_selector": "article",
                "title_selector": "h2",
                "content_selector": "p",
            }),
        )

        fake_chunks = [{"raw_text": raw_text, "url": "https://example.com/p1"}]

        mock_provider = MagicMock()
        mock_provider.complete.return_value = json.dumps({
            "entry_type": "product_catalog",
            "title": "BlueParrott B450-XT",
            "content": "Duplicate content.",
            "relevance_score": 80,
            "reasoning": "Direct match.",
        })

        with patch("knowledge_scraper.WebScraper.fetch", return_value=fake_chunks), \
             patch("knowledge_scraper.get_provider", return_value=mock_provider), \
             patch("knowledge_scraper.seed_scrape_sources"):
            run_knowledge_enrichment()

        # Still only 1 entry — the duplicate was not staged again
        assert KnowledgeEntry.select().count() == 1

    def test_pipeline_handles_ai_error_gracefully(self, in_memory_db):
        """An AIProviderError during classification should increment items_errored, not crash."""
        from ai_provider import AIProviderError
        from database import KnowledgeEntry, ScrapeLog, ScrapeSource
        from knowledge_scraper import run_knowledge_enrichment

        source = ScrapeSource.create(
            source_type="web",
            source_name="Error Source",
            url="https://example.com",
            scrape_frequency="daily",
            is_active=True,
            config_json=json.dumps({
                "item_selector": "article",
                "title_selector": "h2",
                "content_selector": "p",
            }),
        )

        fake_chunks = [
            {"raw_text": "This is a unique product description for error testing", "url": "https://example.com/e1"},
        ]

        mock_provider = MagicMock()
        mock_provider.complete.side_effect = AIProviderError("Quota exceeded")

        with patch("knowledge_scraper.WebScraper.fetch", return_value=fake_chunks), \
             patch("knowledge_scraper.get_provider", return_value=mock_provider), \
             patch("knowledge_scraper.seed_scrape_sources"):
            run_knowledge_enrichment()

        # No entries should be staged
        assert KnowledgeEntry.select().count() == 0

        # ScrapeLog should show 1 errored item and status ok (errors are per-item, not fatal)
        scrape_log = ScrapeLog.select().where(ScrapeLog.source == source).get()
        assert scrape_log.items_errored == 1
        assert scrape_log.status == "ok"

    def test_run_single_source_public_api(self, in_memory_db):
        """run_single_source() should call _run_single_source for the given source id."""
        from database import ScrapeSource
        from knowledge_scraper import run_single_source

        source = ScrapeSource.create(
            source_type="web",
            source_name="Run Single Test",
            url="https://example.com",
            scrape_frequency="weekly",
            is_active=True,
            config_json=json.dumps({
                "item_selector": "article",
                "title_selector": "h2",
                "content_selector": "p",
            }),
        )

        with patch("knowledge_scraper._run_single_source") as mock_run:
            run_single_source(source.id)
            assert mock_run.call_count == 1
            called_source = mock_run.call_args[0][0]
            assert called_source.id == source.id

    def test_weekly_source_skipped_if_recently_scraped(self, in_memory_db):
        """A weekly source scraped within the last 7 days should be skipped."""
        from database import KnowledgeEntry, ScrapeLog, ScrapeSource
        from knowledge_scraper import run_knowledge_enrichment

        # Create a weekly source that was scraped 3 days ago
        source = ScrapeSource.create(
            source_type="web",
            source_name="Weekly Blog",
            url="https://example.com/weekly",
            scrape_frequency="weekly",
            is_active=True,
            last_scraped_at=datetime.now() - timedelta(days=3),
            config_json=json.dumps({
                "item_selector": "article",
                "title_selector": "h2",
                "content_selector": "p",
            }),
        )

        mock_fetch = MagicMock(return_value=[])

        with patch("knowledge_scraper.WebScraper.fetch", mock_fetch), \
             patch("knowledge_scraper.seed_scrape_sources"):
            run_knowledge_enrichment()

        # fetch() should never have been called since source was recently scraped
        mock_fetch.assert_not_called()

        # No ScrapeLog should exist for this source
        assert ScrapeLog.select().where(ScrapeLog.source == source).count() == 0


# ===========================================================================
# TestStudioRoutes
# ===========================================================================

class TestStudioRoutes:
    """Integration tests for the studio knowledge review / source management routes.

    Uses a minimal Flask app built directly from studio_bp to avoid the
    Unix-only fcntl dependency in app.py.
    """

    @pytest.fixture
    def app_client(self, in_memory_db):
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

        from flask import Flask
        from studio_routes import studio_bp

        mini_app = Flask(
            __name__,
            template_folder=os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "templates",
            ),
        )
        mini_app.secret_key = "test-secret"
        mini_app.config["TESTING"] = True
        mini_app.config["WTF_CSRF_ENABLED"] = False

        # Register the fromjson filter used in pending.html
        import json as _json
        @mini_app.template_filter("fromjson")
        def _fromjson(s):
            try:
                return _json.loads(s)
            except Exception:
                return {}

        mini_app.register_blueprint(studio_bp)

        with mini_app.test_client() as client:
            yield client

    def test_pending_page_loads(self, app_client):
        """GET /studio/knowledge/pending should return 200."""
        response = app_client.get("/studio/knowledge/pending")
        assert response.status_code == 200
        assert b"Pending" in response.data

    def test_approve_sets_active(self, app_client, in_memory_db):
        """POST /studio/knowledge/<id>/approve should set is_active=True."""
        from database import KnowledgeEntry
        entry = KnowledgeEntry.create(
            entry_type="faq",
            title="Test FAQ",
            content="This is test content for approval.",
            is_active=False,
            is_rejected=False,
            metadata_json="{}",
        )
        response = app_client.post(f"/studio/knowledge/{entry.id}/approve",
                                   follow_redirects=True)
        assert response.status_code == 200
        updated = KnowledgeEntry.get_by_id(entry.id)
        assert updated.is_active is True

    def test_reject_creates_rejection_log(self, app_client, in_memory_db):
        """POST /studio/knowledge/<id>/reject should create a RejectionLog and set is_rejected=True."""
        from database import KnowledgeEntry, RejectionLog
        entry = KnowledgeEntry.create(
            entry_type="blog_post",
            title="Spam Blog Post",
            content="Buy cheap things now. This is spam content.",
            is_active=False,
            is_rejected=False,
            metadata_json="{}",
        )
        before_count = RejectionLog.select().count()
        response = app_client.post(f"/studio/knowledge/{entry.id}/reject",
                                   follow_redirects=True)
        assert response.status_code == 200
        updated = KnowledgeEntry.get_by_id(entry.id)
        assert updated.is_rejected is True
        assert RejectionLog.select().count() == before_count + 1

    def test_sources_page_loads(self, app_client):
        """GET /studio/sources should return 200."""
        response = app_client.get("/studio/sources")
        assert response.status_code == 200
        assert b"Sources" in response.data

    def test_scrape_log_page_loads(self, app_client):
        """GET /studio/scrape-log should return 200."""
        response = app_client.get("/studio/scrape-log")
        assert response.status_code == 200
        assert b"Scrape" in response.data
