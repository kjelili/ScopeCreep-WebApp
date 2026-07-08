"""
SMS alerting via Twilio (FR5, §5.7.1) with de-duplication.

Fixes relative to the prototype:
  * De-duplication is actually wired in: the same (number, email-hash) pair
    is never alerted twice; history persists to sms_history.json in the
    app's data directory (NOT the repo).
  * SMS bodies no longer include email content — only the risk level and a
    result reference — so no project or personal data crosses the
    unencrypted SMS channel (NFR3 / GDPR, thesis §3.6).

Twilio docs: https://www.twilio.com/docs/messaging/api
Environment: TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Optional

E164 = re.compile(r"^\+\d{8,15}$")

_DATA_DIR = Path(os.getenv("SCOPEAPP_DATA_DIR", Path.home() / ".scopecreep"))
_HISTORY_FILE = _DATA_DIR / "sms_history.json"


def clean_phone_number(phone) -> str:
    if not isinstance(phone, str):
        return ""
    cleaned = re.sub(r"[^0-9+]", "", phone)
    if cleaned.startswith("+"):
        return "+" + re.sub(r"\D", "", cleaned[1:])
    return re.sub(r"\D", "", cleaned)


def valid_number(phone: str) -> bool:
    return bool(E164.match(clean_phone_number(phone)))


def twilio_configured() -> bool:
    return all([os.getenv("TWILIO_ACCOUNT_SID"),
                os.getenv("TWILIO_AUTH_TOKEN"),
                valid_number(os.getenv("TWILIO_PHONE_NUMBER", ""))])


class SmsHistory:
    """Persistent (number, content-hash) ledger preventing repeat alerts."""

    def __init__(self, path: Path = _HISTORY_FILE):
        self.path = path
        self._seen: dict[str, float] = {}
        try:
            if path.exists():
                self._seen = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            self._seen = {}

    @staticmethod
    def key(number: str, email_body: str) -> str:
        h = hashlib.sha256(email_body.strip().encode("utf-8")).hexdigest()[:16]
        return f"{number}:{h}"

    def already_sent(self, number: str, email_body: str) -> bool:
        return self.key(number, email_body) in self._seen

    def record(self, number: str, email_body: str) -> None:
        self._seen[self.key(number, email_body)] = time.time()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self._seen))
        except OSError:
            pass  # history is best-effort; alerting still works


def send_sms(to_number: str, message: str) -> tuple[bool, Optional[str]]:
    """Send one SMS. Returns (ok, error_message)."""
    to_number = clean_phone_number(to_number)
    if not E164.match(to_number):
        return False, f"Invalid recipient number: {to_number or '(empty)'}"
    if not message:
        return False, "Empty message"
    if not twilio_configured():
        return False, ("Twilio not configured. Set TWILIO_ACCOUNT_SID, "
                       "TWILIO_AUTH_TOKEN and TWILIO_PHONE_NUMBER.")
    try:
        from twilio.rest import Client
        from twilio.base.exceptions import TwilioRestException
    except ImportError:
        return False, "twilio package not installed (pip install twilio)"

    try:
        client = Client(os.getenv("TWILIO_ACCOUNT_SID"),
                        os.getenv("TWILIO_AUTH_TOKEN"))
        msg = client.messages.create(
            body=message[:1600],
            from_=clean_phone_number(os.getenv("TWILIO_PHONE_NUMBER", "")),
            to=to_number)
        return bool(msg.sid), None if msg.sid else f"status={msg.status}"
    except TwilioRestException as exc:  # pragma: no cover (needs live creds)
        return False, f"Twilio API error: {exc}"
    except Exception as exc:  # pragma: no cover
        return False, f"{type(exc).__name__}: {exc}"


def alert_message(risk_level: str, row_index: int, job_id: str,
                  grounded: bool) -> str:
    """Content-free alert body (NFR3): risk + reference, no email content."""
    return (f"SCOPE CREEP ALERT — risk {risk_level.upper()}"
            f"{' (evidence-grounded)' if grounded else ''}. "
            f"Item #{row_index + 1}, analysis {job_id[:8]}. "
            "Review in the Scope Creep Detector dashboard.")
