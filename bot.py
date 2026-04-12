#!/usr/bin/env python3
"""
SPEED DOWNLOADER — Telegram Bot
Powered by SPEED AI | Creator: @SPEED_prime
Ultra-fast media downloader with yt-dlp + FFmpeg
"""

import os
import re
import sys
import time
import json
import asyncio
import logging
import uuid
import shutil
import traceback
from datetime import datetime
from pathlib import Path
from collections import defaultdict

import yt_dlp
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    InputFile, BotCommand
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)
from telegram.constants import ParseMode, ChatAction

# ── Config ──────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8707229530:AAHqUWiWy_ja7dUIrE7HmUxSRbB549w5lYM")
BOT_NAME = "SPEED DOWNLOADER"
CREATOR = "@SPEED_prime"
CHANNEL = "@SPEED_AI_COMMUNITY"
POWERED_BY = "⚡ <b>Powered by SPEED AI</b>"

DOWNLOAD_DIR = Path("/tmp/speed_downloader/downloads")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

FFMPEG_PATH = "/usr/bin/ffmpeg"
MAX_TG_FILE = 2_000_000_000  # 2 GB
FALLBACK_HEIGHTS = [2160, 1440, 1080, 720, 480, 360]

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO
)
log = logging.getLogger("SpeedBot")

# ── User data stores ────────────────────────────────────────
user_settings = defaultdict(lambda: {
    "quality": "best",
    "audio_format": "mp3",
    "audio_bitrate": "320",
    "embed_subs": False,
    "embed_thumb": True,
})

# Store pending URLs for callback buttons
pending_urls = {}  # msg_id -> url

# Store music search results
music_search_results = {}  # msg_id -> list of results

# ── URL detection ───────────────────────────────────────────
URL_REGEX = re.compile(
    r'(https?://[^\s<>"\']+)',
    re.IGNORECASE
)

# ── Helpers ─────────────────────────────────────────────────
def fmt_size(b):
    if not b or b <= 0:
        return "Unknown"
    b = float(b)
    for u in ["B", "KB", "MB", "GB"]:
        if b < 1024:
            return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} TB"

def fmt_duration(s):
    if not s:
        return "Unknown"
    s = int(s)
    h, r = divmod(s, 3600)
    m, sec = divmod(r, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"

def progress_bar(pct, length=10):
    pct = max(0, min(100, pct))
    filled = int(length * pct / 100)
    return "■" * filled + "□" * (length - filled)

async def safe_edit(msg, text, reply_markup=None):
    """Edit message safely, ignore errors."""
    try:
        await msg.edit_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    except Exception:
        pass

# ── yt-dlp wrappers ────────────────────────────────────────
def _extract_info_sync(url, user_id=None):
    """Extract media info without downloading (synchronous)."""
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        "skip_download": True,
        "noplaylist": True,
        "ignoreerrors": True,
        "extractor_args": {"youtube": {"player_client": ["web", "android"]}},
        "http_headers": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        "ffmpeg_location": FFMPEG_PATH,
        "socket_timeout": 30,
        "live_from_start": True,
        "wait_for_video": (5, 30),
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)

async def extract_info(url, user_id=None):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _extract_info_sync, url, user_id)

def _download_video_sync(url, format_sel, output_dir, user_id=None, progress_data=None):
    """Download video (synchronous)."""
    outtmpl = str(output_dir / "%(title).70s.%(ext)s")

    opts = {
        "format": format_sel,
        "outtmpl": outtmpl,
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "ffmpeg_location": FFMPEG_PATH,
        "noplaylist": True,
        "ignoreerrors": True,
        "extractor_args": {"youtube": {"player_client": ["web", "android"]}},
        "http_headers": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        "socket_timeout": 30,
        "retries": 5,
        "fragment_retries": 5,
        "file_access_retries": 5,
        "extractor_retries": 5,
        "overwrites": True,
        "noprogress": True,
        "postprocessors": [{
            "key": "FFmpegVideoConvertor",
            "preferedformat": "mp4",
        }],
        "concurrent_fragment_downloads": 5,
        "live_from_start": True,
        "wait_for_video": (5, 30),
        "hls_use_mpegts": True,
    }

    if progress_data is not None:
        def hook(d):
            if d.get("status") == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                downloaded = d.get("downloaded_bytes") or 0
                speed = d.get("speed") or 0
                pct = (downloaded / total * 100) if total > 0 else 0
                progress_data["pct"] = pct
                progress_data["downloaded"] = downloaded
                progress_data["total"] = total
                progress_data["speed"] = speed
            elif d.get("status") == "finished":
                progress_data["pct"] = 100
                progress_data["status"] = "finished"
        opts["progress_hooks"] = [hook]

    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=True)

def _download_audio_sync(url, audio_fmt, bitrate, output_dir, user_id=None, progress_data=None):
    """Download audio only (synchronous)."""
    outtmpl = str(output_dir / "%(title).70s.%(ext)s")

    pp = [
        {
            "key": "FFmpegExtractAudio",
            "preferredcodec": audio_fmt,
            "preferredquality": str(bitrate),
        },
        {"key": "FFmpegMetadata"},
    ]

    opts = {
        "format": "bestaudio/best",
        "outtmpl": outtmpl,
        "quiet": True,
        "no_warnings": True,
        "ffmpeg_location": FFMPEG_PATH,
        "noplaylist": True,
        "ignoreerrors": True,
        "extractor_args": {"youtube": {"player_client": ["web", "android"]}},
        "http_headers": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        "socket_timeout": 30,
        "retries": 5,
        "postprocessors": pp,
        "writethumbnail": True,
        "noprogress": True,
    }

    if progress_data is not None:
        def hook(d):
            if d.get("status") == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                downloaded = d.get("downloaded_bytes") or 0
                speed = d.get("speed") or 0
                pct = (downloaded / total * 100) if total > 0 else 0
                progress_data["pct"] = pct
                progress_data["downloaded"] = downloaded
                progress_data["total"] = total
                progress_data["speed"] = speed
            elif d.get("status") == "finished":
                progress_data["pct"] = 100
                progress_data["status"] = "finished"
        opts["progress_hooks"] = [hook]

    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=True)

# ── Music search via yt-dlp ────────────────────────────────
def _search_music_sync(query, max_results=5):
    """Search YouTube Music / YouTube for a song query."""
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
        "default_search": "ytsearch",
        "ffmpeg_location": FFMPEG_PATH,
    }
    search_url = f"ytsearch{max_results}:{query}"
    with yt_dlp.YoutubeDL(opts) as ydl:
        result = ydl.extract_info(search_url, download=False)
    entries = result.get("entries") or []
    results = []
    for e in entries:
        results.append({
            "title": e.get("title", "Unknown"),
            "url": e.get("url") or e.get("webpage_url") or f"https://www.youtube.com/watch?v={e.get('id', '')}",
            "duration": fmt_duration(e.get("duration")),
            "channel": e.get("channel") or e.get("uploader") or "Unknown",
            "id": e.get("id", ""),
        })
    return results

async def search_music(query, max_results=5):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _search_music_sync, query, max_results)

# ── Format helpers ──────────────────────────────────────────
def build_format_selector(quality):
    if quality == "best":
        return "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best"
    elif quality == "audio":
        return "bestaudio/best"
    elif quality == "8k":
        return "bestvideo[height<=4320][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=4320]+bestaudio/best"
    elif quality == "4k":
        return "bestvideo[height<=2160][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=2160]+bestaudio/best"
    elif quality == "1440p":
        return "bestvideo[height<=1440][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1440]+bestaudio/best"
    elif quality in ("1080p", "720p", "480p"):
        h = quality.replace("p", "")
        return f"bestvideo[height<={h}][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<={h}]+bestaudio/best[height<={h}]/best"
    else:
        return "bestvideo+bestaudio/best"

def find_output_file(task_dir):
    """Find the largest media file in the output directory."""
    media_exts = {".mp4", ".mkv", ".webm", ".mp3", ".m4a", ".flac", ".opus", ".ogg", ".wav", ".aac", ".avi", ".mov"}
    files = []
    for f in task_dir.iterdir():
        if f.is_file() and f.suffix.lower() in media_exts and f.stat().st_size > 0:
            files.append(f)
    if not files:
        files = [f for f in task_dir.iterdir() if f.is_file() and f.stat().st_size > 1000]
    if files:
        return max(files, key=lambda f: f.stat().st_size)
    return None

# ── /start ──────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = (
        f"🙌 <b>Welcome {user.mention_html()}!</b>\n\n"
        f"📥 I'm <b>{BOT_NAME}</b>\n"
        f"Advanced video & music downloader with premium features!\n\n"
        f"⚡ <b>Supported Platforms:</b>\n"
        f"  • 🎬 YouTube (8K/4K/HD/SD)\n"
        f"  • 📸 Instagram (Reels/Posts/Stories)\n"
        f"  • 🎵 SoundCloud, Bandcamp, Mixcloud\n"
        f"  • 📺 Vimeo, Facebook, X (Twitter), TikTok\n"
        f"  • And many more! (Over 1000+ sites supported)\n\n"
        f"🚀 <b>How to use:</b>\n"
        f"  • Send me any supported media link to download.\n"
        f"  • Use /settings to customize your download preferences.\n"
        f"  • Use /music &lt;query&gt; to search and download music.\n"
        f"  • Use /playlist &lt;url&gt; to download entire playlists.\n\n"
        f"⚙️ <b>Default Settings:</b>\n"
        f"  • Video Quality: Best available\n"
        f"  • Audio Format: MP3 (320kbps)\n"
        f"  • Embed Thumbnail: Yes\n"
        f"  • Embed Subtitles: No\n\n"
        f"Enjoy fast and high-quality downloads!\n\n"
        f"{POWERED_BY} | {CHANNEL}"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

# ── /settings ───────────────────────────────────────────────
async def cmd_settings(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    settings = user_settings[user_id]

    keyboard = [
        [InlineKeyboardButton(f"Video Quality: {settings['quality']}", callback_data="set_quality")],
        [InlineKeyboardButton(f"Audio Format: {settings['audio_format']}", callback_data="set_audio_format")],
        [InlineKeyboardButton(f"Audio Bitrate: {settings['audio_bitrate']}kbps", callback_data="set_audio_bitrate")],
        [InlineKeyboardButton(f"Embed Thumbnail: {'✅' if settings['embed_thumb'] else '❌'}", callback_data="toggle_embed_thumb")],
        [InlineKeyboardButton(f"Embed Subtitles: {'✅' if settings['embed_subs'] else '❌'}", callback_data="toggle_embed_subs")],
        [InlineKeyboardButton("Close", callback_data="close_settings")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "⚙️ <b>Your Settings:</b>\n\n" +
        "Choose an option to change your download preferences.",
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML
    )

async def cb_set_quality(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    current_quality = user_settings[user_id]["quality"]

    qualities = ["best", "8k", "4k", "1440p", "1080p", "720p", "480p", "audio"]
    current_idx = qualities.index(current_quality) if current_quality in qualities else 0
    next_idx = (current_idx + 1) % len(qualities)
    new_quality = qualities[next_idx]
    user_settings[user_id]["quality"] = new_quality

    await query.answer(f"Video Quality set to {new_quality}")
    await cmd_settings(update, ctx) # Refresh settings message

async def cb_set_audio_format(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    current_format = user_settings[user_id]["audio_format"]

    formats = ["mp3", "m4a", "flac", "opus", "wav"]
    current_idx = formats.index(current_format) if current_format in formats else 0
    next_idx = (current_idx + 1) % len(formats)
    new_format = formats[next_idx]
    user_settings[user_id]["audio_format"] = new_format

    await query.answer(f"Audio Format set to {new_format}")
    await cmd_settings(update, ctx) # Refresh settings message

async def cb_set_audio_bitrate(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    current_bitrate = user_settings[user_id]["audio_bitrate"]

    bitrates = ["320", "256", "192", "128", "96"]
    current_idx = bitrates.index(current_bitrate) if current_bitrate in bitrates else 0
    next_idx = (current_idx + 1) % len(bitrates)
    new_bitrate = bitrates[next_idx]
    user_settings[user_id]["audio_bitrate"] = new_bitrate

    await query.answer(f"Audio Bitrate set to {new_bitrate}kbps")
    await cmd_settings(update, ctx) # Refresh settings message

async def cb_toggle_embed_thumb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    user_settings[user_id]["embed_thumb"] = not user_settings[user_id]["embed_thumb"]

    status = "Enabled" if user_settings[user_id]["embed_thumb"] else "Disabled"
    await query.answer(f"Embed Thumbnail {status}")
    await cmd_settings(update, ctx) # Refresh settings message

async def cb_toggle_embed_subs(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    user_settings[user_id]["embed_subs"] = not user_settings[user_id]["embed_subs"]

    status = "Enabled" if user_settings[user_id]["embed_subs"] else "Disabled"
    await query.answer(f"Embed Subtitles {status}")
    await cmd_settings(update, ctx) # Refresh settings message

async def cb_close_settings(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.message.delete()

# ── /download ───────────────────────────────────────────────
async def cmd_download(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    url = ctx.args[0] if ctx.args else None
    if not url:
        await update.message.reply_text("Please provide a URL to download. Usage: /download &lt;url&gt;")
        return
    await handle_url_download(update, ctx, url)

# ── /music ──────────────────────────────────────────────────
async def cmd_music(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query_text = " ".join(ctx.args)
    if not query_text:
        await update.message.reply_text("Please provide a search query. Usage: /music &lt;song name&gt;")
        return

    msg = await update.message.reply_text(f"🎵 Searching for '{query_text}'...")
    results = await search_music(query_text)

    if not results:
        await safe_edit(msg, f"❌ No music found for '{query_text}'.\n\n{POWERED_BY}")
        return

    music_search_results[msg.message_id] = results

    keyboard = []
    for i, res in enumerate(results):
        keyboard.append([InlineKeyboardButton(f"{i+1}. {res['title']} ({res['duration']}) - {res['channel']}", callback_data=f"select_music_{i}")])
    keyboard.append([InlineKeyboardButton("Close", callback_data="close_settings")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    await safe_edit(msg, f"🎵 <b>Search Results for '{query_text}':</b>\n\n" +
                         "Select a song to download:",
                    reply_markup=reply_markup)

async def cb_select_music(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data.split('_')
    msg_id = query.message.message_id
    index = int(data[2])

    results = music_search_results.get(msg_id)
    if not results or index >= len(results):
        await query.answer("Search results expired or invalid selection.")
        await query.message.delete()
        return

    selected_song = results[index]
    url = selected_song["url"]
    title = selected_song["title"]

    await query.answer(f"Downloading {title}...")
    await safe_edit(query.message, f"📥 Downloading <b>{title}</b>...\n\n{POWERED_BY}")
    await handle_url_download(update, ctx, url, is_audio_only=True)
    del music_search_results[msg_id] # Clean up

# ── /playlist ───────────────────────────────────────────────
async def cmd_playlist(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    url = ctx.args[0] if ctx.args else None
    if not url:
        await update.message.reply_text("Please provide a playlist URL. Usage: /playlist &lt;url&gt;")
        return

    msg = await update.message.reply_text(f"Processing playlist from {url}...")
    await handle_playlist_download(update, ctx, url, msg)

# ── Core Download Logic ─────────────────────────────────────
async def handle_url_download(update: Update, ctx: ContextTypes.DEFAULT_TYPE, url: str, is_audio_only: bool = False):
    user_id = update.effective_user.id
    settings = user_settings[user_id]
    task_id = str(uuid.uuid4())
    task_dir = DOWNLOAD_DIR / task_id
    task_dir.mkdir(parents=True, exist_ok=True)

    log.info(f"[{user_id}] Starting download for {url} in {task_dir}")

    message_to_edit = None
    if not is_audio_only:
        message_to_edit = await update.message.reply_text(
            f"🔍 Fetching info for {url}...\n\n{POWERED_BY}",
            parse_mode=ParseMode.HTML
        )
    else:
        message_to_edit = update.callback_query.message if update.callback_query else await update.message.reply_text(
            f"🔍 Fetching info for {url}...\n\n{POWERED_BY}",
            parse_mode=ParseMode.HTML
        )

    try:
        info = await extract_info(url, user_id)
        if not info:
            await safe_edit(message_to_edit, f"❌ Could not fetch info for {url}. Invalid URL or unsupported site.\n\n{POWERED_BY}")
            return

        title = info.get("title", "Unknown Title")
        duration = fmt_duration(info.get("duration"))
        uploader = info.get("uploader", info.get("channel", "Unknown Uploader"))
        thumbnail_url = info.get("thumbnail")

        if is_audio_only or settings["quality"] == "audio":
            await safe_edit(message_to_edit, f"🎵 Downloading audio: <b>{title}</b> ({duration}) from {uploader}\n" +
                                         f"Format: {settings['audio_format']} @ {settings['audio_bitrate']}kbps\n\n{POWERED_BY}")
            progress_data = {"pct": 0, "downloaded": 0, "total": 0, "speed": 0, "status": "downloading"}
            download_task = asyncio.create_task(
                asyncio.to_thread(
                    _download_audio_sync,
                    url,
                    settings["audio_format"],
                    settings["audio_bitrate"],
                    task_dir,
                    user_id,
                    progress_data
                )
            )
            await monitor_download_progress(message_to_edit, progress_data, title, is_audio_only=True)
            await download_task
        else:
            format_sel = build_format_selector(settings["quality"])
            await safe_edit(message_to_edit, f"🎬 Downloading video: <b>{title}</b> ({duration}) from {uploader}\n" +
                                         f"Quality: {settings['quality']}\n\n{POWERED_BY}")
            progress_data = {"pct": 0, "downloaded": 0, "total": 0, "speed": 0, "status": "downloading"}
            download_task = asyncio.create_task(
                asyncio.to_thread(
                    _download_video_sync,
                    url,
                    format_sel,
                    task_dir,
                    user_id,
                    progress_data
                )
            )
            await monitor_download_progress(message_to_edit, progress_data, title)
            await download_task

        output_file = find_output_file(task_dir)
        if not output_file or not output_file.exists():
            await safe_edit(message_to_edit, f"❌ Download failed for {title}. No output file found.\n\n{POWERED_BY}")
            return

        file_size = output_file.stat().st_size
        if file_size > MAX_TG_FILE:
            await safe_edit(message_to_edit, f"⚠️ File size ({fmt_size(file_size)}) exceeds Telegram's {fmt_size(MAX_TG_FILE)} limit. Cannot upload.\n\n{POWERED_BY}")
            return

        await safe_edit(message_to_edit, f"📤 Uploading <b>{title}</b> ({fmt_size(file_size)})...\n\n{POWERED_BY}")
        await ctx.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_DOCUMENT)

        with open(output_file, "rb") as f:
            if is_audio_only or settings["quality"] == "audio":
                await ctx.bot.send_audio(
                    chat_id=update.effective_chat.id,
                    audio=InputFile(f),
                    caption=f"🎵 <b>{title}</b>\n<i>{uploader}</i>\n\n{POWERED_BY}",
                    parse_mode=ParseMode.HTML,
                    duration=int(info.get("duration", 0)),
                    performer=uploader,
                    title=title,
                    thumbnail=InputFile(thumbnail_url) if settings["embed_thumb"] and thumbnail_url else None
                )
            else:
                await ctx.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=InputFile(f),
                    caption=f"🎬 <b>{title}</b>\n<i>{uploader}</i>\n\n{POWERED_BY}",
                    parse_mode=ParseMode.HTML,
                    thumbnail=InputFile(thumbnail_url) if settings["embed_thumb"] and thumbnail_url else None
                )
        await message_to_edit.delete()
        log.info(f"[{user_id}] Successfully uploaded {title}")

    except Exception as e:
        log.error(f"[{user_id}] Download/Upload error for {url}: {e}", exc_info=True)
        await safe_edit(message_to_edit, f"❌ An error occurred during download or upload: {str(e)[:200]}\n\n{POWERED_BY}")
    finally:
        shutil.rmtree(task_dir, ignore_errors=True)

async def monitor_download_progress(message, progress_data, title, is_audio_only=False):
    last_edit_time = time.time()
    while progress_data["pct"] < 100 and progress_data["status"] == "downloading":
        if time.time() - last_edit_time > 3:
            pct = progress_data["pct"]
            downloaded = progress_data["downloaded"]
            total = progress_data["total"]
            speed = progress_data["speed"]

            status_text = "🎵 Downloading audio" if is_audio_only else "🎬 Downloading video"
            text = (
                f"{status_text}: <b>{title}</b>\n"
                f"{progress_bar(pct)} {pct:.1f}%\n"
                f"Size: {fmt_size(downloaded)} / {fmt_size(total)}\n"
                f"Speed: {fmt_size(speed)}/s\n\n{POWERED_BY}"
            )
            await safe_edit(message, text)
            last_edit_time = time.time()
        await asyncio.sleep(1)
    # Final update to 100% or finished status
    if progress_data["status"] == "finished":
        status_text = "🎵 Downloaded audio" if is_audio_only else "🎬 Downloaded video"
        text = (
            f"{status_text}: <b>{title}</b>\n"
            f"{progress_bar(100)} 100.0%\n"
            f"Size: {fmt_size(progress_data['total'])} / {fmt_size(progress_data['total'])}\n"
            f"Speed: {fmt_size(progress_data['speed'])}/s\n\n{POWERED_BY}"
        )
        await safe_edit(message, text)

async def handle_playlist_download(update: Update, ctx: ContextTypes.DEFAULT_TYPE, url: str, message_to_edit):
    user_id = update.effective_user.id
    settings = user_settings[user_id]

    try:
        await safe_edit(message_to_edit, f"🔍 Fetching playlist info for {url}...\n\n{POWERED_BY}")
        info = await extract_info(url, user_id)

        if not info or "entries" not in info:
            await safe_edit(message_to_edit, f"❌ Could not fetch playlist info for {url}. Invalid URL or unsupported site.\n\n{POWERED_BY}")
            return

        playlist_title = info.get("title", "Unknown Playlist")
        entries = info.get("entries", [])

        await safe_edit(message_to_edit, f"▶️ Starting playlist download: <b>{playlist_title}</b> ({len(entries)} items)\n\n{POWERED_BY}")

        for i, entry in enumerate(entries):
            if not entry:
                continue

            video_url = entry.get("url") or entry.get("webpage_url") or f"https://www.youtube.com/watch?v={entry.get('id', '')}"
            video_title = entry.get("title", "Unknown Video")

            await safe_edit(message_to_edit,
                f"▶️ Downloading playlist: <b>{playlist_title}</b>\n"
                f"Item {i+1}/{len(entries)}: <b>{video_title}</b>\n\n{POWERED_BY}"
            )

            task_id = str(uuid.uuid4())
            task_dir = DOWNLOAD_DIR / task_id
            task_dir.mkdir(parents=True, exist_ok=True)

            try:
                if settings["quality"] == "audio":
                    progress_data = {"pct": 0, "downloaded": 0, "total": 0, "speed": 0, "status": "downloading"}
                    download_task = asyncio.create_task(
                        asyncio.to_thread(
                            _download_audio_sync,
                            video_url,
                            settings["audio_format"],
                            settings["audio_bitrate"],
                            task_dir,
                            user_id,
                            progress_data
                        )
                    )
                    await monitor_download_progress(message_to_edit, progress_data, video_title, is_audio_only=True)
                    await download_task
                else:
                    format_sel = build_format_selector(settings["quality"])
                    progress_data = {"pct": 0, "downloaded": 0, "total": 0, "speed": 0, "status": "downloading"}
                    download_task = asyncio.create_task(
                        asyncio.to_thread(
                            _download_video_sync,
                            video_url,
                            format_sel,
                            task_dir,
                            user_id,
                            progress_data
                        )
                    )
                    await monitor_download_progress(message_to_edit, progress_data, video_title)
                    await download_task

                output_file = find_output_file(task_dir)
                if output_file and output_file.exists():
                    file_size = output_file.stat().st_size
                    if file_size <= MAX_TG_FILE:
                        await ctx.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_DOCUMENT)
                        with open(output_file, "rb") as f:
                            if settings["quality"] == "audio":
                                await ctx.bot.send_audio(
                                    chat_id=update.effective_chat.id,
                                    audio=InputFile(f),
                                    caption=f"🎵 <b>{video_title}</b>\n<i>{entry.get('uploader', 'Unknown')}</i>\n\n{POWERED_BY}",
                                    parse_mode=ParseMode.HTML,
                                    duration=int(entry.get("duration", 0)),
                                    performer=entry.get("uploader", "Unknown"),
                                    title=video_title,
                                    thumbnail=InputFile(entry.get("thumbnail")) if settings["embed_thumb"] and entry.get("thumbnail") else None
                                )
                            else:
                                await ctx.bot.send_document(
                                    chat_id=update.effective_chat.id,
                                    document=InputFile(f),
                                    caption=f"🎬 <b>{video_title}</b>\n<i>{entry.get('uploader', 'Unknown')}</i>\n\n{POWERED_BY}",
                                    parse_mode=ParseMode.HTML,
                                    thumbnail=InputFile(entry.get("thumbnail")) if settings["embed_thumb"] and entry.get("thumbnail") else None
                                )
                    else:
                        await ctx.bot.send_message(chat_id=update.effective_chat.id,
                                                   text=f"⚠️ File size for {video_title} ({fmt_size(file_size)}) exceeds Telegram's {fmt_size(MAX_TG_FILE)} limit. Cannot upload.")
                else:
                    await ctx.bot.send_message(chat_id=update.effective_chat.id,
                                               text=f"❌ Download failed for {video_title}. No output file found.")
            except Exception as e:
                log.error(f"Playlist item {i} error: {e}")
            finally:
                shutil.rmtree(task_dir, ignore_errors=True)

        await safe_edit(message_to_edit,
            f"✅ <b>Playlist complete!</b> ({len(entries)} items)\n\n{POWERED_BY}")
    except Exception as e:
        await safe_edit(message_to_edit, f"❌ Error: {str(e)[:200]}\n\n{POWERED_BY}")
    return

# ── Auto-detect URLs or music search in messages ──────────
async def handle_text_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type in ["group", "supergroup"] and not update.message.text.startswith("/music"):
        return
    
    user_id = update.effective_user.id
    text = update.message.text
    urls = URL_REGEX.findall(text)

    if urls:
        for url in urls:
            # For now, just handle the first URL found
            await handle_url_download(update, ctx, url)
            break
    else:
        # If no URL, treat as music search in private chats
        if update.effective_chat.type == "private":
            await cmd_music(update, ctx, args=[text])

# ── Error handler ───────────────────────────────────────────
async def error_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Exception while handling an update:", exc_info=ctx.error)
    try:
        if update.effective_message:
            await update.effective_message.reply_text(
                f"❌ An error occurred: {ctx.error}\n\n{POWERED_BY}",
                parse_mode=ParseMode.HTML
            )
    except Exception as e:
        log.error(f"Error sending error message: {e}")

# ── Main ────────────────────────────────────────────────────
def main():
    log.info("Bot starting...")
    application = Application.builder().token(BOT_TOKEN).build()

    # Handlers
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("settings", cmd_settings))
    application.add_handler(CommandHandler("download", cmd_download))
    application.add_handler(CommandHandler("music", cmd_music))
    application.add_handler(CommandHandler("playlist", cmd_playlist))

    application.add_handler(CallbackQueryHandler(cb_set_quality, pattern="^set_quality$"))
    application.add_handler(CallbackQueryHandler(cb_set_audio_format, pattern="^set_audio_format$"))
    application.add_handler(CallbackQueryHandler(cb_set_audio_bitrate, pattern="^set_audio_bitrate$"))
    application.add_handler(CallbackQueryHandler(cb_toggle_embed_thumb, pattern="^toggle_embed_thumb$"))
    application.add_handler(CallbackQueryHandler(cb_toggle_embed_subs, pattern="^toggle_embed_subs$"))
    application.add_handler(CallbackQueryHandler(cb_close_settings, pattern="^close_settings$"))
    application.add_handler(CallbackQueryHandler(cb_select_music, pattern="^select_music_\d+$"))

    # Message handler for URLs and music search
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    # Error handler
    application.add_error_handler(error_handler)

    # Run the bot until the user presses Ctrl-C
    log.info("Bot connected and polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
