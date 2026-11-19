"""General helpers: secure randomness, delays, snowflakes, date parsing and console logging."""

from __future__ import annotations

import os
from datetime import UTC, datetime

from rich.console import Console

#: Shared console instance. ``print()`` is never used anywhere in the project.
console: Console = Console(highlight=False)


class GoBack(Exception):
    """Raised when the user presses Backspace / Esc to return to the previous step."""


#: Discord epoch in milliseconds (2015-01-01T00:00:00.000Z).
DISCORD_EPOCH_MS: int = 1_420_070_400_000

# Layer 2 — adaptive human-like delay defaults for message deletion (safe against rate limits).
DELAY_MIN: float = 0.9
DELAY_MAX: float = 2.1
REST_EVERY: int = 15
REST_MIN: float = 4.5
REST_MAX: float = 8.0

# Polite pause between pagination requests while scanning message history.
# HeaderRateLimiter already obeys Discord's X-RateLimit-* buckets on every response.
SCAN_DELAY_MIN: float = 0.08
SCAN_DELAY_MAX: float = 0.18

# Layer 1 — jitter added after a header-announced reset.
HEADER_RESET_JITTER: float = 0.1


def secure_uniform(low: float, high: float) -> float:
    """Return a float uniformly distributed in ``[low, high]``.

    Entropy is drawn directly from :func:`os.urandom` (not the ``random`` module),
    so the delay pattern cannot be predicted from a PRNG seed.
    """
    if high < low:
        low, high = high, low
    fraction = int.from_bytes(os.urandom(8), "big") / float(1 << 64)
    return low + (high - low) * fraction


def human_delay() -> float:
    """Return a random inter-delete delay in seconds."""
    return secure_uniform(DELAY_MIN, DELAY_MAX)


def rest_delay() -> float:
    """Return a random longer rest in seconds, applied periodically during deletion."""
    return secure_uniform(REST_MIN, REST_MAX)


def scan_delay() -> float:
    """Return a small random delay between message-history pagination calls."""
    return secure_uniform(SCAN_DELAY_MIN, SCAN_DELAY_MAX)


def next_delay(completed: int, total: int) -> float:
    """Return the human-like delay that should follow the ``completed``-th delete request.

    No delay is applied after the final request. After every ``REST_EVERY`` requests an
    additional rest period is added.
    """
    if completed >= total:
        return 0.0
    delay = human_delay()
    if completed % REST_EVERY == 0:
        delay += rest_delay()
    return delay


def estimate_seconds(count: int) -> float:
    """Estimate the total run time in seconds for deleting ``count`` messages."""
    if count <= 0:
        return 0.0
    mean_delay = (DELAY_MIN + DELAY_MAX) / 2
    mean_rest = (REST_MIN + REST_MAX) / 2
    gaps = count - 1
    rests = gaps // REST_EVERY
    return gaps * mean_delay + rests * mean_rest


def format_duration(seconds: float) -> str:
    """Format a duration as a short human-readable string (e.g. ``45s`` or ``3m 12s``)."""
    seconds = max(0.0, seconds)
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes, secs = divmod(int(round(seconds)), 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def backoff_429(retry_after: float, attempt: int) -> float:
    """Compute the 429 backoff: ``retry_after * 1.5 ** attempt`` plus a small jitter."""
    base = max(retry_after, 0.5)
    return base * (1.5**attempt) + secure_uniform(0.0, HEADER_RESET_JITTER)


def datetime_to_snowflake(dt: datetime) -> str:
    """Convert a timezone-aware (or UTC-assumed) datetime to a Discord snowflake ID string."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    ms = int(dt.timestamp() * 1000) - DISCORD_EPOCH_MS
    if ms < 0:
        ms = 0
    return str(ms << 22)


def snowflake_to_datetime(snowflake: str | int) -> datetime:
    """Convert a Discord snowflake ID into a UTC :class:`datetime`."""
    ms = (int(snowflake) >> 22) + DISCORD_EPOCH_MS
    return datetime.fromtimestamp(ms / 1000.0, tz=UTC)


def parse_date_input(raw: str, *, end_of_day: bool = False) -> datetime | None:
    """Parse a user-supplied date string (``YYYY-MM-DD`` or ``DD.MM.YYYY``) as UTC datetime.

    Returns ``None`` if the input cannot be parsed.
    """
    text = raw.strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            parsed = datetime.strptime(text, fmt).replace(tzinfo=UTC)
            if end_of_day:
                return parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
            return parsed.replace(hour=0, minute=0, second=0, microsecond=0)
        except ValueError:
            continue
    return None


def log_info(message: str) -> None:
    """Log an informational message to the console."""
    console.print(f"[cyan]•[/cyan] {message}")


def log_success(message: str) -> None:
    """Log a success message to the console."""
    console.print(f"[green]+[/green] {message}")


def log_warning(message: str) -> None:
    """Log a warning message to the console."""
    console.print(f"[yellow]![/yellow] {message}")


def log_error(message: str) -> None:
    """Log an error message to the console."""
    console.print(f"[red]×[/red] {message}")
