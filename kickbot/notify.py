"""Optional external alerting.

Without this, a silent scraping failure (e.g. Kicktipp changes its page
layout and odds extraction stops working) would only show up in the log
file - nobody reads that proactively. If NTFY_TOPIC or WEBHOOK_URL is
configured, kickbot pushes a notification for anything that needs a human
to look, instead of just failing quietly run after run.
"""

import logging

import requests

from .config import Config

logger = logging.getLogger("kickbot")


def notify(config: Config, title: str, message: str) -> None:
    sent = False

    if config.ntfy_topic:
        url = f"{config.ntfy_url.rstrip('/')}/{config.ntfy_topic}"
        try:
            requests.post(
                url,
                data=message.encode("utf-8"),
                headers={
                    "Title": title.encode("utf-8"),
                    "Priority": "high",
                    "Tags": "warning",
                },
                timeout=10,
            )
            sent = True
        except requests.RequestException as exc:
            logger.warning("Failed to send ntfy notification: %s", exc)

    if config.webhook_url:
        try:
            requests.post(
                config.webhook_url,
                json={"title": title, "message": message},
                timeout=10,
            )
            sent = True
        except requests.RequestException as exc:
            logger.warning("Failed to send webhook notification: %s", exc)

    if not sent:
        logger.debug(
            "No notification channel configured (NTFY_TOPIC / WEBHOOK_URL) - "
            "would have alerted: %s - %s",
            title,
            message,
        )
