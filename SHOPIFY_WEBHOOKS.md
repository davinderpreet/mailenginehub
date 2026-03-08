# Shopify Webhooks Setup

Your MailEngine now has **real-time customer ingestion** via Shopify webhooks!

## What's New

- **Real-time sync**: New customers are added instantly when created in Shopify (no manual refresh needed)
- **HMAC verification**: All webhooks are cryptographically signed and verified for security
- **Auto-enrollment**: New contacts automatically enroll in your flows

## Setup Instructions

### Step 1: Note Your Webhook URLs

These are the two endpoints MailEngine listens on:

```
https://your-domain.com/webhooks/shopify/customer/create
https://your-domain.com/webhooks/shopify/customer/update
```

Replace `your-domain.com` with your actual MailEngine server URL (e.g., if running locally: `http://localhost:5000`).

**Note**: For localhost testing, you'll need ngrok or similar to expose port 5000 to the internet.

### Step 2: Register Webhooks in Shopify Admin

1. Go to **Shopify Admin** → **Settings** → **Apps and integrations**
2. Click **Develop apps**
3. Click **Create an app** (or select your existing MailEngine app)
4. Go to **Configuration** tab
5. Under **Admin API access scopes**, make sure these are checked:
   - `read:customers`
   - `write:webhooks`

### Step 3: Add Webhooks

In your app's configuration:

1. Scroll to **Webhooks** → **Add webhook**
2. Set:
   - **Topic**: `customers/create`
   - **Webhook URL**: `https://your-domain.com/webhooks/shopify/customer/create`
   - **Webhook API version**: Latest stable
3. Click **Save**

4. Add a second webhook:
   - **Topic**: `customers/update`
   - **Webhook URL**: `https://your-domain.com/webhooks/shopify/customer/update`
   - **Webhook API version**: Latest stable
5. Click **Save**

### Step 4: Verify

After saving:
- Shopify shows "Webhook saved" ✓
- You should see a "Subscribed" status next to both topics
- MailEngine will now receive real-time events

## How It Works

When a customer is created or updated in Shopify:
1. Shopify POSTs customer data + HMAC signature to MailEngine
2. MailEngine verifies the signature using your `SHOPIFY_ACCESS_TOKEN` (in `.env`)
3. Contact is created or updated with full details:
   - Email, name, phone
   - Shopify ID, order count, total spent
   - City, country, creation date
4. New customers auto-enroll in any "contact_created" trigger flows

## Testing (Local Development)

To test webhooks locally, use **ngrok**:

```bash
# Install ngrok: https://ngrok.com/download
# Then expose port 5000:
ngrok http 5000
```

This gives you a public URL like `https://abc123.ngrok.io`. Use that in Step 2 above.

## Troubleshooting

**Webhooks not firing?**
- Check that `SHOPIFY_ACCESS_TOKEN` in `.env` matches your app's token
- Verify the webhook URLs are correct in Shopify Admin
- Check `server.log` for any webhook rejection errors

**HMAC verification failed?**
- This means the signature doesn't match — usually a token mismatch
- Regenerate your access token in Shopify Admin
- Update `SHOPIFY_ACCESS_TOKEN` in `.env`
- Restart the app

**Contacts not appearing?**
- Webhooks can take a few seconds to process
- Check that the Contact was created (view `/contacts`)
- Flows with "contact_created" trigger should auto-enroll them

## Notes

- Existing contacts from manual Shopify syncs are preserved
- Webhooks only trigger on **new** and **updated** customers, not deletions
- Each webhook call is idempotent (safe to call multiple times)
