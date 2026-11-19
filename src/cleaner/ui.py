"""All terminal UI: banner, client detection, DM picker, filters, preview, progress, summary."""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import TracebackType

import questionary
from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.data_structures import Point
from prompt_toolkit.filters import Condition, has_focus
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent, merge_key_bindings
from prompt_toolkit.key_binding.key_processor import KeyPress
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import HSplit, Layout, VSplit, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import Frame
from rich import box
from rich.align import Align
from rich.live import Live
from rich.markup import escape as escape_markup
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeRemainingColumn,
)
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text

from . import __version__
from .api import (
    DateFilter,
    DateFilterMode,
    DeleteResult,
    DeleteStatus,
    DMChannel,
    Message,
    MessageFilter,
    MessageKind,
    ScanSummary,
    User,
)
from .backup import BackupFormat
from .checkpoint import Checkpoint
from .i18n import detect_default_language, set_language, t
from .security import _read_clipboard
from .utils import (
    GoBack,
    console,
    estimate_seconds,
    format_duration,
    log_error,
    parse_date_input,
)

# --------------------------------------------------------------------------- #
# Step 0 — Banner (ANSI Shadow, Consolas-safe)
# --------------------------------------------------------------------------- #
_BANNER_LINES: tuple[str, ...] = (
    "███████╗ ██████╗ ██████╗ ██╗██╗  ██╗ ██████╗████████╗██╗   ██╗███╗   ██╗",
    "╚══███╔╝██╔═══██╗██╔══██╗██║██║  ██║██╔════╝╚══██╔══╝██║   ██║████╗  ██║",
    "  ███╔╝ ██║   ██║██║  ██║██║███████║██║        ██║   ██║   ██║██╔██╗ ██║",
    " ███╔╝  ██║   ██║██║  ██║██║╚════██║██║        ██║   ╚██╗ ██╔╝██║╚██╗██║",
    "███████╗╚██████╔╝██████╔╝██║     ██║╚██████╗   ██║    ╚████╔╝ ██║ ╚████║",
    "╚══════╝ ╚═════╝ ╚═════╝ ╚═╝     ╚═╝ ╚═════╝   ╚═╝     ╚═══╝  ╚═╝  ╚═══╝",
)

_BANNER_GRADIENT: tuple[str, ...] = (
    "#7b2cbf",
    "#9d4edd",
    "#b5179e",
    "#d90368",
    "#e040fb",
    "#ff00ff",
)


def show_banner() -> None:
    """Render the centered ANSI Shadow ``zodi4ctvn`` banner with neon gradient."""
    art = Text()
    for index, (line, color) in enumerate(zip(_BANNER_LINES, _BANNER_GRADIENT, strict=True)):
        art.append(line, style=f"bold {color}")
        if index < len(_BANNER_LINES) - 1:
            art.append("\n")

    subtitle = Text()
    subtitle.append("─────────  ", style="dim magenta")
    subtitle.append("Discord DM Cleaner", style="bold bright_white")
    subtitle.append("  ·  ", style="dim")
    subtitle.append(t("version", v=__version__), style="bold bright_cyan")
    subtitle.append("  ─────────", style="dim magenta")

    console.print()
    console.print(Align.center(art))
    console.print(Align.center(subtitle))
    console.print()


def clear_and_banner() -> None:
    """Clear the terminal buffer completely and redraw the banner."""
    if sys.platform == "win32" and sys.stdout.isatty():
        os.system("cls")
    else:
        console.clear()
    show_banner()


#: Single-select style: only the currently highlighted (cursor) option is green.
_SELECT_STYLE = questionary.Style(
    [
        ("qmark", "fg:ansimagenta bold"),
        ("question", "bold"),
        ("instruction", "fg:ansibrightblack"),
        ("pointer", "fg:ansigreen bold"),
        ("highlighted", "fg:ansigreen bold noreverse"),
        ("selected", "fg:ansibrightblack noreverse"),
        ("text", "fg:ansibrightblack noreverse"),
        ("answer", "fg:ansigreen bold"),
    ]
)


def _attach_nav_keys(question: questionary.Question, *, allow_back: bool = False) -> None:
    """Add W/S navigation (for keyboards without arrow keys) and optional Backspace/Esc."""
    extra = KeyBindings()

    @extra.add("w")
    @extra.add("W")
    def _up(event: KeyPressEvent) -> None:
        event.app.key_processor.feed(KeyPress(Keys.Up), first=True)

    @extra.add("s")
    @extra.add("S")
    def _down(event: KeyPressEvent) -> None:
        event.app.key_processor.feed(KeyPress(Keys.Down), first=True)

    if allow_back:

        @extra.add("backspace")
        @extra.add("escape", eager=True)
        def _go_back(event: KeyPressEvent) -> None:
            event.app.exit(exception=GoBack())

    existing = question.application.key_bindings
    question.application.key_bindings = (
        merge_key_bindings([extra, existing]) if existing is not None else extra
    )


_TEXT_PROMPT_STYLE = questionary.Style(
    [
        ("qmark", "fg:ansimagenta bold"),
        ("question", "bold"),
        ("answer", "fg:ansigreen"),
    ]
)


def _prompt_text_with_back(message: str) -> str:
    """Read text with ``Ctrl+V`` paste and ``Backspace`` (when empty) / ``Esc`` to go back."""
    if not sys.stdin.isatty():
        return Prompt.ask(
            f"[bold bright_magenta]?[/bold bright_magenta] [bold]{message}[/bold]",
            default="",
            show_default=False,
            console=console,
        ).strip()

    question = questionary.text(message, style=_TEXT_PROMPT_STYLE)
    extra = KeyBindings()
    buf_empty = Condition(lambda: len(question.application.current_buffer.text) == 0)

    @extra.add("c-v")
    def _paste(event: KeyPressEvent) -> None:
        text = _read_clipboard().strip()
        if text:
            event.current_buffer.insert_text(text)

    @extra.add("backspace", filter=buf_empty)
    @extra.add("escape", eager=True)
    def _go_back(event: KeyPressEvent) -> None:
        event.app.exit(exception=GoBack())

    existing = question.application.key_bindings
    question.application.key_bindings = (
        merge_key_bindings([extra, existing]) if existing is not None else extra
    )
    value: str = question.unsafe_ask()
    return value.strip()


def select_language() -> str:
    """Ask the user to pick Turkish or English and activate the chosen language."""
    tr_choice = questionary.Choice(title="Türkçe  (TR)", value="tr")
    en_choice = questionary.Choice(title="English (EN)", value="en")
    choices = (
        [tr_choice, en_choice] if detect_default_language() == "tr" else [en_choice, tr_choice]
    )
    question = questionary.select(
        t("lang_prompt"),
        choices=choices,
        instruction="(↑↓ / W·S · Enter)",
        style=_SELECT_STYLE,
    )
    _attach_nav_keys(question, allow_back=False)
    selected: str = question.unsafe_ask()
    set_language(selected)
    return selected


# --------------------------------------------------------------------------- #
# Discord client detection (cosmetic / informational only)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class DetectedClient:
    """An installed Discord desktop client."""

    key: str
    label: str
    location: str


_CLIENT_SPECS: tuple[tuple[str, str, str, str, str, str], ...] = (
    ("Discord", "Stable", "Discord.app", "discord", "discord", "com.discordapp.Discord"),
    (
        "DiscordCanary",
        "Canary",
        "Discord Canary.app",
        "discord-canary",
        "discordcanary",
        "com.discordapp.DiscordCanary",
    ),
    (
        "DiscordPTB",
        "PTB",
        "Discord PTB.app",
        "discord-ptb",
        "discordptb",
        "com.discordapp.DiscordPTB",
    ),
)


def _detect_windows(key: str) -> str | None:
    if sys.platform == "win32":
        import winreg

        subkey = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{key}"
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, subkey) as handle:
                    for value_name in ("InstallLocation", "DisplayIcon", "UninstallString"):
                        try:
                            value, _ = winreg.QueryValueEx(handle, value_name)
                        except OSError:
                            continue
                        if value:
                            return str(value).strip('"')
                    return f"registry:{subkey}"
            except OSError:
                continue
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidate = Path(local_app_data) / key
        if (candidate / "Update.exe").exists() or candidate.is_dir():
            return str(candidate)
    return None


def _detect_macos(bundle: str) -> str | None:
    for root in (Path("/Applications"), Path.home() / "Applications"):
        candidate = root / bundle
        if candidate.exists():
            return str(candidate)
    return None


def _detect_linux(binary: str, config_dir: str, flatpak_id: str) -> str | None:
    found = shutil.which(binary)
    if found:
        return found
    candidates = [
        Path("/opt") / binary,
        Path("/usr/share") / binary,
        Path("/usr/lib") / binary,
        Path("/var/lib/flatpak/app") / flatpak_id,
        Path.home() / ".local/share/flatpak/app" / flatpak_id,
        Path("/snap") / binary,
        Path.home() / ".config" / config_dir,
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def detect_clients() -> list[DetectedClient]:
    """Detect installed Discord Stable / Canary / PTB clients on the current OS."""
    detected: list[DetectedClient] = []
    for key, label, bundle, binary, config_dir, flatpak_id in _CLIENT_SPECS:
        if sys.platform == "win32":
            location = _detect_windows(key)
        elif sys.platform == "darwin":
            location = _detect_macos(bundle)
        else:
            location = _detect_linux(binary, config_dir, flatpak_id)
        if location:
            detected.append(DetectedClient(key=key, label=label, location=location))
    return detected


def select_clients(detected: list[DetectedClient]) -> list[str]:
    """Let the user pick which detected client profile to target (informational)."""
    if not detected:
        console.print(f"[yellow]{t('no_client')}[/yellow]")
        return []

    choices: list[questionary.Choice] = [
        questionary.Choice(title=f"{c.label}  ({c.location})", value=[c.label]) for c in detected
    ]
    question = questionary.select(
        t("client_prompt"),
        choices=choices,
        instruction=t("client_instruction"),
        style=_SELECT_STYLE,
    )
    _attach_nav_keys(question, allow_back=True)
    selected: list[str] = question.unsafe_ask()
    return selected


def describe_clients(labels: list[str]) -> str:
    """Return a human-readable client description."""
    return " + ".join(labels) if labels else t("token_only")


# --------------------------------------------------------------------------- #
# Login & fetch feedback
# --------------------------------------------------------------------------- #
def show_logged_in(user: User) -> None:
    """Print the ``+ Logged in as ...`` line."""
    line = Text("+ ", style="bold green")
    line.append(t("logged_in"), style="green")
    line.append(user.tag, style="bold bright_white")
    line.append(f"  (ID: {user.id})", style="dim cyan")
    console.print(line)


def show_dm_count(channels: list[DMChannel]) -> None:
    """Print how many open 1-1 DMs and Group DMs were found."""
    dm_count = sum(1 for c in channels if c.is_dm)
    group_count = sum(1 for c in channels if c.is_group)
    console.print(t("found_dms", total=len(channels), dm=dm_count, group=group_count))


# --------------------------------------------------------------------------- #
# Two-panel DM / Group DM selector TUI
# --------------------------------------------------------------------------- #
_SELECTOR_STYLE = Style.from_dict(
    {
        "title": "bg:#5865f2 fg:#ffffff bold",
        "cursor": "bg:#2b2d31 fg:#00d1ff bold",
        "checked": "fg:#57f287 bold",
        "unchecked": "fg:#6d6f78",
        "dm": "fg:#00d1ff bold",
        "group": "fg:#e040fb bold",
        "muted": "fg:#80848e",
        "stat": "fg:#ffffff bold",
        "toolbar": "bg:#1e1f22 fg:#b5bac1",
        "key": "bg:#1e1f22 fg:#00d1ff bold",
        "warning": "bg:#fee75c fg:#1e1f22 bold",
        "frame.border": "fg:#5865f2",
        "frame.label": "fg:#eb459e bold",
    }
)


class ChannelSelector:
    """Full-screen two-panel DM picker: checkbox list on the left, live search on the right."""

    def __init__(self, channels: list[DMChannel], preselected: set[str] | None = None) -> None:
        self.channels = list(channels)
        valid_ids = {c.id for c in self.channels}
        self.selected: set[str] = {cid for cid in (preselected or set()) if cid in valid_ids}
        self.cursor = 0
        self.warning: str = ""
        self.search = Buffer(multiline=False, on_text_changed=self._on_search_changed)
        self.list_window = Window(
            FormattedTextControl(
                self._list_fragments,
                focusable=True,
                show_cursor=False,
                get_cursor_position=lambda: Point(x=0, y=self.cursor),
            ),
            wrap_lines=False,
        )
        self.app: Application[list[DMChannel]] = Application(
            layout=Layout(self._build_root(), focused_element=self.list_window),
            key_bindings=self._build_keys(),
            style=_SELECTOR_STYLE,
            full_screen=True,
            mouse_support=False,
        )

    def visible(self) -> list[DMChannel]:
        query = self.search.text.strip().casefold()
        if not query:
            return self.channels
        return [
            c
            for c in self.channels
            if query in c.name.casefold()
            or query in c.id
            or any(query in r.casefold() for r in c.recipients)
        ]

    def _on_search_changed(self, _: Buffer) -> None:
        self.cursor = 0

    def _clamp(self, count: int) -> None:
        self.cursor = max(0, min(self.cursor, count - 1)) if count else 0

    def _list_fragments(self) -> StyleAndTextTuples:
        items = self.visible()
        self._clamp(len(items))
        if not items:
            return [("class:muted", f"{t('sel_no_match')}\n")]
        out: StyleAndTextTuples = []
        dm_badge = t("dm_badge")
        group_badge = t("group_badge")

        for index, ch in enumerate(items):
            on_cursor = index == self.cursor
            prefix = "class:cursor " if on_cursor else ""
            pointer = "» " if on_cursor else "  "
            if ch.id in self.selected:
                out.append((prefix + "class:checked", f"{pointer}● "))
            else:
                out.append((prefix + "class:unchecked", f"{pointer}○ "))

            if ch.is_group:
                out.append((prefix + "class:group", f"{group_badge} "))
            else:
                out.append((prefix + "class:dm", f"{dm_badge} "))

            out.append((prefix.strip(), ch.name))
            out.append(("", "\n"))
        return out

    def _stats_fragments(self) -> StyleAndTextTuples:
        items = self.visible()
        dm_count = sum(1 for c in self.channels if c.is_dm)
        group_count = sum(1 for c in self.channels if c.is_group)
        out: StyleAndTextTuples = [
            ("", t("stat_matching")),
            ("class:stat", f"{len(items)} / {len(self.channels)}\n"),
            ("", t("stat_selected")),
            ("class:checked", f"{len(self.selected)}\n"),
            ("", t("stat_dms")),
            ("class:dm", f"{dm_count}\n"),
            ("", t("stat_groups")),
            ("class:group", f"{group_count}\n\n"),
        ]
        names = [c.name for c in self.channels if c.id in self.selected]
        if names:
            out.append(("class:stat", f"{t('stat_will_clean')}\n"))
            for name in names[:15]:
                out.append(("", f"  • {name}\n"))
            if len(names) > 15:
                out.append(("class:muted", f"{t('stat_more', n=len(names) - 15)}\n"))
        else:
            out.append(("class:muted", f"{t('stat_nothing')}\n"))
        return out

    def _toolbar_fragments(self) -> StyleAndTextTuples:
        if self.warning:
            return [("class:warning", f" {self.warning}")]
        pairs = (
            (t("key_space"), t("key_toggle")),
            ("A", t("key_select_all")),
            ("N", t("key_deselect_all")),
            ("/", t("key_search")),
            ("↑↓/WS", t("key_move")),
            ("Enter", t("key_confirm")),
            ("Backspace", t("key_back")),
            ("Ctrl+C", t("key_quit")),
        )
        out: StyleAndTextTuples = []
        for key, label in pairs:
            out.append(("class:key", f" {key}"))
            out.append(("class:toolbar", f" = {label}  "))
        return out

    def _build_root(self) -> HSplit:
        title = Window(
            FormattedTextControl([("class:title", t("sel_title"))]),
            height=1,
        )
        left = Frame(self.list_window, title=t("frame_channels"))
        right = HSplit(
            [
                Frame(Window(BufferControl(self.search), height=1), title=t("frame_search")),
                Frame(
                    Window(FormattedTextControl(self._stats_fragments), wrap_lines=True),
                    title=t("frame_selection"),
                ),
            ]
        )
        body = VSplit(
            [
                HSplit([left], width=Dimension(weight=3)),
                HSplit([right], width=Dimension(weight=2)),
            ]
        )
        toolbar = Window(
            FormattedTextControl(self._toolbar_fragments), height=1, style="class:toolbar"
        )
        return HSplit([title, body, toolbar])

    def _build_keys(self) -> KeyBindings:
        kb = KeyBindings()
        in_list = has_focus(self.list_window)
        in_search = has_focus(self.search)

        @kb.add("c-c")
        def _quit(event: KeyPressEvent) -> None:
            event.app.exit(exception=KeyboardInterrupt())

        @kb.add("backspace", filter=in_list)
        @kb.add("escape", filter=in_list, eager=True)
        def _go_back(event: KeyPressEvent) -> None:
            event.app.exit(exception=GoBack())

        @kb.add("up", filter=in_list)
        @kb.add("w", filter=in_list)
        @kb.add("W", filter=in_list)
        @kb.add("k", filter=in_list)
        def _up(event: KeyPressEvent) -> None:
            self.cursor = max(0, self.cursor - 1)

        @kb.add("down", filter=in_list)
        @kb.add("s", filter=in_list)
        @kb.add("S", filter=in_list)
        @kb.add("j", filter=in_list)
        def _down(event: KeyPressEvent) -> None:
            self.cursor = min(len(self.visible()) - 1, self.cursor + 1)
            self._clamp(len(self.visible()))

        @kb.add("pageup", filter=in_list)
        def _page_up(event: KeyPressEvent) -> None:
            self.cursor = max(0, self.cursor - 10)

        @kb.add("pagedown", filter=in_list)
        def _page_down(event: KeyPressEvent) -> None:
            self.cursor = min(len(self.visible()) - 1, self.cursor + 10)
            self._clamp(len(self.visible()))

        @kb.add("home", filter=in_list)
        def _home(event: KeyPressEvent) -> None:
            self.cursor = 0

        @kb.add("end", filter=in_list)
        def _end(event: KeyPressEvent) -> None:
            self.cursor = max(0, len(self.visible()) - 1)

        @kb.add("space", filter=in_list)
        def _toggle(event: KeyPressEvent) -> None:
            items = self.visible()
            if not items:
                return
            ch = items[self.cursor]
            self.warning = ""
            self.selected.symmetric_difference_update({ch.id})

        @kb.add("a", filter=in_list)
        @kb.add("A", filter=in_list)
        def _select_all(event: KeyPressEvent) -> None:
            self.warning = ""
            self.selected.update(c.id for c in self.visible())

        @kb.add("n", filter=in_list)
        @kb.add("N", filter=in_list)
        def _deselect_all(event: KeyPressEvent) -> None:
            self.selected.difference_update(c.id for c in self.visible())

        @kb.add("/", filter=in_list)
        @kb.add("tab", filter=in_list)
        def _focus_search(event: KeyPressEvent) -> None:
            event.app.layout.focus(self.search)

        @kb.add("enter", filter=in_search)
        @kb.add("escape", filter=in_search, eager=True)
        @kb.add("down", filter=in_search)
        @kb.add("tab", filter=in_search)
        def _focus_list(event: KeyPressEvent) -> None:
            event.app.layout.focus(self.list_window)

        @kb.add("enter", filter=in_list)
        def _confirm(event: KeyPressEvent) -> None:
            if not self.selected:
                self.warning = t("sel_warning")
                return
            event.app.exit(result=[c for c in self.channels if c.id in self.selected])

        return kb

    def run(self) -> list[DMChannel]:
        return self.app.run()


def select_channels(
    channels: list[DMChannel], preselected: set[str] | None = None
) -> list[DMChannel]:
    """Show the two-panel DM picker and return the selected conversations."""
    return ChannelSelector(channels, preselected).run()


# --------------------------------------------------------------------------- #
# Date filter menu
# --------------------------------------------------------------------------- #
def _prompt_single_date(prompt_text: str, *, end_of_day: bool = False) -> datetime:
    """Prompt until a valid date is entered, or raise :class:`GoBack` on empty / Backspace."""
    while True:
        raw = _prompt_text_with_back(prompt_text)
        if not raw:
            raise GoBack()
        parsed = parse_date_input(raw, end_of_day=end_of_day)
        if parsed is None:
            log_error(t("filter_date_invalid"))
            continue
        return parsed


def select_date_filter() -> DateFilter:
    """Ask the user to pick a date range filter for their messages."""
    while True:
        choices = [
            questionary.Choice(title=t("filter_all"), value=DateFilterMode.ALL),
            questionary.Choice(title=t("filter_last_days"), value=DateFilterMode.LAST_DAYS),
            questionary.Choice(title=t("filter_before"), value=DateFilterMode.BEFORE),
            questionary.Choice(title=t("filter_after"), value=DateFilterMode.AFTER),
            questionary.Choice(title=t("filter_between"), value=DateFilterMode.BETWEEN),
        ]
        question = questionary.select(
            t("filter_prompt"),
            choices=choices,
            instruction=t("client_instruction"),
            style=_SELECT_STYLE,
        )
        _attach_nav_keys(question, allow_back=True)
        mode: DateFilterMode = question.unsafe_ask()

        if mode == DateFilterMode.ALL:
            return DateFilter.all_messages()

        try:
            if mode == DateFilterMode.LAST_DAYS:
                while True:
                    raw = _prompt_text_with_back(t("filter_days_prompt"))
                    if not raw:
                        raise GoBack()
                    if not raw.isdigit() or int(raw) <= 0:
                        log_error(t("filter_days_invalid"))
                        continue
                    return DateFilter.last_days(int(raw))

            if mode == DateFilterMode.BEFORE:
                dt = _prompt_single_date(t("filter_date_before_prompt"), end_of_day=False)
                return DateFilter.before(dt)

            if mode == DateFilterMode.AFTER:
                dt = _prompt_single_date(t("filter_date_after_prompt"), end_of_day=False)
                return DateFilter.after(dt)

            if mode == DateFilterMode.BETWEEN:
                while True:
                    start_dt = _prompt_single_date(t("filter_date_start_prompt"), end_of_day=False)
                    end_dt = _prompt_single_date(t("filter_date_end_prompt"), end_of_day=True)
                    if start_dt > end_dt:
                        log_error(t("filter_date_order_err"))
                        continue
                    return DateFilter.between(start_dt, end_dt)
        except GoBack:
            # Return to the date filter mode menu instead of jumping all the way out.
            continue


# --------------------------------------------------------------------------- #
# Resume checkpoint prompt
# --------------------------------------------------------------------------- #
def ask_resume_checkpoint(cp: Checkpoint) -> bool:
    """Inform the user of an unfinished session and ask whether to resume."""
    console.print()
    console.print(
        f"[yellow]![/yellow] "
        f"{t('resume_found', when=cp.created_at_utc[:19].replace('T', ' '), rem=cp.remaining_count, tot=cp.total_count)}"
    )
    question = questionary.select(
        t("resume_prompt"),
        choices=[
            questionary.Choice(title=t("resume_continue", rem=cp.remaining_count), value=True),
            questionary.Choice(title=t("resume_discard"), value=False),
        ],
        instruction="(↑↓ / W·S · Enter)",
        style=_SELECT_STYLE,
    )
    _attach_nav_keys(question, allow_back=True)
    answer: bool = question.unsafe_ask()
    return answer


# --------------------------------------------------------------------------- #
# Message type, pinned protection & keep-latest filter menu
# --------------------------------------------------------------------------- #
def select_message_filter() -> MessageFilter:
    """Ask for message type, pinned protection and keep-newest-N count."""
    step = 0
    kind = MessageKind.ALL
    skip_pinned = True
    keep_latest = 0

    while True:
        if step == 0:
            q_kind = questionary.select(
                t("kind_prompt"),
                choices=[
                    questionary.Choice(title=t("kind_all"), value=MessageKind.ALL),
                    questionary.Choice(
                        title=t("kind_attachments"), value=MessageKind.ATTACHMENTS_ONLY
                    ),
                    questionary.Choice(title=t("kind_links"), value=MessageKind.LINKS_ONLY),
                ],
                instruction=t("client_instruction"),
                style=_SELECT_STYLE,
            )
            _attach_nav_keys(q_kind, allow_back=True)
            kind = q_kind.unsafe_ask()
            step = 1
        elif step == 1:
            try:
                q_pin = questionary.select(
                    t("pinned_prompt"),
                    choices=[
                        questionary.Choice(title=t("pinned_keep"), value=True),
                        questionary.Choice(title=t("pinned_delete"), value=False),
                    ],
                    instruction=t("client_instruction"),
                    style=_SELECT_STYLE,
                )
                _attach_nav_keys(q_pin, allow_back=True)
                skip_pinned = q_pin.unsafe_ask()
                step = 2
            except GoBack:
                step = 0
        else:
            try:
                q_keep = questionary.select(
                    t("keep_latest_prompt"),
                    choices=[
                        questionary.Choice(title=t("keep_latest_none"), value=0),
                        questionary.Choice(title=t("keep_latest_25"), value=25),
                        questionary.Choice(title=t("keep_latest_50"), value=50),
                        questionary.Choice(title=t("keep_latest_100"), value=100),
                        questionary.Choice(title=t("keep_latest_custom"), value="custom"),
                    ],
                    instruction=t("client_instruction"),
                    style=_SELECT_STYLE,
                )
                _attach_nav_keys(q_keep, allow_back=True)
                picked: int | str = q_keep.unsafe_ask()
                if isinstance(picked, int):
                    keep_latest = picked
                    return MessageFilter(
                        kind=kind, skip_pinned=skip_pinned, keep_latest=keep_latest
                    )
                while True:
                    raw = _prompt_text_with_back(t("keep_latest_custom_prompt"))
                    if not raw:
                        raise GoBack()
                    if not raw.isdigit():
                        log_error(t("limit_custom_invalid"))
                        continue
                    keep_latest = int(raw)
                    return MessageFilter(
                        kind=kind, skip_pinned=skip_pinned, keep_latest=keep_latest
                    )
            except GoBack:
                step = 1


def select_execution_mode() -> str:
    """Choose between ``standard`` (scan + preview + backup) and ``stream`` (scan-while-delete)."""
    question = questionary.select(
        t("mode_prompt"),
        choices=[
            questionary.Choice(title=t("mode_standard"), value="standard"),
            questionary.Choice(title=t("mode_stream"), value="stream"),
        ],
        instruction=t("client_instruction"),
        style=_SELECT_STYLE,
    )
    _attach_nav_keys(question, allow_back=True)
    mode: str = question.unsafe_ask()
    return mode


# --------------------------------------------------------------------------- #
# Message limit menu
# --------------------------------------------------------------------------- #
LARGE_BATCH_THRESHOLD: int = 1000


def describe_limit(max_messages: int | None) -> str:
    """Return a localized label for the active message limit."""
    if max_messages is None:
        return t("limit_desc_unlimited")
    return t("limit_desc_count", n=max_messages)


def select_message_limit() -> int | None:
    """Ask the user for a maximum number of own messages to scan & delete (``None`` = unlimited)."""
    while True:
        choices = [
            questionary.Choice(title=t("limit_unlimited"), value="unlimited"),
            questionary.Choice(title=t("limit_250"), value=250),
            questionary.Choice(title=t("limit_500"), value=500),
            questionary.Choice(title=t("limit_1000"), value=1000),
            questionary.Choice(title=t("limit_custom"), value="custom"),
        ]
        question = questionary.select(
            t("limit_prompt"),
            choices=choices,
            instruction=t("client_instruction"),
            style=_SELECT_STYLE,
        )
        _attach_nav_keys(question, allow_back=True)
        picked: str | int = question.unsafe_ask()

        if picked == "unlimited":
            return None
        if isinstance(picked, int):
            return picked

        try:
            while True:
                raw = _prompt_text_with_back(t("limit_custom_prompt"))
                if not raw:
                    raise GoBack()
                if not raw.isdigit() or int(raw) <= 0:
                    log_error(t("limit_custom_invalid"))
                    continue
                return int(raw)
        except GoBack:
            continue


# --------------------------------------------------------------------------- #
# Dry-Run Preview & Action Choice
# --------------------------------------------------------------------------- #
def _estimate_text(count: int) -> str:
    seconds = estimate_seconds(count)
    if seconds < 60:
        return t("est_seconds", n=max(1, round(seconds)))
    return f"~{format_duration(seconds)}"


def _pick_sample_messages(messages: list[Message], limit: int = 8) -> list[Message]:
    """Pick up to ``limit`` sample messages (newest + oldest) for the Dry-Run preview."""
    if len(messages) <= limit:
        return messages
    half = limit // 2
    return [*messages[:half], *messages[-(limit - half) :]]


def show_preview_and_choose_action(
    summary: ScanSummary,
    date_filter: DateFilter,
    max_messages: int | None = None,
    msg_filter: MessageFilter | None = None,
) -> str:
    """Render the Dry-Run preview panel + sample table and return the chosen action."""
    total = len(summary.messages)
    mflt = msg_filter or MessageFilter()

    grid = Table(box=None, show_header=False, padding=(0, 1))
    grid.add_column(no_wrap=False)
    grid.add_row(t("preview_channels", n=len(summary.channels)))
    grid.add_row(t("preview_messages", n=total))
    grid.add_row(t("preview_filter", flt=date_filter.describe()))
    grid.add_row(t("preview_msg_filter", mflt=mflt.describe()))
    grid.add_row(t("preview_limit", lim=describe_limit(max_messages)))
    grid.add_row(t("preview_eta", eta=_estimate_text(total)))
    grid.add_row("")
    grid.add_row(Text(t("preview_breakdown"), style="bold bright_white"))
    for idx, ch in enumerate(summary.channels, start=1):
        cnt = summary.per_channel_counts.get(ch.id, 0)
        row = Text(f"  {idx:>2}. ", style="dim cyan")
        row.append(ch.name, style="white")
        row.append(f"  ({cnt})", style="bold bright_cyan" if cnt else "dim")
        grid.add_row(row)

    console.print()
    console.print(
        Panel(
            grid,
            title=f"[bold bright_cyan]{t('preview_panel_title')}[/bold bright_cyan]",
            box=box.ROUNDED,
            border_style="bright_cyan",
            padding=(1, 2),
            expand=False,
        )
    )

    if total >= LARGE_BATCH_THRESHOLD:
        console.print(
            Panel(
                Text(t("preview_large_warning", n=total), style="bold yellow"),
                box=box.ROUNDED,
                border_style="yellow",
                padding=(0, 2),
                expand=False,
            )
        )

    samples = _pick_sample_messages(summary.messages)
    if samples:
        sample_table = Table(
            title=f"[bold bright_magenta]{t('preview_samples_title')}[/bold bright_magenta]",
            box=box.SIMPLE_HEAVY,
            border_style="dim magenta",
            show_lines=False,
        )
        sample_table.add_column(t("preview_col_date"), style="dim cyan", no_wrap=True)
        sample_table.add_column(t("preview_col_channel"), style="cyan", max_width=24)
        sample_table.add_column(t("preview_col_content"), style="white")
        for msg in samples:
            sample_table.add_row(
                msg.timestamp.strftime("%Y-%m-%d %H:%M"),
                msg.channel_name,
                msg.preview_text(64),
            )
        console.print(sample_table)

    console.print()
    choices = [
        questionary.Choice(title=t("preview_action_proceed"), value="proceed"),
        questionary.Choice(title=t("preview_action_backup_only"), value="backup_only"),
        questionary.Choice(title=t("preview_action_back"), value="back"),
        questionary.Choice(title=t("preview_action_cancel"), value="cancel"),
    ]
    question = questionary.select(
        t("preview_action_prompt"),
        choices=choices,
        instruction=t("client_instruction"),
        style=_SELECT_STYLE,
    )
    _attach_nav_keys(question, allow_back=True)
    action: str = question.unsafe_ask()
    return action


# --------------------------------------------------------------------------- #
# Backup selection
# --------------------------------------------------------------------------- #
def select_backup_format(*, allow_none: bool = True) -> BackupFormat:
    """Ask the user which local backup format to export."""
    choices = [
        questionary.Choice(title=t("backup_all"), value=BackupFormat.ALL),
        questionary.Choice(title=t("backup_html"), value=BackupFormat.HTML),
        questionary.Choice(title=t("backup_both"), value=BackupFormat.BOTH),
        questionary.Choice(title=t("backup_json"), value=BackupFormat.JSON),
        questionary.Choice(title=t("backup_txt"), value=BackupFormat.TXT),
    ]
    if allow_none:
        choices.append(questionary.Choice(title=t("backup_none"), value=BackupFormat.NONE))

    question = questionary.select(
        t("backup_prompt"),
        choices=choices,
        instruction=t("client_instruction"),
        style=_SELECT_STYLE,
    )
    _attach_nav_keys(question, allow_back=True)
    fmt: BackupFormat = question.unsafe_ask()
    return fmt


def ask_download_media(count: int) -> bool:
    """Ask whether to also download image/file attachments locally."""
    question = questionary.select(
        t("backup_media_prompt", n=count),
        choices=[
            questionary.Choice(title=t("yes"), value=True),
            questionary.Choice(title=t("no"), value=False),
        ],
        instruction=t("client_instruction"),
        style=_SELECT_STYLE,
    )
    _attach_nav_keys(question, allow_back=True)
    answer: bool = question.unsafe_ask()
    return answer


# --------------------------------------------------------------------------- #
# Final Confirmation
# --------------------------------------------------------------------------- #
def confirm_delete(
    summary: ScanSummary,
    date_filter: DateFilter,
    backup_paths: list[Path],
    clients: list[str],
    msg_filter: MessageFilter | None = None,
) -> bool:
    """Show the confirmation panel and require typing ``CONFIRM`` / ``ONAYLA``."""
    total = len(summary.messages)
    mflt = msg_filter or MessageFilter()
    backup_desc = (
        ", ".join(str(p) for p in backup_paths) if backup_paths else t("confirm_backup_none")
    )

    table = Table(box=None, show_header=False, padding=(0, 1))
    table.add_column(no_wrap=False)
    table.add_row(Text(t("confirm_title"), style="bold bright_red"))
    table.add_row("")
    table.add_row(t("confirm_count", n=total))
    table.add_row(t("confirm_channels", n=len(summary.channels)))
    table.add_row(t("confirm_filter", flt=date_filter.describe()))
    table.add_row(t("confirm_msg_filter", mflt=mflt.describe()))
    table.add_row(t("confirm_backup", backup=escape_markup(backup_desc)))
    table.add_row(t("confirm_client", clients=describe_clients(clients)))
    table.add_row(t("confirm_eta", eta=_estimate_text(total)))

    console.print()
    console.print(
        Panel(
            table,
            title=f"[bold bright_red]{t('confirm_panel_title')}[/bold bright_red]",
            box=box.DOUBLE,
            border_style="bright_red",
            padding=(1, 2),
            expand=False,
        )
    )

    try:
        answer = _prompt_text_with_back(t("confirm_prompt"))
    except GoBack:
        return False
    return answer in (t("confirm_keyword"), "CONFIRM", "ONAYLA")


def confirm_stream_delete(
    channels: list[DMChannel],
    date_filter: DateFilter,
    msg_filter: MessageFilter,
    max_messages: int | None,
    clients: list[str],
) -> bool:
    """Show confirmation panel for Live Stream (Scan-while-Delete) mode."""
    table = Table(box=None, show_header=False, padding=(0, 1))
    table.add_column(no_wrap=False)
    table.add_row(Text(t("confirm_title"), style="bold bright_red"))
    table.add_row("")
    table.add_row(t("stream_confirm_note"))
    table.add_row(t("confirm_channels", n=len(channels)))
    table.add_row(t("confirm_filter", flt=date_filter.describe()))
    table.add_row(t("confirm_msg_filter", mflt=msg_filter.describe()))
    table.add_row(t("preview_limit", lim=describe_limit(max_messages)))
    table.add_row(t("confirm_client", clients=describe_clients(clients)))

    console.print()
    console.print(
        Panel(
            table,
            title=f"[bold bright_red]{t('confirm_panel_title')}[/bold bright_red]",
            box=box.DOUBLE,
            border_style="bright_red",
            padding=(1, 2),
            expand=False,
        )
    )

    try:
        answer = _prompt_text_with_back(t("confirm_prompt"))
    except GoBack:
        return False
    return answer in (t("confirm_keyword"), "CONFIRM", "ONAYLA")


# --------------------------------------------------------------------------- #
# Live Deletion Progress View (flicker-free on Windows CMD)
# --------------------------------------------------------------------------- #
def _status_label(status: DeleteStatus) -> tuple[str, str]:
    if status is DeleteStatus.DELETED:
        return t("status_deleted"), "bold green"
    if status is DeleteStatus.SKIPPED:
        return t("status_skipped"), "bold yellow"
    return t("status_error"), "bold red"


class DeleteProgressView:
    """Flicker-free single-line progress bar + permanent log of every processed message."""

    def __init__(self, total: int | None) -> None:
        self.total = total
        self.results: list[DeleteResult] = []
        counter_fmt = (
            "\\[{task.completed:.0f}/{task.total:.0f}]"
            if total is not None
            else "\\[{task.completed:.0f}]"
        )
        columns: list[object] = [
            SpinnerColumn(spinner_name="line", style="bright_magenta"),
            TextColumn(counter_fmt, style="bold bright_cyan"),
            BarColumn(
                bar_width=32,
                style="dim white",
                complete_style="bright_magenta",
                finished_style="bold green",
            ),
            TextColumn("{task.fields[current]}", style="bold white"),
            TextColumn("{task.fields[status]}"),
        ]
        if total is not None:
            columns.extend([TextColumn(t("eta"), style="dim"), TimeRemainingColumn()])
        self.progress = Progress(
            *columns,  # type: ignore[arg-type]
            console=console,
            expand=False,
        )
        self.task: TaskID = self.progress.add_task("delete", total=total, current="", status="")
        self._live = Live(self.progress, console=console, refresh_per_second=4, transient=True)

    @staticmethod
    def _short(text: str, width: int = 30) -> str:
        clipped = text if len(text) <= width else text[: width - 1] + "…"
        return clipped.replace("[", "\\[")

    def start(self, message: Message) -> None:
        self.progress.update(
            self.task,
            current=self._short(message.channel_name, 22),
            status=f"[cyan]{t('status_deleting')}[/cyan]",
        )

    def record(self, result: DeleteResult) -> None:
        self.results.append(result)
        index = len(self.results)
        label, style = _status_label(result.status)
        msg = result.message

        row = Text()
        row.append(f"  {index:>4}. ", style="dim cyan")
        row.append(f"{label:<11} ", style=style)
        row.append(f"[{msg.channel_name}] ", style="cyan")
        row.append(msg.preview_text(44), style="white")
        if result.status is not DeleteStatus.DELETED and result.detail:
            row.append(f"  — {result.detail}", style="dim")
        self._live.console.print(row)

        self.progress.update(self.task, advance=1, status=f"[{style}]{label}[/{style}]")

    def waiting(self, seconds: float) -> None:
        if seconds <= 0:
            return
        self.progress.update(
            self.task, status=f"[dim]{t('status_waiting', s=f'{seconds:.1f}')}[/dim]"
        )

    def __enter__(self) -> DeleteProgressView:
        console.print()
        console.print(f"[bold bright_magenta]── {t('exec_title')} ──[/bold bright_magenta]")
        self._live.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.progress.update(self.task, current="", status="")
        self._live.stop()


# --------------------------------------------------------------------------- #
# Summary & Clipboard prompt
# --------------------------------------------------------------------------- #
def show_summary(results: list[DeleteResult], backup_paths: list[Path], elapsed: float) -> None:
    """Render the final summary panel."""
    deleted = sum(1 for r in results if r.status is DeleteStatus.DELETED)
    skipped = [r for r in results if r.status is DeleteStatus.SKIPPED]
    errors = [r for r in results if r.status is DeleteStatus.ERROR]

    grid = Table(box=None, show_header=False, padding=(0, 2))
    grid.add_column(style="bold")
    grid.add_column(justify="right")
    grid.add_row(Text(t("sum_deleted"), style="bold green"), Text(str(deleted), style="bold green"))
    if skipped:
        grid.add_row(
            Text(t("sum_skipped"), style="yellow"), Text(str(len(skipped)), style="bold yellow")
        )
    err_style = "bold red" if errors else "dim"
    grid.add_row(Text(t("sum_errors"), style=err_style), Text(str(len(errors)), style=err_style))
    if backup_paths:
        grid.add_row(
            Text(t("sum_backup"), style="cyan"),
            Text(", ".join(str(p) for p in backup_paths), style="bold cyan"),
        )
    grid.add_row(
        Text(t("sum_elapsed"), style="cyan"),
        Text(format_duration(elapsed), style="bold bright_cyan"),
    )

    border = "bright_green" if not errors else "yellow"
    console.print()
    console.print(
        Panel(
            grid,
            title=f"[bold {border}]{t('summary_title')}[/bold {border}]",
            box=box.DOUBLE,
            border_style=border,
            expand=False,
            padding=(1, 2),
        )
    )

    for result in errors[:15]:
        label, style = _status_label(result.status)
        line = Text(f"{label}: ", style=style)
        line.append(f"[{result.message.channel_name}] {result.message.id}", style="bold")
        line.append(f" — {result.detail}", style="dim")
        console.print(line)


def ask_clear_clipboard() -> bool:
    """Optionally ask the user whether to clear their clipboard before exiting."""
    question = questionary.select(
        t("clipboard_prompt"),
        choices=[
            questionary.Choice(title=t("yes"), value=True),
            questionary.Choice(title=t("no"), value=False),
        ],
        instruction="(↑↓ / W·S · Enter)",
        style=_SELECT_STYLE,
    )
    _attach_nav_keys(question, allow_back=False)
    answer: bool = question.unsafe_ask()
    return answer
