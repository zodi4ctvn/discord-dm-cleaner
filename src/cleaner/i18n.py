"""Tiny i18n layer: Turkish and English UI strings."""

from __future__ import annotations

import locale
import os
from typing import Any

SUPPORTED: tuple[str, ...] = ("tr", "en")
_current: str = "en"

STRINGS: dict[str, dict[str, str]] = {
    "en": {
        # banner / language
        "version": "v{v}",
        "lang_prompt": "Dil / Language:",
        # client detection
        "no_client": "! No Discord desktop client detected. Continuing in token-only mode.",
        "client_prompt": "Select the Discord client to target:",
        "client_instruction": "(↑↓ / W·S · Enter = select · Backspace = back)",
        "token_only": "Token-only",
        # token
        "env_warning": (
            "! Using token from the {var} environment variable. This is insecure: "
            "environment variables can leak via shell history, process listings and crash dumps."
        ),
        "token_prompt": "Enter your Discord token (masked · Esc/Backspace = back):",
        "err_token_format": "Invalid token format. Refusing to contact Discord.",
        # login / fetch
        "logging_in": "Authenticating with Discord...",
        "logged_in": "Logged in as ",
        "fetching_dms": "Fetching your open DM & Group DM channels...",
        "found_dms": (
            "• Found [bold bright_cyan]{total}[/bold bright_cyan] open conversations  "
            "([cyan]1-1 DM: {dm}[/cyan]  ·  [magenta]Group DM: {group}[/magenta])"
        ),
        "no_open_dms": "No open DM or Group DM conversations were found.",
        # channel picker TUI
        "sel_title": " Discord DM Cleaner — Select conversations to clean ",
        "sel_no_match": "  No conversations match your search.",
        "dm_badge": "[DM]",
        "group_badge": "[GROUP]",
        "stat_matching": " • Matching:  ",
        "stat_selected": " + Selected:  ",
        "stat_dms": " • 1-1 DMs:   ",
        "stat_groups": " • Group DMs: ",
        "stat_will_clean": " » Will scan:",
        "stat_more": "  … and {n} more",
        "stat_nothing": " Nothing selected yet.",
        "key_space": "Space",
        "key_toggle": "toggle",
        "key_select_all": "select all",
        "key_deselect_all": "clear",
        "key_search": "search",
        "key_move": "move",
        "key_confirm": "confirm",
        "key_back": "back",
        "key_quit": "quit",
        "frame_channels": " Conversations (Recent first) ",
        "frame_search": " Search  ( / ) ",
        "frame_selection": " Selection ",
        "sel_warning": "! You did not select any conversations — select at least one!",
        # date filter
        "filter_prompt": "Select date filter for your messages:",
        "filter_all": "All my messages (no date limit)",
        "filter_last_days": "Messages from the last N days",
        "filter_before": "Messages before a specific date",
        "filter_after": "Messages after a specific date",
        "filter_between": "Messages between two dates",
        "filter_days_prompt": "How many days back? (e.g. 7, 30, 90):",
        "filter_days_invalid": "Please enter a positive whole number of days.",
        "filter_date_before_prompt": "Delete messages BEFORE date (YYYY-MM-DD or DD.MM.YYYY):",
        "filter_date_after_prompt": "Delete messages AFTER date (YYYY-MM-DD or DD.MM.YYYY):",
        "filter_date_start_prompt": "Start date (YYYY-MM-DD or DD.MM.YYYY):",
        "filter_date_end_prompt": "End date (YYYY-MM-DD or DD.MM.YYYY):",
        "filter_date_invalid": "Invalid date format. Use YYYY-MM-DD or DD.MM.YYYY.",
        "filter_date_order_err": "Start date must be earlier than or equal to end date.",
        "filter_desc_all": "All messages",
        "filter_desc_last_days": "Last {days} days (since {since})",
        "filter_desc_before": "Before {before}",
        "filter_desc_after": "After {after}",
        "filter_desc_between": "{after} → {before}",
        # message limit
        "limit_prompt": "Maximum number of your messages to scan & delete:",
        "limit_unlimited": "Unlimited (all matching messages)",
        "limit_250": "Newest 250 messages (fast & safe batch)",
        "limit_500": "Newest 500 messages (recommended safe batch)",
        "limit_1000": "Newest 1,000 messages",
        "limit_custom": "Custom limit...",
        "limit_custom_prompt": "Enter max message count (e.g. 200, 750 · Backspace/Esc = back):",
        "limit_custom_invalid": "Please enter a positive whole number.",
        "limit_desc_unlimited": "Unlimited",
        "limit_desc_count": "Max {n} newest",
        # message type / pinned / keep-latest filters
        "kind_prompt": "Which message types should be deleted?",
        "kind_all": "All message types",
        "kind_attachments": "Only messages with images / file attachments",
        "kind_links": "Only messages containing links (URLs)",
        "kind_desc_all": "All types",
        "kind_desc_attachments": "Attachments only",
        "kind_desc_links": "Links only",
        "pinned_prompt": "Protect pinned messages?",
        "pinned_keep": "Yes — skip pinned messages (recommended)",
        "pinned_delete": "No — delete pinned messages too",
        "pinned_desc_keep": "Pinned protected",
        "keep_latest_prompt": "Keep your newest N messages in each conversation?",
        "keep_latest_none": "No — include newest messages (keep 0)",
        "keep_latest_25": "Keep newest 25 messages per conversation",
        "keep_latest_50": "Keep newest 50 messages per conversation",
        "keep_latest_100": "Keep newest 100 messages per conversation",
        "keep_latest_custom": "Custom number...",
        "keep_latest_custom_prompt": "How many newest messages to keep per conversation? (Backspace/Esc = back):",
        "keep_latest_desc": "Keep newest {n}",
        # execution mode
        "mode_prompt": "Choose execution mode:",
        "mode_standard": "Standard Mode — Scan → Dry-Run Preview → Backup → Delete (recommended)",
        "mode_stream": "Live Stream Mode (Scan-while-Delete) — Delete each 100-msg page immediately",
        "stream_confirm_note": "• Mode:           [bold magenta]Live Stream (Scan-while-Delete)[/bold magenta]",
        # resume / checkpoint
        "resume_found": (
            "Found an unfinished session from [bold cyan]{when}[/bold cyan]: "
            "[bold bright_yellow]{rem}[/bold bright_yellow] / {tot} message(s) remaining."
        ),
        "resume_prompt": "Resume where you left off?",
        "resume_continue": "Yes — resume remaining {rem} message(s)",
        "resume_discard": "No — discard checkpoint and start fresh",
        "checkpoint_saved": "Progress saved to checkpoint ({rem} remaining) — you can resume next time.",
        # scanning
        "scanning_channel": (
            "Scanning [bold cyan]{name}[/bold cyan] ({idx}/{total}) — "
            "checked {scanned}, found {matched} of yours..."
        ),
        "scan_done": (
            "Scan complete: found [bold bright_cyan]{matched}[/bold bright_cyan] "
            "of your messages across [bold]{channels}[/bold] conversation(s)."
        ),
        "no_messages_found": "No matching messages of yours were found in the selected conversations.",
        # preview / dry-run
        "preview_panel_title": "DRY-RUN PREVIEW",
        "preview_channels": "• Conversations:  [bold cyan]{n}[/bold cyan]",
        "preview_messages": "• Your messages:  [bold bright_cyan]{n}[/bold bright_cyan]",
        "preview_filter": "• Date filter:    [bold yellow]{flt}[/bold yellow]",
        "preview_msg_filter": "• Content filter: [bold yellow]{mflt}[/bold yellow]",
        "preview_limit": "• Message limit:  [bold yellow]{lim}[/bold yellow]",
        "preview_eta": "• Estimated time: [bold yellow]{eta}[/bold yellow]",
        "preview_large_warning": (
            "! Large batch ({n} messages): Deleting thousands of messages in one session "
            "increases the risk of Discord flagging or locking your account. Consider using "
            "a smaller batch limit (e.g. 500–1,000 per session) or a date filter."
        ),
        "preview_breakdown": "» Breakdown by conversation:",
        "preview_samples_title": "Sample Matching Messages (newest & oldest)",
        "preview_col_date": "Date (UTC)",
        "preview_col_channel": "Conversation",
        "preview_col_content": "Content",
        "preview_attachment_only": "[attachment: {n}]",
        "preview_embed_only": "[embed]",
        "preview_empty": "[empty]",
        "preview_action_prompt": "What would you like to do?",
        "preview_action_proceed": "Proceed to backup & delete these messages",
        "preview_action_backup_only": "Export backup only and exit (Dry-Run — delete nothing)",
        "preview_action_back": "Go back and change filter / conversations",
        "preview_action_cancel": "Exit without deleting anything (Dry-Run)",
        "dry_run_exited": "Dry-Run finished. No messages were deleted.",
        # backup
        "backup_prompt": "Back up matching messages locally before deleting?",
        "backup_all": "Yes — export ALL (JSON + TXT + Discord Dark HTML)",
        "backup_html": "Yes — export as Discord-themed HTML archive",
        "backup_both": "Yes — export JSON + TXT",
        "backup_json": "Yes — export as JSON",
        "backup_txt": "Yes — export as TXT",
        "backup_none": "No — skip backup",
        "backup_media_prompt": "Also download image/file attachments locally ({n} file(s))?",
        "backup_media_downloading": "Downloading attachments locally...",
        "backup_media_saved": "Downloaded {ok}/{total} attachment(s) to: {dir}",
        "backup_saved": "Backup saved to: {path}",
        "backup_error": "Backup failed: {err}",
        # confirmation
        "confirm_panel_title": "! CONFIRMATION REQUIRED",
        "confirm_title": "» You are about to PERMANENTLY DELETE:",
        "confirm_count": "• Messages:       [bold bright_red]{n}[/bold bright_red] message(s)",
        "confirm_channels": "• Conversations:  [bold cyan]{n}[/bold cyan]",
        "confirm_filter": "• Date filter:    [bold yellow]{flt}[/bold yellow]",
        "confirm_msg_filter": "• Content filter: [bold yellow]{mflt}[/bold yellow]",
        "confirm_backup": "• Local backup:   [bold green]{backup}[/bold green]",
        "confirm_backup_none": "None",
        "confirm_client": "• Client:         [bold cyan]{clients}[/bold cyan]",
        "confirm_eta": "• Estimated time: [bold yellow]{eta}[/bold yellow]",
        "confirm_prompt": "Type CONFIRM to proceed (Backspace/Esc = back):",
        "confirm_keyword": "CONFIRM",
        "est_seconds": "~{n} seconds",
        "going_back": "« Going back...",
        # execution
        "exec_title": "Deleting Messages",
        "status_deleted": "+ Deleted",
        "status_skipped": "! Skipped",
        "status_error": "× Error",
        "status_deleting": "» Deleting...",
        "status_waiting": "… Human delay {s}s",
        "eta": "ETA",
        # api details / errors
        "detail_deleted": "Deleted",
        "detail_404": "Already deleted (404)",
        "detail_forbidden": "Cannot delete (forbidden / system message)",
        "detail_unexpected": "Unexpected status {status}",
        "err_network": "Network error: {name}",
        "err_429": "Rate limited too many times (429).",
        "err_5xx": "Discord server error ({status}).",
        "err_account": "Could not fetch account ({status}).",
        "err_bot": "Bot tokens are not supported — use a user token.",
        "err_dms": "Could not fetch DM channels ({status}).",
        "err_messages": "Could not fetch messages ({status}).",
        "err_401": "Token is invalid or has been revoked (HTTP 401). Aborting immediately.",
        "err_unexpected": "Unexpected error ({name}): {msg}",
        # summary / exit
        "summary_title": "Summary",
        "sum_deleted": "Deleted",
        "sum_skipped": "Skipped",
        "sum_errors": "Errors",
        "sum_backup": "Backup",
        "sum_elapsed": "Elapsed",
        "interrupted": "Interrupted by user. Stopping cleanly.",
        "clipboard_prompt": "Your token is still in the clipboard. Clear it?",
        "clipboard_cleared": "Clipboard cleared.",
        "token_wiped": "Token wiped from memory.",
        "yes": "Yes",
        "no": "No",
    },
    "tr": {
        # banner / language
        "version": "v{v}",
        "lang_prompt": "Dil / Language:",
        # client detection
        "no_client": "! Yüklü Discord masaüstü istemcisi bulunamadı. Sadece token modunda devam ediliyor.",
        "client_prompt": "Hedeflenecek Discord istemcisini seçin:",
        "client_instruction": "(↑↓ / W·S · Enter = seç · Backspace = geri)",
        "token_only": "Sadece token",
        # token
        "env_warning": (
            "! Token {var} ortam değişkeninden okunuyor. Bu güvensizdir: "
            "ortam değişkenleri kabuk geçmişi, işlem listesi ve çökme dökümlerinden sızabilir."
        ),
        "token_prompt": "Discord token'ınızı girin (gizli · Esc/Backspace = geri):",
        "err_token_format": "Geçersiz token formatı. Discord'a bağlanılmadı.",
        # login / fetch
        "logging_in": "Discord ile kimlik doğrulanıyor...",
        "logged_in": "Giriş yapıldı: ",
        "fetching_dms": "Açık DM ve Grup DM sohbetleriniz getiriliyor...",
        "found_dms": (
            "• [bold bright_cyan]{total}[/bold bright_cyan] açık sohbet bulundu  "
            "([cyan]1-1 DM: {dm}[/cyan]  ·  [magenta]Grup DM: {group}[/magenta])"
        ),
        "no_open_dms": "Açık DM veya Grup DM sohbeti bulunamadı.",
        # channel picker TUI
        "sel_title": " Discord DM Cleaner — Temizlenecek sohbetleri seçin ",
        "sel_no_match": "  Aramanızla eşleşen sohbet bulunamadı.",
        "dm_badge": "[DM]",
        "group_badge": "[GRUP]",
        "stat_matching": " • Eşleşen:   ",
        "stat_selected": " + Seçili:    ",
        "stat_dms": " • 1-1 DM:    ",
        "stat_groups": " • Grup DM:   ",
        "stat_will_clean": " » Taranacak:",
        "stat_more": "  … ve {n} tane daha",
        "stat_nothing": " Henüz bir şey seçilmedi.",
        "key_space": "Boşluk",
        "key_toggle": "seç/bırak",
        "key_select_all": "tümünü seç",
        "key_deselect_all": "temizle",
        "key_search": "ara",
        "key_move": "gezin",
        "key_confirm": "onayla",
        "key_back": "geri",
        "key_quit": "çıkış",
        "frame_channels": " Sohbetler (En son aktif) ",
        "frame_search": " Ara  ( / ) ",
        "frame_selection": " Seçim ",
        "sel_warning": "! Hiçbir sohbet seçmediniz — en az bir tane seçin!",
        # date filter
        "filter_prompt": "Mesajlarınız için tarih filtresi seçin:",
        "filter_all": "Tüm mesajlarım (tarih sınırı yok)",
        "filter_last_days": "Son N gün içindeki mesajlarım",
        "filter_before": "Belirli bir tarihten önceki mesajlarım",
        "filter_after": "Belirli bir tarihten sonraki mesajlarım",
        "filter_between": "İki tarih arasındaki mesajlarım",
        "filter_days_prompt": "Kaç gün geriye gidilsin? (örn. 7, 30, 90):",
        "filter_days_invalid": "Lütfen pozitif bir tam sayı girin.",
        "filter_date_before_prompt": "Şu tarihten ÖNCEKİ mesajları sil (YYYY-AA-GG veya GG.AA.YYYY):",
        "filter_date_after_prompt": "Şu tarihten SONRAKİ mesajları sil (YYYY-AA-GG veya GG.AA.YYYY):",
        "filter_date_start_prompt": "Başlangıç tarihi (YYYY-AA-GG veya GG.AA.YYYY):",
        "filter_date_end_prompt": "Bitiş tarihi (YYYY-AA-GG veya GG.AA.YYYY):",
        "filter_date_invalid": "Geçersiz tarih formatı. YYYY-AA-GG veya GG.AA.YYYY kullanın.",
        "filter_date_order_err": "Başlangıç tarihi bitiş tarihinden büyük olamaz.",
        "filter_desc_all": "Tüm mesajlar",
        "filter_desc_last_days": "Son {days} gün ({since} sonrası)",
        "filter_desc_before": "{before} öncesi",
        "filter_desc_after": "{after} sonrası",
        "filter_desc_between": "{after} → {before}",
        # message limit
        "limit_prompt": "Taranıp silinecek en fazla mesaj sayısı:",
        "limit_unlimited": "Sınırsız (eşleşen tüm mesajlarım)",
        "limit_250": "En yeni 250 mesaj (hızlı & güvenli parti)",
        "limit_500": "En yeni 500 mesaj (önerilen güvenli parti)",
        "limit_1000": "En yeni 1.000 mesaj",
        "limit_custom": "Özel adet gir...",
        "limit_custom_prompt": "En fazla kaç mesaj silinsin? (örn. 200, 750 · Backspace/Esc = geri):",
        "limit_custom_invalid": "Lütfen pozitif bir tam sayı girin.",
        "limit_desc_unlimited": "Sınırsız",
        "limit_desc_count": "En yeni {n} mesaj",
        # message type / pinned / keep-latest filters
        "kind_prompt": "Hangi mesaj türleri silinsin?",
        "kind_all": "Tüm mesaj türleri",
        "kind_attachments": "Sadece fotoğraf / dosya eki içeren mesajlar",
        "kind_links": "Sadece link (URL) içeren mesajlar",
        "kind_desc_all": "Tüm türler",
        "kind_desc_attachments": "Sadece ek/medya",
        "kind_desc_links": "Sadece linkler",
        "pinned_prompt": "Sabitlenmiş (Pinned) mesajlar korunsun mu?",
        "pinned_keep": "Evet — sabitlenmiş mesajları atla (önerilen)",
        "pinned_delete": "Hayır — sabitlenmiş mesajları da sil",
        "pinned_desc_keep": "Sabitlenenler korunur",
        "keep_latest_prompt": "Her sohbette en yeni N mesajın korunsun mu?",
        "keep_latest_none": "Hayır — en yeni mesajlar da dahil (0 koru)",
        "keep_latest_25": "Her sohbette en yeni 25 mesajı koru",
        "keep_latest_50": "Her sohbette en yeni 50 mesajı koru",
        "keep_latest_100": "Her sohbette en yeni 100 mesajı koru",
        "keep_latest_custom": "Özel sayı gir...",
        "keep_latest_custom_prompt": "Her sohbette en yeni kaç mesaj korunsun? (Backspace/Esc = geri):",
        "keep_latest_desc": "Son {n} mesaj korunur",
        # execution mode
        "mode_prompt": "Çalışma modunu seçin:",
        "mode_standard": "Standart Mod — Tara → Dry-Run Önizleme → Yedekle → Sil (önerilen)",
        "mode_stream": "Canlı Akış Modu (Scan-while-Delete) — Her 100'lük sayfa geldikçe anında sil",
        "stream_confirm_note": "• Mod:            [bold magenta]Canlı Akış (Scan-while-Delete)[/bold magenta]",
        # resume / checkpoint
        "resume_found": (
            "[bold cyan]{when}[/bold cyan] tarihinden yarım kalan bir oturum bulundu: "
            "[bold bright_yellow]{rem}[/bold bright_yellow] / {tot} mesaj kaldı."
        ),
        "resume_prompt": "Kaldığınız yerden devam etmek ister misiniz?",
        "resume_continue": "Evet — kalan {rem} mesajla devam et",
        "resume_discard": "Hayır — kaydı sil ve sıfırdan başla",
        "checkpoint_saved": "İlerleme kaydedildi ({rem} mesaj kaldı) — sonraki açılışta devam edebilirsiniz.",
        # scanning
        "scanning_channel": (
            "[bold cyan]{name}[/bold cyan] taranıyor ({idx}/{total}) — "
            "{scanned} mesaj incelendi, size ait {matched} mesaj bulundu..."
        ),
        "scan_done": (
            "Tarama tamamlandı: [bold]{channels}[/bold] sohbette size ait "
            "[bold bright_cyan]{matched}[/bold bright_cyan] mesaj bulundu."
        ),
        "no_messages_found": "Seçilen sohbetlerde filtreyle eşleşen size ait mesaj bulunamadı.",
        # preview / dry-run
        "preview_panel_title": "DRY-RUN ÖNİZLEME",
        "preview_channels": "• Sohbet sayısı:  [bold cyan]{n}[/bold cyan]",
        "preview_messages": "• Mesaj sayınız:  [bold bright_cyan]{n}[/bold bright_cyan]",
        "preview_filter": "• Tarih filtresi: [bold yellow]{flt}[/bold yellow]",
        "preview_msg_filter": "• İçerik filtresi:[bold yellow] {mflt}[/bold yellow]",
        "preview_limit": "• Mesaj limiti:   [bold yellow]{lim}[/bold yellow]",
        "preview_eta": "• Tahmini süre:   [bold yellow]{eta}[/bold yellow]",
        "preview_large_warning": (
            "! Yüksek mesaj sayısı ({n} mesaj): Tek oturumda binlerce mesaj silmek "
            "Discord'un hesabını şüpheli işaretleme veya kilitleme riskini artırır. "
            "Daha güvenli temizlik için 500–1.000'lik partiler veya tarih filtresi kullanmanız önerilir."
        ),
        "preview_breakdown": "» Sohbet bazında dağılım:",
        "preview_samples_title": "Eşleşen Mesaj Örnekleri (en yeni ve en eski)",
        "preview_col_date": "Tarih (UTC)",
        "preview_col_channel": "Sohbet",
        "preview_col_content": "İçerik",
        "preview_attachment_only": "[ek dosya: {n}]",
        "preview_embed_only": "[embed]",
        "preview_empty": "[boş]",
        "preview_action_prompt": "Ne yapmak istersiniz?",
        "preview_action_proceed": "Yedekleme ve silme adımına geç",
        "preview_action_backup_only": "Sadece yedek al ve çık (Dry-Run — hiçbir şey silme)",
        "preview_action_back": "Geri dön ve filtreyi / sohbetleri değiştir",
        "preview_action_cancel": "Hiçbir şey silmeden çık (Dry-Run)",
        "dry_run_exited": "Dry-Run tamamlandı. Hiçbir mesaj silinmedi.",
        # backup
        "backup_prompt": "Silmeden önce eşleşen mesajları yerel diske yedeklemek ister misiniz?",
        "backup_all": "Evet — HEPSİ (JSON + TXT + Discord Karanlık Tema HTML)",
        "backup_html": "Evet — Discord görünümlü HTML arşivi olarak yedekle",
        "backup_both": "Evet — JSON + TXT olarak yedekle",
        "backup_json": "Evet — JSON olarak yedekle",
        "backup_txt": "Evet — TXT olarak yedekle",
        "backup_none": "Hayır — yedek almadan devam et",
        "backup_media_prompt": "Mesajlardaki fotoğraf/dosya ekleri de indirilsin mi ({n} dosya)?",
        "backup_media_downloading": "Ek dosyalar yerel diske indiriliyor...",
        "backup_media_saved": "{ok}/{total} ek dosya indirildi: {dir}",
        "backup_saved": "Yedek kaydedildi: {path}",
        "backup_error": "Yedekleme başarısız: {err}",
        # confirmation
        "confirm_panel_title": "! ONAY GEREKLİ",
        "confirm_title": "» KALICI OLARAK SİLİNMEK ÜZERE:",
        "confirm_count": "• Mesaj sayısı:   [bold bright_red]{n}[/bold bright_red] mesaj",
        "confirm_channels": "• Sohbet sayısı:  [bold cyan]{n}[/bold cyan]",
        "confirm_filter": "• Tarih filtresi: [bold yellow]{flt}[/bold yellow]",
        "confirm_msg_filter": "• İçerik filtresi:[bold yellow] {mflt}[/bold yellow]",
        "confirm_backup": "• Yerel yedek:    [bold green]{backup}[/bold green]",
        "confirm_backup_none": "Yok",
        "confirm_client": "• İstemci:        [bold cyan]{clients}[/bold cyan]",
        "confirm_eta": "• Tahmini süre:   [bold yellow]{eta}[/bold yellow]",
        "confirm_prompt": "Devam etmek için ONAYLA yazın (Backspace/Esc = geri):",
        "confirm_keyword": "ONAYLA",
        "est_seconds": "~{n} saniye",
        "going_back": "« Geri dönülüyor...",
        # execution
        "exec_title": "Mesajlar Siliniyor",
        "status_deleted": "+ Silindi",
        "status_skipped": "! Atlandı",
        "status_error": "× Hata",
        "status_deleting": "» Siliniyor...",
        "status_waiting": "… İnsan gecikmesi {s}sn",
        "eta": "Kalan",
        # api details / errors
        "detail_deleted": "Silindi",
        "detail_404": "Zaten silinmiş (404)",
        "detail_forbidden": "Silinemiyor (yetki yok / sistem mesajı)",
        "detail_unexpected": "Beklenmeyen durum {status}",
        "err_network": "Ağ hatası: {name}",
        "err_429": "Çok fazla hız sınırına takıldı (429).",
        "err_5xx": "Discord sunucu hatası ({status}).",
        "err_account": "Hesap bilgisi alınamadı ({status}).",
        "err_bot": "Bot token'ları desteklenmez — kullanıcı token'ı girin.",
        "err_dms": "DM sohbetleri alınamadı ({status}).",
        "err_messages": "Mesajlar alınamadı ({status}).",
        "err_401": "Token geçersiz veya iptal edilmiş (HTTP 401). İşlem derhal durduruldu.",
        "err_unexpected": "Beklenmeyen hata ({name}): {msg}",
        # summary / exit
        "summary_title": "Özet",
        "sum_deleted": "Silinen",
        "sum_skipped": "Atlanan",
        "sum_errors": "Hata",
        "sum_backup": "Yedek",
        "sum_elapsed": "Süre",
        "interrupted": "Kullanıcı tarafından durduruldu. Temiz çıkış yapılıyor.",
        "clipboard_prompt": "Token'ınız hâlâ panoda duruyor. Temizlensin mi?",
        "clipboard_cleared": "Pano temizlendi.",
        "token_wiped": "Token bellekten silindi.",
        "yes": "Evet",
        "no": "Hayır",
    },
}


def detect_default_language() -> str:
    """Detect the preferred default language from ``DDC_LANG`` or OS locale."""
    env_lang = os.environ.get("DDC_LANG", "").strip().lower()
    if env_lang in SUPPORTED:
        return env_lang
    try:
        loc = locale.getlocale()[0] or ""
    except ValueError:
        loc = ""
    if loc.lower().startswith("tr"):
        return "tr"
    return "en"


def set_language(lang: str) -> None:
    """Activate ``lang`` (``'tr'`` or ``'en'``)."""
    global _current
    _current = lang if lang in SUPPORTED else "en"


def get_language() -> str:
    """Return the currently active language code."""
    return _current


def t(key: str, **kwargs: Any) -> str:
    """Look up ``key`` in the active language and format with ``kwargs``."""
    table = STRINGS.get(_current, STRINGS["en"])
    template = table.get(key, STRINGS["en"].get(key, key))
    if kwargs:
        return template.format(**kwargs)
    return template
