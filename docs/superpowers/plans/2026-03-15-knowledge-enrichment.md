# Knowledge Base Auto-Enrichment Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an autonomous scraping + AI classification pipeline that populates the Studio knowledge base from LDAS products, competitors, Amazon reviews, and email marketing trend sites — with self-learning from user rejections.

**Architecture:** Nightly cron job (4:30am via APScheduler in app.py) runs 4 scraper types (Shopify, Web, Amazon, EmailTrends) → AI classifier scores and categorizes each chunk → staged as inactive KnowledgeEntry rows → user reviews in Studio dashboard → rejections feed back into classifier prompts.

**Tech Stack:** Flask, Peewee ORM (SQLite), BeautifulSoup4 (html.parser), anthropic SDK (Claude Haiku), requests, APScheduler.

**Spec:** `docs/superpowers/specs/2026-03-15-knowledge-enrichment-design.md`

---

## File Structure

| File | Responsibility |
|---|---|
| `database.py` (modify) | Add 3 new models (ScrapeSource, ScrapeLog, RejectionLog), add `is_rejected` field to KnowledgeEntry, add all to `create_tables()` |
| `knowledge_scraper.py` (create) | All scrapers (BaseScraper, ShopifyScraper, WebScraper, AmazonScraper, EmailTrendsScraper), AI classifier, pipeline orchestrator, seed sources |
| `studio_routes.py` (modify) | Add 8 new routes for pending review, source management, scrape log |
| `app.py` (modify) | Add APScheduler cron job at 4:30am for `run_knowledge_enrichment` |
| `requirements.txt` (modify) | Add `beautifulsoup4>=4.12.0` |
| `templates/studio/pending.html` (create) | Pending review UI (approve/reject staged entries) |
| `templates/studio/sources.html` (create) | Source management UI |
| `templates/studio/scrape_log.html` (create) | Scrape run history UI |
| `templates/studio/dashboard.html` (modify) | Add pending count badge + source health warnings |
| `tests/test_knowledge_scraper.py` (create) | All unit tests |

---

## Chunk 1: Database Models + Core Scraper Module

### Task 1: Database Models

**Files:**
- Modify: `database.py:738-751` (create_tables list)
- Modify: `database.py:1426-1438` (KnowledgeEntry — add is_rejected)
- Modify: `database.py:1488+` (add new models after TemplatePerformance)
- Test: `tests/test_knowledge_scraper.py`

- [ ] **Step 1: Write failing test for new models**

Create `tests/test_knowledge_scraper.py`:

```python
"""Tests for Knowledge Base Auto-Enrichment pipeline."""

import json
import pytest
from datetime import datetime, timedelta


class TestDatabaseModels:
    """Test that the 3 new models + KnowledgeEntry.is_rejected work correctly."""

    def test_scrape_source_create(self, in_memory_db):
        from database import ScrapeSource
        src = ScrapeSource.create(
            source_type="web",
            source_name="Test Blog",
            url="https://example.com/blog",
            scrape_frequency="weekly",
        )
        assert src.id is not None
        assert src.is_active is True
        assert src.last_scraped_at is None

    def test_scrape_log_create(self, in_memory_db):
        from database import ScrapeSource, ScrapeLog
        src = ScrapeSource.create(
            source_type="web", source_name="Test", url="https://example.com",
        )
        log = ScrapeLog.create(
            source=src, status="ok",
            items_found=10, items_staged=7, items_skipped=2, items_errored=1,
        )
        assert log.id is not None
        assert log.source.source_name == "Test"

    def test_rejection_log_create(self, in_memory_db):
        from database import ScrapeSource, RejectionLog
        src = ScrapeSource.create(
            source_type="amazon", source_name="Test", url="test",
        )
        rej = RejectionLog.create(
            original_entry_type="competitor_intel",
            source=src,
            title="Office Headset XYZ",
            content_snippet="A wireless headset designed for open office...",
            source_url="https://amazon.ca/dp/B123",
            content_hash="abc123",
        )
        assert rej.id is not None
        assert rej.content_hash == "abc123"

    def test_knowledge_entry_is_rejected_field(self, in_memory_db):
        from database import KnowledgeEntry
        entry = KnowledgeEntry.create(
            entry_type="product_catalog",
            title="Test Product",
            content="Some content",
        )
        assert entry.is_rejected is False
        entry.is_rejected = True
        entry.is_active = False
        entry.save()
        reloaded = KnowledgeEntry.get_by_id(entry.id)
        assert reloaded.is_rejected is True
        assert reloaded.is_active is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "C:\Users\davin\Claude Work Folder\mailenginehub-repo" && python -m pytest tests/test_knowledge_scraper.py::TestDatabaseModels -v`
Expected: FAIL — `ScrapeSource` does not exist, `is_rejected` not a field

- [ ] **Step 3: Add new models to database.py**

After `TemplatePerformance` class (after line 1499), add:

```python
# ═══════════════════════════════════════════════════════════════
# Knowledge Scraper Models
# ═══════════════════════════════════════════════════════════════

class ScrapeSource(BaseModel):
    """Configured scraping target for knowledge enrichment."""
    source_type     = CharField()           # "shopify" | "web" | "amazon"
    source_name     = CharField()
    url             = CharField()
    scrape_frequency = CharField(default="weekly")  # "daily" | "weekly"
    is_active       = BooleanField(default=True)
    last_scraped_at = DateTimeField(null=True)
    config_json     = TextField(default="{}")
    created_at      = DateTimeField(default=datetime.now)

    class Meta:
        table_name = "scrape_sources"


class ScrapeLog(BaseModel):
    """One row per scrape run for observability."""
    source          = ForeignKeyField(ScrapeSource, backref="logs")
    started_at      = DateTimeField(default=datetime.now)
    completed_at    = DateTimeField(null=True)
    status          = CharField(default="running")  # "running" | "ok" | "error"
    items_found     = IntegerField(default=0)
    items_staged    = IntegerField(default=0)
    items_skipped   = IntegerField(default=0)
    items_errored   = IntegerField(default=0)
    error_message   = TextField(default="")
    created_at      = DateTimeField(default=datetime.now)

    class Meta:
        table_name = "scrape_logs"


class RejectionLog(BaseModel):
    """Tracks user rejections for self-learning classifier."""
    original_entry_type = CharField()
    source          = ForeignKeyField(ScrapeSource, null=True, backref="rejections")
    title           = CharField()
    content_snippet = TextField(default="")
    source_url      = CharField(default="")
    content_hash    = CharField(default="")
    created_at      = DateTimeField(default=datetime.now)

    class Meta:
        table_name = "rejection_logs"
```

Add `is_rejected` field to `KnowledgeEntry` class (after `is_active` field, around line 1433):

```python
    is_rejected     = BooleanField(default=False)
```

Update `create_tables` list at line 738-751 — add `ScrapeSource, ScrapeLog, RejectionLog` to the list:

```python
    db.create_tables(
        [Contact, EmailTemplate, Campaign, CampaignEmail, WarmupConfig, WarmupLog,
         Flow, FlowStep, FlowEnrollment, FlowEmail, AbandonedCheckout, AgentMessage,
         ContactScore, AIMarketingPlan, AIDecisionLog,
         OmnisendOrder, OmnisendOrderItem, CustomerProfile,
         ShopifyOrder, ShopifyOrderItem, ShopifyCustomer,
         CustomerActivity, PendingTrigger, AIGeneratedEmail,
         ProductImageCache, GeneratedDiscount,
         SuppressionEntry, BounceLog,
         PreflightLog,
         MessageDecision, MessageDecisionHistory,
         SuggestedCampaign, OpportunityScanLog, ProductCommercial,
         SystemConfig, ActionLedger, DeliveryQueue, IdentityJob, AIRenderLog,
         KnowledgeEntry, AIModelConfig, StudioJob, TemplateCandidate, TemplatePerformance,
         ScrapeSource, ScrapeLog, RejectionLog],
        safe=True
    )
```

Add a migration function for the `is_rejected` column:

```python
def _migrate_knowledge_entry_fields():
    """Add is_rejected column to knowledge_entries if missing."""
    try:
        cursor = db.execute_sql("PRAGMA table_info(knowledge_entries)")
        existing = {row[1] for row in cursor.fetchall()}
        if "is_rejected" not in existing:
            db.execute_sql("ALTER TABLE knowledge_entries ADD COLUMN is_rejected INTEGER DEFAULT 0")
            sys.stderr.write("  [migrate] knowledge_entries: added is_rejected column\n")
        else:
            sys.stderr.write("  [migrate] knowledge_entries: is_rejected OK\n")
    except Exception as e:
        sys.stderr.write("  [migrate] knowledge_entries warning: %s\n" % e)
```

Call it from `init_db()` after the last `_migrate_*()` call.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd "C:\Users\davin\Claude Work Folder\mailenginehub-repo" && python -m pytest tests/test_knowledge_scraper.py::TestDatabaseModels -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add database.py tests/test_knowledge_scraper.py
git commit -m "feat: add ScrapeSource, ScrapeLog, RejectionLog models + KnowledgeEntry.is_rejected"
```

---

### Task 2: Base Scraper + Dedup Logic

**Files:**
- Create: `knowledge_scraper.py`
- Test: `tests/test_knowledge_scraper.py`

- [ ] **Step 1: Write failing test for dedup**

Add to `tests/test_knowledge_scraper.py`:

```python
import hashlib


class TestDedup:
    """Test content hash deduplication."""

    def test_is_duplicate_false_for_new_content(self, in_memory_db):
        from knowledge_scraper import BaseScraper
        from database import ScrapeSource
        src = ScrapeSource.create(source_type="web", source_name="T", url="http://x")
        scraper = BaseScraper(src)
        assert scraper.is_duplicate("brand new content") is False

    def test_is_duplicate_true_for_existing_knowledge_entry(self, in_memory_db):
        from knowledge_scraper import BaseScraper
        from database import ScrapeSource, KnowledgeEntry
        import json
        raw = "this content already exists"
        h = hashlib.sha256(raw.encode()).hexdigest()
        KnowledgeEntry.create(
            entry_type="product_catalog", title="Existing",
            content="...", metadata_json=json.dumps({"raw_content_hash": h}),
        )
        src = ScrapeSource.create(source_type="web", source_name="T", url="http://x")
        scraper = BaseScraper(src)
        assert scraper.is_duplicate(raw) is True

    def test_is_duplicate_true_for_rejected_entry(self, in_memory_db):
        from knowledge_scraper import BaseScraper
        from database import ScrapeSource, RejectionLog
        raw = "previously rejected content"
        h = hashlib.sha256(raw.encode()).hexdigest()
        src = ScrapeSource.create(source_type="web", source_name="T", url="http://x")
        RejectionLog.create(
            original_entry_type="competitor_intel", source=src,
            title="Bad Entry", content_hash=h,
        )
        scraper = BaseScraper(src)
        assert scraper.is_duplicate(raw) is True

    def test_is_duplicate_checks_rejected_knowledge_entries(self, in_memory_db):
        from knowledge_scraper import BaseScraper
        from database import ScrapeSource, KnowledgeEntry
        import json
        raw = "rejected but row kept"
        h = hashlib.sha256(raw.encode()).hexdigest()
        KnowledgeEntry.create(
            entry_type="faq", title="Rejected FAQ",
            content="...", is_active=False, is_rejected=True,
            metadata_json=json.dumps({"raw_content_hash": h}),
        )
        src = ScrapeSource.create(source_type="web", source_name="T", url="http://x")
        scraper = BaseScraper(src)
        assert scraper.is_duplicate(raw) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "C:\Users\davin\Claude Work Folder\mailenginehub-repo" && python -m pytest tests/test_knowledge_scraper.py::TestDedup -v`
Expected: FAIL — `knowledge_scraper` module does not exist

- [ ] **Step 3: Write BaseScraper with dedup logic**

Create `knowledge_scraper.py`:

```python
"""
knowledge_scraper.py -- Autonomous knowledge base enrichment pipeline.

Scrapers fetch content from external sources (Shopify, web, Amazon, email blogs).
AI classifier scores and categorizes each chunk. Staged as inactive KnowledgeEntry
rows for human review. Rejections feed self-learning via RejectionLog.
"""

import hashlib
import json
import logging
import os
import time
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup

from database import (
    KnowledgeEntry, ScrapeSource, ScrapeLog, RejectionLog, db,
)
from ai_provider import get_provider, AIProviderError

log = logging.getLogger(__name__)

# =========================================================================
#  Rate limiting
# =========================================================================

_last_request_time = 0.0


def _rate_limit(min_interval: float = 0.5):
    """Sleep if needed to enforce minimum interval between requests."""
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < min_interval:
        time.sleep(min_interval - elapsed)
    _last_request_time = time.time()


# =========================================================================
#  Base Scraper
# =========================================================================

class BaseScraper:
    """Base class for all scrapers."""

    def __init__(self, source: ScrapeSource):
        self.source = source

    def fetch(self):
        """
        Returns list of raw content chunks.
        Each chunk: {"raw_text": str, "url": str, "image_urls": list}
        """
        raise NotImplementedError

    def is_duplicate(self, raw_text: str) -> bool:
        """
        Check SHA-256 hash against existing KnowledgeEntry rows (all states)
        and RejectionLog entries.
        """
        content_hash = hashlib.sha256(raw_text.encode()).hexdigest()

        # Check KnowledgeEntry metadata_json
        for entry in KnowledgeEntry.select(KnowledgeEntry.metadata_json):
            try:
                meta = json.loads(entry.metadata_json or "{}")
                if meta.get("raw_content_hash") == content_hash:
                    return True
            except (json.JSONDecodeError, TypeError):
                continue

        # Check RejectionLog
        if RejectionLog.select().where(
            RejectionLog.content_hash == content_hash
        ).exists():
            return True

        return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd "C:\Users\davin\Claude Work Folder\mailenginehub-repo" && python -m pytest tests/test_knowledge_scraper.py::TestDedup -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add knowledge_scraper.py tests/test_knowledge_scraper.py
git commit -m "feat: add BaseScraper with SHA-256 content deduplication"
```

---

### Task 3: AI Classifier

**Files:**
- Modify: `knowledge_scraper.py`
- Test: `tests/test_knowledge_scraper.py`

- [ ] **Step 1: Write failing test for classifier**

Add to `tests/test_knowledge_scraper.py`:

```python
from unittest.mock import patch, MagicMock


class TestAIClassifier:
    """Test the AI classification function."""

    def test_classify_returns_dict_for_relevant_content(self, in_memory_db):
        from knowledge_scraper import classify_content
        from database import ScrapeSource
        src = ScrapeSource.create(source_type="web", source_name="T", url="http://x")
        chunk = {
            "raw_text": "The LDAS TH11 headset features noise cancellation for truckers.",
            "url": "https://ldas.ca/products/th11",
            "image_urls": [],
        }
        mock_response = json.dumps({
            "entry_type": "product_catalog",
            "title": "LDAS TH11 Headset",
            "content": "The TH11 features active noise cancellation designed for truck drivers.",
            "relevance_score": 92,
            "reasoning": "Direct LDAS product info for truckers.",
        })
        with patch("knowledge_scraper.get_provider") as mock_prov:
            mock_prov.return_value.complete.return_value = mock_response
            result = classify_content(chunk, src, rejections=[])
        assert result is not None
        assert result["entry_type"] == "product_catalog"
        assert result["relevance_score"] == 92

    def test_classify_returns_none_for_low_relevance(self, in_memory_db):
        from knowledge_scraper import classify_content
        from database import ScrapeSource
        src = ScrapeSource.create(source_type="web", source_name="T", url="http://x")
        chunk = {"raw_text": "Gaming mouse review", "url": "http://x", "image_urls": []}
        mock_response = json.dumps({
            "entry_type": "competitor_intel",
            "title": "Gaming Mouse",
            "content": "A gaming mouse review.",
            "relevance_score": 12,
            "reasoning": "Not relevant to trucking.",
        })
        with patch("knowledge_scraper.get_provider") as mock_prov:
            mock_prov.return_value.complete.return_value = mock_response
            result = classify_content(chunk, src, rejections=[])
        assert result is None

    def test_classify_includes_rejection_context(self, in_memory_db):
        from knowledge_scraper import classify_content, _build_rejection_context
        from database import ScrapeSource, RejectionLog
        src = ScrapeSource.create(source_type="web", source_name="T", url="http://x")
        RejectionLog.create(
            original_entry_type="competitor_intel", source=src,
            title="Office Headset", content_snippet="For open offices...",
            source_url="https://example.com", content_hash="abc",
        )
        rejections = list(RejectionLog.select().order_by(RejectionLog.created_at.desc()).limit(50))
        context_str = _build_rejection_context(rejections)
        assert "Office Headset" in context_str
        assert "competitor_intel" in context_str

    def test_classify_raises_on_api_error_propagates(self, in_memory_db):
        from knowledge_scraper import classify_content
        from database import ScrapeSource
        src = ScrapeSource.create(source_type="web", source_name="T", url="http://x")
        chunk = {"raw_text": "test", "url": "http://x", "image_urls": []}
        with patch("knowledge_scraper.get_provider") as mock_prov:
            mock_prov.return_value.complete.side_effect = AIProviderError("timeout")
            with pytest.raises(AIProviderError):
                classify_content(chunk, src, rejections=[])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "C:\Users\davin\Claude Work Folder\mailenginehub-repo" && python -m pytest tests/test_knowledge_scraper.py::TestAIClassifier -v`
Expected: FAIL — `classify_content` does not exist

- [ ] **Step 3: Implement AI classifier**

Add to `knowledge_scraper.py`:

```python
# =========================================================================
#  AI Classifier
# =========================================================================

_CLASSIFIER_SYSTEM_PROMPT = """You are a knowledge curator for LDAS Electronics, a Canadian brand selling
dash cams, headsets, and accessories to semi truck drivers.

Your job: classify and summarize scraped web content for the knowledge base.

RELEVANCE RULES:
- 80-100: Directly about LDAS products, or truck driver electronics
- 60-79: About trucking lifestyle, road safety, fleet tech, or email marketing best practices
- 30-59: Tangentially related - general electronics, delivery drivers, automotive
- 0-29: IRRELEVANT - office products, gaming, consumer tech, unrelated industries

REJECT (score 0): Office headsets, gaming accessories, consumer electronics not for drivers,
content about industries other than trucking/transportation.

The user has rejected these entries recently. Avoid similar content:
%s

Respond with JSON only:
{"entry_type": "...", "title": "...", "content": "...", "relevance_score": N, "reasoning": "..."}

Valid entry_type values: product_catalog, brand_copy, testimonial, blog_post, competitor_intel, faq"""


def _build_rejection_context(rejections):
    """Format recent RejectionLog entries for injection into classifier prompt."""
    if not rejections:
        return "(no rejections yet)"
    lines = []
    for r in rejections[:50]:
        lines.append('- [%s] "%s" -- %s (REJECTED: %s)' % (
            r.original_entry_type, r.title,
            r.content_snippet[:100] if r.content_snippet else "",
            r.source_url or "unknown",
        ))
    return "\n".join(lines)


def classify_content(raw_chunk, source, rejections):
    """
    Send raw content to Claude Haiku for classification.

    Returns dict with entry_type/title/content/relevance_score/reasoning,
    or None if relevance_score < 30.
    Raises AIProviderError on API failure (caller must catch).
    """
    rejection_context = _build_rejection_context(rejections)
    system_prompt = _CLASSIFIER_SYSTEM_PROMPT % rejection_context

    user_prompt = json.dumps({
        "source_type": source.source_type,
        "source_name": source.source_name,
        "source_url": raw_chunk.get("url", ""),
        "raw_text": raw_chunk["raw_text"][:3000],  # trim to fit context
    })

    provider = get_provider()
    raw_response = provider.complete(system_prompt, user_prompt, max_tokens=512)

    # Parse response — strip markdown fences if present
    cleaned = raw_response.strip()
    if cleaned.startswith("```"):
        first_nl = cleaned.index("\n") if "\n" in cleaned else len(cleaned)
        cleaned = cleaned[first_nl + 1:]
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3].rstrip()

    data = json.loads(cleaned)

    score = int(data.get("relevance_score", 0))
    if score < 30:
        return None

    return {
        "entry_type": str(data.get("entry_type", "blog_post")),
        "title": str(data.get("title", "")),
        "content": str(data.get("content", "")),
        "relevance_score": score,
        "reasoning": str(data.get("reasoning", "")),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd "C:\Users\davin\Claude Work Folder\mailenginehub-repo" && python -m pytest tests/test_knowledge_scraper.py::TestAIClassifier -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add knowledge_scraper.py tests/test_knowledge_scraper.py
git commit -m "feat: add AI classifier with rejection context injection"
```

---

### Task 4: Web Scraper + Amazon Scraper + Email Trends Scraper

**Files:**
- Modify: `knowledge_scraper.py`
- Test: `tests/test_knowledge_scraper.py`

- [ ] **Step 1: Write failing test for WebScraper**

Add to `tests/test_knowledge_scraper.py`:

```python
class TestWebScraper:
    """Test WebScraper HTML parsing."""

    def test_parse_html_extracts_content(self, in_memory_db):
        from knowledge_scraper import WebScraper
        from database import ScrapeSource
        src = ScrapeSource.create(
            source_type="web", source_name="Test Blog", url="https://example.com/blog",
            config_json=json.dumps({
                "item_selector": "article",
                "title_selector": "h2",
                "content_selector": "p",
            }),
        )
        scraper = WebScraper(src)
        html = """
        <html><body>
          <article>
            <h2>Truck Driver Safety Tips</h2>
            <p>Always check your mirrors before changing lanes.</p>
          </article>
          <article>
            <h2>Best Dash Cams 2026</h2>
            <p>Top picks for commercial trucking.</p>
          </article>
        </body></html>
        """
        chunks = scraper._parse_html(html, "https://example.com/blog")
        assert len(chunks) == 2
        assert "Truck Driver Safety" in chunks[0]["raw_text"]
        assert chunks[0]["url"] == "https://example.com/blog"

    def test_parse_html_handles_empty_page(self, in_memory_db):
        from knowledge_scraper import WebScraper
        from database import ScrapeSource
        src = ScrapeSource.create(
            source_type="web", source_name="Empty", url="https://example.com",
            config_json="{}",
        )
        scraper = WebScraper(src)
        chunks = scraper._parse_html("<html><body></body></html>", "https://example.com")
        assert chunks == []


class TestAmazonScraper:
    """Test AmazonScraper search URL building and user agent rotation."""

    def test_build_search_url(self, in_memory_db):
        from knowledge_scraper import AmazonScraper
        from database import ScrapeSource
        src = ScrapeSource.create(
            source_type="amazon", source_name="LDAS",
            url="LDAS Electronics",
        )
        scraper = AmazonScraper(src)
        url = scraper._build_search_url()
        assert "amazon.ca" in url
        assert "LDAS" in url or "LDAS+Electronics" in url or "LDAS%20Electronics" in url

    def test_user_agent_rotation(self, in_memory_db):
        from knowledge_scraper import AmazonScraper
        from database import ScrapeSource
        src = ScrapeSource.create(
            source_type="amazon", source_name="T", url="test",
        )
        scraper = AmazonScraper(src)
        agents = set()
        for _ in range(10):
            agents.add(scraper._get_user_agent())
        assert len(agents) >= 2  # at least 2 different agents


class TestEmailTrendsScraper:
    """Test EmailTrendsScraper tags content correctly."""

    def test_tags_email_trends(self, in_memory_db):
        from knowledge_scraper import EmailTrendsScraper
        from database import ScrapeSource
        src = ScrapeSource.create(
            source_type="web", source_name="Litmus",
            url="https://litmus.com/blog",
            config_json=json.dumps({"item_selector": "article", "title_selector": "h2", "content_selector": "p"}),
        )
        scraper = EmailTrendsScraper(src)
        html = '<html><body><article><h2>Dark Mode Email Design</h2><p>Tips for dark mode.</p></article></body></html>'
        chunks = scraper._parse_html(html, "https://litmus.com/blog")
        assert len(chunks) == 1
        assert chunks[0].get("tags") == "email_trends"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "C:\Users\davin\Claude Work Folder\mailenginehub-repo" && python -m pytest tests/test_knowledge_scraper.py::TestWebScraper tests/test_knowledge_scraper.py::TestAmazonScraper tests/test_knowledge_scraper.py::TestEmailTrendsScraper -v`
Expected: FAIL — classes don't exist

- [ ] **Step 3: Implement all scrapers**

Add to `knowledge_scraper.py`:

```python
# =========================================================================
#  User-Agent rotation (for Amazon / web scraping)
# =========================================================================

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
]

import random


# =========================================================================
#  Shopify Scraper
# =========================================================================

class ShopifyScraper(BaseScraper):
    """Pulls product data from Shopify API (already authenticated)."""

    def fetch(self):
        chunks = []
        try:
            from database import ProductImageCache
            for product in ProductImageCache.select():
                raw_text = "%s - %s - $%s" % (
                    product.product_title or "",
                    product.product_type or "",
                    product.price or "",
                )
                if product.handle:
                    raw_text += " - https://ldas.ca/products/%s" % product.handle
                chunks.append({
                    "raw_text": raw_text,
                    "url": "https://ldas.ca/products/%s" % (product.handle or ""),
                    "image_urls": [product.image_url] if product.image_url else [],
                })
        except Exception as e:
            log.warning("ShopifyScraper error: %s", e)
        return chunks


# =========================================================================
#  Web Scraper
# =========================================================================

class WebScraper(BaseScraper):
    """Generic HTTP + BeautifulSoup scraper, configured via config_json."""

    def fetch(self):
        url = self.source.url
        config = json.loads(self.source.config_json or "{}")
        max_pages = config.get("max_pages", 1)
        all_chunks = []

        for page_num in range(max_pages):
            _rate_limit(0.5)
            page_url = url if page_num == 0 else "%s?page=%d" % (url, page_num + 1)
            try:
                resp = requests.get(page_url, headers={
                    "User-Agent": random.choice(_USER_AGENTS),
                }, timeout=15)
                resp.raise_for_status()
                chunks = self._parse_html(resp.text, page_url)
                all_chunks.extend(chunks)
                if not chunks:
                    break  # no more content
            except requests.RequestException as e:
                log.warning("WebScraper fetch error for %s: %s", page_url, e)
                break

        return all_chunks

    def _parse_html(self, html, page_url):
        """Parse HTML into content chunks using config_json selectors."""
        config = json.loads(self.source.config_json or "{}")
        item_sel = config.get("item_selector", "article")
        title_sel = config.get("title_selector", "h2")
        content_sel = config.get("content_selector", "p")

        soup = BeautifulSoup(html, "html.parser")
        items = soup.select(item_sel)

        chunks = []
        for item in items:
            title_el = item.select_one(title_sel)
            content_els = item.select(content_sel)
            title = title_el.get_text(strip=True) if title_el else ""
            body = " ".join(el.get_text(strip=True) for el in content_els)
            raw_text = ("%s\n%s" % (title, body)).strip()
            if not raw_text or len(raw_text) < 20:
                continue

            img_urls = []
            for img in item.select("img[src]"):
                src = img.get("src", "")
                if src and not src.startswith("data:"):
                    img_urls.append(src)

            chunks.append({
                "raw_text": raw_text,
                "url": page_url,
                "image_urls": img_urls,
            })

        return chunks


# =========================================================================
#  Amazon Scraper
# =========================================================================

class AmazonScraper(WebScraper):
    """Searches Amazon.ca and extracts product listings + reviews."""

    def _build_search_url(self):
        from urllib.parse import quote_plus
        search_term = self.source.url  # url field stores search term for amazon type
        return "https://www.amazon.ca/s?k=%s" % quote_plus(search_term)

    def _get_user_agent(self):
        return random.choice(_USER_AGENTS)

    def fetch(self):
        search_url = self._build_search_url()
        chunks = []
        _rate_limit(3.0)  # Amazon: 1 req per 3 seconds
        try:
            resp = requests.get(search_url, headers={
                "User-Agent": self._get_user_agent(),
                "Accept-Language": "en-CA,en;q=0.9",
            }, timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            # Extract product cards
            for item in soup.select('[data-component-type="s-search-result"]')[:10]:
                title_el = item.select_one("h2 a span")
                price_el = item.select_one(".a-price .a-offscreen")
                rating_el = item.select_one(".a-icon-alt")
                link_el = item.select_one("h2 a")

                title = title_el.get_text(strip=True) if title_el else ""
                price = price_el.get_text(strip=True) if price_el else ""
                rating = rating_el.get_text(strip=True) if rating_el else ""
                link = "https://www.amazon.ca" + link_el.get("href", "") if link_el else ""

                if not title:
                    continue

                raw_text = "Product: %s | Price: %s | Rating: %s" % (title, price, rating)
                img_urls = []
                img_el = item.select_one("img.s-image")
                if img_el:
                    img_urls.append(img_el.get("src", ""))

                chunks.append({
                    "raw_text": raw_text,
                    "url": link,
                    "image_urls": img_urls,
                })

        except requests.RequestException as e:
            log.warning("AmazonScraper fetch error: %s", e)

        return chunks


# =========================================================================
#  Email Trends Scraper
# =========================================================================

class EmailTrendsScraper(WebScraper):
    """Scrapes email marketing blogs, tags output as email_trends."""

    def _parse_html(self, html, page_url):
        chunks = super()._parse_html(html, page_url)
        for chunk in chunks:
            chunk["tags"] = "email_trends"
        return chunks
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd "C:\Users\davin\Claude Work Folder\mailenginehub-repo" && python -m pytest tests/test_knowledge_scraper.py::TestWebScraper tests/test_knowledge_scraper.py::TestAmazonScraper tests/test_knowledge_scraper.py::TestEmailTrendsScraper -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Update requirements.txt**

Add `beautifulsoup4>=4.12.0` to `requirements.txt`.

- [ ] **Step 6: Commit**

```bash
git add knowledge_scraper.py tests/test_knowledge_scraper.py requirements.txt
git commit -m "feat: add WebScraper, AmazonScraper, ShopifyScraper, EmailTrendsScraper"
```

---

### Task 5: Pipeline Orchestrator + Seed Sources

**Files:**
- Modify: `knowledge_scraper.py`
- Test: `tests/test_knowledge_scraper.py`

- [ ] **Step 1: Write failing test for orchestrator**

Add to `tests/test_knowledge_scraper.py`:

```python
class TestPipelineOrchestrator:
    """Test the main run_knowledge_enrichment pipeline."""

    def test_seed_sources_created_on_first_run(self, in_memory_db):
        from knowledge_scraper import seed_scrape_sources
        from database import ScrapeSource
        assert ScrapeSource.select().count() == 0
        seed_scrape_sources()
        assert ScrapeSource.select().count() == 10  # 10 seed sources

    def test_seed_sources_idempotent(self, in_memory_db):
        from knowledge_scraper import seed_scrape_sources
        from database import ScrapeSource
        seed_scrape_sources()
        seed_scrape_sources()
        assert ScrapeSource.select().count() == 10

    def test_pipeline_stages_entries_as_inactive(self, in_memory_db):
        from knowledge_scraper import _run_single_source
        from database import ScrapeSource, KnowledgeEntry, ScrapeLog
        src = ScrapeSource.create(
            source_type="web", source_name="Test", url="https://example.com",
            config_json=json.dumps({"item_selector": "article", "title_selector": "h2", "content_selector": "p"}),
        )
        mock_classified = {
            "entry_type": "blog_post",
            "title": "Trucker Tips",
            "content": "Useful tips for long haul drivers.",
            "relevance_score": 75,
            "reasoning": "Relevant to trucking.",
        }
        mock_chunks = [{"raw_text": "some content", "url": "https://example.com/post", "image_urls": []}]

        with patch("knowledge_scraper.WebScraper.fetch", return_value=mock_chunks):
            with patch("knowledge_scraper.classify_content", return_value=mock_classified):
                _run_single_source(src, rejections=[])

        entries = list(KnowledgeEntry.select().where(KnowledgeEntry.is_active == False))
        assert len(entries) == 1
        assert entries[0].title == "Trucker Tips"
        assert entries[0].is_rejected is False

        logs = list(ScrapeLog.select().where(ScrapeLog.source == src))
        assert len(logs) == 1
        assert logs[0].items_staged == 1
        assert logs[0].status == "ok"

    def test_pipeline_skips_duplicates(self, in_memory_db):
        from knowledge_scraper import _run_single_source
        from database import ScrapeSource, KnowledgeEntry
        src = ScrapeSource.create(
            source_type="web", source_name="Test", url="https://example.com",
            config_json="{}",
        )
        # Pre-create an entry with matching hash
        raw = "duplicate content here"
        h = hashlib.sha256(raw.encode()).hexdigest()
        KnowledgeEntry.create(
            entry_type="blog_post", title="Existing",
            content="...", metadata_json=json.dumps({"raw_content_hash": h}),
        )
        mock_chunks = [{"raw_text": raw, "url": "http://x", "image_urls": []}]

        with patch("knowledge_scraper.WebScraper.fetch", return_value=mock_chunks):
            _run_single_source(src, rejections=[])

        # Should not create a new entry (dedup caught it)
        assert KnowledgeEntry.select().count() == 1

    def test_pipeline_handles_ai_error_gracefully(self, in_memory_db):
        from knowledge_scraper import _run_single_source
        from database import ScrapeSource, ScrapeLog
        src = ScrapeSource.create(
            source_type="web", source_name="Test", url="https://example.com",
            config_json="{}",
        )
        mock_chunks = [
            {"raw_text": "chunk one", "url": "http://x", "image_urls": []},
            {"raw_text": "chunk two", "url": "http://x", "image_urls": []},
        ]

        def classify_side_effect(chunk, source, rejections):
            if "one" in chunk["raw_text"]:
                raise AIProviderError("API timeout")
            return {
                "entry_type": "blog_post", "title": "Good", "content": "ok",
                "relevance_score": 80, "reasoning": "good",
            }

        with patch("knowledge_scraper.WebScraper.fetch", return_value=mock_chunks):
            with patch("knowledge_scraper.classify_content", side_effect=classify_side_effect):
                _run_single_source(src, rejections=[])

        log_row = ScrapeLog.select().where(ScrapeLog.source == src).get()
        assert log_row.items_errored == 1
        assert log_row.items_staged == 1
        assert log_row.status == "ok"

    def test_weekly_source_skipped_if_recently_scraped(self, in_memory_db):
        from knowledge_scraper import run_knowledge_enrichment
        from database import ScrapeSource
        src = ScrapeSource.create(
            source_type="web", source_name="Weekly", url="https://example.com",
            scrape_frequency="weekly",
            last_scraped_at=datetime.now() - timedelta(days=2),  # 2 days ago
        )
        with patch("knowledge_scraper._run_single_source") as mock_run:
            with patch("knowledge_scraper.seed_scrape_sources"):
                run_knowledge_enrichment()
            mock_run.assert_not_called()  # should be skipped — last scraped < 7 days
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "C:\Users\davin\Claude Work Folder\mailenginehub-repo" && python -m pytest tests/test_knowledge_scraper.py::TestPipelineOrchestrator -v`
Expected: FAIL — functions don't exist

- [ ] **Step 3: Implement orchestrator + seed sources**

Add to `knowledge_scraper.py`:

```python
# =========================================================================
#  Scraper registry
# =========================================================================

_SCRAPER_CLASSES = {
    "shopify": ShopifyScraper,
    "web": WebScraper,
    "amazon": AmazonScraper,
}


def _get_scraper(source):
    """Return the right scraper class for a source. EmailTrends uses WebScraper with tags."""
    config = json.loads(source.config_json or "{}")
    if config.get("tags") == "email_trends":
        return EmailTrendsScraper(source)
    cls = _SCRAPER_CLASSES.get(source.source_type, WebScraper)
    return cls(source)


# =========================================================================
#  Seed sources
# =========================================================================

_SEED_SOURCES = [
    ("shopify", "LDAS Shopify Products", "", "daily", {}),
    ("web", "LDAS Blog", "https://ldas.ca/blogs", "daily",
     {"item_selector": "article", "title_selector": "h2", "content_selector": "p", "max_pages": 3}),
    ("amazon", "Amazon.ca - LDAS", "LDAS Electronics", "daily", {}),
    ("amazon", "Amazon.ca - BlueParrott", "BlueParrott trucker headset", "weekly", {}),
    ("amazon", "Amazon.ca - Jabra Trucker", "Jabra trucker headset", "weekly", {}),
    ("amazon", "Amazon.ca - Poly Trucker", "Plantronics trucker headset", "weekly", {}),
    ("web", "BlueParrott Products", "https://www.blueparrott.com/headsets", "weekly",
     {"item_selector": ".product-card, .product-item, article", "title_selector": "h2, h3, .product-name", "content_selector": "p, .description"}),
    ("web", "Jabra Driver Headsets", "https://www.jabra.com/business/office-headsets/jabra-perform", "weekly",
     {"item_selector": ".product-card, article", "title_selector": "h2, h3", "content_selector": "p"}),
    ("web", "Really Good Emails", "https://reallygoodemails.com", "weekly",
     {"item_selector": "article, .email-card, .card", "title_selector": "h2, h3", "content_selector": "p", "tags": "email_trends"}),
    ("web", "Litmus Blog", "https://www.litmus.com/blog", "weekly",
     {"item_selector": "article", "title_selector": "h2", "content_selector": "p", "tags": "email_trends"}),
]


def seed_scrape_sources():
    """Create seed ScrapeSource rows if table is empty."""
    if ScrapeSource.select().count() > 0:
        return
    for source_type, name, url, freq, config in _SEED_SOURCES:
        ScrapeSource.create(
            source_type=source_type,
            source_name=name,
            url=url,
            scrape_frequency=freq,
            config_json=json.dumps(config),
        )
    log.info("Seeded %d scrape sources", len(_SEED_SOURCES))


# =========================================================================
#  Pipeline: run a single source
# =========================================================================

def _run_single_source(source, rejections):
    """Fetch, dedup, classify, and stage entries for one ScrapeSource."""
    scrape_log = ScrapeLog.create(source=source, status="running")
    scraper = _get_scraper(source)

    items_found = 0
    items_staged = 0
    items_skipped = 0
    items_errored = 0

    try:
        chunks = scraper.fetch()
        items_found = len(chunks)

        for chunk in chunks:
            # Dedup
            if scraper.is_duplicate(chunk["raw_text"]):
                continue

            # Classify
            _rate_limit(0.5)
            try:
                result = classify_content(chunk, source, rejections)
            except (AIProviderError, json.JSONDecodeError, ValueError) as e:
                log.warning("Classify error for source %s: %s", source.source_name, e)
                items_errored += 1
                continue

            if result is None:
                items_skipped += 1
                continue

            # Stage as inactive KnowledgeEntry
            content_hash = hashlib.sha256(chunk["raw_text"].encode()).hexdigest()
            metadata = {
                "scrape_source_id": source.id,
                "scraped_at": datetime.now().isoformat(),
                "source_url": chunk.get("url", ""),
                "relevance_score": result["relevance_score"],
                "ai_reasoning": result["reasoning"],
                "image_urls": chunk.get("image_urls", []),
                "raw_content_hash": content_hash,
            }
            tags = chunk.get("tags", "")
            if tags:
                metadata["tags"] = tags

            KnowledgeEntry.create(
                entry_type=result["entry_type"],
                title=result["title"],
                content=result["content"],
                metadata_json=json.dumps(metadata),
                is_active=False,
                is_rejected=False,
            )
            items_staged += 1

        scrape_log.status = "ok"

    except Exception as e:
        log.exception("Source %s failed: %s", source.source_name, e)
        scrape_log.status = "error"
        scrape_log.error_message = str(e)[:500]

    scrape_log.items_found = items_found
    scrape_log.items_staged = items_staged
    scrape_log.items_skipped = items_skipped
    scrape_log.items_errored = items_errored
    scrape_log.completed_at = datetime.now()
    scrape_log.save()

    source.last_scraped_at = datetime.now()
    source.save()


# =========================================================================
#  Pipeline: main entry point
# =========================================================================

def run_knowledge_enrichment():
    """
    Main entry point -- called nightly at 4:30am via APScheduler.
    Runs all active sources, respecting frequency settings.
    """
    seed_scrape_sources()

    # Load recent rejections for classifier context
    rejections = list(
        RejectionLog.select()
        .order_by(RejectionLog.created_at.desc())
        .limit(50)
    )

    sources = ScrapeSource.select().where(ScrapeSource.is_active == True)  # noqa: E712

    for source in sources:
        # Check frequency — skip weekly if scraped < 7 days ago
        if source.scrape_frequency == "weekly" and source.last_scraped_at:
            days_since = (datetime.now() - source.last_scraped_at).days
            if days_since < 7:
                log.debug("Skipping weekly source %s (last scraped %d days ago)",
                          source.source_name, days_since)
                continue

        # Skip daily if scraped today
        if source.scrape_frequency == "daily" and source.last_scraped_at:
            if source.last_scraped_at.date() == datetime.now().date():
                log.debug("Skipping daily source %s (already scraped today)",
                          source.source_name)
                continue

        log.info("Scraping source: %s (%s)", source.source_name, source.source_type)
        _run_single_source(source, rejections)

    log.info("Knowledge enrichment complete")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd "C:\Users\davin\Claude Work Folder\mailenginehub-repo" && python -m pytest tests/test_knowledge_scraper.py::TestPipelineOrchestrator -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add knowledge_scraper.py tests/test_knowledge_scraper.py
git commit -m "feat: add pipeline orchestrator with seed sources and frequency-based scheduling"
```

---

## Chunk 2: Routes, Templates, Scheduler Integration

### Task 6: Studio Routes (Pending Review + Sources + Scrape Log)

**Files:**
- Modify: `studio_routes.py`
- Test: `tests/test_knowledge_scraper.py`

- [ ] **Step 1: Write failing test for routes**

Add to `tests/test_knowledge_scraper.py`:

```python
class TestStudioRoutes:
    """Test the new Studio routes for pending review and source management."""

    @pytest.fixture
    def app_client(self, in_memory_db):
        """Create a Flask test client."""
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from app import app
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        with app.test_client() as client:
            yield client

    def test_pending_page_loads(self, app_client, in_memory_db):
        resp = app_client.get("/studio/knowledge/pending")
        assert resp.status_code == 200

    def test_approve_sets_active(self, app_client, in_memory_db):
        from database import KnowledgeEntry
        entry = KnowledgeEntry.create(
            entry_type="product_catalog", title="Test",
            content="Content", is_active=False, is_rejected=False,
        )
        resp = app_client.post("/studio/knowledge/%d/approve" % entry.id, follow_redirects=True)
        assert resp.status_code == 200
        reloaded = KnowledgeEntry.get_by_id(entry.id)
        assert reloaded.is_active is True

    def test_reject_creates_rejection_log(self, app_client, in_memory_db):
        from database import KnowledgeEntry, RejectionLog, ScrapeSource
        src = ScrapeSource.create(source_type="web", source_name="T", url="http://x")
        entry = KnowledgeEntry.create(
            entry_type="competitor_intel", title="Office Headset",
            content="An office headset review that is not relevant.",
            is_active=False, is_rejected=False,
            metadata_json=json.dumps({
                "scrape_source_id": src.id,
                "source_url": "http://example.com",
                "raw_content_hash": "abc123",
            }),
        )
        resp = app_client.post("/studio/knowledge/%d/reject" % entry.id, follow_redirects=True)
        assert resp.status_code == 200
        reloaded = KnowledgeEntry.get_by_id(entry.id)
        assert reloaded.is_rejected is True
        assert reloaded.is_active is False
        assert RejectionLog.select().count() == 1
        rej = RejectionLog.get()
        assert rej.title == "Office Headset"
        assert rej.content_hash == "abc123"

    def test_sources_page_loads(self, app_client, in_memory_db):
        resp = app_client.get("/studio/sources")
        assert resp.status_code == 200

    def test_scrape_log_page_loads(self, app_client, in_memory_db):
        resp = app_client.get("/studio/scrape-log")
        assert resp.status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "C:\Users\davin\Claude Work Folder\mailenginehub-repo" && python -m pytest tests/test_knowledge_scraper.py::TestStudioRoutes -v`
Expected: FAIL — routes don't exist

- [ ] **Step 3: Add routes to studio_routes.py**

Add these routes to `studio_routes.py` (after existing routes, before the API section):

```python
# -----------------------------------------
#  PENDING REVIEW (Knowledge Enrichment)
# -----------------------------------------

@studio_bp.route("/knowledge/pending")
def knowledge_pending():
    """Show staged entries awaiting review (is_active=False, is_rejected=False)."""
    entries = list(
        KnowledgeEntry.select()
        .where(
            KnowledgeEntry.is_active == False,  # noqa: E712
            KnowledgeEntry.is_rejected == False,  # noqa: E712
        )
        .order_by(KnowledgeEntry.created_at.desc())
    )
    # Parse metadata for display
    for entry in entries:
        try:
            entry._meta_parsed = json.loads(entry.metadata_json or "{}")
        except (json.JSONDecodeError, TypeError):
            entry._meta_parsed = {}

    return render_template("studio/pending.html", entries=entries)


@studio_bp.route("/knowledge/<int:id>/approve", methods=["POST"])
def knowledge_approve(id):
    """Approve a staged entry — set is_active=True."""
    entry = KnowledgeEntry.get_by_id(id)
    entry.is_active = True
    entry.updated_at = datetime.now()
    entry.save()
    flash("Entry approved: %s" % entry.title, "success")
    return redirect(url_for("studio.knowledge_pending"))


@studio_bp.route("/knowledge/<int:id>/reject", methods=["POST"])
def knowledge_reject(id):
    """Reject a staged entry — mark rejected + create RejectionLog."""
    from database import RejectionLog, ScrapeSource
    entry = KnowledgeEntry.get_by_id(id)

    # Parse metadata for source info
    try:
        meta = json.loads(entry.metadata_json or "{}")
    except (json.JSONDecodeError, TypeError):
        meta = {}

    # Find source
    source = None
    source_id = meta.get("scrape_source_id")
    if source_id:
        try:
            source = ScrapeSource.get_by_id(source_id)
        except ScrapeSource.DoesNotExist:
            pass

    # Create rejection log
    RejectionLog.create(
        original_entry_type=entry.entry_type,
        source=source,
        title=entry.title,
        content_snippet=entry.content[:200] if entry.content else "",
        source_url=meta.get("source_url", ""),
        content_hash=meta.get("raw_content_hash", ""),
    )

    # Mark entry as rejected (keep for dedup)
    entry.is_rejected = True
    entry.is_active = False
    entry.updated_at = datetime.now()
    entry.save()

    flash("Entry rejected: %s" % entry.title, "success")
    return redirect(url_for("studio.knowledge_pending"))


# -----------------------------------------
#  SCRAPE SOURCES
# -----------------------------------------

@studio_bp.route("/sources")
def sources_list():
    """Manage scrape sources."""
    from database import ScrapeSource, ScrapeLog
    sources = list(ScrapeSource.select().order_by(ScrapeSource.created_at.desc()))

    # Compute rejection rates per source
    for src in sources:
        try:
            approved = KnowledgeEntry.select().where(
                KnowledgeEntry.metadata_json.contains('"scrape_source_id": %d' % src.id),
                KnowledgeEntry.is_active == True,  # noqa: E712
                KnowledgeEntry.is_rejected == False,  # noqa: E712
            ).count()
            rejected = KnowledgeEntry.select().where(
                KnowledgeEntry.metadata_json.contains('"scrape_source_id": %d' % src.id),
                KnowledgeEntry.is_rejected == True,  # noqa: E712
            ).count()
            total = approved + rejected
            src._rejection_rate = round(rejected / total * 100) if total >= 10 else None
            src._total_entries = total
        except Exception:
            src._rejection_rate = None
            src._total_entries = 0

        # Last log
        try:
            src._last_log = (
                ScrapeLog.select()
                .where(ScrapeLog.source == src)
                .order_by(ScrapeLog.created_at.desc())
                .get()
            )
        except ScrapeLog.DoesNotExist:
            src._last_log = None

    return render_template("studio/sources.html", sources=sources)


@studio_bp.route("/sources/add", methods=["POST"])
def sources_add():
    """Add a new scrape source."""
    from database import ScrapeSource
    ScrapeSource.create(
        source_type=request.form.get("source_type", "web"),
        source_name=request.form.get("source_name", "").strip(),
        url=request.form.get("url", "").strip(),
        scrape_frequency=request.form.get("scrape_frequency", "weekly"),
        config_json=request.form.get("config_json", "{}"),
    )
    flash("Source added.", "success")
    return redirect(url_for("studio.sources_list"))


@studio_bp.route("/sources/<int:id>/toggle", methods=["POST"])
def sources_toggle(id):
    """Enable/disable a scrape source."""
    from database import ScrapeSource
    src = ScrapeSource.get_by_id(id)
    src.is_active = not src.is_active
    src.save()
    flash("Source %s." % ("enabled" if src.is_active else "disabled"), "success")
    return redirect(url_for("studio.sources_list"))


@studio_bp.route("/sources/<int:id>/run", methods=["POST"])
def sources_run(id):
    """Manual trigger — run scraper for one source in background thread."""
    import threading
    from database import ScrapeSource, RejectionLog
    from knowledge_scraper import _run_single_source

    src = ScrapeSource.get_by_id(id)
    rejections = list(
        RejectionLog.select()
        .order_by(RejectionLog.created_at.desc())
        .limit(50)
    )

    def _run():
        _run_single_source(src, rejections)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    flash("Scrape started for %s. Check scrape log for results." % src.source_name, "success")
    return redirect(url_for("studio.sources_list"))


# -----------------------------------------
#  SCRAPE LOG
# -----------------------------------------

@studio_bp.route("/scrape-log")
def scrape_log():
    """View scrape run history."""
    from database import ScrapeLog
    logs = list(
        ScrapeLog.select(ScrapeLog, ScrapeSource)
        .join(ScrapeSource)
        .order_by(ScrapeLog.created_at.desc())
        .limit(50)
    )
    return render_template("studio/scrape_log.html", logs=logs)
```

Add `ScrapeSource` to the imports at the top of `studio_routes.py`:

```python
from database import (
    KnowledgeEntry, AIModelConfig, StudioJob, TemplateCandidate,
    TemplatePerformance, EmailTemplate, ScrapeSource, db
)
```

- [ ] **Step 4: Create the 3 HTML templates**

Create `templates/studio/pending.html`, `templates/studio/sources.html`, and `templates/studio/scrape_log.html`. All should extend `base.html` and follow the existing dark theme design system. Implementer should reference existing `templates/studio/knowledge.html` and `templates/studio/dashboard.html` for the exact pattern.

**pending.html key elements:**
- Page title: "Pending Review"
- Counter: "X entries awaiting review"
- For each entry: card showing title, entry_type badge, relevance score, AI reasoning, source URL, content preview (first 200 chars)
- Two buttons per card: "Approve" (POST to /studio/knowledge/{id}/approve) and "Reject" (POST to /studio/knowledge/{id}/reject)
- Empty state: "No pending entries. The knowledge base is up to date."

**sources.html key elements:**
- Page title: "Scrape Sources"
- Table of sources: name, type, URL, frequency, last scraped, status (active/inactive), rejection rate warning if >50%
- Toggle button per source (POST to /studio/sources/{id}/toggle)
- "Run Now" button per source (POST to /studio/sources/{id}/run)
- Add source form at bottom

**scrape_log.html key elements:**
- Page title: "Scrape History"
- Table: source name, started, completed, status, found/staged/skipped/errored counts, error message

- [ ] **Step 5: Run test to verify it passes**

Run: `cd "C:\Users\davin\Claude Work Folder\mailenginehub-repo" && python -m pytest tests/test_knowledge_scraper.py::TestStudioRoutes -v`
Expected: All 5 tests PASS

- [ ] **Step 6: Commit**

```bash
git add studio_routes.py templates/studio/pending.html templates/studio/sources.html templates/studio/scrape_log.html tests/test_knowledge_scraper.py
git commit -m "feat: add Studio routes + templates for pending review, source management, scrape log"
```

---

### Task 7: APScheduler Integration + Dashboard Update

**Files:**
- Modify: `app.py:5407-5448` (scheduler block)
- Modify: `templates/studio/dashboard.html`

- [ ] **Step 1: Add scheduler job to app.py**

Add this block after the last `_scheduler.add_job(...)` call and before `_scheduler.start()` (around line 5446):

```python
    # Nightly knowledge enrichment at 4:30am
    def _run_nightly_knowledge_enrichment():
        try:
            import sys as _sk; _sk.path.insert(0, APP_DIR)
            from knowledge_scraper import run_knowledge_enrichment
            app.logger.info("Nightly knowledge enrichment starting...")
            run_knowledge_enrichment()
            app.logger.info("Knowledge enrichment complete")
        except Exception as _e:
            app.logger.error(f"Knowledge enrichment failed: {_e}")

    _scheduler.add_job(_run_nightly_knowledge_enrichment, "cron", hour=4, minute=30,
                       id="knowledge_enrichment", replace_existing=True)
```

- [ ] **Step 2: Update dashboard.html**

Add a pending review counter badge to the Studio dashboard. Read the existing `templates/studio/dashboard.html` first and add a stat card for "Pending Review" that shows the count of `KnowledgeEntry` rows where `is_active=False` and `is_rejected=False`. Link it to `/studio/knowledge/pending`.

Also add a "Source Health" section that shows any sources with >50% rejection rate as amber warnings.

Update the `dashboard()` route in `studio_routes.py` to pass the pending count:

```python
# Add to the dashboard() route's context:
pending_count = KnowledgeEntry.select().where(
    KnowledgeEntry.is_active == False,  # noqa: E712
    KnowledgeEntry.is_rejected == False,  # noqa: E712
).count()
```

Pass `pending_count=pending_count` to the `render_template()` call.

- [ ] **Step 3: Commit**

```bash
git add app.py studio_routes.py templates/studio/dashboard.html
git commit -m "feat: add 4:30am knowledge enrichment cron job + dashboard pending count"
```

---

### Task 8: Run All Tests + Final Verification

**Files:**
- Test: `tests/test_knowledge_scraper.py`

- [ ] **Step 1: Run the full test suite**

Run: `cd "C:\Users\davin\Claude Work Folder\mailenginehub-repo" && python -m pytest tests/test_knowledge_scraper.py -v`
Expected: All tests PASS (should be ~25+ tests across 7 test classes)

- [ ] **Step 2: Run the existing test suite to check for regressions**

Run: `cd "C:\Users\davin\Claude Work Folder\mailenginehub-repo" && python -m pytest tests/ -v --timeout=60`
Expected: All existing tests still PASS

- [ ] **Step 3: Verify the app starts without errors**

Run: `cd "C:\Users\davin\Claude Work Folder\mailenginehub-repo" && python -c "from database import *; init_db(); print('ScrapeSource table:', ScrapeSource.select().count()); print('KnowledgeEntry has is_rejected:', hasattr(KnowledgeEntry, 'is_rejected'))"`
Expected: No errors, prints table counts

- [ ] **Step 4: Final commit if any cleanup needed**

```bash
git add -u
git commit -m "chore: final cleanup for knowledge enrichment pipeline"
```
