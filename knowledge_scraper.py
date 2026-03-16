"""
knowledge_scraper.py — Knowledge Base Auto-Enrichment Pipeline

Scrapes products, blog posts, competitor info, and email trends.
Classifies content via AI and stages KnowledgeEntry rows for review.
"""

import hashlib
import json
import logging
import random
import time
import urllib.parse
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup

from ai_provider import get_provider, AIProviderError
from database import (
    KnowledgeEntry,
    ProductImageCache,
    RejectionLog,
    ScrapeLog,
    ScrapeSource,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Rate Limiter
# ---------------------------------------------------------------------------

_last_request_time = 0.0


def _rate_limit(min_interval: float = 0.5):
    """Sleep if needed to enforce a minimum interval between HTTP requests."""
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < min_interval:
        time.sleep(min_interval - elapsed)
    _last_request_time = time.time()


# ---------------------------------------------------------------------------
# User-Agent pool
# ---------------------------------------------------------------------------

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_3_1) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.3.1 Safari/605.1.15",
]


# ---------------------------------------------------------------------------
# BaseScraper
# ---------------------------------------------------------------------------

class BaseScraper:
    """Base class for all scrapers. Provides deduplication logic."""

    def __init__(self, source: ScrapeSource):
        self.source = source

    def fetch(self) -> list:
        """Return a list of raw chunk dicts. Subclasses must implement."""
        raise NotImplementedError

    def is_duplicate(self, raw_text: str) -> bool:
        """
        Return True if this content has already been seen.

        Checks:
        1. KnowledgeEntry.metadata_json for a matching raw_content_hash
           (includes both active and rejected entries).
        2. RejectionLog.content_hash
        """
        content_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()

        # Check all KnowledgeEntry rows (active + rejected)
        for entry in KnowledgeEntry.select():
            try:
                meta = json.loads(entry.metadata_json or "{}")
            except (json.JSONDecodeError, TypeError):
                meta = {}
            if meta.get("raw_content_hash") == content_hash:
                return True

        # Check RejectionLog
        exists = (
            RejectionLog.select()
            .where(RejectionLog.content_hash == content_hash)
            .exists()
        )
        return exists


# ---------------------------------------------------------------------------
# AI Classifier
# ---------------------------------------------------------------------------

_CLASSIFIER_SYSTEM_PROMPT = """You are a knowledge curator for LDAS Electronics, a Canadian B2B/B2C \
electronics reseller specialising in trucker headsets (BlueParrott, Jabra, Poly/Plantronics), \
two-way radios, and accessories.

Your job is to classify and summarise scraped web content for our internal knowledge base.

RECENTLY REJECTED CONTENT (do NOT re-classify similar items):
%s

Given a raw text chunk and its source URL, respond with a single JSON object:
{
  "entry_type": "<product_catalog|brand_copy|blog_post|competitor_intel|faq|testimonial>",
  "title": "<concise title, max 120 chars>",
  "content": "<cleaned, useful content — remove navigation/boilerplate>",
  "relevance_score": <0-100 integer — how relevant to LDAS Electronics>,
  "reasoning": "<one sentence explaining the score>"
}

Scoring guide:
- 80-100: Directly about LDAS products, trucker headsets, or email marketing tactics
- 50-79: Related industry content, competitor products, useful context
- 30-49: Tangentially relevant
- 0-29: Irrelevant (ads, unrelated products, pure navigation)

Return ONLY the JSON object, no markdown fences."""


def _build_rejection_context(rejections) -> str:
    """Format recent rejections into a bullet list for the system prompt."""
    if not rejections:
        return "(no rejections yet)"
    lines = []
    for r in rejections:
        snippet = (r.content_snippet or "")[:80]
        lines.append(
            f'- [{r.original_entry_type}] "{r.title}" -- {snippet} (REJECTED: {r.source_url})'
        )
    return "\n".join(lines)


def classify_content(raw_chunk: dict, source: ScrapeSource, rejections) -> dict | None:
    """
    Call the AI provider to classify a raw content chunk.

    Returns a dict with entry_type, title, content, relevance_score, reasoning,
    or None if relevance_score < 30.

    Raises AIProviderError on API failures (caller handles).
    """
    rejection_context = _build_rejection_context(rejections)
    system_prompt = _CLASSIFIER_SYSTEM_PROMPT % rejection_context

    raw_text = raw_chunk.get("raw_text", "")
    source_url = raw_chunk.get("url", source.url)
    user_prompt = f"SOURCE URL: {source_url}\n\nRAW CONTENT:\n{raw_text}"

    provider = get_provider()
    response_text = provider.complete(system_prompt, user_prompt, max_tokens=1024)

    # Strip markdown fences if present
    cleaned = response_text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        # Remove opening fence
        lines = lines[1:]
        # Remove closing fence
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    result = json.loads(cleaned)

    if result.get("relevance_score", 0) < 30:
        return None

    return result


# ---------------------------------------------------------------------------
# ShopifyScraper
# ---------------------------------------------------------------------------

class ShopifyScraper(BaseScraper):
    """Reads from the ProductImageCache table and yields content chunks."""

    def fetch(self) -> list:
        chunks = []
        for product in ProductImageCache.select():
            handle = product.handle or product.product_id
            url = f"https://ldas.ca/products/{handle}"
            raw_text = (
                f"{product.product_title} - {product.product_type} "
                f"- ${product.price} - {url}"
            )
            chunks.append({
                "raw_text": raw_text,
                "url": url,
                "image_urls": [product.image_url] if product.image_url else [],
            })
        return chunks


# ---------------------------------------------------------------------------
# WebScraper
# ---------------------------------------------------------------------------

class WebScraper(BaseScraper):
    """Generic scraper for arbitrary websites using CSS selectors."""

    def fetch(self) -> list:
        try:
            config = json.loads(self.source.config_json or "{}")
        except (json.JSONDecodeError, TypeError):
            config = {}

        base_url = self.source.url
        max_pages = int(config.get("max_pages", 1))
        chunks = []

        for page_num in range(1, max_pages + 1):
            if page_num == 1:
                page_url = base_url
            else:
                sep = "&" if "?" in base_url else "?"
                page_url = f"{base_url}{sep}page={page_num}"

            _rate_limit(0.5)
            try:
                headers = {"User-Agent": random.choice(_USER_AGENTS)}
                response = requests.get(page_url, headers=headers, timeout=15)
                response.raise_for_status()
            except requests.RequestException as exc:
                log.warning("WebScraper fetch error for %s: %s", page_url, exc)
                break

            page_chunks = self._parse_html(response.text, page_url)
            chunks.extend(page_chunks)

            if not page_chunks:
                break  # No content found — stop paginating

        return chunks

    def _parse_html(self, html: str, page_url: str) -> list:
        """Parse HTML using CSS selectors from config_json."""
        try:
            config = json.loads(self.source.config_json or "{}")
        except (json.JSONDecodeError, TypeError):
            config = {}

        item_selector = config.get("item_selector", "article")
        title_selector = config.get("title_selector", "h2")
        content_selector = config.get("content_selector", "p")

        soup = BeautifulSoup(html, "html.parser")
        items = soup.select(item_selector)

        chunks = []
        for item in items:
            # Extract title
            title_el = item.select_one(title_selector)
            title = title_el.get_text(strip=True) if title_el else ""

            # Extract content paragraphs
            content_els = item.select(content_selector)
            content = " ".join(el.get_text(strip=True) for el in content_els)

            raw_text = f"{title} {content}".strip()

            # Skip items with fewer than 20 characters
            if len(raw_text) < 20:
                continue

            # Extract image URLs
            image_urls = []
            for img in item.select("img[src]"):
                src = img.get("src", "")
                if src:
                    image_urls.append(src)

            chunks.append({
                "raw_text": raw_text,
                "url": page_url,
                "image_urls": image_urls,
            })

        return chunks


# ---------------------------------------------------------------------------
# AmazonScraper
# ---------------------------------------------------------------------------

class AmazonScraper(WebScraper):
    """Scrapes Amazon.ca search results for a given search term."""

    def _build_search_url(self) -> str:
        search_term = urllib.parse.quote_plus(self.source.url)
        return f"https://www.amazon.ca/s?k={search_term}"

    def _get_user_agent(self) -> str:
        return random.choice(_USER_AGENTS)

    def fetch(self) -> list:
        search_url = self._build_search_url()
        _rate_limit(3.0)

        try:
            headers = {"User-Agent": self._get_user_agent()}
            response = requests.get(search_url, headers=headers, timeout=15)
            response.raise_for_status()
        except requests.RequestException as exc:
            log.warning("AmazonScraper fetch error: %s", exc)
            return []

        soup = BeautifulSoup(response.text, "html.parser")
        results = soup.select('[data-component-type="s-search-result"]')

        chunks = []
        for item in results[:10]:
            # Title
            title_el = item.select_one("h2 a span")
            title = title_el.get_text(strip=True) if title_el else ""

            # Price
            price_el = item.select_one(".a-price .a-offscreen")
            price = price_el.get_text(strip=True) if price_el else ""

            # Rating
            rating_el = item.select_one(".a-icon-alt")
            rating = rating_el.get_text(strip=True) if rating_el else ""

            parts = [p for p in [title, price, rating] if p]
            raw_text = " | ".join(parts)

            if len(raw_text) < 20:
                continue

            chunks.append({
                "raw_text": raw_text,
                "url": search_url,
                "image_urls": [],
            })

        return chunks


# ---------------------------------------------------------------------------
# EmailTrendsScraper
# ---------------------------------------------------------------------------

class EmailTrendsScraper(WebScraper):
    """WebScraper subclass that tags chunks as email_trends."""

    def _parse_html(self, html: str, page_url: str) -> list:
        chunks = super()._parse_html(html, page_url)
        for chunk in chunks:
            chunk["tags"] = "email_trends"
        return chunks


# ---------------------------------------------------------------------------
# Seed Sources
# ---------------------------------------------------------------------------

_SEED_SOURCES = [
    (
        "shopify",
        "LDAS Shopify Products",
        "",
        "daily",
        {},
    ),
    (
        "web",
        "LDAS Blog",
        "https://ldas.ca/blogs",
        "daily",
        {
            "item_selector": "article",
            "title_selector": "h2",
            "content_selector": "p",
            "max_pages": 3,
        },
    ),
    (
        "amazon",
        "Amazon.ca - LDAS",
        "LDAS Electronics",
        "daily",
        {},
    ),
    (
        "amazon",
        "Amazon.ca - BlueParrott",
        "BlueParrott trucker headset",
        "weekly",
        {},
    ),
    (
        "amazon",
        "Amazon.ca - Jabra Trucker",
        "Jabra trucker headset",
        "weekly",
        {},
    ),
    (
        "amazon",
        "Amazon.ca - Poly Trucker",
        "Plantronics trucker headset",
        "weekly",
        {},
    ),
    (
        "web",
        "BlueParrott Products",
        "https://www.blueparrott.com/headsets",
        "weekly",
        {
            "item_selector": ".product-card",
            "title_selector": "h3",
            "content_selector": "p",
            "max_pages": 2,
        },
    ),
    (
        "web",
        "Jabra Driver Headsets",
        "https://www.jabra.com/business/office-headsets/jabra-perform",
        "weekly",
        {
            "item_selector": ".product-item",
            "title_selector": "h2",
            "content_selector": ".product-description",
            "max_pages": 2,
        },
    ),
    (
        "web",
        "Really Good Emails",
        "https://reallygoodemails.com",
        "weekly",
        {
            "item_selector": ".email-card",
            "title_selector": "h3",
            "content_selector": "p",
            "max_pages": 2,
            "tags": "email_trends",
        },
    ),
    (
        "web",
        "Litmus Blog",
        "https://www.litmus.com/blog",
        "weekly",
        {
            "item_selector": "article",
            "title_selector": "h2",
            "content_selector": "p",
            "max_pages": 3,
            "tags": "email_trends",
        },
    ),
]


def seed_scrape_sources():
    """Create seed ScrapeSource rows. Idempotent — only runs if the table is empty."""
    if ScrapeSource.select().count() > 0:
        return

    for source_type, source_name, url, frequency, config in _SEED_SOURCES:
        ScrapeSource.create(
            source_type=source_type,
            source_name=source_name,
            url=url,
            scrape_frequency=frequency,
            is_active=True,
            config_json=json.dumps(config),
        )
    log.info("Seeded %d scrape sources.", len(_SEED_SOURCES))


# ---------------------------------------------------------------------------
# Pipeline Orchestrator
# ---------------------------------------------------------------------------

_SCRAPER_CLASSES = {
    "shopify": ShopifyScraper,
    "web": WebScraper,
    "amazon": AmazonScraper,
}


def _get_scraper(source: ScrapeSource) -> BaseScraper:
    """Return the appropriate scraper instance for the given source."""
    try:
        config = json.loads(source.config_json or "{}")
    except (json.JSONDecodeError, TypeError):
        config = {}

    if config.get("tags") == "email_trends":
        return EmailTrendsScraper(source)

    scraper_cls = _SCRAPER_CLASSES.get(source.source_type, WebScraper)
    return scraper_cls(source)


def _run_single_source(source: ScrapeSource, rejections: list):
    """
    Run the full scrape-classify-stage pipeline for one source.

    Creates a ScrapeLog row, fetches chunks, deduplicates, classifies,
    and stages KnowledgeEntry rows (is_active=False pending review).
    """
    scrape_log = ScrapeLog.create(source=source, status="running")

    items_found = 0
    items_staged = 0
    items_skipped = 0
    items_errored = 0
    error_message = ""

    try:
        scraper = _get_scraper(source)
        chunks = scraper.fetch()
        items_found = len(chunks)

        for chunk in chunks:
            raw_text = chunk.get("raw_text", "")
            content_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()

            # Dedup check
            if scraper.is_duplicate(raw_text):
                items_skipped += 1
                continue

            # AI classification
            try:
                result = classify_content(chunk, source, rejections)
            except AIProviderError as exc:
                log.warning("AI error classifying chunk from %s: %s", source.source_name, exc)
                items_errored += 1
                continue

            if result is None:
                items_skipped += 1
                continue

            # Build metadata
            metadata = {
                "raw_content_hash": content_hash,
                "source_id": source.id,
                "source_name": source.source_name,
                "source_url": chunk.get("url", source.url),
                "image_urls": chunk.get("image_urls", []),
                "relevance_score": result.get("relevance_score", 0),
                "reasoning": result.get("reasoning", ""),
            }
            if chunk.get("tags"):
                metadata["tags"] = chunk["tags"]

            KnowledgeEntry.create(
                entry_type=result["entry_type"],
                title=result["title"],
                content=result["content"],
                metadata_json=json.dumps(metadata),
                is_active=False,
                is_rejected=False,
            )
            items_staged += 1

        status = "ok"

    except Exception as exc:
        log.error("Error running source %s: %s", source.source_name, exc)
        error_message = str(exc)
        status = "error"

    # Update scrape log
    ScrapeLog.update(
        status=status,
        completed_at=datetime.now(),
        items_found=items_found,
        items_staged=items_staged,
        items_skipped=items_skipped,
        items_errored=items_errored,
        error_message=error_message,
    ).where(ScrapeLog.id == scrape_log.id).execute()

    # Update source last_scraped_at
    ScrapeSource.update(last_scraped_at=datetime.now()).where(
        ScrapeSource.id == source.id
    ).execute()

    log.info(
        "Source '%s': found=%d staged=%d skipped=%d errored=%d",
        source.source_name,
        items_found,
        items_staged,
        items_skipped,
        items_errored,
    )


def run_knowledge_enrichment():
    """
    Main entry point for the enrichment pipeline.

    1. Seeds scrape sources if the table is empty.
    2. Loads recent rejection context.
    3. Runs each eligible active source (respecting frequency).
    """
    seed_scrape_sources()

    # Load last 50 rejections for AI context
    rejections = list(
        RejectionLog.select().order_by(RejectionLog.created_at.desc()).limit(50)
    )

    now = datetime.now()
    today = now.date()

    for source in ScrapeSource.select().where(ScrapeSource.is_active == True):  # noqa: E712
        last = source.last_scraped_at

        if source.scrape_frequency == "weekly":
            if last and (now - last) < timedelta(days=7):
                log.debug("Skipping weekly source '%s' (scraped recently).", source.source_name)
                continue

        elif source.scrape_frequency == "daily":
            if last and last.date() == today:
                log.debug("Skipping daily source '%s' (already scraped today).", source.source_name)
                continue

        _run_single_source(source, rejections)
