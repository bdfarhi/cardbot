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

def load_snapshot() -> dict:
    if not os.path.exists(SNAPSHOT_FILE):
        return {}
    try:
        with open(SNAPSHOT_FILE, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        return {cat: set(cards) for cat, cards in raw.items()}
    except Exception as e:
        print(f"[WARN] Could not load snapshot ({e}), treating as first run.")
        return {}

def save_snapshot(snapshot: dict):
    serialisable = {cat: sorted(list(cards)) for cat, cards in snapshot.items()}
    with open(SNAPSHOT_FILE, 'w', encoding='utf-8') as f:
        json.dump(serialisable, f, ensure_ascii=False, indent=2)
    print(f"  [snapshot saved: {sum(len(v) for v in snapshot.values())} total cards]")


# ── scraping via "Load more" button clicks ───────────────────────────────────

def scrape_category(url: str) -> set:
    """
    Loads the page, then repeatedly clicks the "Load more" button until it
    disappears or becomes disabled. This is deterministic — no scroll
    position guessing, no virtual-scroll flicker.
    """
    print(f"  Scraping: {url}")
    cards = set()

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            tab = browser.new_context().new_page()
            tab.goto(url, wait_until='networkidle', timeout=30_000)
            tab.wait_for_selector('h2.line-clamp-3', timeout=15_000)

            # Repeatedly try to grow the card count, either by clicking
            # "Load more" or by letting scroll-triggered loads happen on
            # their own. The page sometimes loads more cards from scrolling
            # alone, which can make a queued click land on a stale/removed
            # button — so we re-locate the button fresh every iteration and
            # tolerate click failures rather than treating them as fatal.
            click_count   = 0
            max_attempts  = 300   # safety cap on iterations, not just clicks
            no_growth_streak = 0
            max_no_growth = 4     # stop after this many attempts with zero growth

            prev_count = tab.locator('h2.line-clamp-3').count()
            print(f"    starting count: {prev_count}")

            for attempt in range(max_attempts):
                tab.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                tab.wait_for_timeout(800)

                load_more = tab.get_by_role("button", name="Load more")
                btn_present = load_more.count() > 0

                if btn_present:
                    # The button briefly disables itself while a batch is
                    # loading. Wait it out instead of giving up — it can take
                    # a few seconds for the next batch of cards to appear.
                    try:
                        load_more.first.wait_for(state="visible", timeout=2000)
                        is_enabled = load_more.first.is_enabled()
                    except Exception:
                        is_enabled = False

                    if not is_enabled:
                        print(f"    'Load more' present but disabled (still loading) — waiting...")
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
                            pass  # still disabled after waiting — fall through to growth check below

                        # Re-check after waiting
                        load_more = tab.get_by_role("button", name="Load more")
                        btn_present = load_more.count() > 0
                        is_enabled = btn_present and load_more.first.is_enabled()
                    else:
                        is_enabled = True

                    if btn_present and is_enabled:
                        try:
                            load_more.first.click(timeout=3000)
                            click_count += 1
                        except Exception as e:
                            print(f"    [info] click attempt failed, will recheck count: {e}")

                # Wait for either the button-click or a scroll-triggered
                # load to actually add cards, rather than assuming the click worked.
                try:
                    tab.wait_for_function(
                        f"document.querySelectorAll('h2.line-clamp-3').length > {prev_count}",
                        timeout=8000
                    )
                except Exception:
                    pass  # no growth this round, handled below

                curr_count = tab.locator('h2.line-clamp-3').count()

                if curr_count > prev_count:
                    print(f"    progress: {prev_count} → {curr_count} cards")
                    prev_count = curr_count
                    no_growth_streak = 0
                else:
                    no_growth_streak += 1
                    print(f"    no growth ({no_growth_streak}/{max_no_growth}) at {curr_count} cards")

                # Stop conditions: button is truly gone from DOM with no growth,
                # or growth has stalled for several attempts in a row.
                still_has_button = tab.get_by_role("button", name="Load more").count() > 0
                if not still_has_button and no_growth_streak >= 1:
                    print(f"    'Load more' button gone — fully loaded at {curr_count} cards")
                    break
                if no_growth_streak >= max_no_growth:
                    print(f"    no growth for {max_no_growth} attempts — assuming fully loaded at {curr_count} cards")
                    break

            # Let final batch settle
            tab.wait_for_timeout(1000)

            soup   = BeautifulSoup(tab.content(), 'lxml')
            names  = soup.select('h2.line-clamp-3')
            prices = [
                p for p in soup.find_all('p')
                if sorted(p.get('class', [])) == sorted(PRICE_CLASS)
            ]
            browser.close()

            for name_tag, price_tag in zip(names, prices):
                name  = name_tag.get_text(strip=True)
                price = price_tag.get_text(strip=True)
                cards.add(f"{name}\n Price:{price}")

            print(f"    → {len(cards)} cards scraped (after {click_count} 'Load more' clicks)")

    except Exception as e:
        print(f"  [ERROR] scraping {url}: {e}")

    return cards


# ── main loop ────────────────────────────────────────────────────────────────

def main():
    print("Bot started.")

    while True:
        print("\n─── Check cycle ───")
        try:
            snapshot  = load_snapshot()
            first_run = not bool(snapshot)

            current       = {}
            scrape_errors = []

            for cat, url in CATEGORIES.items():
                result = scrape_category(url)
                if not result and cat in snapshot:
                    print(f"  [WARN] {cat}: 0 cards returned, keeping previous snapshot.")
                    scrape_errors.append(cat)
                    current[cat] = snapshot[cat]
                else:
                    current[cat] = result

            if len(scrape_errors) == len(CATEGORIES):
                print("[ERROR] Every category failed. Pausing briefly before retry.")
                time.sleep(LOOP_PAUSE)
                continue

            if first_run:
                print("First run — saving initial snapshot, no email sent.")
                save_snapshot(current)
                time.sleep(LOOP_PAUSE)
                continue

            # Detect new cards
            additions = {}
            for cat, cur_set in current.items():
                prev_set  = snapshot.get(cat, set())
                new_cards = cur_set - prev_set
                if new_cards:
                    additions[cat] = sorted(new_cards)

            if additions:
                body = "New Cards Posted on CardsHQ\n\n"
                for cat, cards in additions.items():
                    body += f"📦 {cat}:\n"
                    for card in cards:
                        body += f"  - {card}\n"
                    body += "\n"
                print("New cards found — sending email:\n", body)
                send_email(subject='🃏 New Cards Posted on CardsHQ', body=body)
            else:
                print("No new cards.")

            save_snapshot(current)

        except Exception as e:
            print(f"[ERROR] cycle failed: {e}")

        print(f"Cycle complete. Pausing {LOOP_PAUSE}s before next cycle.")
        time.sleep(LOOP_PAUSE)


if __name__ == "__main__":
    main()
