import os
import time
import requests

BOT_TOKEN = os.environ["BOT_TOKEN"]

API = f"https://api.telegram.org/bot{BOT_TOKEN}"


def telegram(method, data=None):
    response = requests.post(
        f"{API}/{method}",
        json=data or {},
        timeout=30
    )
    response.raise_for_status()
    return response.json()


def send_message(chat_id, text):
    return telegram(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text
        }
    )


def main():
    print("SMC Telegram Bot starting...")

    offset = None
    sent_test = set()

    while True:
        try:
            data = {}

            if offset is not None:
                data["offset"] = offset

            data["timeout"] = 20

            result = telegram("getUpdates", data)

            for update in result.get("result", []):
                offset = update["update_id"] + 1

                message = update.get("message")

                if not message:
                    continue

                chat_id = message["chat"]["id"]
                text = message.get("text", "")

                print(f"Message received: {text}")

                if chat_id not in sent_test:
                    send_message(
                        chat_id,
                        "🟢 SMC SCANNER ONLINE\n\n"
                        "Telegram connection successful.\n\n"
                        "Scanner status: READY\n"
                        "SMC analysis: OFF\n"
                        "News engine: OFF\n"
                        "Trade execution: DISABLED\n\n"
                        "Next step: connect the market scanner."
                    )

                    sent_test.add(chat_id)

                if text == "/start":
                    send_message(
                        chat_id,
                        "Welcome to the SMC Market Scanner.\n\n"
                        "The Telegram connection is working."
                    )

        except Exception as e:
            print(f"Error: {e}")
            time.sleep(10)


if __name__ == "__main__":
    main()
