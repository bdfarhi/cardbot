import subprocess
subprocess.run(["python", "-m", "playwright", "install", "chromium"], check=True)
import requests
from bs4 import BeautifulSoup
import smtplib
from email.mime.text import MIMEText
import time
import json
import random 
import os
from dotenv import load_dotenv
from lxml import etree
import math
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from playwright.sync_api import sync_playwright

load_dotenv()
EMAIL_SENDER= os.getenv('EMAIL_SENDER')
EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD')
EMAIL_RECEIVER = os.getenv('EMAIL_RECEIVER')
NEW_EMAIL= os.getenv('NEW_EMAIL')
NEXT_EMAIL = os.getenv('NEXT_EMAIL')
NEXT_EMAIL2 = os.getenv('NEXT_EMAIL2')

def send_email(subject, body):
    # build message
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From']    = EMAIL_SENDER

    # send to both
    recipients = [EMAIL_RECEIVER]
    if NEW_EMAIL:
        recipients.append(NEW_EMAIL)
        recipients.append(NEXT_EMAIL)
        recipients.append(NEXT_EMAIL2)
    msg['To'] = ", ".join(recipients)

    # send
    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
        smtp.login(EMAIL_SENDER, EMAIL_PASSWORD)
        smtp.send_message(msg, from_addr=EMAIL_SENDER, to_addrs=recipients)



def load_seen_cards():
    if os.path.exists(SEEN_CARDS_FILE):
        with open(SEEN_CARDS_FILE, 'r') as f:
            return set(json.load(f))
    return set()
def load_set(path):
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return set(json.load(f))
        except Exception:
            # If corrupt, start fresh
            return set()
    return set()
def save_seen_cards(seen_cards):
    with open(SEEN_CARDS_FILE, 'w') as f:
        json.dump(list(seen_cards), f)

def save_set(s, path):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(sorted(list(s)), f, ensure_ascii=False)

# def _set_url_page(url, page_num):
#     #set the 'page' param without string-replace pitfalls
#     parts = urlparse(url)
#     q = parse_qs(parts.query)
#     q['page'] = [str(page_num)]
#     new_query = urlencode({k: v[0] if isinstance(v, list) and len(v) == 1 else v for k, v in q.items()})
#     return urlunparse((parts.scheme, parts.netloc, parts.path, parts.params, new_query, parts.fragment))


SEEN_CARDS_FILE = 'seen_cards.json'  # NO LONGER USED
INVENTORY_PREV_FILE = 'inventory_prev.json'    # snapshot from previous cycle
INVENTORY_PREV2_FILE = 'inventory_prev2.json'  # snapshot from two cycles ago

BASEBALL_URL = 'https://www.cardshq.com/collections/baseball-cards?sort_by=created-descending'
BASKETBALL_URL = 'https://www.cardshq.com/collections/basketball-graded?sort_by=created-descending'
FOOTBALL_URL = 'https://www.cardshq.com/collections/football-cards?sort_by=created-descending'
POKEMON_URL = 'https://www.cardshq.com/collections/pokemon-cards?sort_by=created-descending'
CHECK_INTERVAL = 90  # 1 minutes

# def get_driver():
#     options = Options()
#     options.add_argument('--headless')
#     options.add_argument('--no-sandbox')
#     options.add_argument('--disable-dev-shm-usage')
#     options.add_argument('--disable-gpu')
#     options.binary_location = "/usr/bin/chromium"
#     service = Service("/usr/bin/chromedriver")
#     return webdriver.Chrome(service=service, options=options)
    
# def get_current_cards(base_url):
#     print(f"Starting scrape for {base_url}")
#     c = []
#     page = 1

#     with sync_playwright() as p:
#         browser = p.chromium.launch(headless=True)
#         context = browser.new_context()
#         tab = context.new_page()

#         try:
#             while True:
#                 url = _set_url_page(base_url, page)
#                 print(f"Loading page {page}: {url}")
#                 tab.goto(url)
#                 try:
#                     tab.wait_for_selector('h2.line-clamp-3', timeout=10000)
#                     print(f"Cards found on page {page}")
#                 except:
#                     print(f"No cards on page {page}, stopping")
#                     break

#                 soup = BeautifulSoup(tab.content(), 'lxml')
#                 cards = soup.select('h2.line-clamp-3')
#                 target_class = ['text-lg', 'font-bold', 'text-gray-700', 'md:text-base']
#                 matching_p_tags = [
#                     p for p in soup.find_all('p')
#                     if sorted(p.get('class', [])) == sorted(target_class)
#                 ]

#                 if not matching_p_tags:
#                     print(f"No prices on page {page}, stopping")
#                     break

#                 print(f"Found {len(matching_p_tags)} cards on page {page}")
#                 for i in range(len(matching_p_tags)):
#                     card = str(cards[i])
#                     price = matching_p_tags[i]
#                     c.append(card.split(">")[1].split("<")[0] + '\n Price:' + str(price).split(">")[1].split("<")[0])

#                 page += 1
#         finally:
#             browser.close()

#     print(f"Scrape complete, {len(c)} total cards found")
#     return c
def get_current_cards(base_url):
    print(f"Starting scrape for {base_url}")
    c = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        tab = context.new_page()

        try:
            tab.goto(base_url)
            tab.wait_for_selector('h2.line-clamp-3', timeout=10000)

            # Scroll until no new cards load
            prev_count = 0
            while True:
                tab.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                tab.wait_for_timeout(2000)  # wait for new cards to load
                curr_count = tab.locator('h2.line-clamp-3').count()
                print(f"Cards loaded so far: {curr_count}")
                if curr_count == prev_count:
                    break  # nothing new loaded, we're at the bottom
                prev_count = curr_count

            soup = BeautifulSoup(tab.content(), 'lxml')
            cards = soup.select('h2.line-clamp-3')
            target_class = ['text-lg', 'font-bold', 'text-gray-700', 'md:text-base']
            matching_p_tags = [
                p for p in soup.find_all('p')
                if sorted(p.get('class', [])) == sorted(target_class)
            ]

            print(f"Found {len(matching_p_tags)} total cards")
            for i in range(len(matching_p_tags)):
                card = str(cards[i])
                price = matching_p_tags[i]
                c.append(card.split(">")[1].split("<")[0] + '\n Price:' + str(price).split(">")[1].split("<")[0])

        except Exception as e:
            print(f"Error scraping {base_url}: {e}")
        finally:
            browser.close()

    print(f"Scrape complete, {len(c)} total cards found")
    return c


def main():
    # seen_cards = load_seen_cards()
    print("Bot started")
    while True:
        print("Starting check cycle")
        try:
            prev_inventory = load_set(INVENTORY_PREV_FILE)  # last cycle
            prev2_inventory = load_set(INVENTORY_PREV2_FILE)  # two cycles ago
            categories = {
                'Baseball' : BASEBALL_URL,
                "Basketball" : BASKETBALL_URL,
                "Football" :FOOTBALL_URL,
                "Pokemon" : POKEMON_URL,
            }
            all_additions = {}
            current_inventory = set()
            category_current = {}

            for cat, url in categories.items():
                current_cards_list = get_current_cards(url)
                current_cards_set = set(current_cards_list)
                category_current[cat] = current_cards_set
                current_inventory |= current_cards_set

            additions = current_inventory - prev_inventory
            net_new_cards = additions - prev2_inventory

            for cat, current_set in category_current.items():
                cat_adds = (current_set - prev_inventory) - prev2_inventory
                if cat_adds:
                    all_additions[cat] = sorted(cat_adds)

            # if len(all_additions['Baseball']) > 0 or len(all_additions['Basketball']) > 0 or len(all_additions['Football']) > 0 or len(add_additions[:
            if any(len(cards) > 0 for cards in all_additions.values()):
                # print(all_additions)
                message_body = "New Cards Posted on CardsHQ \n\n"
                for category, cards in all_additions.items():
                    message_body += f"📦 {category}:\n"
                    for card in cards:
                        message_body += f"- {card}\n"
                    message_body += "\n"

                send_email(
                    subject='🃏 New Cards Posted on CardsHQ',
                    body=message_body
                )
                print("Sent notification for new cards:\n", message_body)
            else:
                print("No new cards found.")

                # Two cycles ago <= last cycle
            save_set(prev_inventory, INVENTORY_PREV2_FILE)
                # Last cycle <= current snapshot
            save_set(current_inventory, INVENTORY_PREV_FILE)

        except Exception as e:
            print(f"Error during check: {e}")

        time.sleep(CHECK_INTERVAL)
# def main():
#     while True:
#         try:
#             prev_inventory  = load_set(INVENTORY_PREV_FILE)
#             prev2_inventory = load_set(INVENTORY_PREV2_FILE)

#             categories = {
#                 'Baseball'  : BASEBALL_URL,
#                 'Basketball': BASKETBALL_URL,
#                 'Football'  : FOOTBALL_URL,
#             }

#             all_additions = {}
#             current_inventory = set()
#             category_current = {}

#             for cat, url in categories.items():
#                 current_cards_list = get_current_cards(url)
#                 current_cards_set  = set(current_cards_list)
#                 category_current[cat] = current_cards_set
#                 current_inventory |= current_cards_set

#             additions     = current_inventory - prev_inventory
#             net_new_cards = additions - prev2_inventory

#             for cat, current_set in category_current.items():
#                 cat_adds = (current_set - prev_inventory) - prev2_inventory
#                 if cat_adds:
#                     all_additions[cat] = sorted(cat_adds)

#             if any(len(v) > 0 for v in all_additions.values()):
#                 message_body = "New Cards Posted on CardsHQ \n\n"
#                 for category, cards in all_additions.items():
#                     if not cards:
#                         continue
#                     message_body += f"📦 {category}:\n"
#                     for card in cards:
#                         message_body += f"- {card}\n"
#                     message_body += "\n"

#                 send_email(
#                     subject='🃏 New Cards Posted on CardsHQ',
#                     body=message_body
#                 )
#                 print("Sent notification for new cards:\n", message_body)
#             else:
#                 print("No new cards found.")

#             save_set(prev_inventory,     INVENTORY_PREV2_FILE)
#             save_set(current_inventory,  INVENTORY_PREV_FILE)

#         except Exception as e:
#             print(f"Error during check: {e}")

#         time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
