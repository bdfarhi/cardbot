import subprocess
subprocess.run(["python", "-m", "playwright", "install", "chromium"], check=True)

import smtplib
import time
import json
import os
from email.mime.text import MIMEText
from dotenv import load_dotenv
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

load_dotenv()
EMAIL_SENDER   = os.getenv('EMAIL_SENDER')
EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD')
EMAIL_RECEIVER = os.getenv('EMAIL_RECEIVER')
NEW_EMAIL      = os.getenv('NEW_EMAIL')
NEXT_EMAIL     = os.getenv('NEXT_EMAIL')
NEXT_EMAIL2    = os.getenv('NEXT_EMAIL2')

SNAPSHOT_FILE  = 'inventory_snapshot.json'
LOOP_PAUSE     = 10  # short pause between full cycles, in seconds
MISS_THRESHOLD = 3   # a card must be absent this many CONSECUTIVE cycles
                      # before it's treated as sold/removed. This absorbs
                      # one-off scraping misses (slow page, missed click,
                      # network hiccup) without ever-growing the snapshot.

CATEGORIES = {
    'Baseball':   'https://www.cardshq.com/collections/baseball-cards?sort_by=created-descending',
    'Basketball': 'https://www.cardshq.com/collections/basketball-graded?sort_by=created-descending',
    'Football':   'https://www.cardshq.com/collections/football-cards?sort_by=created-descending',
    'Pokemon':    'https://www.cardshq.com/collections/pokemon-cards?sort_by=created-descending',
}

PRICE_CLASS = ['text-lg', 'font-bold', 'text-gray-700', 'md:text-base']


# ── email ────────────────────────────────────────────────────────────────────

def send_email(subject, body):
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From']    = EMAIL_SENDER
    recipients = [r for r in [EMAIL_RECEIVER, NEW_EMAIL, NEXT_EMAIL, NEXT_EMAIL2] if r]
    msg['To'] = ", ".join(recipients)
    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
        smtp.login(EMAIL_SENDER, EMAIL_PASSWORD)
        smtp.send_message(msg, from_addr=EMAIL_SENDER, to_addrs=recipients)


# ── persistence ──────────────────────────────────────────────────────────────
# Snapshot format on disk: { category: { card_key: miss_count } }
# miss_count = 0 means "seen in the most recent scrape".
# miss_count > 0 means "missing for this many consecutive cycles in a row".
# A card is only actually dropped once miss_count reaches MISS_THRESHOLD.

def load_snapshot() -> dict:
    if not os.path.exists(SNAPSHOT_FILE):
        return {}
    try:
        with open(SNAPSHOT_FILE, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        # Backward compatible with old format (plain list of cards)
        out = {}
        for cat, val in raw.items():
            if isinstance(val, list):
                out[cat] = {card: 0 for card in val}
            else:
                out[cat] = val
        return out
    except Exception as e:
        print(f"[WARN] Could not load snapshot ({e}), treating as first run.")
        return {}

def save_snapshot(snapshot: dict):
    with open(SNAPSHOT_FILE, 'w', encoding='utf-8') as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)
    total = sum(len(v) for v in snapshot.values())
    print(f"  [snapshot saved: {total} tracked cards across all categories]")


# ── scraping ──────────────────────────────────────────────────────────────────

def scrape_category(url: str) -> set:
    """
    Loads the page, clicks 'Load more' (waiting out the disabled/loading
    state rather than giving up on it) until no more cards appear, then
    extracts each card's title+price from WITHIN its own product link —
    never from two separately-queried lists matched by position. This is
    what prevents a card from being paired with the wrong price.
    """
    print(f"  Scraping: {url}")
    cards = set()

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            tab = browser.new_context().new_page()
            tab.goto(url, wait_until='networkidle', timeout=30_000)
            tab.wait_for_selector('div[data-testid="product-card"]', timeout=15_000)

            click_count      = 0
            max_attempts      = 300
            no_growth_streak  = 0
            max_no_growth     = 4

            prev_count = tab.locator('div[data-testid="product-card"]').count()
            print(f"    starting count: {prev_count}")

            for attempt in range(max_attempts):
                tab.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                tab.wait_for_timeout(800)

                load_more = tab.get_by_role("button", name="Load more")
                btn_present = load_more.count() > 0

                if btn_present:
                    try:
                        load_more.first.wait_for(state="visible", timeout=2000)
                        is_enabled = load_more.first.is_enabled()
                    except Exception:
                        is_enabled = False

                    if not is_enabled:
                        # Wait out the "still loading" disabled state rather
                        # than giving up immediately.
                        try:
                            tab.wait_for_function(
                                """
                                () => {
                                    const btns = [...document.querySelectorAll('button')]
                                        .filter(b => b.textContent.trim().toLowerCase().includes('load more'));
                                    return btns.length > 0 && !btns[0].disabled;
                                }
                                """,
                                timeout=8000
                            )
                        except Exception:
                            pass

                        load_more = tab.get_by_role("button", name="Load more")
                        btn_present = load_more.count() > 0
                        is_enabled = btn_present and load_more.first.is_enabled()

                    if btn_present and is_enabled:
                        try:
                            load_more.first.click(timeout=3000)
                            click_count += 1
                        except Exception as e:
                            print(f"    [info] click attempt failed, will recheck count: {e}")

                try:
                    tab.wait_for_function(
                        f"document.querySelectorAll('div[data-testid=\"product-card\"]').length > {prev_count}",
                        timeout=8000
                    )
                except Exception:
                    pass

                curr_count = tab.locator('div[data-testid="product-card"]').count()

                if curr_count > prev_count:
                    print(f"    progress: {prev_count} → {curr_count} cards")
                    prev_count = curr_count
                    no_growth_streak = 0
                else:
                    no_growth_streak += 1
                    print(f"    no growth ({no_growth_streak}/{max_no_growth}) at {curr_count} cards")

                still_has_button = tab.get_by_role("button", name="Load more").count() > 0
                if not still_has_button and no_growth_streak >= 1:
                    print(f"    'Load more' button gone — fully loaded at {curr_count} cards")
                    break
                if no_growth_streak >= max_no_growth:
                    print(f"    no growth for {max_no_growth} attempts — assuming fully loaded at {curr_count} cards")
                    break

            tab.wait_for_timeout(1000)

            soup = BeautifulSoup(tab.content(), 'lxml')
            browser.close()

            # VERIFIED STRUCTURE — confirmed directly from real DOM inspection
            # (dev tools screenshot), not assumed from markdown rendering:
            #
            # <div data-testid="product-card">
            #   <a data-testid="product-card-link" href="/products/<slug>-<id>">
            #     <img alt="CARD NAME">
            #   </a>
            #   <div> ... price lives somewhere in here ... </div>
            # </div>
            #
            # There is NO <h2> anywhere in this structure. The card name only
            # exists as the <img>'s alt text. The href slug ends in a unique
            # numeric ID, making it the most reliable identity — better than
            # name+price, since two listings can share the exact same name.
            product_cards = soup.select('div[data-testid="product-card"]')
            found = 0

            for card_div in product_cards:
                link = card_div.select_one('a[data-testid="product-card-link"]')
                if not link or not link.get('href'):
                    continue

                href = link['href']
                img = link.find('img')
                name = img.get('alt', '').strip() if img else ''
                if not name:
                    continue

                # Price text contains a "$" — search the whole card
                # container for it rather than assuming an exact class name,
                # since the price block's markup wasn't fully expanded.
                price_tag = card_div.find(
                    lambda tag: tag.name == 'p' and '$' in tag.get_text()
                )
                price = price_tag.get_text(strip=True) if price_tag else "UNKNOWN"

                # href (with its unique trailing numeric ID) is the identity —
                # this is what goes in the snapshot/comparison set.
                cards.add(f"{href}\n {name}\n Price:{price}")
                found += 1

            print(f"    matched {found} of {len(product_cards)} product-card containers")

    except Exception as e:
        print(f"  [ERROR] scraping {url}: {e}")

    return cards


# ── main loop ────────────────────────────────────────────────────────────────

def main():
    print("Bot started.")

    while True:
        print("\n─── Check cycle ───")
        try:
            snapshot  = load_snapshot()   # { cat: { card: miss_count } }
            first_run = not bool(snapshot)

            current       = {}   # { cat: set of cards seen THIS cycle }
            scrape_errors = []

            for cat, url in CATEGORIES.items():
                result = scrape_category(url)
                if not result and cat in snapshot:
                    print(f"  [WARN] {cat}: 0 cards returned this scrape.")
                    scrape_errors.append(cat)
                current[cat] = result  # may be empty; handled below via miss-counting

            if len(scrape_errors) == len(CATEGORIES):
                print("[ERROR] Every category failed. Pausing briefly before retry.")
                time.sleep(LOOP_PAUSE)
                continue

            if first_run:
                print("First run — saving initial snapshot, no email sent.")
                initial = {cat: {card: 0 for card in cards} for cat, cards in current.items()}
                save_snapshot(initial)
                time.sleep(LOOP_PAUSE)
                continue

            # ── FIX FOR PROBLEM 2: miss-count based removal ─────────────────
            # A card is only dropped from tracking after MISS_THRESHOLD
            # consecutive cycles of not being seen — a single bad/incomplete
            # scrape can no longer make a card vanish and "reappear as new".
            new_snapshot = {}
            additions    = {}
            removals     = {}

            for cat in CATEGORIES:
                prev_cards = snapshot.get(cat, {})   # { card: miss_count }
                seen_now   = current.get(cat, set())

                updated = {}
                cat_additions = []
                cat_removals  = []

                # Cards seen this cycle: reset their miss-count to 0.
                for card in seen_now:
                    if card not in prev_cards:
                        cat_additions.append(card)   # genuinely new
                    updated[card] = 0

                # Cards NOT seen this cycle: increment miss-count, keep
                # tracking them unless they've now exceeded the threshold.
                for card, miss_count in prev_cards.items():
                    if card in seen_now:
                        continue  # already handled above
                    new_miss = miss_count + 1
                    if new_miss >= MISS_THRESHOLD:
                        cat_removals.append(card)    # now considered sold
                    else:
                        updated[card] = new_miss     # still within grace period

                new_snapshot[cat] = updated
                if cat_additions:
                    additions[cat] = sorted(cat_additions)
                if cat_removals:
                    removals[cat] = sorted(cat_removals)

            if additions:
                body = "New Cards Posted on CardsHQ\n\n"
                for cat, cards in additions.items():
                    body += f"📦 {cat}:\n"
                    for card in cards:
                        body += f"  - {card}\n"
                    body += "\n"
                print("New cards found — sending email:\n", body)
                # send_email(subject='🃏 New Cards Posted on CardsHQ', body=body)
            else:
                print("No new cards.")

            if removals:
                for cat, cards in removals.items():
                    print(f"  [{cat}] {len(cards)} card(s) sold/removed after {MISS_THRESHOLD} consecutive misses")

            save_snapshot(new_snapshot)

        except Exception as e:
            print(f"[ERROR] cycle failed: {e}")

        print(f"Cycle complete. Pausing {LOOP_PAUSE}s before next cycle.")
        time.sleep(LOOP_PAUSE)


if __name__ == "__main__":
    main()
# import subprocess
# subprocess.run(["python", "-m", "playwright", "install", "chromium"], check=True)

# import smtplib
# import time
# import json
# import os
# from email.mime.text import MIMEText
# from dotenv import load_dotenv
# from bs4 import BeautifulSoup
# from playwright.sync_api import sync_playwright

# load_dotenv()
# EMAIL_SENDER   = os.getenv('EMAIL_SENDER')
# EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD')
# EMAIL_RECEIVER = os.getenv('EMAIL_RECEIVER')
# NEW_EMAIL      = os.getenv('NEW_EMAIL')
# NEXT_EMAIL     = os.getenv('NEXT_EMAIL')
# NEXT_EMAIL2    = os.getenv('NEXT_EMAIL2')

# SNAPSHOT_FILE  = 'inventory_snapshot.json'
# LOOP_PAUSE     = 10  # short pause between full cycles, in seconds
# MISS_THRESHOLD = 3   # a card must be absent this many CONSECUTIVE cycles
#                       # before it's treated as sold/removed. This absorbs
#                       # one-off scraping misses (slow page, missed click,
#                       # network hiccup) without ever-growing the snapshot.

# CATEGORIES = {
#     'Baseball':   'https://www.cardshq.com/collections/baseball-cards?sort_by=created-descending',
#     'Basketball': 'https://www.cardshq.com/collections/basketball-graded?sort_by=created-descending',
#     'Football':   'https://www.cardshq.com/collections/football-cards?sort_by=created-descending',
#     'Pokemon':    'https://www.cardshq.com/collections/pokemon-cards?sort_by=created-descending',
# }

# PRICE_CLASS = ['text-lg', 'font-bold', 'text-gray-700', 'md:text-base']


# # ── email ────────────────────────────────────────────────────────────────────

# def send_email(subject, body):
#     msg = MIMEText(body)
#     msg['Subject'] = subject
#     msg['From']    = EMAIL_SENDER
#     recipients = [r for r in [EMAIL_RECEIVER, NEW_EMAIL, NEXT_EMAIL, NEXT_EMAIL2] if r]
#     msg['To'] = ", ".join(recipients)
#     with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
#         smtp.login(EMAIL_SENDER, EMAIL_PASSWORD)
#         smtp.send_message(msg, from_addr=EMAIL_SENDER, to_addrs=recipients)


# # ── persistence ──────────────────────────────────────────────────────────────
# # Snapshot format on disk: { category: { card_key: miss_count } }
# # miss_count = 0 means "seen in the most recent scrape".
# # miss_count > 0 means "missing for this many consecutive cycles in a row".
# # A card is only actually dropped once miss_count reaches MISS_THRESHOLD.

# def load_snapshot() -> dict:
#     if not os.path.exists(SNAPSHOT_FILE):
#         return {}
#     try:
#         with open(SNAPSHOT_FILE, 'r', encoding='utf-8') as f:
#             raw = json.load(f)
#         # Backward compatible with old format (plain list of cards)
#         out = {}
#         for cat, val in raw.items():
#             if isinstance(val, list):
#                 out[cat] = {card: 0 for card in val}
#             else:
#                 out[cat] = val
#         return out
#     except Exception as e:
#         print(f"[WARN] Could not load snapshot ({e}), treating as first run.")
#         return {}

# def save_snapshot(snapshot: dict):
#     with open(SNAPSHOT_FILE, 'w', encoding='utf-8') as f:
#         json.dump(snapshot, f, ensure_ascii=False, indent=2)
#     total = sum(len(v) for v in snapshot.values())
#     print(f"  [snapshot saved: {total} tracked cards across all categories]")


# # ── scraping ──────────────────────────────────────────────────────────────────

# def scrape_category(url: str) -> set:
#     """
#     Loads the page, clicks 'Load more' (waiting out the disabled/loading
#     state rather than giving up on it) until no more cards appear, then
#     extracts each card's title+price from WITHIN its own product link —
#     never from two separately-queried lists matched by position. This is
#     what prevents a card from being paired with the wrong price.
#     """
#     print(f"  Scraping: {url}")
#     cards = set()

#     try:
#         with sync_playwright() as p:
#             browser = p.chromium.launch(headless=True)
#             tab = browser.new_context().new_page()
#             tab.goto(url, wait_until='networkidle', timeout=30_000)
#             tab.wait_for_selector('h2.line-clamp-3', timeout=15_000)

#             click_count      = 0
#             max_attempts      = 300
#             no_growth_streak  = 0
#             max_no_growth     = 4

#             prev_count = tab.locator('h2.line-clamp-3').count()
#             print(f"    starting count: {prev_count}")

#             for attempt in range(max_attempts):
#                 tab.evaluate("window.scrollTo(0, document.body.scrollHeight)")
#                 tab.wait_for_timeout(800)

#                 load_more = tab.get_by_role("button", name="Load more")
#                 btn_present = load_more.count() > 0

#                 if btn_present:
#                     try:
#                         load_more.first.wait_for(state="visible", timeout=2000)
#                         is_enabled = load_more.first.is_enabled()
#                     except Exception:
#                         is_enabled = False

#                     if not is_enabled:
#                         # Wait out the "still loading" disabled state rather
#                         # than giving up immediately.
#                         try:
#                             tab.wait_for_function(
#                                 """
#                                 () => {
#                                     const btns = [...document.querySelectorAll('button')]
#                                         .filter(b => b.textContent.trim().toLowerCase().includes('load more'));
#                                     return btns.length > 0 && !btns[0].disabled;
#                                 }
#                                 """,
#                                 timeout=8000
#                             )
#                         except Exception:
#                             pass

#                         load_more = tab.get_by_role("button", name="Load more")
#                         btn_present = load_more.count() > 0
#                         is_enabled = btn_present and load_more.first.is_enabled()

#                     if btn_present and is_enabled:
#                         try:
#                             load_more.first.click(timeout=3000)
#                             click_count += 1
#                         except Exception as e:
#                             print(f"    [info] click attempt failed, will recheck count: {e}")

#                 try:
#                     tab.wait_for_function(
#                         f"document.querySelectorAll('h2.line-clamp-3').length > {prev_count}",
#                         timeout=8000
#                     )
#                 except Exception:
#                     pass

#                 curr_count = tab.locator('h2.line-clamp-3').count()

#                 if curr_count > prev_count:
#                     print(f"    progress: {prev_count} → {curr_count} cards")
#                     prev_count = curr_count
#                     no_growth_streak = 0
#                 else:
#                     no_growth_streak += 1
#                     print(f"    no growth ({no_growth_streak}/{max_no_growth}) at {curr_count} cards")

#                 still_has_button = tab.get_by_role("button", name="Load more").count() > 0
#                 if not still_has_button and no_growth_streak >= 1:
#                     print(f"    'Load more' button gone — fully loaded at {curr_count} cards")
#                     break
#                 if no_growth_streak >= max_no_growth:
#                     print(f"    no growth for {max_no_growth} attempts — assuming fully loaded at {curr_count} cards")
#                     break

#             tab.wait_for_timeout(1000)

#             soup = BeautifulSoup(tab.content(), 'lxml')
#             browser.close()

#             # FIX FOR PROBLEM 1: extract title+price from WITHIN each
#             # product's own <a href="/products/..."> link — never from two
#             # separately-queried lists matched by position. Each card's
#             # title and price are pulled from the same self-contained
#             # element, so they can never be mismatched with another card's.
#             product_links = [a for a in soup.find_all('a', href=True) if '/products/' in a['href']]

#             seen_hrefs = set()
#             found = 0
#             for a in product_links:
#                 href = a['href']
#                 if href in seen_hrefs:
#                     continue  # same card can appear twice (image link + title link)

#                 name_tag = a.find('h2')
#                 if not name_tag:
#                     continue  # this <a> is the image-only link, skip it

#                 seen_hrefs.add(href)
#                 name = name_tag.get_text(strip=True)
#                 if not name:
#                     continue

#                 price_tag = next(
#                     (p for p in a.find_all('p') if sorted(p.get('class', [])) == sorted(PRICE_CLASS)),
#                     None
#                 )
#                 price = price_tag.get_text(strip=True) if price_tag else "UNKNOWN"

#                 # Product URL is the unique key — names CAN collide
#                 # (two listings of the same card), but the URL never does.
#                 cards.add(f"{href}\n {name}\n Price:{price}")
#                 found += 1

#             print(f"    matched {found} product cards from {len(product_links)} product links")

#     except Exception as e:
#         print(f"  [ERROR] scraping {url}: {e}")

#     return cards


# # ── main loop ────────────────────────────────────────────────────────────────

# def main():
#     print("Bot started.")

#     while True:
#         print("\n─── Check cycle ───")
#         try:
#             snapshot  = load_snapshot()   # { cat: { card: miss_count } }
#             first_run = not bool(snapshot)

#             current       = {}   # { cat: set of cards seen THIS cycle }
#             scrape_errors = []

#             for cat, url in CATEGORIES.items():
#                 result = scrape_category(url)
#                 if not result and cat in snapshot:
#                     print(f"  [WARN] {cat}: 0 cards returned this scrape.")
#                     scrape_errors.append(cat)
#                 current[cat] = result  # may be empty; handled below via miss-counting

#             if len(scrape_errors) == len(CATEGORIES):
#                 print("[ERROR] Every category failed. Pausing briefly before retry.")
#                 time.sleep(LOOP_PAUSE)
#                 continue

#             if first_run:
#                 print("First run — saving initial snapshot, no email sent.")
#                 initial = {cat: {card: 0 for card in cards} for cat, cards in current.items()}
#                 save_snapshot(initial)
#                 time.sleep(LOOP_PAUSE)
#                 continue

#             # ── FIX FOR PROBLEM 2: miss-count based removal ─────────────────
#             # A card is only dropped from tracking after MISS_THRESHOLD
#             # consecutive cycles of not being seen — a single bad/incomplete
#             # scrape can no longer make a card vanish and "reappear as new".
#             new_snapshot = {}
#             additions    = {}
#             removals     = {}

#             for cat in CATEGORIES:
#                 prev_cards = snapshot.get(cat, {})   # { card: miss_count }
#                 seen_now   = current.get(cat, set())

#                 updated = {}
#                 cat_additions = []
#                 cat_removals  = []

#                 # Cards seen this cycle: reset their miss-count to 0.
#                 for card in seen_now:
#                     if card not in prev_cards:
#                         cat_additions.append(card)   # genuinely new
#                     updated[card] = 0

#                 # Cards NOT seen this cycle: increment miss-count, keep
#                 # tracking them unless they've now exceeded the threshold.
#                 for card, miss_count in prev_cards.items():
#                     if card in seen_now:
#                         continue  # already handled above
#                     new_miss = miss_count + 1
#                     if new_miss >= MISS_THRESHOLD:
#                         cat_removals.append(card)    # now considered sold
#                     else:
#                         updated[card] = new_miss     # still within grace period

#                 new_snapshot[cat] = updated
#                 if cat_additions:
#                     additions[cat] = sorted(cat_additions)
#                 if cat_removals:
#                     removals[cat] = sorted(cat_removals)

#             if additions:
#                 body = "New Cards Posted on CardsHQ\n\n"
#                 for cat, cards in additions.items():
#                     body += f"📦 {cat}:\n"
#                     for card in cards:
#                         body += f"  - {card}\n"
#                     body += "\n"
#                 print("New cards found — sending email:\n", body)
#                 # send_email(subject='🃏 New Cards Posted on CardsHQ', body=body)
#             else:
#                 print("No new cards.")

#             if removals:
#                 for cat, cards in removals.items():
#                     print(f"  [{cat}] {len(cards)} card(s) sold/removed after {MISS_THRESHOLD} consecutive misses")

#             save_snapshot(new_snapshot)

#         except Exception as e:
#             print(f"[ERROR] cycle failed: {e}")

#         print(f"Cycle complete. Pausing {LOOP_PAUSE}s before next cycle.")
#         time.sleep(LOOP_PAUSE)


# if __name__ == "__main__":
#     main()
