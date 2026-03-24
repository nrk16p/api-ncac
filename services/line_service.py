import os
import requests as http_requests

LINE_TOKEN = os.getenv("LINE_TOKEN")


def send_line_message(message: str, group_id: str = "C7ef9639fa04e1962bd34e7aa98575296"):
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_TOKEN}",
    }
    body = {
        "to": group_id,
        "messages": [{"type": "text", "text": message}],
    }
    resp = http_requests.post(url, json=body, headers=headers, timeout=10)
    resp.raise_for_status()
