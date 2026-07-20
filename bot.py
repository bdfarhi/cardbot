import os
import sys
import json
import time
import requests
import smtplib

from dotenv import load_dotenv
from email.mime.text import MIMEText


# Force real-time log output regardless of whether PYTHONUNBUFFERED or
# `python -u` made it into the actual deploy config. reconfigure() is
# available on Python 3.7+ and makes stdout flush after every newline,
# so "Sleeping 5 minutes..." (and everything else) shows up in Render's
# logs immediately instead of sitting in a buffer until it happens to
# fill up.
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass


# ============================================================
# ENVIRONMENT
# ============================================================

load_dotenv()

EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")

EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER")
NEW_EMAIL = os.getenv("NEW_EMAIL")
NEXT_EMAIL = os.getenv("NEXT_EMAIL")
NEXT_EMAIL2 = os.getenv("NEXT_EMAIL2")


# ============================================================
# CONFIG
# ============================================================

CATEGORIES = {
    "baseball-cards": "⚾ Baseball Cards",
    "football-cards": "🏈 Football Cards",
    "basketball-graded": "🏀 Basketball Cards",
    "pokemon-cards": "⚡ Pokemon Cards"
}

# Shopify's standard, public, unauthenticated JSON endpoint for a
# collection's product list. This is a built-in Shopify storefront
# feature (not something specific to cardshq.com), and - unlike the
# Next.js RSC page we were scraping before - it returns the complete,
# deterministic product list for the collection, with real pagination.
#
# Why this replaces the old approach:
#   - The RSC-embedded "initialProducts" data we were parsing turned
#     out to be an unstable partial snapshot (sometimes 36 items,
#     sometimes 66, sometimes 71, for the SAME collection) - almost
#     certainly CDN/cache variance in what gets server-rendered into
#     the initial page load. That's what was causing 60-70 "new
#     card" false positives a day: items would drop out of one
#     snapshot and reappear in a later one, and the bot (correctly,
#     given what it could see) treated that as new inventory.
#   - We could never get real pagination working against the RSC
#     page because Next.js server-action ids aren't in the page
#     source in a stable, discoverable way, and they regenerate on
#     every deployment anyway.
#   - products.json has none of that: it's a plain paginated JSON
#     list, capped at 250 per page, and you page through it with
#     ?page=N until you get an empty page back.

PRODUCTS_JSON_URL = "https://cardshq.myshopify.com/collections/{}/products.json"

PAGE_LIMIT = 250

SEEN_FILE = "seen_inventory.json"

LOOP_PAUSE = 300

session = requests.Session()


# ============================================================
# EMAIL
# ============================================================

def send_email(subject, body):

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = EMAIL_SENDER

    recipients = [
        r for r in [EMAIL_RECEIVER, NEW_EMAIL, NEXT_EMAIL, NEXT_EMAIL2]
        if r
    ]

    msg["To"] = ", ".join(recipients)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(EMAIL_SENDER, EMAIL_PASSWORD)
        smtp.send_message(msg, from_addr=EMAIL_SENDER, to_addrs=recipients)


# ============================================================
# STORAGE
# ============================================================

def load_seen():
    if not os.path.exists(SEEN_FILE):
        return {}
    with open(SEEN_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_seen(data):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# ============================================================
# SCRAPER (Shopify products.json)
# ============================================================

def fetch_collection_products(category, debug=False):
    """
    Pull the FULL product list for a collection via Shopify's public
    products.json endpoint, paging with ?limit=250&page=N until an
    empty page comes back. This is deterministic - it always returns
    the same complete set for a given inventory state, unlike the old
    RSC-embedded snapshot.
    """

    products = []
    page = 1
    seen_ids_this_fetch = set()

    # Hard ceiling as a last-resort circuit breaker. 200 pages * 250 =
    # 50,000 products, far beyond anything this catalog could
    # plausibly hold - if we ever hit this, pagination is broken, not
    # the catalog actually being that big.
    MAX_PAGES = 200

    while True:

        if page > MAX_PAGES:
            print(f"  hit MAX_PAGES ({MAX_PAGES}) - stopping, pagination is likely stuck")
            break

        url = PRODUCTS_JSON_URL.format(category)

        response = session.get(
            url,
            params={"limit": PAGE_LIMIT, "page": page},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=(10, 30)   # (connect timeout, read timeout) in seconds
        )

        print(f"  page {page} -> status {response.status_code}", flush=True)

        if response.status_code != 200:
            print("  request failed:", response.text[:300])
            break

        try:
            data = response.json()
        except ValueError:
            print("  response was not JSON - products.json may be disabled for this store")
            print("  first 300 chars:", response.text[:300])
            break

        batch = data.get("products", [])

        if debug and page == 1 and batch:
            print("  SAMPLE RAW PRODUCT:", json.dumps(batch[0], indent=2)[:800])

        if not batch:
            break

        # Safety net: if this page's product ids are all ones we
        # already collected earlier in this same fetch, the ?page=
        # param isn't actually advancing (e.g. CDN serving a cached
        # page 1 for every page number) - stop instead of looping.
        batch_ids = {p.get("id") for p in batch}
        if batch_ids and batch_ids.issubset(seen_ids_this_fetch):
            print(f"  page {page} returned only already-seen ids - pagination isn't advancing, stopping")
            break
        seen_ids_this_fetch |= batch_ids

        for p in batch:
            variants = p.get("variants") or []

            # products.json returns everything ever published, including
            # sold-out items (variant "available": false). Only count
            # what a shopper could actually buy right now, so a sellout
            # doesn't get mixed up with a genuinely new listing.
            in_stock_variants = [v for v in variants if v.get("available")]

            if not in_stock_variants:
                continue

            price = in_stock_variants[0].get("price")
            product_id = p.get("id")
            title = p.get("title")

            if product_id is None or not title or price is None:
                continue

            products.append({
                "id": str(product_id),   # Shopify's real, stable numeric product id
                "title": title,
                "price": price
            })

        if len(batch) < PAGE_LIMIT:
            # last page
            break

        page += 1
        time.sleep(0.5)

    return products


def scrape_all_categories(debug=False):

    inventory = {}

    for category in CATEGORIES:
        print("\nScanning", category, flush=True)
        inventory[category] = fetch_collection_products(category, debug=debug)
        print(category, len(inventory[category]), "products", flush=True)

    return inventory


# ============================================================
# DETECT NEW
# ============================================================

def detect_new_cards(current):

    previous = load_seen()
    additions = {}

    for category, cards in current.items():

        old = set(previous.get(category, []))

        new = []
        added_this_run = set()

        for card in cards:
            if card["id"] not in old and card["id"] not in added_this_run:
                new.append(card)
                added_this_run.add(card["id"])

        if new:
            additions[category] = new

    save_seen({
        category: list({c["id"] for c in cards})
        for category, cards in current.items()
    })

    return additions


# ============================================================
# MAIN
# ============================================================

def main():

    print("CardsHQ Bot Started (v2 - products.json)")

    if os.getenv("RESET_INVENTORY") == "true":
        if os.path.exists(SEEN_FILE):
            os.remove(SEEN_FILE)
            print("Seen inventory reset via RESET_INVENTORY env var")

    debug = os.getenv("DEBUG") == "true"

    while True:

        try:

            inventory = scrape_all_categories(debug=debug)
            new_cards = detect_new_cards(inventory)

            if new_cards:

                body = "New Cards Posted on CardsHQ\n\n"

                for category, cards in new_cards.items():
                    body += CATEGORIES[category] + "\n\n"
                    for card in cards:
                        body += f"{card['title']}\n"
                        body += f"Price: ${card['price']}\n\n"

                print(body)

                # send_email(
                #     "🃏 New Cards Added to CardsHQ",
                #     body
                # )

            else:
                print("No new cards")

        except Exception as e:
            print("BOT ERROR:", e)

        print("Sleeping 5 minutes...", flush=True)
        time.sleep(LOOP_PAUSE)


if __name__ == "__main__":
    main()
