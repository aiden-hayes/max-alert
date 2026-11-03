"""Send a short message to ALERT_WEBHOOK_URL (optional) so you hear about problems.

Supported:
  - ntfy.sh (free push notifications to your phone, no account needed):
        ALERT_WEBHOOK_URL=https://ntfy.sh/<a-long-random-topic-name>
  - Discord channel webhook:  https://discord.com/api/webhooks/...
  - Slack incoming webhook:   https://hooks.slack.com/services/...

With no URL set, messages just go to the server log.
"""
from __future__ import annotations

import logging

import requests

import config

log = logging.getLogger("max-alert")


def send(message: str) -> bool:
    log.warning("ALERT: %s", message)
    url = config.ALERT_WEBHOOK_URL
    if not url:
        return False
    try:
        if "discord.com" in url or "discordapp.com" in url:
            resp = requests.post(url, json={"content": message[:1900]}, timeout=10)
        elif "hooks.slack.com" in url:
            resp = requests.post(url, json={"text": message}, timeout=10)
        else:  # ntfy and anything else that takes a plain-text body
            resp = requests.post(url, data=message.encode(), headers={"Title": "Max Alert"}, timeout=10)
        resp.raise_for_status()
        return True
    except requests.RequestException as e:
        log.error("Couldn't send alert to webhook: %s", e)
        return False
