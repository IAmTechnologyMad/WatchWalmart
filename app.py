import os
import json
import time
import logging
import threading
from datetime import datetime, timezone

from flask import Flask, jsonify
from curl_cffi import requests as cffi_requests
from bs4 import BeautifulSoup
import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PRODUCT_URL = os.environ.get(
    "PRODUCT_URL",
    "https://www.walmart.com/ip/NeeDoh-Nice-Cube-Satisfying-Square-Shaped-Sensory-Toy-Colors-May-Vary-Children-Ages-3/3523128211",
)

DISCORD_WEBHOOK_URL = os.environ.get(
    "DISCORD_WEBHOOK_URL",
    "https://discordapp.com/api/webhooks/1509316704302268447/XfE0ssFPicgrovIBOHQ1Gt87-dBSZwjUDK__p16GV95Ov2rmp907UsKgE7DNu4lDAG-e",
)

import tempfile

# Check interval tracking – we only alert once when it *transitions* to in-stock
LAST_STATUS_FILE = os.path.join(tempfile.gettempdir(), "last_stock_status.txt")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Background Task
# ---------------------------------------------------------------------------

def background_task(interval_seconds=300):
    """Runs the stock check periodically in the background."""
    log.info("🚀 Background task started. Will check stock every %d seconds.", interval_seconds)
    while True:
        try:
            log.info("⏰ Background timer triggered. Initiating stock check...")
            check_stock()
            log.info("💤 Stock check complete. Sleeping for %d seconds...", interval_seconds)
        except Exception as e:
            log.error("❌ Error in background task: %s", e)
        time.sleep(interval_seconds)

# Start background thread automatically
bg_thread = threading.Thread(target=background_task, daemon=True)
bg_thread.start()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fetch_page(url: str) -> str | None:
    """Fetch the Walmart product page using curl_cffi to bypass TLS fingerprinting."""
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    try:
        resp = cffi_requests.get(
            url,
            headers=headers,
            impersonate="chrome124",
            timeout=30,
        )
        if resp.status_code == 200:
            return resp.text
        log.warning("Non-200 status code: %s", resp.status_code)
        return None
    except Exception as exc:
        log.error("Failed to fetch page: %s", exc)
        return None


def parse_stock_status(html: str) -> dict:
    """Extract stock / availability info from Walmart's __NEXT_DATA__ JSON."""
    soup = BeautifulSoup(html, "html.parser")
    script_tag = soup.find("script", {"id": "__NEXT_DATA__"})

    result = {
        "in_stock": False,
        "availability": "UNKNOWN",
        "product_name": "Unknown Product",
        "price": None,
        "image_url": None,
    }

    if not script_tag or not script_tag.string:
        # Fallback: look for common out-of-stock text patterns in page body
        page_text = soup.get_text(separator=" ", strip=True).lower()
        if "add to cart" in page_text:
            result["in_stock"] = True
            result["availability"] = "IN_STOCK (text-match)"
        elif "out of stock" in page_text:
            result["availability"] = "OUT_OF_STOCK (text-match)"
        elif "get in-stock alert" in page_text:
            result["availability"] = "OUT_OF_STOCK (text-match)"
        log.info("No __NEXT_DATA__ found – fell back to text matching: %s", result["availability"])
        return result

    try:
        data = json.loads(script_tag.string)
        # Navigate the Next.js data structure
        props = data.get("props", {}).get("pageProps", {})
        initial_data = props.get("initialData", {}).get("data", {})

        product = initial_data.get("product", {})
        if not product:
            # Try alternative path
            product = initial_data.get("contentLayout", {}).get("modules", [{}])
            if isinstance(product, list):
                for module in product:
                    if "product" in str(module).lower():
                        product = module
                        break

        # Extract product name
        result["product_name"] = (
            product.get("name")
            or product.get("usItemName")
            or props.get("product", {}).get("name", "Unknown Product")
        )

        # Extract price
        price_info = product.get("priceInfo", {}).get("currentPrice", {})
        result["price"] = price_info.get("priceString") or price_info.get("price")

        # Extract image
        image_info = product.get("imageInfo", {})
        if image_info.get("thumbnailUrl"):
            result["image_url"] = image_info["thumbnailUrl"]
        elif image_info.get("allImages"):
            result["image_url"] = image_info["allImages"][0].get("url")

        # Extract availability status
        availability = (
            product.get("availabilityStatus")
            or product.get("availability", {}).get("status")
            or product.get("offerType")
            or ""
        )
        result["availability"] = availability

        in_stock_signals = ["IN_STOCK", "AVAILABLE", "Available"]
        out_of_stock_signals = ["OUT_OF_STOCK", "NOT_AVAILABLE", "UNAVAILABLE"]

        availability_upper = availability.upper()
        if any(sig.upper() in availability_upper for sig in in_stock_signals):
            result["in_stock"] = True
        elif any(sig.upper() in availability_upper for sig in out_of_stock_signals):
            result["in_stock"] = False
        else:
            # Check fulfillment options as fallback
            fulfillment = product.get("fulfillmentOptions", [])
            for option in fulfillment:
                if option.get("availabilityStatus", "").upper() in ("IN_STOCK", "AVAILABLE"):
                    result["in_stock"] = True
                    break

        log.info("Parsed availability: %s | in_stock: %s", result["availability"], result["in_stock"])
    except (KeyError, TypeError, IndexError) as exc:
        log.error("Error parsing __NEXT_DATA__: %s", exc)

    return result


def get_last_status() -> str | None:
    """Read the last known stock status from disk."""
    try:
        with open(LAST_STATUS_FILE, "r") as f:
            return f.read().strip()
    except FileNotFoundError:
        return None


def save_status(status: str):
    """Persist the current stock status to disk."""
    with open(LAST_STATUS_FILE, "w") as f:
        f.write(status)


def send_discord_notification(product_info: dict):
    """Send a rich embed to the Discord webhook."""
    if not DISCORD_WEBHOOK_URL:
        log.warning("DISCORD_WEBHOOK_URL not set – skipping notification")
        return False

    embed = {
        "title": "🟢 Product is IN STOCK!",
        "description": f"**{product_info['product_name']}**\nis now available on Walmart!",
        "color": 0x00FF00,  # Green
        "fields": [
            {
                "name": "💰 Price",
                "value": str(product_info.get("price") or "N/A"),
                "inline": True,
            },
            {
                "name": "📦 Status",
                "value": product_info.get("availability", "IN_STOCK"),
                "inline": True,
            },
            {
                "name": "🔗 Link",
                "value": f"[Buy Now]({PRODUCT_URL})",
                "inline": False,
            },
        ],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": "Walmart Stock Tracker"},
    }

    if product_info.get("image_url"):
        embed["thumbnail"] = {"url": product_info["image_url"]}

    payload = {
        "username": "Walmart Stock Alert",
        "content": "🚨 **STOCK ALERT** 🚨",
        "embeds": [embed],
    }

    try:
        resp = requests.post(
            DISCORD_WEBHOOK_URL,
            json=payload,
            timeout=10,
        )
        if resp.status_code in (200, 204):
            log.info("✅ Discord notification sent successfully!")
            return True
        else:
            log.error("Discord webhook failed: %s – %s", resp.status_code, resp.text)
            return False
    except Exception as exc:
        log.error("Failed to send Discord notification: %s", exc)
        return False


def check_stock() -> dict:
    """Main routine: fetch page, parse status, notify if newly in stock."""
    log.info("🔍 Checking stock for: %s", PRODUCT_URL)

    log.info("🌐 Fetching product page...")
    html = fetch_page(PRODUCT_URL)
    if html is None:
        log.error("⚠️ Failed to fetch product page.")
        return {
            "success": False,
            "error": "Failed to fetch product page",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    log.info("📄 Page fetched successfully. Parsing stock status...")
    product_info = parse_stock_status(html)
    
    last_status = get_last_status()
    current_status = "IN_STOCK" if product_info["in_stock"] else "OUT_OF_STOCK"
    notified = False

    log.info("📊 Parsed status: in_stock=%s, availability='%s', price='%s', product='%s'", 
             product_info["in_stock"], product_info["availability"], product_info.get("price"), product_info.get("product_name"))
    log.info("🔄 Previous status: %s | Current status: %s", last_status, current_status)

    # Only notify on transition: was out-of-stock (or first check) → now in-stock
    if product_info["in_stock"] and last_status != "IN_STOCK":
        log.info("🎉 Product is IN STOCK! Sending Discord notification...")
        notified = send_discord_notification(product_info)
    elif product_info["in_stock"]:
        log.info("✅ Product still in stock – no new notification needed")
    else:
        log.info("❌ Product is out of stock")

    save_status(current_status)

    return {
        "success": True,
        "product_name": product_info["product_name"],
        "in_stock": product_info["in_stock"],
        "availability": product_info["availability"],
        "price": product_info.get("price"),
        "notified": notified,
        "previous_status": last_status,
        "current_status": current_status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def home():
    return jsonify({
        "service": "Walmart Stock Tracker",
        "status": "running",
        "product_url": PRODUCT_URL,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


@app.route("/check")
def check():
    """Endpoint to trigger a stock check. Hit this with an external cron."""
    result = check_stock()
    return jsonify(result)


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
