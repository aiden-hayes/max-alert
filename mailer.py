"""Sending email (password resets) through Brevo's HTTP API.

Set BREVO_API_KEY and MAIL_FROM (a sender address verified in Brevo). Without them:
  - on your computer, emails are written to the server log instead, so you can test;
  - on Render, password reset says email isn't set up (links are never written to
    Render's logs, since anyone who can read the logs could use them).
"""
from __future__ import annotations

import logging

import requests

import config

log = logging.getLogger(__name__)
BREVO_URL = "https://api.brevo.com/v3/smtp/email"


def mode() -> str | None:
    """'brevo' (really sends), 'log' (local testing) or None (not set up)."""
    if config.BREVO_API_KEY and config.MAIL_FROM:
        return "brevo"
    return None if config.ON_RENDER else "log"


def send(to: str, subject: str, text: str, html: str) -> bool:
    m = mode()
    if m == "log":
        log.warning("Email isn't set up (BREVO_API_KEY / MAIL_FROM), so here it is instead:\n"
                    "To: %s\nSubject: %s\n\n%s", to, subject, text)
        return True
    if m is None:
        log.error("Couldn't send %r: BREVO_API_KEY / MAIL_FROM aren't set.", subject)
        return False
    try:
        resp = requests.post(BREVO_URL, timeout=15, headers={
            "api-key": config.BREVO_API_KEY, "accept": "application/json",
        }, json={
            "sender": {"name": config.MAIL_FROM_NAME, "email": config.MAIL_FROM},
            "to": [{"email": to}],
            "subject": subject, "textContent": text, "htmlContent": html,
        })
        if resp.status_code >= 400:
            log.error("Brevo refused the email (%s): %s", resp.status_code, resp.text[:300])
            return False
        return True
    except requests.RequestException as e:
        log.error("Couldn't reach Brevo: %s", e)
        return False
