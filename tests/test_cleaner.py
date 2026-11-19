"""Unit tests for Discord DM Cleaner (utils, security, API, backup)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from cleaner import __author__
from cleaner.api import (
    DateFilter,
    DeleteStatus,
    DiscordClient,
    DMChannel,
    Message,
    MessageFilter,
    MessageKind,
    User,
    _parse_channel,
)
from cleaner.backup import BackupFormat, export_backup
from cleaner.checkpoint import Checkpoint, clear_checkpoint, load_checkpoint, save_checkpoint
from cleaner.i18n import STRINGS
from cleaner.security import (
    SignatureError,
    TokenRevokedError,
    TokenSession,
    generate_fingerprint,
    validate_token_format,
    verify_signature,
)
from cleaner.ui import _BANNER_LINES
from cleaner.utils import (
    datetime_to_snowflake,
    next_delay,
    parse_date_input,
    snowflake_to_datetime,
)


def _dummy_token() -> str:
    """Build a synthetic token-shaped string at runtime (never a literal secret)."""
    return ".".join(("A" * 24, "B" * 6, "C" * 27))


def test_signature_valid() -> None:
    verify_signature(__author__, _BANNER_LINES)


def test_signature_tampered_raises() -> None:
    with pytest.raises(SignatureError):
        verify_signature("someone_else", _BANNER_LINES)


def test_token_format_validation() -> None:
    valid_token = _dummy_token()
    assert validate_token_format(valid_token) is True
    assert validate_token_format(f"Bot {valid_token}") is False
    assert validate_token_format("short") is False
    assert validate_token_format("") is False


def test_token_session_wipe() -> None:
    token = _dummy_token()
    with TokenSession(token) as session:
        assert session.active is True
        assert "TokenSession" in repr(session)
        assert token not in repr(session)
    assert session.active is False


def test_snowflake_roundtrip() -> None:
    dt = datetime(2025, 6, 15, 12, 30, 0, tzinfo=UTC)
    sf = datetime_to_snowflake(dt)
    recovered = snowflake_to_datetime(sf)
    assert abs((recovered - dt).total_seconds()) < 0.01


def test_parse_date_input() -> None:
    d1 = parse_date_input("2025-03-10")
    d2 = parse_date_input("10.03.2025")
    d3 = parse_date_input("10/03/2025", end_of_day=True)
    assert d1 == datetime(2025, 3, 10, 0, 0, 0, tzinfo=UTC)
    assert d2 == d1
    assert d3 is not None and d3.hour == 23 and d3.minute == 59
    assert parse_date_input("not-a-date") is None


def test_date_filter_modes() -> None:
    t1 = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
    t2 = datetime(2025, 6, 1, 12, 0, tzinfo=UTC)
    t3 = datetime(2025, 12, 1, 12, 0, tzinfo=UTC)

    assert DateFilter.all_messages().matches(t1) is True
    assert DateFilter.before(t2).matches(t1) is True
    assert DateFilter.before(t2).matches(t3) is False
    assert DateFilter.after(t2).matches(t3) is True
    assert DateFilter.after(t2).matches(t1) is False
    assert DateFilter.between(t1, t3).matches(t2) is True
    assert DateFilter.between(t1, t2).matches(t3) is False

    f_days = DateFilter.last_days(10, now=t3)
    assert f_days.matches(t3 - timedelta(days=5)) is True
    assert f_days.matches(t3 - timedelta(days=15)) is False


def test_next_delay_bounds() -> None:
    assert next_delay(5, 5) == 0.0
    assert next_delay(1, 5) > 0.0


def test_parse_channel_dm_and_group() -> None:
    dm = _parse_channel(
        {
            "id": "111111111111111111",
            "type": 1,
            "recipients": [{"username": "alice", "global_name": "Alice"}],
            "last_message_id": "222222222222222222",
        }
    )
    assert dm.is_dm is True
    assert dm.name == "Alice (@alice)"

    grp = _parse_channel(
        {
            "id": "333333333333333333",
            "type": 3,
            "name": None,
            "recipients": [
                {"username": "bob"},
                {"username": "carol"},
            ],
        }
    )
    assert grp.is_group is True
    assert grp.name == "@bob, @carol"


def test_export_backup_json_and_txt(tmp_path: Path) -> None:
    user = User(id="100", username="tester", discriminator="0", global_name="Tester")
    ch = DMChannel(id="200", channel_type=1, name="Alice (@alice)", recipients=("Alice (@alice)",))
    msgs = [
        Message(
            id="301",
            channel_id="200",
            channel_name=ch.name,
            author_id="100",
            content="Hello newer",
            timestamp=datetime(2025, 5, 2, 10, 0, tzinfo=UTC),
            attachments=("https://cdn.example/a.png",),
        ),
        Message(
            id="300",
            channel_id="200",
            channel_name=ch.name,
            author_id="100",
            content="Hello older",
            timestamp=datetime(2025, 5, 1, 10, 0, tzinfo=UTC),
        ),
    ]
    paths = export_backup(
        user, [ch], msgs, DateFilter.all_messages(), BackupFormat.BOTH, output_dir=tmp_path
    )
    assert len(paths) == 2
    json_file = next(p for p in paths if p.suffix == ".json")
    txt_file = next(p for p in paths if p.suffix == ".txt")

    data = json.loads(json_file.read_text(encoding="utf-8"))
    assert data["total_messages"] == 2
    assert data["messages"][0]["content"] == "Hello newer"

    txt = txt_file.read_text(encoding="utf-8")
    assert "Hello older" in txt
    assert "Hello newer" in txt
    assert "https://cdn.example/a.png" in txt


@pytest.mark.asyncio
async def test_client_scan_and_delete_with_mock_transport() -> None:
    me_id = "999"
    ch = DMChannel(id="555", channel_type=1, name="@friend")
    mock_messages: list[dict[str, Any]] = [
        {
            "id": "1004",
            "type": 0,
            "content": "my newest message",
            "timestamp": "2025-05-10T12:00:00+00:00",
            "author": {"id": me_id},
            "attachments": [],
            "embeds": [],
        },
        {
            "id": "1003",
            "type": 3,  # call system message — must be ignored
            "content": "",
            "timestamp": "2025-05-09T12:00:00+00:00",
            "author": {"id": me_id},
        },
        {
            "id": "1002",
            "type": 0,
            "content": "other user message",
            "timestamp": "2025-05-08T12:00:00+00:00",
            "author": {"id": "888"},
        },
        {
            "id": "1001",
            "type": 0,
            "content": "too old message",
            "timestamp": "2025-04-01T12:00:00+00:00",
            "author": {"id": me_id},
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/api/v10/channels/555/messages":
            return httpx.Response(200, json=mock_messages)
        if request.method == "DELETE" and request.url.path == "/api/v10/channels/555/messages/1004":
            return httpx.Response(204)
        if request.method == "DELETE" and request.url.path == "/api/v10/channels/555/messages/4040":
            return httpx.Response(404)
        if request.method == "DELETE" and request.url.path == "/api/v10/channels/555/messages/4010":
            return httpx.Response(401)
        return httpx.Response(404)

    session = TokenSession(_dummy_token())
    client = DiscordClient(session, generate_fingerprint())
    client._http = httpx.AsyncClient(
        base_url="https://discord.com/api/v10",
        transport=httpx.MockTransport(handler),
    )
    try:
        flt = DateFilter.after(datetime(2025, 5, 1, 0, 0, tzinfo=UTC))
        found = await client.fetch_own_messages(ch, me_id, flt)
        assert [m.id for m in found] == ["1004"]

        res_ok = await client.delete_message(found[0])
        assert res_ok.status is DeleteStatus.DELETED

        missing = Message(
            id="4040",
            channel_id="555",
            channel_name="@friend",
            author_id=me_id,
            content="gone",
            timestamp=datetime(2025, 5, 10, tzinfo=UTC),
        )
        res_404 = await client.delete_message(missing)
        assert res_404.status is DeleteStatus.SKIPPED

        revoked = Message(
            id="4010",
            channel_id="555",
            channel_name="@friend",
            author_id=me_id,
            content="revoked",
            timestamp=datetime(2025, 5, 10, tzinfo=UTC),
        )
        with pytest.raises(TokenRevokedError):
            await client.delete_message(revoked)
    finally:
        await client.aclose()
        session.wipe()


def test_i18n_key_parity() -> None:
    assert set(STRINGS["en"]) == set(STRINGS["tr"])


@pytest.mark.asyncio
async def test_client_fast_skip_old_channel() -> None:
    """Channels whose last_message_id is older than after_dt must be skipped with 0 requests."""
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json=[])

    old_sf = datetime_to_snowflake(datetime(2024, 1, 1, 0, 0, tzinfo=UTC))
    ch = DMChannel(id="777", channel_type=1, name="@oldfriend", last_message_id=old_sf)
    session = TokenSession(_dummy_token())
    client = DiscordClient(session, generate_fingerprint())
    client._http = httpx.AsyncClient(
        base_url="https://discord.com/api/v10",
        transport=httpx.MockTransport(handler),
    )
    try:
        flt = DateFilter.after(datetime(2025, 1, 1, 0, 0, tzinfo=UTC))
        found = await client.fetch_own_messages(ch, "999", flt)
        assert found == []
        assert called is False
    finally:
        await client.aclose()
        session.wipe()


@pytest.mark.asyncio
async def test_client_max_messages_early_stop() -> None:
    """Scanning must stop immediately once max_messages is reached without extra pages."""
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        page = [
            {
                "id": str(2000 - i),
                "type": 0,
                "content": f"msg {i}",
                "timestamp": "2025-05-10T12:00:00+00:00",
                "author": {"id": "999"},
                "attachments": [],
                "embeds": [],
            }
            for i in range(100)
        ]
        return httpx.Response(200, json=page)

    ch = DMChannel(id="888", channel_type=1, name="@busy")
    session = TokenSession(_dummy_token())
    client = DiscordClient(session, generate_fingerprint())
    client._http = httpx.AsyncClient(
        base_url="https://discord.com/api/v10",
        transport=httpx.MockTransport(handler),
    )
    try:
        found = await client.fetch_own_messages(
            ch, "999", DateFilter.all_messages(), max_messages=5, use_search=False
        )
        assert len(found) == 5
        assert requests == 1
    finally:
        await client.aclose()
        session.wipe()


def test_message_filter_types_and_pinned() -> None:
    ts = datetime(2025, 5, 10, tzinfo=UTC)
    plain = Message("1", "c", "ch", "u", "hello world", ts)
    pinned = Message("2", "c", "ch", "u", "important", ts, pinned=True)
    with_att = Message("3", "c", "ch", "u", "", ts, attachments=("https://cdn/x.png",))
    with_link = Message("4", "c", "ch", "u", "check https://example.com now", ts)

    assert MessageFilter(MessageKind.ALL, skip_pinned=True).matches(pinned) is False
    assert MessageFilter(MessageKind.ALL, skip_pinned=False).matches(pinned) is True
    assert MessageFilter(MessageKind.ATTACHMENTS_ONLY).matches(with_att) is True
    assert MessageFilter(MessageKind.ATTACHMENTS_ONLY).matches(plain) is False
    assert MessageFilter(MessageKind.LINKS_ONLY).matches(with_link) is True
    assert MessageFilter(MessageKind.LINKS_ONLY).matches(plain) is False


@pytest.mark.asyncio
async def test_client_search_api_and_keep_latest() -> None:
    """Search API returns own messages directly and respects keep_latest."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v10/channels/500/messages/search":
            return httpx.Response(
                200,
                json={
                    "total_results": 3,
                    "messages": [
                        [
                            {
                                "id": "3003",
                                "type": 0,
                                "content": "newest (keep)",
                                "timestamp": "2025-05-12T12:00:00+00:00",
                                "author": {"id": "999"},
                                "hit": True,
                            }
                        ],
                        [
                            {
                                "id": "3002",
                                "type": 0,
                                "content": "second (delete)",
                                "timestamp": "2025-05-11T12:00:00+00:00",
                                "author": {"id": "999"},
                                "hit": True,
                            }
                        ],
                        [
                            {
                                "id": "3001",
                                "type": 0,
                                "content": "oldest (delete)",
                                "timestamp": "2025-05-10T12:00:00+00:00",
                                "author": {"id": "999"},
                                "hit": True,
                            }
                        ],
                    ],
                },
            )
        raise AssertionError(f"Unexpected fallback request: {request.url.path}")

    ch = DMChannel(id="500", channel_type=3, name="Busy Group")
    session = TokenSession(_dummy_token())
    client = DiscordClient(session, generate_fingerprint())
    client._http = httpx.AsyncClient(
        base_url="https://discord.com/api/v10",
        transport=httpx.MockTransport(handler),
    )
    try:
        flt = MessageFilter(kind=MessageKind.ALL, skip_pinned=True, keep_latest=1)
        found = await client.fetch_own_messages(
            ch, "999", DateFilter.all_messages(), msg_filter=flt
        )
        assert [m.id for m in found] == ["3002", "3001"]
    finally:
        await client.aclose()
        session.wipe()


@pytest.mark.asyncio
async def test_client_stream_iter_own_messages() -> None:
    """Scan-while-Delete async generator yields messages page by page."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "id": "502",
                    "type": 0,
                    "content": "keep me",
                    "timestamp": "2025-05-11T12:00:00+00:00",
                    "author": {"id": "999"},
                },
                {
                    "id": "501",
                    "type": 0,
                    "content": "delete me https://test.dev",
                    "timestamp": "2025-05-10T12:00:00+00:00",
                    "author": {"id": "999"},
                },
            ],
        )

    ch = DMChannel(id="600", channel_type=1, name="@stream")
    session = TokenSession(_dummy_token())
    client = DiscordClient(session, generate_fingerprint())
    client._http = httpx.AsyncClient(
        base_url="https://discord.com/api/v10",
        transport=httpx.MockTransport(handler),
    )
    try:
        flt = MessageFilter(kind=MessageKind.LINKS_ONLY, skip_pinned=True, keep_latest=1)
        yielded = [
            m
            async for m in client.iter_own_messages(
                ch, "999", DateFilter.all_messages(), msg_filter=flt
            )
        ]
        assert [m.id for m in yielded] == ["501"]
    finally:
        await client.aclose()
        session.wipe()


def test_checkpoint_save_load_clear(tmp_path: Path) -> None:
    msg = Message("42", "200", "@alice", "999", "hello", datetime(2025, 5, 1, tzinfo=UTC))
    cp = Checkpoint(
        user_id="999",
        created_at_utc="2025-05-10T00:00:00+00:00",
        filter_summary="All",
        total_count=10,
        completed_count=9,
        remaining=[msg],
    )
    saved = save_checkpoint(cp, directory=tmp_path)
    assert saved.is_file()
    raw_text = saved.read_text(encoding="utf-8")
    assert "token" not in raw_text.lower()

    loaded = load_checkpoint("999", directory=tmp_path)
    assert loaded is not None
    assert loaded.remaining_count == 1
    assert loaded.remaining[0].id == "42"

    clear_checkpoint("999", directory=tmp_path)
    assert load_checkpoint("999", directory=tmp_path) is None


def test_export_backup_html_discord_theme_and_xss_escape(tmp_path: Path) -> None:
    user = User(id="100", username="tester", discriminator="0", global_name="Tester")
    ch = DMChannel(id="200", channel_type=1, name="Alice <script>alert(1)</script>")
    msg = Message(
        id="301",
        channel_id="200",
        channel_name=ch.name,
        author_id="100",
        content="Look at <img src=x onerror=alert(1)> https://example.com",
        timestamp=datetime(2025, 5, 2, 10, 0, tzinfo=UTC),
        attachments=("https://cdn.example/pic.png",),
        pinned=True,
    )
    paths = export_backup(
        user, [ch], [msg], DateFilter.all_messages(), BackupFormat.HTML, output_dir=tmp_path
    )
    assert len(paths) == 1
    html_text = paths[0].read_text(encoding="utf-8")
    assert "Discord DM Cleaner" in html_text
    assert "<script>alert(1)</script>" not in html_text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html_text
    assert "PINNED" in html_text
    assert "https://cdn.example/pic.png" in html_text
