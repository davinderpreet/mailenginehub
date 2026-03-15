# Knowledge Base Auto-Enrichment — Design Specification

## Goal

Build a fully autonomous scraping + AI classification pipeline that populates the Studio knowledge base from LDAS products, competitors (BlueParrott, Jabra, Plantronics/Poly), Amazon.ca reviews, trucker blogs, and email marketing trend sites. Everything is staged for human review. Deletions feed a self-learning rejection model so the system gets smarter over time.

## Target Audience Context

LDAS Electronics sells to **semi truck drivers** — dash cams, headsets, driver accessories. All scraped content must be relevant to this audience. Office/consumer/gaming products are irrelevant.

---

## Architecture

```
                    NIGHTLY CRON (4:30am, after AI engine jobs)
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
        reject  -> RejectionLog + mark is_rejected=True -> AI learns
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

All 3 models must be added to the `db.create_tables([...], safe=True)` call in `database.py` alongside existing models.

### ScrapeSource

```python
class ScrapeSource(BaseModel):
    source_type     = CharField()           # "shopify" | "web" | "amazon"
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
    items_errored   = IntegerField(default=0)  # AI call failed (network/parse error)
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
    content_hash    = CharField(default="") # SHA-256 hash for dedup across rejections
    created_at      = DateTimeField(default=datetime.now)

    class Meta:
        table_name = "rejection_logs"
```

### KnowledgeEntry changes (existing model)

Add one new field:

```python
is_rejected     = BooleanField(default=False)  # True when user rejects; row kept for dedup
```

When user rejects a staged entry: set `is_rejected=True` and `is_active=False` (do NOT delete the row). This preserves the `raw_content_hash` in `metadata_json` so the dedup check works across runs. The RejectionLog is created simultaneously for the self-learning prompt.

### KnowledgeEntry metadata_json format (new convention for scraped entries)

```json
{
    "scrape_source_id": 3,
    "scraped_at": "2026-03-16T04:30:00",
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
        """
        Check SHA-256 hash of raw_text against existing KnowledgeEntry rows.

        Queries ALL KnowledgeEntry rows (active, inactive, AND rejected) by
        extracting raw_content_hash from metadata_json. Also checks
        RejectionLog.content_hash for previously rejected-then-deleted entries.

        Returns True if this content has been seen before (skip it).
        """
        content_hash = hashlib.sha256(raw_text.encode()).hexdigest()
        # Check KnowledgeEntry metadata_json for matching hash
        for entry in KnowledgeEntry.select(KnowledgeEntry.metadata_json):
            try:
                meta = json.loads(entry.metadata_json or "{}")
                if meta.get("raw_content_hash") == content_hash:
                    return True
            except (json.JSONDecodeError, TypeError):
                continue
        # Check RejectionLog for matching hash
        if RejectionLog.select().where(RejectionLog.content_hash == content_hash).exists():
            return True
        return False
```

### Specialized Scrapers

**ShopifyScraper** — Reuses existing Shopify API patterns from `shopify_products.py`. Reads `ProductImageCache` rows + calls Shopify Admin API for full descriptions. No web scraping needed — uses authenticated API.

**WebScraper** — Generic HTTP + BeautifulSoup(html.parser) parser. All BeautifulSoup calls explicitly use `"html.parser"` (stdlib, no extra dependency). Configurable via `ScrapeSource.config_json`:
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

    Raises:
        AIProviderError: on API timeout or network failure (caller catches and logs)
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

**Rejection context format** — injected as `{rejection_context}` in the system prompt:

```
- [competitor_intel] "Jabra Evolve2 75 Office Headset" — Jabra Evolve2 75 wireless headset designed for open office... (REJECTED: office product, not trucker-relevant)
- [product_catalog] "Gaming Dash Cam with RGB" — RGB-lit dashboard camera for gaming streamers... (REJECTED: gaming product, not commercial trucking)
- (no rejections yet)
```

Each rejection is formatted as: `- [{entry_type}] "{title}" — {content_snippet} (REJECTED: {source_url})`

### Pipeline Orchestrator

```python
def run_knowledge_enrichment():
    """
    Main entry point — called nightly at 4:30am via APScheduler cron job in app.py.

    1. Query active ScrapeSource rows
    2. Filter by frequency (skip weekly if scraped < 7 days ago)
    3. Load recent rejections (last 50 RejectionLog rows)
    4. For each source:
       a. Create ScrapeLog row (status=running)
       b. Call scraper.fetch() -> raw chunks
       c. Deduplicate (skip if content_hash exists in KnowledgeEntry or RejectionLog)
       d. For each new chunk:
          - Try classify_content() via AI
          - If AIProviderError: increment items_errored, log warning, continue to next chunk
          - If result is None (score < 30): increment items_skipped
          - If result is not None: create KnowledgeEntry (is_active=False), increment items_staged
       e. Update ScrapeLog (status=ok, items_found, items_staged, items_skipped, items_errored)
       f. If scraper.fetch() raises: set ScrapeLog status=error, log error_message, continue to next source
    5. Update source.last_scraped_at
    """
```

**Rate limiting:** Max 2 req/sec for web scraping, 1 req/3sec for Amazon. 0.5 sec between AI classifier calls.

---

## Self-Learning System

### How rejection learning works:

1. **User rejects a staged entry** via `/studio/knowledge/<id>/reject` route
2. System sets `is_rejected=True`, `is_active=False` on the `KnowledgeEntry` row (row is NOT deleted — kept for dedup)
3. System creates `RejectionLog` row with: title, content snippet (first 200 chars), source, entry_type, source_url, content_hash
4. **Before each nightly run**, classifier loads the last 50 `RejectionLog` entries
5. These are formatted as rejection context (see format above) and injected into the classifier's system prompt
6. AI naturally avoids similar content patterns in future classifications

### Source health monitoring:

Rejection rate is computed as: `rejections / (rejections + approvals)` for each `ScrapeSource`, counted from `KnowledgeEntry` rows linked to that source via `scrape_source_id` in `metadata_json`:
- Approved = `is_active=True AND is_rejected=False` rows for that source
- Rejected = `is_rejected=True` rows for that source

If rejection rate > 50% (minimum 10 entries to avoid noise), dashboard shows warning: "Source X has N% rejection rate — consider deactivating."

### No fine-tuning, no embeddings:

The self-learning is pure prompt engineering — Claude sees what you've rejected and adjusts scoring. Simple, cheap, effective. No vector DB, no training data, no complexity.

---

## Integration Points

### Scheduling (app.py — APScheduler cron block)

Add a new cron job in `app.py`'s existing APScheduler block (where all other cron jobs are registered). The function `run_knowledge_enrichment()` is defined in `knowledge_scraper.py` and imported into `app.py`.

Schedule at **4:30 AM** — after all existing nightly jobs have completed:
- 2:30 AM — `run_nightly_scoring()` (includes contact scoring + template performance)
- 3:00 AM — `activity_sync`
- 3:30 AM — `nightly_intelligence`
- 3:45 AM — `deliverability_scores`
- **4:30 AM — `run_knowledge_enrichment()` (NEW)**

### Studio Routes (studio_routes.py — 8 new routes)

| Route | Method | Purpose |
|---|---|---|
| /studio/knowledge/pending | GET | Pending staged entries for review |
| /studio/knowledge/<id>/approve | POST | Set is_active=True, redirect back |
| /studio/knowledge/<id>/reject | POST | Set is_rejected=True + create RejectionLog, redirect back |
| /studio/sources | GET | Manage scrape sources |
| /studio/sources/add | POST | Add new source, redirect to sources list |
| /studio/sources/<id>/toggle | POST | Enable/disable source, redirect back |
| /studio/sources/<id>/run | POST | Manual trigger — spawns background thread (threading.Thread daemon=True, same pattern as campaign sends), flashes "Scrape started", redirects to sources page |
| /studio/scrape-log | GET | View scrape history (last 50 ScrapeLog rows) |

### Seed Sources (auto-created on first run)

Created by `seed_scrape_sources()` in `knowledge_scraper.py`, called from `run_knowledge_enrichment()` if `ScrapeSource.select().count() == 0`.

| Source Name | Type | URL/Search | Frequency |
|---|---|---|---|
| LDAS Shopify Products | shopify | (existing API) | daily |
| LDAS Blog | web | https://ldas.ca/blogs | daily |
| Amazon.ca — LDAS | amazon | "LDAS Electronics" | daily |
| Amazon.ca — BlueParrott | amazon | "BlueParrott trucker headset" | weekly |
| Amazon.ca — Jabra Trucker | amazon | "Jabra trucker headset" | weekly |
| Amazon.ca — Poly Trucker | amazon | "Plantronics trucker headset" | weekly |
| BlueParrott Products | web | https://www.blueparrott.com/headsets | weekly |
| Jabra Driver Headsets | web | https://www.jabra.com/business/office-headsets/jabra-perform | weekly |
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
| templates/studio/scrape_log.html | Scrape run history page | ~80 |

### Modified files:
| File | Change |
|---|---|
| database.py | Add 3 new models (ScrapeSource, ScrapeLog, RejectionLog) to class definitions AND to `db.create_tables([...], safe=True)` list. Add `is_rejected` BooleanField to KnowledgeEntry. |
| app.py | Import `run_knowledge_enrichment` from `knowledge_scraper`, add APScheduler cron job at 4:30am in existing scheduler block |
| studio_routes.py | Add 8 new routes for pending review + source management |
| requirements.txt | Add `beautifulsoup4>=4.12.0` (note: `requests` already present) |
| templates/studio/dashboard.html | Add pending count badge + source health warnings section |

---

## Verification

1. **Unit test:** Create ScrapeSource, run ShopifyScraper, verify KnowledgeEntry rows created with is_active=False and is_rejected=False
2. **Classification test:** Feed sample raw content to AI classifier, verify correct entry_type and relevance_score
3. **Dedup test:** Run scraper twice on same content, verify no duplicate entries (content_hash check works across KnowledgeEntry + RejectionLog)
4. **Rejection flow test:** Reject entries, verify is_rejected=True set on KnowledgeEntry, RejectionLog created with content_hash, entry NOT re-scraped on next run
5. **Rejection learning test:** Reject entries, run classifier again on similar content, verify lower scores from rejection context injection
6. **Source health test:** Create source with >50% rejection rate (min 10 entries), verify dashboard warning appears
7. **AI error handling test:** Mock AI provider to raise, verify items_errored incremented and pipeline continues to next chunk
8. **Integration test:** Run full run_knowledge_enrichment(), verify ScrapeLog rows correct
9. **UI test:** Navigate pending review page, approve/reject entries, verify state changes
10. **Rate limit test:** Verify Amazon scraper respects 1req/3sec limit
11. **Manual trigger test:** Click "Run Now" on a source, verify it runs in background thread and doesn't block the HTTP response
