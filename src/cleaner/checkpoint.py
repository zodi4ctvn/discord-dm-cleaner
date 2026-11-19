"""Resume checkpoint persistence for interrupted deletion runs (never stores tokens)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .api import Message

DEFAULT_CHECKPOINT_DIR: Path = Path("checkpoints")


@dataclass(slots=True)
class Checkpoint:
    """Serialized state of an unfinished deletion session."""

    user_id: str
    created_at_utc: str
    filter_summary: str
    total_count: int
    completed_count: int
    remaining: list[Message]

    @property
    def remaining_count(self) -> int:
        return len(self.remaining)


def checkpoint_path(user_id: str, directory: Path | None = None) -> Path:
    """Return the checkpoint JSON path for ``user_id``."""
    base = directory or DEFAULT_CHECKPOINT_DIR
    safe_id = "".join(ch for ch in user_id if ch.isdigit()) or "unknown"
    return base / f"resume_{safe_id}.json"


def save_checkpoint(cp: Checkpoint, directory: Path | None = None) -> Path:
    """Atomically write ``cp`` to disk (excluding any token or secret data)."""
    path = checkpoint_path(cp.user_id, directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "user_id": cp.user_id,
        "created_at_utc": cp.created_at_utc,
        "filter_summary": cp.filter_summary,
        "total_count": cp.total_count,
        "completed_count": cp.completed_count,
        "remaining": [
            {
                "id": m.id,
                "channel_id": m.channel_id,
                "channel_name": m.channel_name,
                "author_id": m.author_id,
                "content": m.content,
                "timestamp": m.timestamp.isoformat(),
                "attachments": list(m.attachments),
                "embed_count": m.embed_count,
                "message_type": m.message_type,
                "pinned": m.pinned,
            }
            for m in cp.remaining
        ],
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def load_checkpoint(user_id: str, directory: Path | None = None) -> Checkpoint | None:
    """Load an existing checkpoint for ``user_id``, or return ``None`` if absent/corrupt."""
    path = checkpoint_path(user_id, directory)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or str(data.get("user_id")) != user_id:
            return None
        raw_remaining = data.get("remaining")
        if not isinstance(raw_remaining, list) or not raw_remaining:
            return None
        remaining: list[Message] = []
        for item in raw_remaining:
            if not isinstance(item, dict):
                continue
            ts = datetime.fromisoformat(str(item["timestamp"]))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            remaining.append(
                Message(
                    id=str(item["id"]),
                    channel_id=str(item["channel_id"]),
                    channel_name=str(item.get("channel_name", "")),
                    author_id=str(item.get("author_id", user_id)),
                    content=str(item.get("content", "")),
                    timestamp=ts.astimezone(UTC),
                    attachments=tuple(str(a) for a in (item.get("attachments") or [])),
                    embed_count=int(item.get("embed_count", 0)),
                    message_type=int(item.get("message_type", 0)),
                    pinned=bool(item.get("pinned", False)),
                )
            )
        if not remaining:
            return None
        return Checkpoint(
            user_id=user_id,
            created_at_utc=str(data.get("created_at_utc", "")),
            filter_summary=str(data.get("filter_summary", "")),
            total_count=int(data.get("total_count", len(remaining))),
            completed_count=int(data.get("completed_count", 0)),
            remaining=remaining,
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def clear_checkpoint(user_id: str, directory: Path | None = None) -> None:
    """Delete any saved checkpoint for ``user_id``."""
    path = checkpoint_path(user_id, directory)
    if path.exists():
        path.unlink(missing_ok=True)
