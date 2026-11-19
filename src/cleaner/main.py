"""Entry point: wires security, API, backup, checkpoint and UI layers into the interactive flow."""

from __future__ import annotations

import asyncio
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from rich.markup import escape as escape_markup

from . import ui
from .api import (
    APIError,
    DateFilter,
    DeleteResult,
    DiscordClient,
    DMChannel,
    Message,
    MessageFilter,
    ScanSummary,
    User,
)
from .backup import (
    BackupFormat,
    download_attachments,
    export_backup,
    prepare_backup_target,
)
from .checkpoint import Checkpoint, clear_checkpoint, load_checkpoint, save_checkpoint
from .i18n import detect_default_language, set_language, t
from .integrity import verify_integrity
from .security import (
    SignatureError,
    TokenError,
    TokenRevokedError,
    TokenSession,
    acquire_token,
    clear_clipboard,
    clipboard_has_token,
    generate_fingerprint,
)
from .utils import (
    REST_EVERY,
    GoBack,
    console,
    human_delay,
    log_error,
    log_info,
    log_success,
    log_warning,
    next_delay,
    rest_delay,
)

Fingerprint = tuple[str, str]
CHECKPOINT_SAVE_EVERY: int = 5


async def _login_and_fetch(
    session: TokenSession, fingerprint: Fingerprint
) -> tuple[User, list[DMChannel]]:
    """Validate the token via ``/users/@me`` and fetch open DM / Group DM channels."""
    async with DiscordClient(session, fingerprint) as client:
        with console.status(t("logging_in"), spinner="line"):
            user = await client.get_me()
        ui.show_logged_in(user)
        with console.status(t("fetching_dms"), spinner="line"):
            channels = await client.fetch_dm_channels()
    return user, channels


async def _scan_channels(
    session: TokenSession,
    fingerprint: Fingerprint,
    channels: list[DMChannel],
    user_id: str,
    date_filter: DateFilter,
    msg_filter: MessageFilter,
    max_messages: int | None = None,
) -> ScanSummary:
    """Scan selected channels for messages authored by ``user_id`` up to ``max_messages``."""
    summary = ScanSummary(channels=channels)
    total_channels = len(channels)

    async with DiscordClient(session, fingerprint) as client:
        with console.status("", spinner="line") as status:
            for idx, ch in enumerate(channels, start=1):
                remaining: int | None = None
                if max_messages is not None:
                    remaining = max_messages - len(summary.messages)
                    if remaining <= 0:
                        summary.per_channel_counts[ch.id] = 0
                        continue

                safe_name = escape_markup(ch.name)
                status.update(
                    t(
                        "scanning_channel",
                        name=safe_name,
                        idx=idx,
                        total=total_channels,
                        scanned=0,
                        matched=0,
                    )
                )

                def _on_progress(
                    scanned: int,
                    matched: int,
                    *,
                    _name: str = safe_name,
                    _idx: int = idx,
                ) -> None:
                    status.update(
                        t(
                            "scanning_channel",
                            name=_name,
                            idx=_idx,
                            total=total_channels,
                            scanned=scanned,
                            matched=matched,
                        )
                    )

                msgs = await client.fetch_own_messages(
                    ch,
                    user_id,
                    date_filter,
                    on_progress=_on_progress,
                    max_messages=remaining,
                    msg_filter=msg_filter,
                )
                summary.per_channel_counts[ch.id] = len(msgs)
                summary.messages.extend(msgs)

    return summary


async def _delete_all(
    session: TokenSession,
    fingerprint: Fingerprint,
    messages: list[Message],
    *,
    user_id: str,
    filter_summary: str,
    prior_completed: int = 0,
    overall_total: int | None = None,
) -> list[DeleteResult]:
    """Delete each matched message sequentially with adaptive delays & resume checkpoint."""
    total = len(messages)
    grand_total = overall_total if overall_total is not None else total
    created_at = datetime.now(UTC).isoformat()

    cp = Checkpoint(
        user_id=user_id,
        created_at_utc=created_at,
        filter_summary=filter_summary,
        total_count=grand_total,
        completed_count=prior_completed,
        remaining=list(messages),
    )
    save_checkpoint(cp)

    view = ui.DeleteProgressView(total)
    processed = 0
    finished_cleanly = False
    try:
        async with DiscordClient(session, fingerprint) as client:
            with view:
                for index, msg in enumerate(messages, start=1):
                    view.start(msg)
                    result = await client.delete_message(msg)  # 401 propagates → abort
                    view.record(result)
                    processed = index
                    if index % CHECKPOINT_SAVE_EVERY == 0 and index < total:
                        cp.completed_count = prior_completed + index
                        cp.remaining = messages[index:]
                        save_checkpoint(cp)
                    delay = next_delay(index, total)
                    if delay > 0:
                        view.waiting(delay)
                        await asyncio.sleep(delay)
        finished_cleanly = True
    finally:
        if finished_cleanly:
            clear_checkpoint(user_id)
        else:
            remaining = messages[processed:]
            if remaining:
                cp.completed_count = prior_completed + processed
                cp.remaining = remaining
                save_checkpoint(cp)
                log_info(t("checkpoint_saved", rem=len(remaining)))
            else:
                clear_checkpoint(user_id)

    return view.results


async def _stream_delete_all(
    session: TokenSession,
    fingerprint: Fingerprint,
    channels: list[DMChannel],
    user_id: str,
    date_filter: DateFilter,
    msg_filter: MessageFilter,
    max_messages: int | None,
    collected: list[Message] | None = None,
) -> list[DeleteResult]:
    """Live Stream (Scan-while-Delete): delete matching messages as each 100-msg page arrives."""
    view = ui.DeleteProgressView(max_messages)
    deleted_count = 0

    async with DiscordClient(session, fingerprint) as client:
        with view:
            for ch in channels:
                remaining: int | None = None
                if max_messages is not None:
                    remaining = max_messages - deleted_count
                    if remaining <= 0:
                        break
                async for msg in client.iter_own_messages(
                    ch,
                    user_id,
                    date_filter,
                    msg_filter=msg_filter,
                    max_messages=remaining,
                ):
                    if deleted_count > 0:
                        delay = human_delay()
                        if deleted_count % REST_EVERY == 0:
                            delay += rest_delay()
                        view.waiting(delay)
                        await asyncio.sleep(delay)
                    if collected is not None:
                        collected.append(msg)
                    view.start(msg)
                    result = await client.delete_message(msg)
                    view.record(result)
                    deleted_count += 1
                    if max_messages is not None and deleted_count >= max_messages:
                        break

    return view.results


def _do_backup(
    user: User,
    summary: ScanSummary,
    date_filter: DateFilter,
    fmt: BackupFormat,
    *,
    prompt_media: bool = True,
) -> list[Path]:
    """Run local backup export (with optional attachment download) and print feedback."""
    if fmt == BackupFormat.NONE:
        return []

    target_dir, base_name = prepare_backup_target()
    media_map: dict[str, str] = {}

    unique_atts = {url for msg in summary.messages for url in msg.attachments if url}
    if (
        prompt_media
        and unique_atts
        and fmt in (BackupFormat.HTML, BackupFormat.ALL, BackupFormat.JSON)
    ):
        try:
            if ui.ask_download_media(len(unique_atts)):
                media_dir = target_dir / f"{base_name}_media"
                with console.status(t("backup_media_downloading"), spinner="line"):
                    media_map = asyncio.run(download_attachments(summary.messages, media_dir))
                log_success(
                    t(
                        "backup_media_saved",
                        ok=len(media_map),
                        total=len(unique_atts),
                        dir=str(media_dir),
                    )
                )
        except GoBack:
            pass

    try:
        paths = export_backup(
            user,
            summary.channels,
            summary.messages,
            date_filter,
            fmt,
            output_dir=target_dir,
            base_name=base_name,
            media_map=media_map,
        )
    except OSError as exc:
        log_error(t("backup_error", err=str(exc)))
        return []
    for path in paths:
        log_success(t("backup_saved", path=str(path)))
    return paths


def _try_resume(
    session: TokenSession, fingerprint: Fingerprint, user: User, clients: list[str]
) -> bool:
    """Offer to resume an unfinished checkpoint. Returns ``True`` if resumed & finished."""
    cp = load_checkpoint(user.id)
    if cp is None:
        return False

    if not ui.ask_resume_checkpoint(cp):
        clear_checkpoint(user.id)
        return False

    # Build a synthetic summary for confirmation
    seen_ch: dict[str, DMChannel] = {}
    counts: dict[str, int] = {}
    for m in cp.remaining:
        if m.channel_id not in seen_ch:
            seen_ch[m.channel_id] = DMChannel(
                id=m.channel_id, channel_type=1, name=m.channel_name or m.channel_id
            )
        counts[m.channel_id] = counts.get(m.channel_id, 0) + 1

    summary = ScanSummary(
        channels=list(seen_ch.values()),
        messages=cp.remaining,
        per_channel_counts=counts,
    )
    if not ui.confirm_delete(summary, DateFilter.all_messages(), [], clients):
        log_info(t("going_back"))
        return False

    started = time.monotonic()
    results = asyncio.run(
        _delete_all(
            session,
            fingerprint,
            cp.remaining,
            user_id=user.id,
            filter_summary=cp.filter_summary,
            prior_completed=cp.completed_count,
            overall_total=cp.total_count,
        )
    )
    elapsed = time.monotonic() - started
    ui.show_summary(results, [], elapsed)
    return True


def _configure_filters() -> tuple[DateFilter, MessageFilter, int | None, str]:
    """Run the 4-step filter & mode configuration with full Backspace/Esc navigation."""
    step = 0
    date_filter = DateFilter.all_messages()
    msg_filter = MessageFilter()
    max_messages: int | None = None

    while True:
        if step == 0:
            date_filter = ui.select_date_filter()
            step = 1
        elif step == 1:
            try:
                msg_filter = ui.select_message_filter()
                step = 2
            except GoBack:
                step = 0
        elif step == 2:
            try:
                max_messages = ui.select_message_limit()
                step = 3
            except GoBack:
                step = 1
        else:
            try:
                mode = ui.select_execution_mode()
                return date_filter, msg_filter, max_messages, mode
            except GoBack:
                step = 2


def _run(session: TokenSession, clients: list[str]) -> int:
    """Run the authenticated DM cleaner workflow and return the process exit code."""
    fingerprint = generate_fingerprint()
    user, open_channels = asyncio.run(_login_and_fetch(session, fingerprint))

    if _try_resume(session, fingerprint, user, clients):
        return 0

    if not open_channels:
        log_info(t("no_open_dms"))
        return 0

    ui.show_dm_count(open_channels)

    preselected: set[str] = set()
    while True:
        selected_channels = ui.select_channels(open_channels, preselected)
        if not selected_channels:
            log_warning(t("sel_warning"))
            continue
        preselected = {c.id for c in selected_channels}

        try:
            date_filter, msg_filter, max_messages, mode = _configure_filters()
        except GoBack:
            continue

        # Live Stream (Scan-while-Delete) Mode
        if mode == "stream":
            try:
                stream_fmt = ui.select_backup_format(allow_none=True)
            except GoBack:
                continue

            if ui.confirm_stream_delete(
                selected_channels, date_filter, msg_filter, max_messages, clients
            ):
                streamed_msgs: list[Message] = []
                backup_paths_stream: list[Path] = []
                started = time.monotonic()
                try:
                    results = asyncio.run(
                        _stream_delete_all(
                            session,
                            fingerprint,
                            selected_channels,
                            user.id,
                            date_filter,
                            msg_filter,
                            max_messages,
                            collected=streamed_msgs,
                        )
                    )
                finally:
                    if stream_fmt != BackupFormat.NONE and streamed_msgs:
                        stream_summary = ScanSummary(
                            channels=selected_channels, messages=streamed_msgs
                        )
                        backup_paths_stream = _do_backup(
                            user, stream_summary, date_filter, stream_fmt, prompt_media=False
                        )
                elapsed = time.monotonic() - started
                ui.show_summary(results, backup_paths_stream, elapsed)
                return 0
            log_info(t("going_back"))
            continue

        # Standard Mode: Scan → Preview → Optional Backup → Confirm → Delete (with Checkpoint)
        summary = asyncio.run(
            _scan_channels(
                session,
                fingerprint,
                selected_channels,
                user.id,
                date_filter,
                msg_filter,
                max_messages,
            )
        )
        console.print(
            t("scan_done", matched=len(summary.messages), channels=len(selected_channels))
        )

        if not summary.messages:
            log_warning(t("no_messages_found"))
            continue

        backup_paths: list[Path] = []
        while True:
            try:
                action = ui.show_preview_and_choose_action(
                    summary, date_filter, max_messages, msg_filter
                )
            except GoBack:
                action = "back"

            if action == "cancel":
                log_info(t("dry_run_exited"))
                return 0

            if action == "back":
                break

            if action == "backup_only":
                try:
                    fmt = ui.select_backup_format(allow_none=False)
                except GoBack:
                    continue
                _do_backup(user, summary, date_filter, fmt)
                log_info(t("dry_run_exited"))
                return 0

            try:
                fmt = ui.select_backup_format(allow_none=True)
            except GoBack:
                continue

            if fmt != BackupFormat.NONE:
                backup_paths = _do_backup(user, summary, date_filter, fmt)

            if ui.confirm_delete(summary, date_filter, backup_paths, clients, msg_filter):
                started = time.monotonic()
                results = asyncio.run(
                    _delete_all(
                        session,
                        fingerprint,
                        summary.messages,
                        user_id=user.id,
                        filter_summary=f"{date_filter.describe()} | {msg_filter.describe()}",
                    )
                )
                elapsed = time.monotonic() - started
                ui.show_summary(results, backup_paths, elapsed)
                return 0

            log_info(t("going_back"))


def main() -> None:
    """CLI entry point (``ddc``)."""
    verify_integrity()

    session: TokenSession | None = None
    exit_code = 0
    try:
        set_language(detect_default_language())
        detected = ui.detect_clients()
        clients: list[str] = []
        step = 0
        while True:
            if step == 0:
                ui.clear_and_banner()
                ui.select_language()
                step = 1
            elif step == 1:
                ui.clear_and_banner()
                try:
                    clients = ui.select_clients(detected)
                    step = 2
                except GoBack:
                    step = 0
            elif step == 2:
                ui.clear_and_banner()
                while True:
                    try:
                        session = TokenSession(acquire_token())
                        step = 3
                        break
                    except GoBack:
                        step = 1 if detected else 0
                        break
                    except TokenError as exc:
                        log_error(str(exc))
            else:
                assert session is not None
                try:
                    exit_code = _run(session, clients)
                    break
                except GoBack:
                    session.wipe()
                    session = None
                    step = 2
    except KeyboardInterrupt:
        console.print(f"\n[yellow]{t('interrupted')}[/yellow]")
        exit_code = 130
    except SignatureError as exc:
        log_error(str(exc))
        exit_code = 1
    except TokenError as exc:
        log_error(str(exc))
        exit_code = 2
    except TokenRevokedError:
        log_error(t("err_401"))
        exit_code = 1
    except APIError as exc:
        log_error(str(exc))
        exit_code = 1
    except Exception as exc:
        log_error(t("err_unexpected", name=type(exc).__name__, msg=exc))
        exit_code = 1
    finally:
        if session is not None:
            if session.active and sys.stdin.isatty() and clipboard_has_token(session.token):
                try:
                    if ui.ask_clear_clipboard() and clear_clipboard():
                        console.print(f"[dim]{t('clipboard_cleared')}[/dim]")
                except KeyboardInterrupt:
                    pass
            session.wipe()
            session = None
            console.print(f"[dim]{t('token_wiped')}[/dim]")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
