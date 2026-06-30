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

# ── file that stores the last known full inventory ──────────────────────────
SNAPSHOT_FILE   = 'inventory_snapshot.json'
CHECK_INTERVAL  = 90  # seconds

CATEGORIES = {
    'Baseball':   'https://www.cardshq.com/collections/baseball-cards?sort_by=created-descending',
    'Basketball': 'https://www.cardshq.com/collections/basketball-graded?sort_by=created-descending',
    'Football':   'https://www.cardshq.com/collections/football-cards?sort_by=created-descending',
    'Pokemon':    'https://www.cardshq.com/collections/pokemon-cards?sort_by=created-descending',
}


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
    """
    Returns a dict: { category_name: set_of_card_strings }
    If the file is missing or corrupt, returns an empty dict (first-run mode).
    """
    if not os.path.exists(SNAPSHOT_FILE):
        return {}
    try:
        with open(SNAPSHOT_FILE, 'r', encoding='utf-8') as f:
            raw = json.load(f)          # { cat: [list of cards] }
        return {cat: set(cards) for cat, cards in raw.items()}
    except Exception as e:
        print(f"[WARN] Could not load snapshot ({e}), treating as first run.")
        return {}


def save_snapshot(snapshot: dict):
    """snapshot is { category_name: set_of_card_strings }"""
    serialisable = {cat: sorted(list(cards)) for cat, cards in snapshot.items()}
    with open(SNAPSHOT_FILE, 'w', encoding='utf-8') as f:
        json.dump(serialisable, f, ensure_ascii=False, indent=2)


# ── scraping ─────────────────────────────────────────────────────────────────

PRICE_CLASS = ['text-lg', 'font-bold', 'text-gray-700', 'md:text-base']

def scrape_category(url: str) -> set:
    """
    Scrolls the infinite-scroll page to completion and returns a set of
    "Card Name\n Price:$X.XX" strings.
    Returns an empty set on any error so we never wipe a good snapshot.
    """
    print(f"  Scraping: {url}")
    cards = set()

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            tab = browser.new_context().new_page()

            tab.goto(url)
            tab.wait_for_selector('h2.line-clamp-3', timeout=15_000)

            # scroll until stable
            prev_count = 0
            stall_rounds = 0
            while stall_rounds < 3:          # require 3 consecutive stable reads
                tab.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                tab.wait_for_timeout(2_000)
                curr_count = tab.locator('h2.line-clamp-3').count()
                print(f"    cards visible: {curr_count}")
                if curr_count == prev_count:
                    stall_rounds += 1
                else:
                    stall_rounds = 0
                prev_count = curr_count

            soup  = BeautifulSoup(tab.content(), 'lxml')
            names = soup.select('h2.line-clamp-3')
            prices = [
                p for p in soup.find_all('p')
                if sorted(p.get('class', [])) == sorted(PRICE_CLASS)
            ]

            browser.close()

            # zip defensively — only pair up to the shorter list
            for name_tag, price_tag in zip(names, prices):
                name  = name_tag.get_text(strip=True)
                price = price_tag.get_text(strip=True)
                cards.add(f"{name}\n Price:{price}")

            print(f"    → {len(cards)} cards scraped")

    except Exception as e:
        print(f"  [ERROR] scraping {url}: {e}")

    return cards


# ── main loop ────────────────────────────────────────────────────────────────

def main():
    print("Bot started.")

    while True:
        print("\n─── Check cycle ───")
        try:
            snapshot = load_snapshot()   # { cat: set }
            first_run = not bool(snapshot)

            current: dict = {}
            scrape_errors: list = []
            for cat, url in CATEGORIES.items():
                result = scrape_category(url)
                if not result and cat in snapshot:
                    print(f"  [WARN] {cat}: scrape returned 0 cards; keeping previous snapshot.")
                    scrape_errors.append(cat)
                    current[cat] = snapshot[cat]
                else:
                    current[cat] = result

            # If ALL categories failed, something is systemically wrong — skip
            # this cycle entirely rather than emailing garbage or saving bad state.
            if len(scrape_errors) == len(CATEGORIES):
                print("[ERROR] Every category failed to scrape. Skipping cycle, snapshot unchanged.")
                time.sleep(CHECK_INTERVAL)
                continue

            if first_run:
                print("First run — saving initial snapshot, no email sent.")
                save_snapshot(current)
                time.sleep(CHECK_INTERVAL)
                continue

            # ── detect additions per category ──────────────────────────────
            additions: dict = {}
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

            # Save snapshot only after a successful, non-empty scrape cycle
            save_snapshot(current)

        except Exception as e:
            print(f"[ERROR] cycle failed: {e}")

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
