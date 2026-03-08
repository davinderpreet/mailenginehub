# MailEngineHub

Self-hosted email marketing platform built for e-commerce. Replaces Klaviyo/Omnisend at a fraction of the cost.

**Live at:** [mailenginehub.com](https://mailenginehub.com)

---

## Why This Exists

Klaviyo charges ~$700/month for 100k contacts. MailEngineHub sends emails through Amazon SES at **~$0.10 per 1,000 emails** — that's roughly $10 for 100k sends. Full control over your data, no monthly platform fees.

---

## Features

- **Contact Management** — CSV import, Shopify customer sync, tag-based segmentation
- **Email Template Editor** — HTML editor with live preview
- **Campaign Builder** — Segment targeting, one-click send, real-time stats
- **Automation Flows** — Welcome series, post-purchase, win-back sequences with delays
- **Deliverability Warmup Engine** — 8-phase graduated sending (50/day to unlimited)
- **Health Score Dashboard** — SPF, DKIM, DMARC, open rate, bounce rate tracking (0-100 score)
- **Real-Time Tracking** — Open rate and click tracking via tracking pixels
- **AI Customer Scoring** — Churn prediction, customer segmentation, revenue-at-risk analysis
- **AI Email Generation** — Claude-powered personalized emails with structured JSON output
- **Premium Email Templates** — 8 conversion-optimized HTML templates with product images, discount codes, urgency elements
- **Shopify Discount Codes** — Auto-generated single-use codes via Shopify Price Rules API
- **Customer Profiles** — Purchase history, lifetime value, category preferences, AI risk scores
- **Shopify Webhooks** — Real-time customer and order sync

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Flask (Python 3.10+) |
| Database | SQLite via Peewee ORM |
| Email Delivery | Amazon SES |
| AI Engine | Anthropic Claude API |
| E-commerce | Shopify Admin REST API |
| Production | Gunicorn on Linux VPS |

---

## File Map

| File | Purpose |
|---|---|
| `run.py` | Entry point — loads .env, initializes DB, starts Flask |
| `app.py` | All routes, warmup logic, health scoring, campaign sending |
| `database.py` | Peewee ORM models + `init_db()` |
| `email_sender.py` | Amazon SES email sending via boto3 |
| `shopify_sync.py` | Shopify customer/order sync via REST API |
| `ai_engine.py` | AI contact scoring, decision planning, email generation |
| `email_templates.py` | 8 premium HTML email template renderers |
| `discount_engine.py` | Shopify discount code generation (Price Rules API) |
| `shopify_products.py` | Product image cache from Shopify |
| `shopify_enrichment.py` | Customer profile enrichment from Shopify data |
| `data_enrichment.py` | Additional data enrichment utilities |
| `activity_sync.py` | Customer activity tracking and sync |
| `email_sender.py` | Amazon SES integration |
| `health_check.py` | System diagnostic utility |
| `watchdog.py` | Process monitor — auto-restarts on crash |
| `templates/` | 18 Jinja2 HTML templates (dark glass theme) |

---

## Quick Start

```bash
# Clone the repo
git clone https://github.com/YOUR_USERNAME/mailenginehub.git
cd mailenginehub

# Set up environment
cp .env.example .env
# Edit .env with your AWS, Shopify, and Anthropic credentials

# Install dependencies
pip install -r requirements.txt

# Run
python run.py
```

Open **http://localhost:5000** — login with the admin credentials you set in `.env`.

---

## Warmup Phases

The deliverability engine gradually increases sending volume to build sender reputation:

| Phase | Daily Limit | Duration |
|---|---|---|
| 1 — Ignition | 50/day | 3 days |
| 2 — Spark | 150/day | 4 days |
| 3 — Gaining Trust | 350/day | 7 days |
| 4 — Building | 750/day | 7 days |
| 5 — Momentum | 1,500/day | 7 days |
| 6 — Scaling | 3,000/day | 7 days |
| 7 — High Volume | 7,000/day | 7 days |
| 8 — Full Send | Unlimited | - |

Auto-advances when: open rate >= 15% AND bounce rate < 3%.

---

## AI Email Generation

The AI engine uses Claude to generate personalized emails:

1. **Customer scoring** — Nightly churn prediction using purchase recency, frequency, AOV
2. **Decision planning** — Selects which customers need emails and what type
3. **Structured output** — Claude returns JSON (headline, paragraphs, CTA) not raw HTML
4. **Template rendering** — JSON merged with product images + discount codes into premium HTML
5. **Cost** — ~$0.002 per email generated (~$6.59/month at 100 emails/day)

**8 email purposes:** cart abandonment, browse abandonment, winback, welcome, upsell, loyalty reward, re-engagement, high intent

---

## Documentation

- [CLAUDE.md](CLAUDE.md) — Technical reference (routes, models, functions)
- [SETUP_GUIDE.md](SETUP_GUIDE.md) — Detailed first-time setup
- [SHOPIFY_WEBHOOKS.md](SHOPIFY_WEBHOOKS.md) — Webhook configuration

---

## Environment Variables

See [.env.example](.env.example) for all 17 required variables:

- **Amazon SES** — AWS credentials + region + from email
- **Shopify** — Store URL + access token + webhook secret
- **Flask** — Secret key + environment
- **Admin** — Username + password + base URL
- **AI Engine** — Anthropic API key + enable/disable toggle
- **SES Sandbox** — Sandbox mode flag + test email

---

## License

Proprietary. Built by Davinder Sharma for LDAS Electronics.
