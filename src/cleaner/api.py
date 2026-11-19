"""Discord REST API layer (raw HTTP via httpx — no Discord libraries)."""

from __future__ import annotations

import asyncio
import contextlib
import re
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from types import TracebackType
from typing import Any

import httpx

from .i18n import t
from .security import (
    HeaderRateLimiter,
    TokenRevokedError,
    TokenSession,
    secure_wipe,
)
from .utils import backoff_429, datetime_to_snowflake, scan_delay, snowflake_to_datetime

BASE_URL: str = "https://discord.com/api/v10"
MESSAGE_PAGE_LIMIT: int = 100
SEARCH_PAGE_SIZE: int = 25
SEARCH_MAX_OFFSET: int = 9975
MAX_202_RETRIES: int = 2

MAX_429_RETRIES: int = 5
MAX_5XX_RETRIES: int = 3
SERVER_ERROR_DELAY: float = 5.0

#: Regex matching HTTP/HTTPS URLs inside message content.
URL_PATTERN: re.Pattern[str] = re.compile(r"https?://\S+", re.IGNORECASE)

#: Discord channel types for 1-1 DM and Group DM.
CHANNEL_TYPE_DM: int = 1
CHANNEL_TYPE_GROUP_DM: int = 3

#: Discord message types that a user account is allowed to delete.
#: System messages (e.g. call events, recipient add/remove, pin notices) cannot be deleted.
DELETABLE_MESSAGE_TYPES: frozenset[int] = frozenset({0, 19, 20, 21})


class APIError(Exception):
    """Raised when a request ultimately fails (after retries) with a non-success status."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True, slots=True)
class User:
    """The authenticated Discord user."""

    id: str
    username: str
    discriminator: str
    global_name: str | None

    @property
    def tag(self) -> str:
        """Return ``Username#0000`` (``#0`` accounts show as ``Username#0000``)."""
        disc = self.discriminator if self.discriminator not in ("", "0") else "0000"
        return f"{self.username}#{disc}"


@dataclass(frozen=True, slots=True)
class DMChannel:
    """A Discord 1-1 DM or Group DM channel."""

    id: str
    channel_type: int
    name: str
    recipients: tuple[str, ...] = ()
    last_message_id: str | None = None

    @property
    def is_dm(self) -> bool:
        return self.channel_type == CHANNEL_TYPE_DM

    @property
    def is_group(self) -> bool:
        return self.channel_type == CHANNEL_TYPE_GROUP_DM

    @property
    def sort_key(self) -> int:
        """Numeric key for sorting conversations by most recent activity descending."""
        try:
            return int(self.last_message_id) if self.last_message_id else int(self.id)
        except ValueError:
            return 0


@dataclass(frozen=True, slots=True)
class Message:
    """A single Discord message authored by the user."""

    id: str
    channel_id: str
    channel_name: str
    author_id: str
    content: str
    timestamp: datetime
    attachments: tuple[str, ...] = ()
    embed_count: int = 0
    message_type: int = 0
    pinned: bool = False

    def preview_text(self, max_len: int = 60) -> str:
        """Return a single-line human-readable preview of the message content."""
        cleaned = " ".join(self.content.split())
        if cleaned:
            if len(cleaned) > max_len:
                return cleaned[: max_len - 1] + "…"
            return cleaned
        if self.attachments:
            return t("preview_attachment_only", n=len(self.attachments))
        if self.embed_count > 0:
            return t("preview_embed_only")
        return t("preview_empty")


class DateFilterMode(Enum):
    """Supported date filtering modes."""

    ALL = "all"
    LAST_DAYS = "last_days"
    BEFORE = "before"
    AFTER = "after"
    BETWEEN = "between"


@dataclass(frozen=True, slots=True)
class DateFilter:
    """Date range filter applied when scanning for the user's messages."""

    mode: DateFilterMode = DateFilterMode.ALL
    after_dt: datetime | None = None
    before_dt: datetime | None = None
    days: int | None = None

    @classmethod
    def all_messages(cls) -> DateFilter:
        return cls(mode=DateFilterMode.ALL)

    @classmethod
    def last_days(cls, days: int, now: datetime | None = None) -> DateFilter:
        ref = now or datetime.now(UTC)
        return cls(
            mode=DateFilterMode.LAST_DAYS,
            after_dt=ref - timedelta(days=days),
            days=days,
        )

    @classmethod
    def before(cls, dt: datetime) -> DateFilter:
        return cls(mode=DateFilterMode.BEFORE, before_dt=dt)

    @classmethod
    def after(cls, dt: datetime) -> DateFilter:
        return cls(mode=DateFilterMode.AFTER, after_dt=dt)

    @classmethod
    def between(cls, after_dt: datetime, before_dt: datetime) -> DateFilter:
        return cls(mode=DateFilterMode.BETWEEN, after_dt=after_dt, before_dt=before_dt)

    def matches(self, ts: datetime) -> bool:
        """Return ``True`` if ``ts`` falls within the configured date range."""
        if self.after_dt is not None and ts < self.after_dt:
            return False
        return not (self.before_dt is not None and ts > self.before_dt)

    def describe(self) -> str:
        """Return a localized human-readable description of this filter."""
        if self.mode == DateFilterMode.ALL:
            return t("filter_desc_all")
        if self.mode == DateFilterMode.LAST_DAYS and self.after_dt is not None:
            return t(
                "filter_desc_last_days",
                days=self.days or 0,
                since=self.after_dt.strftime("%Y-%m-%d"),
            )
        if self.mode == DateFilterMode.BEFORE and self.before_dt is not None:
            return t("filter_desc_before", before=self.before_dt.strftime("%Y-%m-%d"))
        if self.mode == DateFilterMode.AFTER and self.after_dt is not None:
            return t("filter_desc_after", after=self.after_dt.strftime("%Y-%m-%d"))
        if (
            self.mode == DateFilterMode.BETWEEN
            and self.after_dt is not None
            and self.before_dt is not None
        ):
            return t(
                "filter_desc_between",
                after=self.after_dt.strftime("%Y-%m-%d"),
                before=self.before_dt.strftime("%Y-%m-%d"),
            )
        return t("filter_desc_all")


class MessageKind(Enum):
    """Message content type filter."""

    ALL = "all"
    ATTACHMENTS_ONLY = "attachments"
    LINKS_ONLY = "links"


@dataclass(frozen=True, slots=True)
class MessageFilter:
    """Content type, pinned protection and keep-latest filter for user messages."""

    kind: MessageKind = MessageKind.ALL
    skip_pinned: bool = True
    keep_latest: int = 0

    def matches(self, message: Message) -> bool:
        """Return ``True`` if ``message`` passes type and pinned checks."""
        if self.skip_pinned and message.pinned:
            return False
        if self.kind == MessageKind.ATTACHMENTS_ONLY:
            return bool(message.attachments)
        if self.kind == MessageKind.LINKS_ONLY:
            return URL_PATTERN.search(message.content) is not None
        return True

    def describe(self) -> str:
        """Return a localized human-readable summary of this filter."""
        parts: list[str] = []
        if self.kind == MessageKind.ATTACHMENTS_ONLY:
            parts.append(t("kind_desc_attachments"))
        elif self.kind == MessageKind.LINKS_ONLY:
            parts.append(t("kind_desc_links"))
        else:
            parts.append(t("kind_desc_all"))
        if self.skip_pinned:
            parts.append(t("pinned_desc_keep"))
        if self.keep_latest > 0:
            parts.append(t("keep_latest_desc", n=self.keep_latest))
        return " · ".join(parts)


class DeleteStatus(Enum):
    """Outcome of a single message delete attempt."""

    DELETED = "deleted"
    SKIPPED = "skipped"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class DeleteResult:
    """Result of attempting to delete one message."""

    message: Message
    status: DeleteStatus
    detail: str = ""


@dataclass(slots=True)
class ScanSummary:
    """Aggregated result of scanning one or more channels for the user's messages."""

    channels: list[DMChannel]
    messages: list[Message] = field(default_factory=list)
    per_channel_counts: dict[str, int] = field(default_factory=dict)


def _format_recipient(raw: dict[str, Any]) -> str:
    """Build a readable label for a recipient user object."""
    username = str(raw.get("username", "unknown"))
    global_name = raw.get("global_name")
    if isinstance(global_name, str) and global_name.strip() and global_name.strip() != username:
        return f"{global_name.strip()} (@{username})"
    return f"@{username}"


def _parse_channel(raw: dict[str, Any]) -> DMChannel:
    """Convert a raw Discord channel JSON object into a :class:`DMChannel`."""
    cid = str(raw["id"])
    ctype = int(raw.get("type", CHANNEL_TYPE_DM))
    raw_recipients = raw.get("recipients") or []
    recipients = tuple(_format_recipient(r) for r in raw_recipients if isinstance(r, dict))
    raw_name = raw.get("name")
    name = raw_name.strip() if isinstance(raw_name, str) and raw_name.strip() else ""

    if not name:
        if ctype == CHANNEL_TYPE_DM and recipients:
            name = recipients[0]
        elif ctype == CHANNEL_TYPE_GROUP_DM and recipients:
            if len(recipients) <= 3:
                name = ", ".join(recipients)
            else:
                name = f"{', '.join(recipients[:3])} +{len(recipients) - 3}"
        else:
            name = f"Channel {cid}"

    last_msg = raw.get("last_message_id")
    return DMChannel(
        id=cid,
        channel_type=ctype,
        name=name,
        recipients=recipients,
        last_message_id=str(last_msg) if last_msg else None,
    )


def _parse_timestamp(raw_ts: str) -> datetime:
    """Parse an ISO-8601 Discord message timestamp into a UTC :class:`datetime`."""
    # Python 3.11+ datetime.fromisoformat handles 'Z' and '+00:00' natively.
    dt = datetime.fromisoformat(raw_ts)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


class DiscordClient:
    """Minimal async Discord REST client for a *user* token.

    Endpoints used:
      - ``GET /users/@me``
      - ``GET /users/@me/channels``
      - ``GET /channels/{id}/messages/search`` (fast own-message index, with auto fallback)
      - ``GET /channels/{id}/messages``
      - ``DELETE /channels/{id}/messages/{message_id}``
    """

    def __init__(self, session: TokenSession, fingerprint: tuple[str, str]) -> None:
        self._session = session
        self._fingerprint = fingerprint
        self._limiter = HeaderRateLimiter()
        self._http: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------ lifecycle
    async def __aenter__(self) -> DiscordClient:
        user_agent, super_properties = self._fingerprint
        headers = {
            "Authorization": self._session.token,
            "Content-Type": "application/json",
            "User-Agent": user_agent,
            "X-Super-Properties": super_properties,
        }
        self._http = httpx.AsyncClient(
            base_url=BASE_URL,
            headers=headers,
            http2=True,
            timeout=httpx.Timeout(20.0, connect=10.0),
            follow_redirects=False,
            trust_env=False,
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._http is None:
            return
        http, self._http = self._http, None
        try:
            await http.aclose()
        finally:
            with contextlib.suppress(Exception):
                for key, value in http.headers.raw:
                    if key.lower() == b"authorization":
                        secure_wipe(value)

    # ------------------------------------------------------------------ core request
    @staticmethod
    def _retry_after(response: httpx.Response) -> float:
        try:
            body = response.json()
            if isinstance(body, dict) and "retry_after" in body:
                return float(body["retry_after"])
        except (ValueError, TypeError):
            pass
        for header in ("Retry-After", "X-RateLimit-Reset-After"):
            value = response.headers.get(header)
            if value:
                try:
                    return float(value)
                except ValueError:
                    continue
        return 1.0

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        if self._http is None:
            raise RuntimeError("DiscordClient used outside of 'async with'.")

        attempts_429 = 0
        attempts_5xx = 0
        while True:
            try:
                response = await self._http.request(method, path, **kwargs)
            except httpx.TransportError as exc:
                if attempts_5xx >= MAX_5XX_RETRIES:
                    raise APIError(t("err_network", name=type(exc).__name__)) from None
                attempts_5xx += 1
                await asyncio.sleep(SERVER_ERROR_DELAY)
                continue

            status = response.status_code
            if status == 401:
                raise TokenRevokedError(t("err_401"))

            if status == 429:
                if attempts_429 >= MAX_429_RETRIES:
                    raise APIError(t("err_429"), status)
                await asyncio.sleep(backoff_429(self._retry_after(response), attempts_429))
                attempts_429 += 1
                continue

            if 500 <= status < 600:
                if attempts_5xx >= MAX_5XX_RETRIES:
                    raise APIError(t("err_5xx", status=status), status)
                attempts_5xx += 1
                await asyncio.sleep(SERVER_ERROR_DELAY)
                continue

            await self._limiter.observe(response)
            return response

    # ------------------------------------------------------------------ endpoints
    async def get_me(self) -> User:
        """Validate the token and return the authenticated user (``GET /users/@me``)."""
        response = await self._request("GET", "/users/@me")
        if response.status_code != 200:
            raise APIError(t("err_account", status=response.status_code), response.status_code)
        data = response.json()
        if data.get("bot"):
            raise APIError(t("err_bot"))
        return User(
            id=str(data["id"]),
            username=str(data.get("username", "unknown")),
            discriminator=str(data.get("discriminator", "0")),
            global_name=data.get("global_name"),
        )

    async def fetch_dm_channels(self) -> list[DMChannel]:
        """Return all open 1-1 DM and Group DM channels, sorted by most recent activity."""
        response = await self._request("GET", "/users/@me/channels")
        if response.status_code != 200:
            raise APIError(t("err_dms", status=response.status_code), response.status_code)
        data = response.json()
        if not isinstance(data, list):
            return []
        channels = [
            _parse_channel(raw)
            for raw in data
            if isinstance(raw, dict)
            and int(raw.get("type", 0)) in (CHANNEL_TYPE_DM, CHANNEL_TYPE_GROUP_DM)
        ]
        channels.sort(key=lambda ch: ch.sort_key, reverse=True)
        return channels

    @staticmethod
    def _parse_message(raw: dict[str, Any], channel: DMChannel, user_id: str) -> Message | None:
        """Convert a raw Discord message dict to :class:`Message` if owned & deletable."""
        raw_ts = raw.get("timestamp")
        if not isinstance(raw_ts, str):
            return None
        author = raw.get("author") or {}
        if str(author.get("id", "")) != user_id:
            return None
        msg_type = int(raw.get("type", 0))
        if msg_type not in DELETABLE_MESSAGE_TYPES:
            return None
        attachments = tuple(
            str(att.get("url", ""))
            for att in (raw.get("attachments") or [])
            if isinstance(att, dict) and att.get("url")
        )
        embeds = raw.get("embeds") or []
        return Message(
            id=str(raw["id"]),
            channel_id=channel.id,
            channel_name=channel.name,
            author_id=user_id,
            content=str(raw.get("content") or ""),
            timestamp=_parse_timestamp(raw_ts),
            attachments=attachments,
            embed_count=len(embeds) if isinstance(embeds, list) else 0,
            message_type=msg_type,
            pinned=bool(raw.get("pinned", False)),
        )

    async def _fetch_via_search(
        self,
        channel: DMChannel,
        user_id: str,
        date_filter: DateFilter,
        msg_filter: MessageFilter,
        on_progress: Callable[[int, int], None] | None = None,
        max_messages: int | None = None,
    ) -> list[Message] | None:
        """Try Discord's Search API for fast own-message lookup.

        Returns ``None`` if the endpoint is unavailable, still indexing after retries,
        or hits the offset limit — signaling the caller to fall back to standard paging.
        """
        matched: list[Message] = []
        seen_ids: set[str] = set()
        own_seen = 0
        scanned = 0
        offset = 0

        while True:
            if offset > SEARCH_MAX_OFFSET:
                return None  # Fallback to standard pagination for >10k deep histories

            params: dict[str, str | int] = {
                "author_id": user_id,
                "sort_by": "timestamp",
                "sort_order": "desc",
                "offset": offset,
            }
            if date_filter.after_dt is not None:
                params["min_id"] = datetime_to_snowflake(date_filter.after_dt)
            if date_filter.before_dt is not None:
                params["max_id"] = datetime_to_snowflake(
                    date_filter.before_dt + timedelta(seconds=1)
                )
            if msg_filter.keep_latest == 0:
                if msg_filter.kind == MessageKind.ATTACHMENTS_ONLY:
                    params["has"] = "file"
                elif msg_filter.kind == MessageKind.LINKS_ONLY:
                    params["has"] = "link"

            retries_202 = 0
            while True:
                response = await self._request(
                    "GET", f"/channels/{channel.id}/messages/search", params=params
                )
                if response.status_code == 202:
                    if retries_202 >= MAX_202_RETRIES:
                        return None
                    retries_202 += 1
                    wait = min(self._retry_after(response), 3.0)
                    await asyncio.sleep(max(wait, 0.5))
                    continue
                break

            if response.status_code != 200:
                return None

            data = response.json()
            if not isinstance(data, dict):
                return None
            groups = data.get("messages")
            if not isinstance(groups, list) or not groups:
                break

            for group in groups:
                if not isinstance(group, list):
                    continue
                for raw in group:
                    if not isinstance(raw, dict):
                        continue
                    if not raw.get("hit", True):
                        continue
                    scanned += 1
                    msg = self._parse_message(raw, channel, user_id)
                    if msg is None or msg.id in seen_ids:
                        continue
                    seen_ids.add(msg.id)
                    if not date_filter.matches(msg.timestamp):
                        continue
                    own_seen += 1
                    if own_seen <= msg_filter.keep_latest:
                        continue
                    if not msg_filter.matches(msg):
                        continue
                    matched.append(msg)
                    if max_messages is not None and len(matched) >= max_messages:
                        if on_progress is not None:
                            on_progress(scanned, len(matched))
                        return matched

            if on_progress is not None:
                on_progress(scanned, len(matched))

            total_results = int(data.get("total_results", 0))
            offset += len(groups)
            if offset >= total_results or len(groups) < SEARCH_PAGE_SIZE:
                break
            await asyncio.sleep(scan_delay())

        return matched

    async def iter_own_messages(
        self,
        channel: DMChannel,
        user_id: str,
        date_filter: DateFilter,
        msg_filter: MessageFilter | None = None,
        on_progress: Callable[[int, int], None] | None = None,
        max_messages: int | None = None,
    ) -> AsyncIterator[Message]:
        """Stream matching deletable messages page-by-page (used by Scan-while-Delete & fallback)."""
        flt = msg_filter or MessageFilter()
        if max_messages is not None and max_messages <= 0:
            return

        if channel.last_message_id is not None and date_filter.after_dt is not None:
            with contextlib.suppress(ValueError):
                if snowflake_to_datetime(channel.last_message_id) < date_filter.after_dt:
                    return

        scanned = 0
        yielded = 0
        own_seen = 0

        before_cursor: str | None = None
        if date_filter.before_dt is not None:
            before_cursor = datetime_to_snowflake(date_filter.before_dt + timedelta(seconds=1))

        while True:
            params: dict[str, str | int] = {"limit": MESSAGE_PAGE_LIMIT}
            if before_cursor is not None:
                params["before"] = before_cursor

            response = await self._request("GET", f"/channels/{channel.id}/messages", params=params)
            if response.status_code != 200:
                raise APIError(t("err_messages", status=response.status_code), response.status_code)

            page = response.json()
            if not isinstance(page, list) or not page:
                break

            reached_lower_bound = False
            for raw in page:
                if not isinstance(raw, dict):
                    continue
                scanned += 1
                raw_ts = raw.get("timestamp")
                if not isinstance(raw_ts, str):
                    continue
                ts = _parse_timestamp(raw_ts)

                if date_filter.after_dt is not None and ts < date_filter.after_dt:
                    reached_lower_bound = True
                    break

                msg = self._parse_message(raw, channel, user_id)
                if msg is None:
                    continue
                if not date_filter.matches(msg.timestamp):
                    continue

                own_seen += 1
                if own_seen <= flt.keep_latest:
                    continue
                if not flt.matches(msg):
                    continue

                yielded += 1
                yield msg
                if max_messages is not None and yielded >= max_messages:
                    reached_lower_bound = True
                    break

            if on_progress is not None:
                on_progress(scanned, yielded)

            if reached_lower_bound or len(page) < MESSAGE_PAGE_LIMIT:
                break

            before_cursor = str(page[-1]["id"])
            await asyncio.sleep(scan_delay())

    async def fetch_own_messages(
        self,
        channel: DMChannel,
        user_id: str,
        date_filter: DateFilter,
        on_progress: Callable[[int, int], None] | None = None,
        max_messages: int | None = None,
        msg_filter: MessageFilter | None = None,
        use_search: bool = True,
    ) -> list[Message]:
        """Collect deletable messages authored by ``user_id`` (Search API with auto fallback)."""
        flt = msg_filter or MessageFilter()
        if max_messages is not None and max_messages <= 0:
            return []

        if channel.last_message_id is not None and date_filter.after_dt is not None:
            with contextlib.suppress(ValueError):
                if snowflake_to_datetime(channel.last_message_id) < date_filter.after_dt:
                    return []

        if use_search:
            via_search = await self._fetch_via_search(
                channel,
                user_id,
                date_filter,
                flt,
                on_progress=on_progress,
                max_messages=max_messages,
            )
            if via_search is not None:
                return via_search

        matched: list[Message] = []
        async for msg in self.iter_own_messages(
            channel,
            user_id,
            date_filter,
            msg_filter=flt,
            on_progress=on_progress,
            max_messages=max_messages,
        ):
            matched.append(msg)
        return matched

    async def delete_message(self, message: Message) -> DeleteResult:
        """Delete a single message (``DELETE /channels/{cid}/messages/{mid}``)."""
        try:
            response = await self._request(
                "DELETE", f"/channels/{message.channel_id}/messages/{message.id}"
            )
        except APIError as exc:
            return DeleteResult(message, DeleteStatus.ERROR, str(exc))

        status = response.status_code
        if status in (200, 204):
            return DeleteResult(message, DeleteStatus.DELETED, t("detail_deleted"))
        if status == 404:
            return DeleteResult(message, DeleteStatus.SKIPPED, t("detail_404"))
        if status == 403:
            return DeleteResult(message, DeleteStatus.SKIPPED, t("detail_forbidden"))
        return DeleteResult(message, DeleteStatus.ERROR, t("detail_unexpected", status=status))
