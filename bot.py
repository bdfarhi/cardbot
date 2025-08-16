import requests
from bs4 import BeautifulSoup
import smtplib
from email.mime.text import MIMEText
import time
import json
import os
from dotenv import load_dotenv
from lxml import etree
import math
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
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



# def load_seen_cards():
#     if os.path.exists(SEEN_CARDS_FILE):
#         with open(SEEN_CARDS_FILE, 'r') as f:
#             return set(json.load(f))
#     return set()
def load_set(path):
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return set(json.load(f))
        except Exception:
            # If corrupt, start fresh
            return set()
    return set()
# def save_seen_cards(seen_cards):
#     with open(SEEN_CARDS_FILE, 'w') as f:
#         json.dump(list(seen_cards), f)

def save_set(s, path):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(sorted(list(s)), f, ensure_ascii=False)

def _set_url_page(url, page_num):
    #set the 'page' param without string-replace pitfalls
    parts = urlparse(url)
    q = parse_qs(parts.query)
    q['page'] = [str(page_num)]
    new_query = urlencode({k: v[0] if isinstance(v, list) and len(v) == 1 else v for k, v in q.items()})
    return urlunparse((parts.scheme, parts.netloc, parts.path, parts.params, new_query, parts.fragment))


SEEN_CARDS_FILE = 'seen_cards.json'  # NO LONGER USED
INVENTORY_PREV_FILE = 'inventory_prev.json'    # snapshot from previous cycle
INVENTORY_PREV2_FILE = 'inventory_prev2.json'  # snapshot from two cycles ago

BASEBALL_URL = 'https://www.cardshq.com/collections/baseball-cards?page=1&sort_by=created-descending'
BASKETBALL_URL = 'https://www.cardshq.com/collections/basketball-graded?page=1&sort_by=created-descending'
FOOTBALL_URL = 'https://www.cardshq.com/collections/football-cards?page=1&sort_by=created-descending'
CHECK_INTERVAL = 180  # 3 minutes


def get_current_cards(url):
    response = requests.get(url)
    html_content = response.text

    soup = BeautifulSoup(html_content, 'lxml')


    dom = etree.HTML(str(soup))


    xpath = '/html/body/div/main/div[1]/div/div/div[1]/div[2]/div[2]'
    elements = dom.xpath(xpath)


    text = ''.join(elements[0].itertext()).strip()
    text = text.split()
    products = int(text[0])
    print(products)
    page = 1
    c = []
    pageNumbers = math.ceil(products / 36)
    while page <= pageNumbers:
        # print(BASEBALL_URL)

        response = requests.get(url)
        soup = BeautifulSoup(response.text, 'html.parser')

        cards = soup.select('h2.line-clamp-3')
        target_class = ['text-lg', 'font-bold', 'text-gray-700', 'md:text-base']
        matching_p_tags = [
            p for p in soup.find_all('p')
            if sorted(p.get('class', [])) == sorted(target_class)
        ]
        i = 0
        while i < len(matching_p_tags):
            card = cards[i]
            card = str(card)
            price = matching_p_tags[i]
            c.append(card.split(">")[1].split("<")[0] + '\n Price:' + str(price).split(">")[1].split("<")[0])
            i += 1
        url = url.replace(str(page), str(page + 1))
        page += 1
    return c


def main():
    # seen_cards = load_seen_cards()

    while True:
        try:
            prev_inventory = load_set(INVENTORY_PREV_FILE)  # last cycle
            prev2_inventory = load_set(INVENTORY_PREV2_FILE)  # two cycles ago
            categories = {
                'Baseball' : BASEBALL_URL,
                "Basketball" : BASKETBALL_URL,
                "Football" :FOOTBALL_URL,
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

            if len(all_additions['Baseball']) > 0 or len(all_additions['Basketball']) > 0 or len(all_additions['Football']) > 0:
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

if __name__ == "__main__":
    main()
