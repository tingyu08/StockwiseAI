"""Logging filters that prevent configured credentials from reaching logs."""

import logging
from collections.abc import Mapping

from app.core.config import Settings


def _secrets(settings: Settings) -> tuple[str, ...]:
    values = (
        settings.gemini_api_key,
        settings.finmind_token,
        settings.openrouter_api_key,
        settings.job_token,
        settings.alert_webhook_url,
    )
    return tuple(value for value in values if len(value) >= 6)


def redact_sensitive(message: str, settings: Settings) -> str:
    redacted = message
    for secret in _secrets(settings):
        redacted = redacted.replace(secret, "[REDACTED]")
    return redacted


class SecretRedactingFilter(logging.Filter):
    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_sensitive(record.msg, self.settings)
        if isinstance(record.args, Mapping):
            record.args = {
                key: redact_sensitive(value, self.settings)
                if isinstance(value, str)
                else value
                for key, value in record.args.items()
            }
        elif isinstance(record.args, tuple):
            record.args = tuple(
                redact_sensitive(value, self.settings)
                if isinstance(value, str)
                else value
                for value in record.args
            )
        return True


def configure_sensitive_logging(settings: Settings) -> None:
    root = logging.getLogger()
    for handler in root.handlers:
        if not any(isinstance(item, SecretRedactingFilter) for item in handler.filters):
            handler.addFilter(SecretRedactingFilter(settings))
