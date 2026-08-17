import os
import requests
import time

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

def send_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    response = requests.post(
        url,
        json={
            "chat_id": CHAT_ID,
            "text": text
        },
        timeout=20
    )

    response.raise_for_status()
    return response.json()

if __name__ == "__main__":
    send_message(
        "🟢 SMC SCANNER ONLINE\n\n"
        "Telegram connection successful.\n\n"
        "Scanner status: READY\n"
        "Market analysis: OFF\n"
        "Trade execution: DISABLED"
    )

    while True:
        time.sleep(3600)
