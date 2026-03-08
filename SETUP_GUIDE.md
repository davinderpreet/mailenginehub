# 📧 MailEngine — Setup Guide
### Your In-House Email Marketing Platform

---

## What You've Built

A full email marketing platform that replaces Klaviyo/Omnisend, running on your computer.

| Feature | Status |
|---|---|
| 📋 Contact management + CSV import | ✅ Built |
| 🛍️ Shopify customer sync | ✅ Built |
| 🎨 Email template editor (with live preview) | ✅ Built |
| 📤 Campaign builder + one-click send | ✅ Built |
| 📊 Open rate & click tracking | ✅ Built |
| 💌 Unsubscribe handling | ✅ Built |
| 📬 Amazon SES integration | ✅ Built |

**Cost comparison:**
- Klaviyo: ~$700/month for 100k contacts
- This platform: ~$10 per 100k emails sent (Amazon SES only)

---

## Step 1: Install Python

Download and install Python 3.10+ from **python.org**

Verify it works — open Terminal/Command Prompt and run:
```
python --version
```

---

## Step 2: Install Dependencies

Open Terminal, navigate to the `email-platform` folder, and run:

```bash
pip install -r requirements.txt
```

---

## Step 3: Set Up Amazon SES (Free tier = 3,000 emails/month free)

1. Create a free AWS account at **aws.amazon.com**
2. Go to **SES** → **Verified Identities** → Add your sending email address
3. Click the verification link sent to your email
4. Go to **IAM** → **Users** → **Create user**
   - Attach policy: `AmazonSESFullAccess`
   - Create access key → copy the Key ID and Secret
5. **Important:** Request to exit SES Sandbox
   - In SES → Account Dashboard → "Request production access"
   - This allows you to email any address (sandbox = only verified emails)

---

## Step 4: Configure Your .env File

Open the `.env` file in the `email-platform` folder and fill in:

```
AWS_ACCESS_KEY_ID=your_key_from_step_3
AWS_SECRET_ACCESS_KEY=your_secret_from_step_3
AWS_REGION=us-east-1
DEFAULT_FROM_EMAIL=hello@yourdomain.com

SHOPIFY_STORE_URL=https://your-store.myshopify.com
SHOPIFY_ACCESS_TOKEN=shpat_your_token
```

---

## Step 5: Get Shopify API Token

1. In Shopify Admin → **Apps** → **Develop apps** → Create app
2. Set API scopes: check `read_customers`
3. Install the app
4. Copy the **Admin API access token** (starts with `shpat_`)
5. Paste it into `.env` as `SHOPIFY_ACCESS_TOKEN`

---

## Step 6: Run the Platform

```bash
python run.py
```

Open your browser and go to: **http://localhost:5000**

---

## Step 7: First Steps

1. **Settings** → Test your SES connection (send yourself a test email)
2. **Contacts** → Click "Sync Shopify" to pull in your customers
3. **Templates** → 3 starter templates are already there (Welcome, Sale, Win-Back)
4. **Campaigns** → Create your first campaign, pick a template, hit Send!

---

## Sending Your First Campaign

1. Go to **Templates** → Edit or create an email
2. Go to **Campaigns** → New Campaign
3. Fill in: Name, From Name, From Email, select Template
4. Choose Segment: "All subscribers" or filter by tag (e.g. `shopify`)
5. Click **Send Campaign Now**

---

## Personalisation Variables

Use these in your email templates:

| Variable | What it inserts |
|---|---|
| `{{first_name}}` | Customer's first name |
| `{{last_name}}` | Customer's last name |
| `{{email}}` | Customer's email |
| `{{unsubscribe_url}}` | Unsubscribe link (required!) |

---

## Making It Accessible From Anywhere (Optional)

To access your platform from any device on your network:

```bash
python run.py --host=0.0.0.0
```

Then access it at `http://YOUR_COMPUTER_IP:5000`

---

## Future Upgrades We Can Add

- **Automation sequences** (welcome series, abandoned cart, post-purchase)
- **Scheduled sends** (send at a specific time)
- **A/B testing** (test two subject lines)
- **Segments** (VIP customers, first-time buyers, etc.)
- **Klaviyo data migration** (import existing lists)
- **Deploy to a server** (run 24/7, accessible from anywhere)

---

Built for Davinder | Powered by Amazon SES
