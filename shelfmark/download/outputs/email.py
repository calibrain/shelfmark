"""SMTP helpers for Send to Kindle and notification delivery."""

from __future__ import annotations

import mimetypes
import os
import smtplib
import ssl
from contextlib import suppress
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr
from typing import TYPE_CHECKING, Any

import shelfmark.core.config as core_config
from shelfmark.core.logger import setup_logger

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

logger = setup_logger(__name__)

SECURITY_NONE = "none"
SECURITY_STARTTLS = "starttls"
SECURITY_SSL = "ssl"
ALLOWED_SECURITY = {SECURITY_NONE, SECURITY_STARTTLS, SECURITY_SSL}


class EmailOutputError(Exception):
    """Raised when the email output integration fails."""


@dataclass(frozen=True)
class EmailSmtpConfig:
    """SMTP connection settings for the email output."""

    host: str
    port: int
    security: str
    username: str = ""
    password: str = ""
    from_addr: str = ""
    timeout_seconds: int = 60
    allow_unverified_tls: bool = False
    subject_template: str = "{Title}"


def _parse_int(value: Any, label: str, *, minimum: int = 1) -> int:
    if value is None or value == "":
        msg = f"{label} is required"
        raise EmailOutputError(msg)
    if not isinstance(value, (int, float, str)):
        msg = f"{label} must be a number"
        raise EmailOutputError(msg)
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        msg = f"{label} must be a number"
        raise EmailOutputError(msg) from exc
    if parsed < minimum:
        msg = f"{label} must be >= {minimum}"
        raise EmailOutputError(msg)
    return parsed


def build_email_smtp_config(values: Mapping[str, Any]) -> EmailSmtpConfig:
    """Build and validate SMTP settings for the email output."""
    host = str(values.get("EMAIL_SMTP_HOST", "") or "").strip()
    port = _parse_int(values.get("EMAIL_SMTP_PORT", 587), "SMTP port", minimum=1)

    security = str(values.get("EMAIL_SMTP_SECURITY", SECURITY_STARTTLS) or "").strip().lower()
    if security not in ALLOWED_SECURITY:
        msg = f"SMTP security must be one of: {', '.join(sorted(ALLOWED_SECURITY))}"
        raise EmailOutputError(msg)

    username = str(values.get("EMAIL_SMTP_USERNAME", "") or "").strip()
    password = values.get("EMAIL_SMTP_PASSWORD", "") or ""

    from_addr = str(values.get("EMAIL_FROM", "") or "").strip()
    subject_template = str(values.get("EMAIL_SUBJECT_TEMPLATE", "{Title}") or "").strip()
    timeout_seconds = _parse_int(
        values.get("EMAIL_SMTP_TIMEOUT_SECONDS", 60), "SMTP timeout (seconds)", minimum=1
    )
    allow_unverified_tls = bool(values.get("EMAIL_ALLOW_UNVERIFIED_TLS", False))

    if not host:
        msg = "SMTP host is required"
        raise EmailOutputError(msg)
    if username and not password:
        msg = "SMTP password is required when username is set"
        raise EmailOutputError(msg)

    if not from_addr:
        # If From is not configured, fall back to the SMTP username if it is an email address.
        username_email = parseaddr(username)[1]
        if username_email and "@" in username_email:
            from_addr = f"Shelfmark <{username_email}>"
        else:
            msg = "From address is required (or set SMTP username to an email address)."
            raise EmailOutputError(msg)

    return EmailSmtpConfig(
        host=host,
        port=port,
        security=security,
        username=username,
        password=password,
        from_addr=from_addr,
        timeout_seconds=timeout_seconds,
        allow_unverified_tls=allow_unverified_tls,
        subject_template=subject_template or "{Title}",
    )


def _get_email_settings() -> dict[str, Any]:
    def get_setting(key: str, default: object) -> object:
        # SMTP is deployment configuration: retain environment support even
        # when no administrator-facing settings field is registered.
        return os.environ.get(key, core_config.config.get(key, default))

    allow_unverified_tls = get_setting("EMAIL_ALLOW_UNVERIFIED_TLS", False)
    if isinstance(allow_unverified_tls, str):
        allow_unverified_tls = allow_unverified_tls.lower() in {"true", "1", "yes", "on"}

    return {
        "EMAIL_SMTP_HOST": get_setting("EMAIL_SMTP_HOST", ""),
        "EMAIL_SMTP_PORT": get_setting("EMAIL_SMTP_PORT", 587),
        "EMAIL_SMTP_SECURITY": get_setting("EMAIL_SMTP_SECURITY", SECURITY_STARTTLS),
        "EMAIL_SMTP_USERNAME": get_setting("EMAIL_SMTP_USERNAME", ""),
        "EMAIL_SMTP_PASSWORD": get_setting("EMAIL_SMTP_PASSWORD", ""),
        "EMAIL_FROM": get_setting("EMAIL_FROM", ""),
        "EMAIL_SUBJECT_TEMPLATE": get_setting("EMAIL_SUBJECT_TEMPLATE", "{Title}"),
        "EMAIL_SMTP_TIMEOUT_SECONDS": get_setting("EMAIL_SMTP_TIMEOUT_SECONDS", 60),
        "EMAIL_ALLOW_UNVERIFIED_TLS": allow_unverified_tls,
    }


def _msgid_domain(from_addr: str) -> str:
    from_email = parseaddr(from_addr)[1]
    domain = (from_email.partition("@")[2] or "").strip().rstrip(">")
    return domain or "shelfmark.local"


def _create_tls_context(*, allow_unverified: bool) -> ssl.SSLContext:
    context = ssl.create_default_context()
    if allow_unverified:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return context


def test_smtp_connection(smtp_config: EmailSmtpConfig) -> None:
    """Connect and (optionally) authenticate to the SMTP server. Does not send mail."""
    smtp: smtplib.SMTP | None = None
    try:
        if smtp_config.security == SECURITY_SSL:
            context = _create_tls_context(allow_unverified=smtp_config.allow_unverified_tls)
            smtp = smtplib.SMTP_SSL(
                smtp_config.host,
                smtp_config.port,
                timeout=smtp_config.timeout_seconds,
                context=context,
            )
        else:
            smtp = smtplib.SMTP(
                smtp_config.host, smtp_config.port, timeout=smtp_config.timeout_seconds
            )

        smtp.ehlo()

        if smtp_config.security == SECURITY_STARTTLS:
            context = _create_tls_context(allow_unverified=smtp_config.allow_unverified_tls)
            smtp.starttls(context=context)
            smtp.ehlo()

        if smtp_config.username:
            smtp.login(smtp_config.username, smtp_config.password)
    except smtplib.SMTPAuthenticationError as exc:
        msg = "SMTP authentication failed"
        raise EmailOutputError(msg) from exc
    except (smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected, TimeoutError, OSError) as exc:
        msg = f"Could not connect to SMTP server: {exc}"
        raise EmailOutputError(msg) from exc
    finally:
        if smtp is not None:
            with suppress(Exception):
                smtp.quit()
            with suppress(Exception):
                smtp.close()


def send_email_message(smtp_config: EmailSmtpConfig, message: EmailMessage) -> None:
    """Send a prepared email message using the configured SMTP transport."""
    smtp: smtplib.SMTP | None = None
    try:
        if smtp_config.security == SECURITY_SSL:
            context = _create_tls_context(allow_unverified=smtp_config.allow_unverified_tls)
            smtp = smtplib.SMTP_SSL(
                smtp_config.host,
                smtp_config.port,
                timeout=smtp_config.timeout_seconds,
                context=context,
            )
        else:
            smtp = smtplib.SMTP(
                smtp_config.host, smtp_config.port, timeout=smtp_config.timeout_seconds
            )

        smtp.ehlo()

        if smtp_config.security == SECURITY_STARTTLS:
            context = _create_tls_context(allow_unverified=smtp_config.allow_unverified_tls)
            smtp.starttls(context=context)
            smtp.ehlo()

        if smtp_config.username:
            smtp.login(smtp_config.username, smtp_config.password)

        smtp.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        msg = "SMTP authentication failed"
        raise EmailOutputError(msg) from exc
    except (smtplib.SMTPException, TimeoutError, OSError) as exc:
        msg = f"Failed to send email: {exc}"
        raise EmailOutputError(msg) from exc
    finally:
        if smtp is not None:
            with suppress(Exception):
                smtp.quit()
            with suppress(Exception):
                smtp.close()


def _mask_recipient(recipient: str) -> str:
    """Mask an email recipient for API responses, mirroring _post_process_email's label use.

    Reuses the same shape as ``output_args["label"]`` masking: if the recipient
    looks like an email, show ``l***@domain``; otherwise show the raw string.
    This is the MVP masker — no custom per-user masking logic (#04 sub-decision 16).
    """
    cleaned = (recipient or "").strip()
    if not cleaned:
        return ""
    local, _, domain = cleaned.partition("@")
    if not domain or not local:
        return cleaned
    masked_local = local[0] + "***" if local else "***"
    return f"{masked_local}@{domain}"


def send_file_to_email(
    file_path: Path,
    recipient: str,
    *,
    label: str | None = None,
    subject: str | None = None,
) -> str:
    """Send a single file as an email attachment to ``recipient``.

    Reuses ``build_email_smtp_config`` + ``send_email_message`` against the
    instance's SMTP settings (#04 sub-decision 17). No synthetic DownloadTask;
    the message is composed inline. Raises ``EmailOutputError`` on SMTP/config
    failure — library routes translate this into a 500 via _OPERATIONAL_ERRORS.

    Args:
        file_path: File to attach. Must exist.
        recipient: Email address to send to.
        label: Optional display label for logging/masking; falls back to recipient.
        subject: Optional subject line; defaults to the file name.

    Returns:
        The masked recipient string (for the API success response).

    """
    normalized_recipient = (recipient or "").strip()
    if not normalized_recipient:
        msg = "No email recipient configured"
        raise EmailOutputError(msg)

    if not file_path.exists():
        msg = f"File not found: {file_path}"
        raise EmailOutputError(msg)

    smtp_config = build_email_smtp_config(_get_email_settings())

    message = EmailMessage()
    message["From"] = smtp_config.from_addr
    message["To"] = normalized_recipient
    message["Subject"] = subject or file_path.name
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain=_msgid_domain(smtp_config.from_addr))
    message.set_content("")

    data = file_path.read_bytes()
    content_type, encoding = mimetypes.guess_type(file_path.name)
    if content_type is None or encoding is not None:
        content_type = "application/octet-stream"
    main_type, sub_type = content_type.split("/", 1)
    message.add_attachment(data, maintype=main_type, subtype=sub_type, filename=file_path.name)

    send_email_message(smtp_config, message)

    display_label = label or normalized_recipient
    logger.info("Send-to-Kindle: delivered file=%s to=%s", file_path.name, display_label)
    return _mask_recipient(normalized_recipient)
