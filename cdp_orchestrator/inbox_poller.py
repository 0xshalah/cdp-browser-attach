"""Async email/inbox polling with verification link extraction."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class EmailMessage:
    """Represents a parsed email message."""
    sender: str = ""
    subject: str = ""
    body: str = ""
    html: str = ""
    links: List[str] = field(default_factory=list)
    otp_codes: List[str] = field(default_factory=list)


class InboxPoller:
    """Poll an inbox until a matching email arrives."""

    def __init__(
        self,
        fetch_func: Callable,
        condition_func: Optional[Callable] = None,
        timeout: float = 60.0,
        interval: float = 5.0,
    ) -> None:
        self._fetch_func = fetch_func
        self._condition_func = condition_func or (lambda emails: len(emails) > 0)
        self._timeout = timeout
        self._interval = interval

    async def poll(self) -> Optional[List[Any]]:
        """Poll inbox until condition is met or timeout."""
        start = asyncio.get_event_loop().time()

        while asyncio.get_event_loop().time() - start < self._timeout:
            try:
                emails = await self._fetch_func()
                if self._condition_func(emails):
                    logger.info(f"Condition met — got {len(emails)} emails")
                    return emails
            except Exception as e:
                logger.warning(f"Fetch error: {e}")

            await asyncio.sleep(self._interval)

        logger.warning(f"Timeout after {self._timeout}s")
        return None

    @staticmethod
    def extract_links(text: str) -> List[str]:
        """Extract all URLs from text."""
        url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
        return re.findall(url_pattern, text)

    @staticmethod
    def extract_otp(text: str, length: int = 6) -> List[str]:
        """Extract OTP codes (numeric sequences) from text."""
        otp_pattern = rf'\b\d{{{length}}}\b'
        return re.findall(otp_pattern, text)

    @staticmethod
    def parse_email(raw: Any) -> EmailMessage:
        """Parse a raw email into structured format."""
        msg = EmailMessage()

        if isinstance(raw, dict):
            msg.sender = raw.get("from", raw.get("sender", ""))
            msg.subject = raw.get("subject", raw.get("title", ""))
            msg.body = raw.get("body", raw.get("text", raw.get("content", "")))
            msg.html = raw.get("html", "")

            # Extract links and OTP from body
            full_text = f"{msg.subject} {msg.body} {msg.html}"
            msg.links = InboxPoller.extract_links(full_text)
            msg.otp_codes = InboxPoller.extract_otp(full_text)

        elif isinstance(raw, str):
            msg.body = raw
            msg.links = InboxPoller.extract_links(raw)
            msg.otp_codes = InboxPoller.extract_otp(raw)

        return msg
