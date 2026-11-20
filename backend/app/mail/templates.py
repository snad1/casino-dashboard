"""Mail texts. Every value is HTML-escaped in the HTML part; the text part stays plain."""

import html
from dataclasses import dataclass
from string import Template

_HTML = Template(
    '<!doctype html><html><body style="font-family:sans-serif;line-height:1.5">$body</body></html>'
)


@dataclass(frozen=True)
class Rendered:
    subject: str
    text: str
    html: str


def _render(subject: str, lines: list[str], link: tuple[str, str] | None = None) -> Rendered:
    text_lines = list(lines)
    parts = [f"<p>{html.escape(line)}</p>" for line in lines]
    if link is not None:
        label, url = link
        text_lines.insert(1, f"{label}: {url}")
        anchor = f'<p><a href="{html.escape(url, quote=True)}">{html.escape(label)}</a></p>'
        parts.insert(1, anchor)
    return Rendered(subject, "\n\n".join(text_lines) + "\n", _HTML.substitute(body="".join(parts)))


def invite(url: str, role: str, invited_by: str) -> Rendered:
    return _render(
        "You're invited to the casino dashboard",
        [
            f"{invited_by} invited you to the casino dashboard as {role}.",
            "The link works once and expires in 48 hours.",
            "If you did not expect this, you can ignore this email.",
        ],
        ("Accept the invite", url),
    )


def password_reset(url: str) -> Rendered:
    return _render(
        "Reset your casino dashboard password",
        [
            "Someone asked to reset the password for this account.",
            "The link works once and expires in 30 minutes. Every signed-in device is signed out.",
            "If this was not you, ignore this email; your password stays the same.",
        ],
        ("Choose a new password", url),
    )


def new_login(when: str, ip: str, browser: str) -> Rendered:
    return _render(
        "New sign-in to the casino dashboard",
        [
            f"Your account signed in at {when} from {ip} ({browser}).",
            "If this was not you, reset your password now and sign out everywhere.",
        ],
    )


def terminal_opened(who: str, when: str, ip: str, browser: str) -> Rendered:
    return _render(
        "The web terminal was opened",
        [
            f"{who} opened the web terminal at {when} from {ip} ({browser}).",
            "The session is recorded. If this was not you, change your password and sign out "
            "everywhere now.",
        ],
    )


def lockout(until: str) -> Rendered:
    return _render(
        "Your casino dashboard account is locked",
        [
            f"Too many wrong sign-in attempts. Sign-in is blocked until {until}.",
            "If this was not you, someone may be guessing your password.",
        ],
    )


def two_factor_changed(enabled: bool) -> Rendered:
    state = "turned on" if enabled else "turned off"
    return _render(
        f"Two-factor authentication {state}",
        [
            f"Two-factor authentication was {state} for your casino dashboard account.",
            "If this was not you, reset your password now.",
        ],
    )


def alert(message: str) -> Rendered:
    return _render(
        f"Alert: {message[:120]}",
        [message, "You get this because alerts by email are on in your dashboard settings."],
    )


def digest(heading: str, lines: list[str]) -> Rendered:
    return _render(
        heading,
        [
            heading,
            *lines,
            "You get this because the daily summary is on in your alert settings.",
        ],
    )
