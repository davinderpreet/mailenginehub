# Knowledge Base Auto-Enrichment — Design Specification

## Goal

Build a fully autonomous scraping + AI classification pipeline that populates the Studio knowledge base from LDAS products, competitors (BlueParrott, Jabra, Plantronics/Poly), Amazon.ca reviews, trucker blogs, and email marketing trend sites. Everything is staged for human review. Deletions feed a self-learning rejection model so the system gets smarter over time.

## Target Audience Context

LDAS Electronics sells to **semi truck drivers** — dash cams, headsets, driver accessories. All scraped content must be relevant to this audience. Office/consumer/gaming products are irrelevant.

---

## Architecture

```
                    NIGHTLY CRON (3am, after AI engine)
                              |
                    +-------------------+
                    |  Scrape Scheduler  |
                    | (knowledge_scraper)|
                    +----+-+-+-+-+------+
                         | | | | |
          +--------------+ | | | +-------------+
          v                v v v               v
    +----------+  +-------+ +-------+  +-----------+
    | Shopify  |  | Web   | |Amazon |  | Email     |
    | Products |  |Scraper| |Reviews|  | Marketing |
    +----+-----+  +---+---+ +---+---+  | Trends    |
         |            |         |       +-----+-----+
         +-----+------+---------+-----------+
               v
      +----------------+
      | AI Classifier  |  Claude Haiku
      | - relevance    |  Score 0-100
      | - entry_type   |  Clean title/content
      | - rejection    |  Check rejection history
      |   check        |
      +-------+--------+
              v
      +----------------+
      | KnowledgeEntry |  is_active=False (staged)
      | + ScrapeSource |  source tracking
      | + ScrapeLog    |  run history
      | + RejectionLog |  self-learning
      +----------------+
              |
        YOU REVIEW
        approve -> is_active=True -> Studio uses it
        delete  -> RejectionLog   -> AI learns
```

---

## Data Sources

### Stream 1 — Product & Industry Knowledge

| Source | What we scrape | Entry types | Frequency |
|---|---|---|---|
| Shopify (ldas.ca) | Product titles, descriptions, specs, prices, images | product_catalog | Daily |
| ldas.ca/blogs | Blog posts, product tips, trucker content | blog_post, brand_copy | Daily |
| Amazon.ca — LDAS | LDAS product listings + customer reviews | product_catalog, testimonial | Daily |
| Amazon.ca — BlueParrott | Competitor listings + reviews | competitor_intel, testimonial | Weekly |
| Amazon.ca — Jabra | Competitor listings + reviews | competitor_intel | Weekly |
| Amazon.ca — Poly | Competitor listings + reviews | competitor_intel | Weekly |
| BlueParrott website | Product pages, specs, pricing | competitor_intel | Weekly |
| Jabra website | Trucker/driver headset pages | competitor_intel | Weekly |
| Trucker forums/blogs | Driver pain points, lifestyle content | faq, blog_post | Weekly |

### Stream 2 — Email Marketing Intelligence

| Source | What we scrape | Entry types | Frequency |
|---|---|---|---|
| Really Good Emails | Top email designs, layout trends | blog_post (tagged email_trends) | Weekly |
| Litmus blog | Email best practices, rendering tips | blog_post (tagged email_trends) | Weekly |
| Mailchimp/Klaviyo resources | Template galleries, design guides | blog_post (tagged email_trends) | Weekly |

---

## New Database Models (added to database.py)

### ScrapeSource

```python
class ScrapeSource(BaseModel):
    source_type     = CharField()           # "shopify" | "web" | "amazon" | "rss"
    source_name     = CharField()           # "BlueParrott Products"
    url             = CharField()           # base URL or search term
    scrape_frequency = CharField(default="weekly")  # "daily" | "weekly"
    is_active       = BooleanField(default=True)
    last_scraped_at = DateTimeField(null=True)
    config_json     = TextField(default="{}")  # CSS selectors, search terms, pagination config
    created_at      = DateTimeField(default=datetime.now)

    class Meta:
        table_name = "scrape_sources"
```

### ScrapeLog

```python
class ScrapeLog(BaseModel):
    source          = ForeignKeyField(ScrapeSource, backref="logs")
    started_at      = DateTimeField(default=datetime.now)
    completed_at    = DateTimeField(null=True)
    status          = CharField(default="running")  # "running" | "ok" | "error"
    items_found     = IntegerField(default=0)
    items_staged    = IntegerField(default=0)
    items_skipped   = IntegerField(default=0)  # auto-rejected by AI (score < 30)
    error_message   = TextField(default="")
    created_at      = DateTimeField(default=datetime.now)

    class Meta:
        table_name = "scrape_logs"
```

### RejectionLog

```python
class RejectionLog(BaseModel):
    original_entry_type = CharField()       # what the deleted entry was
    source          = ForeignKeyField(ScrapeSource, null=True, backref="rejections")
    title           = CharField()           # title of deleted entry
    content_snippet = TextField(default="") # first 200 chars of deleted content
    source_url      = CharField(default="") # original URL that was scraped
    created_at      = DateTimeField(default=datetime.now)

    class Meta:
        table_name = "rejection_logs"
```

### KnowledgeEntry metadata_json format (existing model, new metadata convention)

```json
{
    "scrape_source_id": 3,
    "scraped_at": "2026-03-16T03:15:00",
    "source_url": "https://amazon.ca/dp/B09XYZ",
    "relevance_score": 82,
    "ai_reasoning": "Customer review of LDAS TH11 headset, trucker mentions road noise cancellation",
    "image_urls": ["https://..."],
    "raw_content_hash": "abc123def456"
}
```

The `raw_content_hash` (SHA-256 of raw scraped text) prevents duplicate entries across runs.

---

## Module: knowledge_scraper.py (~400 lines)

### Base Scraper

```python
class BaseScraper:
    def __init__(self, source: ScrapeSource):
        self.source = source

    def fetch(self) -> list[dict]:
        """Returns list of raw content chunks: [{"raw_text": ..., "url": ..., "image_urls": [...]}]"""
        raise NotImplementedError

    def is_duplicate(self, raw_text: str) -> bool:
        """Check content_hash against existing KnowledgeEntry metadata_json."""
        ...
```

### Specialized Scrapers

**ShopifyScraper** — Reuses existing Shopify API patterns from `shopify_products.py`. Reads `ProductImageCache` rows + calls Shopify Admin API for full descriptions. No web scraping needed — uses authenticated API.

**WebScraper** — Generic HTTP + BeautifulSoup parser. Configurable via `ScrapeSource.config_json`:
```json
{
    "content_selector": "article.blog-post",
    "title_selector": "h1.title",
    "item_selector": ".product-card",
    "max_pages": 3,
    "follow_links": true
}
```
Handles: ldas.ca blog, BlueParrott product pages, Jabra pages, Litmus blog, Really Good Emails.

**AmazonScraper** — Subclass of WebScraper. Searches Amazon.ca with a search term. Extracts: product title, price, star rating, top 5 reviews. Uses rotating User-Agent headers. Rate limited to 1 request per 3 seconds with random jitter.

**EmailTrendsScraper** — Subclass of WebScraper. Targets email marketing sites. Tags all output with `email_trends` in metadata. Extracts article titles, key takeaways, design pattern descriptions.

### AI Classifier

```python
def classify_content(raw_chunk: dict, source: ScrapeSource, rejections: list) -> dict | None:
    """
    Send raw content to Claude Haiku for classification.

    Args:
        raw_chunk: {"raw_text": str, "url": str, "image_urls": list}
        source: the ScrapeSource this came from
        rejections: recent RejectionLog entries (last 50) for self-learning

    Returns:
        {
            "entry_type": str,      # product_catalog | brand_copy | testimonial | etc.
            "title": str,           # clean, concise title
            "content": str,         # summarized, clean content (max 500 words)
            "relevance_score": int, # 0-100
            "reasoning": str,       # why this score
        }
        or None if relevance_score < 30
    """
```

**System prompt for classifier:**

```
You are a knowledge curator for LDAS Electronics, a Canadian brand selling
dash cams, headsets, and accessories to semi truck drivers.

Your job: classify and summarize scraped web content for the knowledge base.

RELEVANCE RULES:
- 80-100: Directly about LDAS products, or truck driver electronics
- 60-79: About trucking lifestyle, road safety, fleet tech, or email marketing best practices
- 30-59: Tangentially related — general electronics, delivery drivers, automotive
- 0-29: IRRELEVANT — office products, gaming, consumer tech, unrelated industries

REJECT (score 0): Office headsets, gaming accessories, consumer electronics not for drivers,
content about industries other than trucking/transportation.

The user has rejected these entries recently. Avoid similar content:
{rejection_context}

Respond with JSON only:
{"entry_type": "...", "title": "...", "content": "...", "relevance_score": N, "reasoning": "..."}
```

### Pipeline Orchestrator

```python
def run_knowledge_enrichment():
    """
    Main entry point — called nightly at 3am.

    1. Query active ScrapeSource rows
    2. Filter by frequency (skip weekly if scraped < 7 days ago)
    3. Load recent rejections (last 50 RejectionLog rows)
    4. For each source:
       a. Create ScrapeLog row (status=running)
       b. Call scraper.fetch() -> raw chunks
       c. Deduplicate (skip if content_hash exists)
       d. Classify each chunk via AI
       e. Stage entries with relevance_score >= 30 as KnowledgeEntry (is_active=False)
       f. Update ScrapeLog (items_found, items_staged, items_skipped)
    5. Update source.last_scraped_at
    """
```

**Rate limiting:** Max 2 req/sec for web scraping, 1 req/3sec for Amazon. 0.5 sec between AI classifier calls.

---

## Self-Learning System

### How rejection learning works:

1. **User deletes a staged entry** via `/studio/knowledge/<id>/reject` route
2. System creates `RejectionLog` row with: title, content snippet (first 200 chars), source, entry_type, source_url
3. **Before each nightly run**, classifier loads the last 50 `RejectionLog` entries
4. These are injected into the classifier's system prompt as rejection context
5. AI naturally avoids similar content patterns in future classifications
6. **Source health monitoring:** If a `ScrapeSource` has >50% rejection rate (over last 20 entries), dashboard shows a warning: "Source X has high rejection rate — consider deactivating"

### No fine-tuning, no embeddings:

The self-learning is pure prompt engineering — Claude sees what you've rejected and adjusts scoring. Simple, cheap, effective. No vector DB, no training data, no complexity.

---

## Integration Points

### Scheduling (ai_engine.py)

Add `run_knowledge_enrichment()` call after existing nightly jobs:

```
1:00 AM — score_all_contacts()          (existing)
2:00 AM — generate_daily_plan()         (existing)
2:30 AM — update_template_performance() (existing)
3:00 AM — run_knowledge_enrichment()    (NEW)
```

### Studio Routes (studio_routes.py — 8 new routes)

| Route | Method | Purpose |
|---|---|---|
| /studio/knowledge/pending | GET | Pending staged entries for review |
| /studio/knowledge/<id>/approve | POST | Set is_active=True |
| /studio/knowledge/<id>/reject | POST | Delete + create RejectionLog |
| /studio/sources | GET | Manage scrape sources |
| /studio/sources/add | POST | Add new source |
| /studio/sources/<id>/toggle | POST | Enable/disable source |
| /studio/sources/<id>/run | POST | Manual trigger — run one source now |
| /studio/scrape-log | GET | View scrape history |

### Seed Sources (auto-created on first run)

| Source Name | Type | URL/Search | Frequency |
|---|---|---|---|
| LDAS Shopify Products | shopify | (existing API) | daily |
| LDAS Blog | web | https://ldas.ca/blogs | daily |
| Amazon.ca — LDAS | amazon | "LDAS Electronics" | daily |
| Amazon.ca — BlueParrott | amazon | "BlueParrott trucker headset" | weekly |
| Amazon.ca — Jabra Trucker | amazon | "Jabra trucker headset" | weekly |
| Amazon.ca — Poly Trucker | amazon | "Plantronics trucker headset" | weekly |
| BlueParrott Products | web | https://www.blueparrott.com/headsets | weekly |
| Jabra Trucker Headsets | web | https://www.jabra.com/business/office-headsets | weekly |
| Really Good Emails | web | https://reallygoodemails.com | weekly |
| Litmus Blog | web | https://www.litmus.com/blog | weekly |

---

## Files

### New files:
| File | Purpose | ~Lines |
|---|---|---|
| knowledge_scraper.py | All scrapers + AI classifier + pipeline orchestrator | ~400 |
| templates/studio/pending.html | Pending review page (approve/reject UI) | ~150 |
| templates/studio/sources.html | Source management page | ~120 |

### Modified files:
| File | Change |
|---|---|
| database.py | Add 3 models (ScrapeSource, ScrapeLog, RejectionLog), add to init_db() |
| ai_engine.py | Add run_knowledge_enrichment() call at 3am |
| studio_routes.py | Add 8 new routes for pending review + source management |
| requirements.txt | Add beautifulsoup4>=4.12.0 |
| templates/studio/dashboard.html | Add pending count badge + source health section |

---

## Verification

1. **Unit test:** Create ScrapeSource, run ShopifyScraper, verify KnowledgeEntry rows created with is_active=False
2. **Classification test:** Feed sample raw content to AI classifier, verify correct entry_type and relevance_score
3. **Dedup test:** Run scraper twice on same content, verify no duplicate entries (content_hash check)
4. **Rejection learning test:** Delete entries, run classifier again on similar content, verify lower scores
5. **Source health test:** Create source with >50% rejection rate, verify dashboard warning appears
6. **Integration test:** Run full run_knowledge_enrichment(), verify ScrapeLog rows correct
7. **UI test:** Navigate pending review page, approve/reject entries, verify state changes
8. **Rate limit test:** Verify Amazon scraper respects 1req/3sec limit
