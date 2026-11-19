"""Local backup exporter for matched Discord messages (JSON, TXT, Discord-themed HTML + media)."""

from __future__ import annotations

import html
import json
import re
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from .api import DateFilter, DMChannel, Message, User

DEFAULT_BACKUP_DIR: Path = Path("backups")
ALLOWED_MEDIA_HOSTS: frozenset[str] = frozenset({"cdn.discordapp.com", "media.discordapp.net"})
MAX_ATTACHMENT_BYTES: int = 50 * 1024 * 1024  # 50 MiB cap per attachment
_IMAGE_EXTS: frozenset[str] = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"})
_SAFE_NAME_RE: re.Pattern[str] = re.compile(r"[^A-Za-z0-9._-]+")
_URL_RE: re.Pattern[str] = re.compile(r"(https?://[^\s<]+)", re.IGNORECASE)


class BackupFormat(Enum):
    """Supported local backup formats."""

    NONE = "none"
    JSON = "json"
    TXT = "txt"
    HTML = "html"
    BOTH = "both"
    ALL = "all"


def _unique_base_name(target_dir: Path) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    base_name = f"dm_backup_{stamp}"
    suffix = 0
    while any((target_dir / f"{base_name}{ext}").exists() for ext in (".json", ".txt", ".html")):
        suffix += 1
        base_name = f"dm_backup_{stamp}_{suffix}"
    return base_name


def _sanitize_filename(url: str, index: int) -> str:
    parsed = urlparse(url)
    raw_name = Path(parsed.path).name or f"file_{index}"
    clean = _SAFE_NAME_RE.sub("_", raw_name).strip("._") or f"file_{index}"
    return f"{index:04d}_{clean[:80]}"


def _is_allowed_media_url(url: str) -> bool:
    """Return ``True`` only for HTTPS URLs hosted on Discord's official CDN domains."""
    parsed = urlparse(url)
    return parsed.scheme == "https" and (parsed.hostname or "").lower() in ALLOWED_MEDIA_HOSTS


async def download_attachments(
    messages: list[Message],
    media_dir: Path,
    *,
    max_bytes: int = MAX_ATTACHMENT_BYTES,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, str]:
    """Download allowed Discord CDN attachments into ``media_dir`` with a per-file size cap."""
    urls: list[str] = []
    seen: set[str] = set()
    for msg in messages:
        for url in msg.attachments:
            if url and url not in seen and _is_allowed_media_url(url):
                seen.add(url)
                urls.append(url)
    if not urls:
        return {}

    media_dir.mkdir(parents=True, exist_ok=True)
    mapping: dict[str, str] = {}
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(20.0, connect=10.0),
        follow_redirects=True,
        trust_env=False,
        transport=transport,
    ) as client:
        for idx, url in enumerate(urls, start=1):
            fname = _sanitize_filename(url, idx)
            dest = media_dir / fname
            try:
                async with client.stream("GET", url) as resp:
                    if resp.status_code != 200 or not _is_allowed_media_url(str(resp.url)):
                        continue
                    cl_header = resp.headers.get("content-length")
                    if cl_header is not None:
                        try:
                            if int(cl_header) > max_bytes:
                                continue
                        except ValueError:
                            pass
                    written = 0
                    exceeded = False
                    with dest.open("wb") as fh:
                        async for chunk in resp.aiter_bytes():
                            written += len(chunk)
                            if written > max_bytes:
                                exceeded = True
                                break
                            fh.write(chunk)
                    if exceeded or written == 0:
                        dest.unlink(missing_ok=True)
                        continue
                    mapping[url] = f"{media_dir.name}/{fname}"
            except (httpx.HTTPError, OSError):
                dest.unlink(missing_ok=True)
                continue
    return mapping


def prepare_backup_target(output_dir: Path | None = None) -> tuple[Path, str]:
    """Ensure the backup directory exists and return ``(target_dir, unique_base_name)``."""
    target_dir = output_dir or DEFAULT_BACKUP_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir, _unique_base_name(target_dir)


def export_backup(
    user: User,
    channels: list[DMChannel],
    messages: list[Message],
    date_filter: DateFilter,
    fmt: BackupFormat,
    output_dir: Path | None = None,
    *,
    base_name: str | None = None,
    media_map: dict[str, str] | None = None,
) -> list[Path]:
    """Export ``messages`` to local JSON / TXT / HTML files and return the saved paths."""
    if fmt == BackupFormat.NONE:
        return []

    target_dir = output_dir or DEFAULT_BACKUP_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    stem = base_name or _unique_base_name(target_dir)
    saved: list[Path] = []

    if fmt in (BackupFormat.JSON, BackupFormat.BOTH, BackupFormat.ALL):
        json_path = target_dir / f"{stem}.json"
        _write_json(json_path, user, channels, messages, date_filter, media_map or {})
        saved.append(json_path)

    if fmt in (BackupFormat.TXT, BackupFormat.BOTH, BackupFormat.ALL):
        txt_path = target_dir / f"{stem}.txt"
        _write_txt(txt_path, user, channels, messages, date_filter)
        saved.append(txt_path)

    if fmt in (BackupFormat.HTML, BackupFormat.ALL):
        html_path = target_dir / f"{stem}.html"
        _write_html(html_path, user, channels, messages, date_filter, media_map or {})
        saved.append(html_path)

    return saved


def _write_json(
    path: Path,
    user: User,
    channels: list[DMChannel],
    messages: list[Message],
    date_filter: DateFilter,
    media_map: dict[str, str],
) -> None:
    payload: dict[str, Any] = {
        "exported_at_utc": datetime.now(UTC).isoformat(),
        "user": {
            "id": user.id,
            "username": user.username,
            "tag": user.tag,
        },
        "date_filter": {
            "mode": date_filter.mode.value,
            "after_utc": date_filter.after_dt.isoformat() if date_filter.after_dt else None,
            "before_utc": date_filter.before_dt.isoformat() if date_filter.before_dt else None,
            "days": date_filter.days,
        },
        "channels": [
            {
                "id": ch.id,
                "name": ch.name,
                "type": ch.channel_type,
                "recipients": list(ch.recipients),
            }
            for ch in channels
        ],
        "total_messages": len(messages),
        "messages": [
            {
                "id": msg.id,
                "channel_id": msg.channel_id,
                "channel_name": msg.channel_name,
                "timestamp_utc": msg.timestamp.isoformat(),
                "content": msg.content,
                "attachments": list(msg.attachments),
                "local_attachments": [media_map[u] for u in msg.attachments if u in media_map],
                "embed_count": msg.embed_count,
                "pinned": msg.pinned,
            }
            for msg in messages
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_txt(
    path: Path,
    user: User,
    channels: list[DMChannel],
    messages: list[Message],
    date_filter: DateFilter,
) -> None:
    lines: list[str] = [
        "Discord DM Cleaner — Local Backup",
        f"Exported (UTC): {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')}",
        f"Account:        {user.tag} ({user.id})",
        f"Date Filter:    {date_filter.describe()}",
        f"Conversations:  {len(channels)}",
        f"Total Messages: {len(messages)}",
        "=" * 72,
        "",
    ]

    by_channel: dict[str, list[Message]] = {}
    for msg in messages:
        by_channel.setdefault(msg.channel_id, []).append(msg)

    for ch in channels:
        ch_msgs = by_channel.get(ch.id, [])
        if not ch_msgs:
            continue
        lines.append(f"### {ch.name} (ID: {ch.id}) — {len(ch_msgs)} message(s)")
        lines.append("-" * 72)
        for msg in reversed(ch_msgs):
            ts_str = msg.timestamp.strftime("%Y-%m-%d %H:%M:%S UTC")
            content = msg.content.replace("\r\n", "\n") if msg.content else ""
            if content:
                lines.append(f"[{ts_str}] (ID:{msg.id}) {content}")
            else:
                lines.append(f"[{ts_str}] (ID:{msg.id}) [no text]")
            for att_url in msg.attachments:
                lines.append(f"    Attachment: {att_url}")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def _linkify_escaped(text: str) -> str:
    escaped = html.escape(text)
    escaped = _URL_RE.sub(r'<a href="\1" target="_blank" rel="noopener noreferrer">\1</a>', escaped)
    return escaped.replace("\n", "<br>")


def _is_image_url(url: str) -> bool:
    ext = Path(urlparse(url).path).suffix.lower()
    return ext in _IMAGE_EXTS


def _write_html(
    path: Path,
    user: User,
    channels: list[DMChannel],
    messages: list[Message],
    date_filter: DateFilter,
    media_map: dict[str, str],
) -> None:
    by_channel: dict[str, list[Message]] = {}
    for msg in messages:
        by_channel.setdefault(msg.channel_id, []).append(msg)

    avatar_letter = html.escape((user.username[:1] or "D").upper())
    user_label = html.escape(user.tag)
    exported_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    filter_desc = html.escape(date_filter.describe())

    sections: list[str] = []
    for ch in channels:
        ch_msgs = by_channel.get(ch.id, [])
        if not ch_msgs:
            continue
        items: list[str] = []
        for msg in reversed(ch_msgs):
            ts_str = msg.timestamp.strftime("%Y-%m-%d %H:%M:%S UTC")
            body_html = (
                _linkify_escaped(msg.content)
                if msg.content
                else '<span class="empty">[no text]</span>'
            )
            pin_badge = '<span class="pin">PINNED</span>' if msg.pinned else ""
            att_blocks: list[str] = []
            for url in msg.attachments:
                href = html.escape(media_map.get(url, url))
                label = html.escape(Path(urlparse(url).path).name or "attachment")
                if _is_image_url(url):
                    att_blocks.append(
                        f'<div class="att"><a href="{href}" target="_blank" rel="noopener noreferrer">'
                        f'<img src="{href}" alt="{label}" loading="lazy"></a></div>'
                    )
                else:
                    att_blocks.append(
                        f'<div class="att"><a class="file" href="{href}" target="_blank" rel="noopener noreferrer">'
                        f"📎 {label}</a></div>"
                    )
            atts_html = "".join(att_blocks)
            items.append(
                f'<div class="msg" data-text="{html.escape(msg.content.lower())}">'
                f'<div class="avatar">{avatar_letter}</div>'
                f'<div class="body">'
                f'<div class="meta"><span class="author">{user_label}</span>'
                f'<span class="ts">{ts_str}</span>{pin_badge}</div>'
                f'<div class="text">{body_html}</div>{atts_html}'
                f"</div></div>"
            )
        sections.append(
            f'<section class="channel">'
            f"<h2># {html.escape(ch.name)} "
            f'<span class="count">{len(ch_msgs)}</span></h2>'
            f"{''.join(items)}</section>"
        )

    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Discord DM Cleaner — Archive ({user_label})</title>
<style>
:root{{--bg:#313338;--panel:#2b2d31;--dark:#1e1f22;--text:#dbdee1;--muted:#949ba4;--blurple:#5865f2;--link:#00a8fc;}}
*{{box-sizing:border-box;}}
body{{margin:0;background:var(--bg);color:var(--text);font:15px/1.45 "Segoe UI",system-ui,sans-serif;}}
header{{position:sticky;top:0;z-index:10;background:var(--dark);padding:14px 24px;border-bottom:1px solid #3f4147;display:flex;flex-wrap:wrap;gap:12px;align-items:center;justify-content:space-between;}}
header h1{{margin:0;font-size:17px;color:#fff;}}
header .sub{{color:var(--muted);font-size:13px;}}
#q{{background:var(--panel);border:1px solid #3f4147;color:#fff;padding:7px 12px;border-radius:6px;min-width:240px;outline:none;}}
#q:focus{{border-color:var(--blurple);}}
main{{max-width:980px;margin:20px auto;padding:0 16px 48px;}}
.channel{{background:var(--panel);border-radius:10px;padding:16px 18px;margin-bottom:20px;}}
.channel h2{{margin:0 0 14px;font-size:16px;color:#fff;border-bottom:1px solid #3f4147;padding-bottom:8px;display:flex;justify-content:space-between;}}
.count{{background:var(--blurple);color:#fff;border-radius:999px;padding:2px 9px;font-size:12px;}}
.msg{{display:flex;gap:12px;padding:8px 6px;border-radius:6px;}}
.msg:hover{{background:rgba(255,255,255,.03);}}
.avatar{{width:38px;height:38px;border-radius:50%;background:var(--blurple);color:#fff;font-weight:700;display:flex;align-items:center;justify-content:center;flex:0 0 38px;}}
.meta{{display:flex;align-items:baseline;gap:8px;margin-bottom:2px;}}
.author{{color:#fff;font-weight:600;}}
.ts{{color:var(--muted);font-size:12px;}}
.pin{{background:#fee75c;color:#1e1f22;font-size:10px;font-weight:700;padding:1px 5px;border-radius:4px;}}
.text{{word-break:break-word;}}
.text a{{color:var(--link);text-decoration:none;}}
.text a:hover{{text-decoration:underline;}}
.empty{{color:var(--muted);font-style:italic;}}
.att{{margin-top:6px;}}
.att img{{max-width:420px;max-height:320px;border-radius:6px;border:1px solid #3f4147;}}
.file{{display:inline-block;background:var(--dark);color:var(--link);padding:6px 10px;border-radius:6px;text-decoration:none;border:1px solid #3f4147;}}
</style>
</head>
<body>
<header>
  <div>
    <h1>Discord DM Cleaner — Backup Archive</h1>
    <div class="sub">{user_label} · {len(messages)} message(s) · {filter_desc} · {exported_at}</div>
  </div>
  <input id="q" type="search" placeholder="Search messages..." oninput="filterMsgs(this.value)">
</header>
<main>{"".join(sections)}</main>
<script>
function filterMsgs(v){{
  const q=v.trim().toLowerCase();
  document.querySelectorAll('.msg').forEach(el=>{{
    el.style.display=!q||el.dataset.text.includes(q)?'flex':'none';
  }});
}}
</script>
</body>
</html>
"""
    path.write_text(doc, encoding="utf-8")
