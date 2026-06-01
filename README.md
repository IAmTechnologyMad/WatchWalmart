# Walmart Stock Tracker — Discord Notifications via Render

A lightweight Python service that monitors a Walmart product page for stock changes and sends **Discord webhook notifications** when the product comes back in stock.

## How It Works

1. A **Flask web server** runs on Render (free tier Web Service).
2. An **external cron service** (like [cron-job.org](https://cron-job.org)) pings the `/check` endpoint every few minutes.
3. The app **scrapes the Walmart product page**, parses the stock status from the `__NEXT_DATA__` JSON blob.
4. If the product **transitions from out-of-stock → in-stock**, it sends a rich embed notification to your **Discord webhook**.
5. It only notifies **once** per transition (no spam).

## Endpoints

| Endpoint  | Description                              |
|-----------|------------------------------------------|
| `/`       | Service info / health                    |
| `/check`  | Triggers a stock check (hit via cron)    |
| `/health` | Simple health check                     |

## Setup

### 1. Environment Variables

Set these in Render's dashboard (Environment tab):

| Variable             | Required | Description                                    |
|----------------------|----------|------------------------------------------------|
| `DISCORD_WEBHOOK_URL`| ✅ Yes   | Your Discord channel webhook URL               |
| `PRODUCT_URL`        | Optional | Walmart product URL (has a default built-in)   |

### 2. Deploy to Render

1. Push this folder to a **GitHub repository**.
2. Go to [render.com](https://render.com) → **New** → **Web Service**.
3. Connect your GitHub repo.
4. Settings:
   - **Runtime:** Docker
   - **Instance Type:** Free
   - **Build Command:** *(leave blank, Docker handles it)*
   - **Start Command:** *(leave blank, Docker handles it)*
5. Add your **Environment Variables** (`DISCORD_WEBHOOK_URL`).
6. Click **Deploy**.

### 3. Set Up External Cron

Since Render's free tier doesn't include cron jobs, use a free external cron service:

1. Go to [cron-job.org](https://cron-job.org) (free account).
2. Create a new cron job:
   - **URL:** `https://your-render-app.onrender.com/check`
   - **Schedule:** Every 5 minutes (`*/5 * * * *`) or your preferred interval.
3. Save. That's it!

> **Tip:** The external cron also keeps your Render free-tier service from sleeping due to inactivity.

## Local Testing

```bash
# Install dependencies
pip install -r requirements.txt

# Set your Discord webhook
set DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/YOUR_WEBHOOK_ID/YOUR_WEBHOOK_TOKEN

# Run the app
python app.py

# In another terminal, trigger a check
curl http://localhost:5000/check
```

## Project Structure

```
UrlWatch/
├── app.py              # Main application
├── Dockerfile          # Container config for Render
├── requirements.txt    # Python dependencies
├── render.yaml         # Render blueprint (optional)
├── .env.example        # Environment variable template
└── README.md           # This file
```
