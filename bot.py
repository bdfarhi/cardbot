import os
import json
import time
import re
import requests
import smtplib

from dotenv import load_dotenv
from email.mime.text import MIMEText


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


BASE_URL = "https://www.cardshq.com/collections/{}"

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
        r for r in [
            EMAIL_RECEIVER,
            NEW_EMAIL,
            NEXT_EMAIL,
            NEXT_EMAIL2
        ]
        if r
    ]

    msg["To"] = ", ".join(recipients)

    with smtplib.SMTP_SSL(
        "smtp.gmail.com",
        465
    ) as smtp:

        smtp.login(
            EMAIL_SENDER,
            EMAIL_PASSWORD
        )

        smtp.send_message(
            msg,
            from_addr=EMAIL_SENDER,
            to_addrs=recipients
        )


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
# NEXT JS SESSION DATA
# ============================================================

def get_action_id(text):

    patterns = [
        r'next-action="([^"]+)"',
        r'name="next-action" value="([^"]+)"',
        r'"actionId":"([^"]+)"',
        r'"action_id":"([^"]+)"',
        r'next-action\\":\\"([^\\"]+)',
        r'actionId\\":\\"([^\\"]+)'
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            print("FOUND ACTION ID:", match.group(1))
            return match.group(1)

    print("NO ACTION ID FOUND")
    return None


def get_session_data(category):

    url = BASE_URL.format(category)

    response = session.get(
        url,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=30
    )

    if response.status_code != 200:
        raise Exception(f"Initial page failed {response.status_code}")

    html = response.text

    flight = re.findall(r'self\.__next_f\.push\((.*?)\)', html)

    print("FLIGHT CHUNKS:", len(flight))

    action_id = get_action_id(html)

    if not action_id:
        action_match = re.search(
            r'next-action.{0,100}?([a-f0-9]{40,})',
            html
        )
        if action_match:
            action_id = action_match.group(1)
            print("FOUND FALLBACK ACTION:", action_id)

    deployment = re.search(r'data-dpl-id="([^"]+)"', html)
    deployment_id = deployment.group(1) if deployment else None

    router_match = re.search(r'next-router-state-tree="([^"]+)"', html)
    router_state = router_match.group(1) if router_match else None

    print("ACTION ID:", action_id)
    print("DEPLOYMENT:", deployment_id)

    return {
        "action_id": action_id,
        "deployment_id": deployment_id,
        "router_state": router_state,
        "cookies": session.cookies
    }


# ============================================================
# HEADERS
# ============================================================

def build_headers(category, session_data):

    router_state = session_data["router_state"]

    if not router_state:
        router_state = (
            "%5B%22%22%2C%7B%22children%22%3A"
            "%5B%22collections%22%2C%7B%22children%22"
            "%3A%5B%5B%22collection%22%2C%22"
            + category +
            "%22%2C%22d%22%2Cnull%5D%5D%7D%5D%7D%5D"
        )

    return {
        "Accept": "text/x-component",
        "Content-Type": "text/plain;charset=UTF-8",
        "User-Agent": "Mozilla/5.0",
        "next-action": session_data["action_id"],
        "next-router-state-tree": router_state,
        "x-deployment-id": session_data["deployment_id"],
        "Origin": "https://www.cardshq.com",
        "Referer": BASE_URL.format(category)
    }


# ============================================================
# BALANCED JSON EXTRACTION
# ============================================================
#
# The old approach used a single regex to pull "title" ... "id" ...
# "priceRange" ... "amount" out of the flight payload. Because that
# pattern was unrestricted (".*?") between those fields, it would
# happily cross the closing "}" of one product object and pick up
# fields from a *different* product (or a non-product object, like a
# "Featured" badge). That's what produced:
#   1) titles that were actually stray numeric ids from another node
#   2) ids that weren't the real, stable product id -> the same card
#      got a "new" id from run to run and got re-emailed
#
# Fixing this properly requires respecting JSON object boundaries,
# so instead of one big regex we scan for balanced {...} spans and
# parse each with json.loads. Only a syntactically complete object
# is ever considered a match, so title/id/price always come from the
# same node.

def extract_balanced(text, start, open_ch='[', close_ch=']'):
    """
    Return the index just past the balanced open/close span starting
    at text[start] == open_ch, respecting quoted strings so brackets
    inside string literals don't confuse the depth count.
    """
    depth = 0
    in_string = False
    escape = False
    i = start
    n = len(text)

    while i < n:
        c = text[i]

        if in_string:
            if escape:
                escape = False
            elif c == '\\':
                escape = True
            elif c == '"':
                in_string = False
        else:
            if c == '"':
                in_string = True
            elif c == open_ch:
                depth += 1
            elif c == close_ch:
                depth -= 1
                if depth == 0:
                    return i + 1

        i += 1

    return -1


def get_flight_text(html):
    """
    Reconstruct the full Next.js flight payload text properly.

    Each self.__next_f.push([...]) call's argument is itself valid
    JSON: [<index>, "<escaped string chunk>"]. Previously we grabbed
    the raw text between "push([" and "])" and glued chunks together
    with "\\n".join(), then ran a single unicode_escape decode over
    the whole blob. That decode has no concept of chunk boundaries or
    which quotes are wrapper syntax vs. real content, so it corrupts
    quote boundaries as soon as chunks are concatenated (visible in
    debug output as a stray lone '"' between chunks).

    Instead: parse each push() argument as real JSON so escapes are
    resolved unambiguously per chunk, then concatenate the *decoded*
    string pieces (not the raw wrapper text).
    """
    pieces = []

    for m in re.finditer(r'self\.__next_f\.push\(', html):
        start = m.end()

        if start >= len(html) or html[start] != '[':
            continue

        end = extract_balanced(html, start, '[', ']')
        if end == -1:
            continue

        try:
            parsed = json.loads(html[start:end])
        except (json.JSONDecodeError, ValueError):
            continue

        for item in parsed:
            if isinstance(item, str):
                pieces.append(item)

    return "".join(pieces)


def collect_dicts(value, found):
    """Recursively walk a parsed JSON value and collect every dict
    found anywhere inside it (not just the top-level one)."""
    if isinstance(value, dict):
        found.append(value)
        for v in value.values():
            collect_dicts(v, found)
    elif isinstance(value, list):
        for item in value:
            collect_dicts(item, found)


def extract_json_objects(text):
    """
    Scan text for balanced {...} spans and parse them. Previously this
    stopped at the first successful top-level match and skipped past
    it - but a successful match is often a large wrapper object (e.g.
    Next.js props containing "initialProducts": [...]) whose real
    product dicts are nested *inside* it. So once a span parses, we
    recursively walk the resulting object to pull out every nested
    dict too.
    """
    all_dicts = []
    i = 0
    n = len(text)

    while i < n:

        if text[i] == '{':

            end = extract_balanced(text, i, '{', '}')

            if end != -1:
                candidate = text[i:end]
                try:
                    obj = json.loads(candidate)
                    collect_dicts(obj, all_dicts)
                    i = end
                    continue
                except (json.JSONDecodeError, ValueError):
                    pass

        i += 1

    return all_dicts


def find_price(obj):
    """
    Try the common Shopify Storefront shapes for a price. Adjust /
    extend this if your debug printout (see parse_rsc_response) shows
    a different structure for cardshq.com specifically.
    """

    if not isinstance(obj, dict):
        return None

    price_range = obj.get("priceRange")
    if isinstance(price_range, dict):
        min_price = price_range.get("minVariantPrice")
        if isinstance(min_price, dict) and "amount" in min_price:
            return min_price["amount"]

    if "price" in obj and isinstance(obj["price"], (str, int, float)):
        return obj["price"]

    # last resort: one level deep, look for anything with "amount"
    for value in obj.values():
        if isinstance(value, dict) and "amount" in value:
            return value["amount"]

    return None


# ============================================================
# RSC PARSER
# ============================================================

def parse_rsc_response(text, debug=False):

    products = []

    combined = get_flight_text(text)

    if not combined:
        print("No flight chunks found")
        return {"products": [], "hasNextPage": False, "endCursor": None}

    if debug:
        print("RECONSTRUCTED FLIGHT TEXT LENGTH:", len(combined))
        # Show raw text around the first few literal occurrences of
        # "title" so we can see the ACTUAL shape of a product node
        # instead of guessing. This is the key diagnostic - paste
        # this output back so the parser can be calibrated correctly.
        idx = 0
        shown = 0
        while shown < 8:
            idx = combined.find('"title"', idx)
            if idx == -1:
                break
            start = max(0, idx - 80)
            end = min(len(combined), idx + 400)
            print(f"\n--- RAW SAMPLE AROUND 'title' #{shown+1} ---")
            print(combined[start:end])
            idx = idx + 7
            shown += 1

        if shown == 0:
            print('\nNO LITERAL "title" KEY FOUND IN RECONSTRUCTED TEXT AT ALL')

        # specifically locate a real product node via priceRange, since
        # the plain "title" occurrences above are mostly page metadata
        price_idx = combined.find('"priceRange"')
        if price_idx != -1:
            start = max(0, price_idx - 300)
            end = min(len(combined), price_idx + 300)
            print("\n--- RAW SAMPLE AROUND 'priceRange' ---")
            print(combined[start:end])
        else:
            print('\nNO LITERAL "priceRange" KEY FOUND - product price field may use a different name')

    all_objs = extract_json_objects(combined)

    if debug:
        # Show every parsed object that has *either* a title or an id,
        # regardless of whether it also has a recognizable price, so
        # we can see what's actually available.
        loose_matches = [
            o for o in all_objs
            if isinstance(o, dict) and ("title" in o or "id" in o)
        ]
        print(f"\n--- {len(loose_matches)} OBJECTS WITH title OR id (showing up to 5) ---")
        for o in loose_matches[:5]:
            print(json.dumps(o, indent=2)[:1000])

    seen_ids = set()

    for obj in all_objs:

        if not isinstance(obj, dict):
            continue

        title = obj.get("title")
        product_id = obj.get("id")

        if not isinstance(title, str) or not title:
            continue
        if not isinstance(product_id, str) or not product_id:
            continue

        # Variants (e.g. "Default Title") and options also have their
        # own title/id/price-shaped fields and would otherwise pass
        # every check below. Only accept true Product nodes.
        if "/Product/" not in product_id:
            continue
        if "ProductVariant" in product_id or "ProductOption" in product_id:
            continue
        if "handle" not in obj:
            continue

        price = find_price(obj)
        if price is None:
            # not a real product node (e.g. a badge/label object) -
            # skip rather than emit a card with no price
            continue

        if product_id in seen_ids:
            continue
        seen_ids.add(product_id)

        products.append({
            "id": product_id,
            "title": title,
            "price": price
        })

    if debug:
        for p in products[:5]:
            print("SAMPLE:", p)

    print("PRODUCT MATCHES:", len(products))

    # Pagination info still comes straight from the payload text
    cursor = None
    has_next = False

    cursor_match = re.search(r'"endCursor":"([^"]+)"', combined)
    if cursor_match:
        cursor = cursor_match.group(1)

    has_next_match = re.search(r'"hasNextPage":(true|false)', combined)
    if has_next_match:
        has_next = has_next_match.group(1) == "true"

    print("PAGINATION:", has_next, cursor)

    return {
        "products": products,
        "hasNextPage": has_next,
        "endCursor": cursor
    }


# ============================================================
# SCRAPER
# ============================================================

def scrape_category(category):

    products = []

    session_data = get_session_data(category)
    headers = build_headers(category, session_data)

    cursor = None
    page = 1

    while True:

        print("\nREQUESTING PAGE", page)

        if page == 1:
            response = session.get(
                BASE_URL.format(category),
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=30
            )
        else:
            payload = [{"collection": category, "after": cursor}]
            response = session.post(
                BASE_URL.format(category),
                headers=headers,
                data=json.dumps(payload, separators=(",", ":")),
                timeout=30
            )

        print("STATUS:", response.status_code)

        if response.status_code != 200:
            print(response.text[:500])
            break

        data = parse_rsc_response(response.text, debug=False)
        batch = data["products"]

        print("FOUND", len(batch), "cards")

        products.extend(batch)

        if not data["hasNextPage"]:
            print("NO MORE PAGES")
            break

        cursor = data["endCursor"]
        print("NEXT CURSOR:", cursor)

        if not cursor:
            break

        page += 1
        time.sleep(1)

    return products


def scrape_all_categories():

    inventory = {}

    for category in CATEGORIES:
        print("\nScanning", category)
        inventory[category] = scrape_category(category)
        print(category, len(inventory[category]))

    return inventory


# ============================================================
# DETECT NEW
# ============================================================

def detect_new_cards(current):

    previous = load_seen()
    additions = {}

    for category, cards in current.items():

        old = set(previous.get(category, []))

        # de-dupe within this run's own batch too, in case the same
        # id showed up twice on a page
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

    print("CardsHQ Bot Started")

    while True:

        try:

            inventory = scrape_all_categories()
            new_cards = detect_new_cards(inventory)

            if new_cards:

                body = "New Cards Posted on CardsHQ\n\n"

                for category, cards in new_cards.items():

                    body += CATEGORIES[category] + "\n\n"

                    for card in cards:
                        body += f"{card['title']}\n"
                        body += f"Price: ${card['price']}\n\n"

                print(body)

                send_email(
                    "🃏 New Cards Added to CardsHQ",
                    body
                )

            else:
                print("No new cards")

        except Exception as e:
            print("BOT ERROR:", e)

        print("Sleeping 5 minutes...")
        time.sleep(LOOP_PAUSE)


if __name__ == "__main__":
    main()
