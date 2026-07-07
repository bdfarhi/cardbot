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


    with open(
        SEEN_FILE,
        "r",
        encoding="utf-8"
    ) as f:

        return json.load(f)



def save_seen(data):

    with open(
        SEEN_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            data,
            f,
            indent=2
        )



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

        match = re.search(
            pattern,
            text
        )

        if match:

            print(
                "FOUND ACTION ID:",
                match.group(1)
            )

            return match.group(1)


    print(
        "NO ACTION ID FOUND"
    )

    return None



def get_session_data(category):

    url = BASE_URL.format(category)


    response = session.get(
        url,
        headers={
            "User-Agent":
                "Mozilla/5.0"
        },
        timeout=30
    )


    if response.status_code != 200:

        raise Exception(
            f"Initial page failed {response.status_code}"
        )


    html = response.text
    # for keyword in [
    #     "products",
    #     "product",
    #     "Shopify",
    #     "graphql",
    #     "collection",
    #     "edges",
    #     "cursor"
    # ]:

    #     print(
    #         keyword,
    #         html.find(keyword)
    #     )
    flight = re.findall(
    r'self\.__next_f\.push\((.*?)\)',
    html
    )

    print(
        "FLIGHT CHUNKS:",
        len(flight)
    )


    for chunk in flight[:3]:

        print(
            chunk[:500]
        )


    action_id = get_action_id(
        html
    )
    if not action_id:

        action_match = re.search(
            r'next-action.{0,100}?([a-f0-9]{40,})',
            html
        )

        if action_match:

            action_id = action_match.group(1)

            print(
                "FOUND FALLBACK ACTION:",
                action_id
            )


    deployment = re.search(
        r'data-dpl-id="([^"]+)"',
        html
    )


    if deployment:

        deployment_id = deployment.group(1)

    else:

        deployment_id = None



    router_match = re.search(
        r'next-router-state-tree="([^"]+)"',
        html
    )


    router_state = None


    if router_match:

        router_state = router_match.group(1)
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


        "Accept":
            "text/x-component",


        "Content-Type":
            "text/plain;charset=UTF-8",


        "User-Agent":
            "Mozilla/5.0",


        "next-action":
            session_data["action_id"],


        "next-router-state-tree":
            router_state,


        "x-deployment-id":
            session_data["deployment_id"],


        "Origin":
            "https://www.cardshq.com",


        "Referer":
            BASE_URL.format(category)

    }



# ============================================================
# RSC PARSER
# ============================================================

def parse_rsc_response(text):

    products = []


    # Extract Next.js flight payload chunks
    chunks = re.findall(
        r'self\.__next_f\.push\(\[(.*?)\]\)',
        text,
        re.DOTALL
    )


    if not chunks:

        print("No flight chunks found")

        return {
            "products": [],
            "hasNextPage": False,
            "endCursor": None
        }



    combined = "\n".join(chunks)


    # Decode escaped JSON inside flight data
    combined = combined.encode(
        "utf-8"
    ).decode(
        "unicode_escape"
    )

    cursor_index = combined.find("cursor")

    if cursor_index != -1:

        print(
            combined[cursor_index-300:cursor_index+300]
        )
    # Find product objects
    matches = re.findall(
        r'\{[^{}]*?"title":"(.*?)".*?"id":"(.*?)".*?"priceRange".*?"amount":"(.*?)".*?\}',
        combined
    )


    print(
        "PRODUCT MATCHES:",
        len(matches)
    )


    for title, product_id, price in matches:


        products.append({

            "id":
                product_id,

            "title":
                title,

            "price":
                price

        })



    # Find pagination
    has_next = False
    cursor = None


    cursor_match = re.search(
        r'"endCursor":"([^"]+)"',
        combined
    )

    if cursor_match:

        cursor = cursor_match.group(1)


    has_next_match = re.search(
        r'"hasNextPage":(true|false)',
        combined
    )

    if has_next_match:

        has_next = (
            has_next_match.group(1)
            == "true"
        )


    print(
        "PAGINATION:",
        has_next,
        cursor
    )


    return {

        "products":
            products,

        "hasNextPage":
            has_next,

        "endCursor":
            cursor

    }



# ============================================================
# SCRAPER
# ============================================================

def scrape_category(category):

    products = []

    session_data = get_session_data(category)

    headers = build_headers(
        category,
        session_data
    )


    cursor = None
    page = 1


    while True:

        print(
            "\nREQUESTING PAGE",
            page
        )


        if page == 1:

            # Initial page load
            response = session.get(
                BASE_URL.format(category),
                headers={
                    "User-Agent": "Mozilla/5.0"
                },
                timeout=30
            )


        else:

            # Load more request
            payload = [
                {
                    "collection": category,
                    "after": cursor
                }
            ]


            response = session.post(

                BASE_URL.format(category),

                headers=headers,

                data=json.dumps(
                    payload,
                    separators=(",", ":")
                ),

                timeout=30

            )


        print(
            "STATUS:",
            response.status_code
        )


        if response.status_code != 200:

            print(
                response.text[:500]
            )

            break



        data = parse_rsc_response(
            response.text
        )


        batch = data["products"]


        print(
            "FOUND",
            len(batch),
            "cards"
        )


        products.extend(
            batch
        )


        if not data["hasNextPage"]:

            print(
                "NO MORE PAGES"
            )

            break


        cursor = data["endCursor"]


        print(
            "NEXT CURSOR:",
            cursor
        )


        if not cursor:

            break


        page += 1


        time.sleep(1)



    return products


def scrape_all_categories():

    inventory = {}


    for category in CATEGORIES:

        print(
            "\nScanning",
            category
        )


        inventory[category] = scrape_category(
            category
        )


        print(
            category,
            len(inventory[category])
        )


    return inventory



# ============================================================
# DETECT NEW
# ============================================================

def detect_new_cards(current):

    previous = load_seen()

    additions = {}


    for category, cards in current.items():


        old = set(
            previous.get(
                category,
                []
            )
        )


        new = []


        for card in cards:

            if card["id"] not in old:

                new.append(card)



        if new:

            additions[category] = new



    save_seen(
        {
            category:
            [
                c["id"]
                for c in cards
            ]

            for category,cards in current.items()
        }
    )


    return additions



# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "CardsHQ Bot Started"
    )


    while True:

        try:


            inventory = scrape_all_categories()


            new_cards = detect_new_cards(
                inventory
            )


            if new_cards:


                body = (
                    "New Cards Posted on CardsHQ\n\n"
                )


                for category,cards in new_cards.items():


                    body += (
                        CATEGORIES[category]
                        +
                        "\n\n"
                    )


                    for card in cards:


                        body += (

                            f"{card['title']}\n"

                            f"Price: ${card['price']}\n\n"

                        )



                print(body)


                send_email(
                    "🃏 New Cards Added to CardsHQ",
                    body
                )


            else:

                print(
                    "No new cards"
                )


        except Exception as e:

            print(
                "BOT ERROR:",
                e
            )


        print(
            "Sleeping 5 minutes..."
        )


        time.sleep(
            LOOP_PAUSE
        )



if __name__ == "__main__":

    main()
