import requests
from bs4 import BeautifulSoup
import smtplib
from email.mime.text import MIMEText
import time
import json
import os
from dotenv import load_dotenv
load_dotenv()
EMAIL_SENDER= os.getenv('EMAIL_SENDER')
EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD')
EMAIL_RECEIVER = os.getenv('EMAIL_RECEIVER')
NEW_EMAIL= os.getenv('NEW_EMAIL')
NEXT_EMAIL = os.getenv('NEXT_EMAIL')


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

def save_seen_cards(seen_cards):
    with open(SEEN_CARDS_FILE, 'w') as f:
        json.dump(list(seen_cards), f)




SEEN_CARDS_FILE = 'seen_cards.json'


BASEBALL_URL = 'https://www.cardshq.com/collections/baseball-cards?page=1&sort_by=created-descending'
BASEKTBALL_URL = 'https://www.cardshq.com/collections/basketball-graded?page=1&sort_by=created-descending'
FOOTBALL_URL = 'https://www.cardshq.com/collections/football-cards?page=1&sort_by=created-descending'
CHECK_INTERVAL = 180  # 3 minutes


def get_current_cards(url):
    response = requests.get(url)
    soup = BeautifulSoup(response.text, 'html.parser')
    cards = soup.select('h2.line-clamp-3')
    target_class = ['text-lg', 'font-bold', 'text-gray-700', 'md:text-base']
    matching_p_tags = [
        p for p in soup.find_all('p')
        if sorted(p.get('class', [])) == sorted(target_class)
    ]
    c = []
    i = 0
    while i < len(matching_p_tags):
        card = cards[i]
        card = str(card)
        price = matching_p_tags[i]
        c.append(card.split(">")[1].split("<")[0] + '\n Price:'+ str(price).split(">")[1].split("<")[0])
        i+=1
    return c


def main():
    seen_cards = load_seen_cards()

    while True:
        try:
            all_new_cards = {}
            for category, url in {
                "Baseball": BASEBALL_URL,
                "Basketball": BASEKTBALL_URL,
                "Football": FOOTBALL_URL,
            }.items():
                current_cards = get_current_cards(url)
                new_cards = []
                split = None
                for idx, card in enumerate(current_cards):
                    if card in seen_cards:
                        split = idx
                        break

                if split is None:
                    new_slice = current_cards
                else:
                    new_slice = current_cards[:split]

                for card in new_slice:
                    if card not in seen_cards:
                        new_cards.append(card)
                        seen_cards.add(card)
                if new_cards:
                    all_new_cards[category] = new_cards

            if all_new_cards:
                print(all_new_cards)
                message_body = "New Cards Posted on CardsHQ \n\n"
                for category, cards in all_new_cards.items():
                    message_body += f"📦 {category}:\n"
                    for card in cards:
                        message_body += f"- {card}\n"
                    message_body += "\n"

                send_email(
                    subject='🃏 New Cards Posted on CardsHQ',
                    body=message_body
                )
                print("Sent notification for new cards:\n", message_body)

                save_seen_cards(seen_cards)
            else:
                print("No new cards found.")

        except Exception as e:
            print(f"Error during check: {e}")

        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
