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
        "extractor_args": {"youtube": {"player_client": ["web", "android"]}},
        "http_headers": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
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
    media_exts = {'.mp4', '.mkv', '.webm', '.mp3', '.m4a', '.flac', '.opus', '.ogg', '.wav', '.aac', '.avi', '.mov'}
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
        f"  • 🎵 TikTok (No Watermark)\n"
        f"  • 📘 Facebook (Videos/Reels)\n"
        f"  • 📌 Pinterest (Videos)\n"
        f"  • 🐦 X / Twitter\n"
        f"  • 🎮 Twitch, Vimeo, Reddit\n"
        f"  • 🔊 SoundCloud, Dailymotion\n"
        f"  • And <b>1000+ more platforms!</b>\n\n"
        f"🚀 <b>Premium Features:</b>\n"
        f"  • 📹 8K & 4K Ultra HD downloads\n"
        f"  • 🎵 Music search — just type a song name!\n"
        f"  • Ultra-fast downloads with live progress\n"
        f"  • Smart quality selection with buttons\n"
        f"  • Audio extraction (MP3/FLAC/Opus)\n"
        f"  • Playlist & channel support\n"
        f"  • Subtitle download & embedding\n"
        f"  • Smart fallback for large files\n\n"
        f"🔗 <b>How to use:</b>\n"
        f"📎 Send any video link — get quality options\n"
        f"🎵 Type any song/music name — search & download!\n"
        f"📹 Use /download, /audio, /video commands\n\n"
        f"💬 Support: {CREATOR}\n"
        f"📢 Channel: {CHANNEL}\n\n"
        f"{POWERED_BY}"
    )
    start_buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Subscribe Our Community", url="https://t.me/SPEED_AI_COMMUNITY")],
        [InlineKeyboardButton("👨‍💻 Creator: @SPEED_prime", url="https://t.me/SPEED_prime")],
    ])
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=start_buttons)

# ── /help ───────────────────────────────────────────────────
async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "📚 <b>Available Commands</b>\n\n"
        "<code>/download &lt;url&gt; [quality]</code> — Download media\n"
        "  <i>quality: 8k, 4k, 1080p, 720p, 480p, audio</i>\n\n"
        "<code>/music &lt;song name&gt;</code> — Search & download music\n\n"
        "<code>/info &lt;url&gt;</code> — Get detailed format info\n\n"
        "<code>/audio &lt;url&gt; [bitrate]</code> — Audio only\n"
        "  <i>bitrate: 128, 192, 320, flac, opus</i>\n\n"
        "<code>/video &lt;url&gt; [resolution]</code> — Video download\n"
        "  <i>resolution: 8k, 4k, 1080p, 720p, 480p</i>\n\n"
        "<code>/playlist &lt;url&gt;</code> — Download playlist\n\n"
        "<code>/subs &lt;url&gt; [lang]</code> — Download subtitles\n\n"
        "<code>/settings</code> — View/change preferences\n\n"
        "<code>/ping</code> — Check bot latency\n\n"
        "🎵 <b>Music Search:</b>\n"
        "Just type any song name without a command!\n"
        "Example: <code>Blinding Lights The Weeknd</code>\n\n"
        f"Bot creator {CREATOR}\n{POWERED_BY}"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

# ── /ping ───────────────────────────────────────────────────
async def cmd_ping(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    start = time.time()
    msg = await update.message.reply_text("🏓 Pinging...")
    ms = (time.time() - start) * 1000
    await msg.edit_text(
        f"🏓 <b>Pong!</b> <code>{ms:.0f}ms</code>\n\n"
        f"Bot creator {CREATOR}\n{POWERED_BY}",
        parse_mode=ParseMode.HTML
    )

# ── /settings ───────────────────────────────────────────────
async def cmd_settings(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    s = user_settings[uid]
    text = (
        "⚙️ <b>Your Settings</b>\n\n"
        f"🎬 Default Quality: <code>{s['quality']}</code>\n"
        f"🎵 Audio Format: <code>{s['audio_format'].upper()}</code>\n"
        f"🔊 Audio Bitrate: <code>{s['audio_bitrate']}kbps</code>\n"
        f"📝 Embed Subtitles: <code>{'Yes' if s['embed_subs'] else 'No'}</code>\n"
        f"🖼 Embed Thumbnail: <code>{'Yes' if s['embed_thumb'] else 'No'}</code>\n\n"
        f"{POWERED_BY}"
    )
    buttons = [
        [
            InlineKeyboardButton("🎬 Quality", callback_data="set_quality"),
            InlineKeyboardButton("🎵 Audio Fmt", callback_data="set_audiofmt"),
        ],
        [
            InlineKeyboardButton("🔊 Bitrate", callback_data="set_bitrate"),
            InlineKeyboardButton("📝 Subs: " + ("✅" if s['embed_subs'] else "❌"), callback_data="set_toggle_subs"),
        ],
        [
            InlineKeyboardButton("🖼 Thumb: " + ("✅" if s['embed_thumb'] else "❌"), callback_data="set_toggle_thumb"),
        ],
    ]
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons))


# ── /info ───────────────────────────────────────────────────
async def cmd_info(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    if not args:
        await update.message.reply_text(
            f"❌ Usage: <code>/info &lt;url&gt;</code>\n\n{POWERED_BY}",
            parse_mode=ParseMode.HTML
        )
        return
    url = args[0]
    msg = await update.message.reply_text("🔍 <b>Analyzing link...</b>", parse_mode=ParseMode.HTML)
    try:
        info = await extract_info(url, update.effective_user.id)
    except Exception as e:
        await safe_edit(msg, f"❌ <b>Error:</b> Could not extract info.\n<code>{str(e)[:200]}</code>\n\n{POWERED_BY}")
        return

    title = info.get("title", "Unknown")
    duration = fmt_duration(info.get("duration"))
    formats = info.get("formats") or []

    resolutions = {}
    for f in formats:
        h = f.get("height")
        if not h or f.get("vcodec") in (None, "none"):
            continue
        label = f"{h}p"
        size = f.get("filesize") or f.get("filesize_approx") or 0
        if label not in resolutions or size > resolutions[label]:
            resolutions[label] = size

    lines = [
        f"📋 <b>Media Information</b>\n",
        f"📌 Title: <b>{title}</b>",
        f"⏱ Duration: <code>{duration}</code>",
        f"🌐 Platform: <code>{info.get('extractor', 'Unknown')}</code>\n",
        f"📦 <b>Available Formats:</b>",
    ]
    for label in sorted(resolutions.keys(), key=lambda x: int(x.replace('p', '')), reverse=True):
        lines.append(f"  • <code>{label}</code> — {fmt_size(resolutions[label])}")

    lines.append(f"  • 🎵 Audio only available")
    lines.append(f"\n{POWERED_BY}")

    pending_urls[f"info_{msg.message_id}"] = url

    buttons = [
        [
            InlineKeyboardButton("📹 8K Ultra", callback_data=f"q_8k|{msg.message_id}"),
            InlineKeyboardButton("📹 4K Ultra", callback_data=f"q_4k|{msg.message_id}"),
        ],
        [
            InlineKeyboardButton("🖥 1080p", callback_data=f"q_1080p|{msg.message_id}"),
            InlineKeyboardButton("🖥 720p", callback_data=f"q_720p|{msg.message_id}"),
        ],
        [
            InlineKeyboardButton("📉 480p Saver", callback_data=f"q_480p|{msg.message_id}"),
            InlineKeyboardButton("🎵 Audio MP3", callback_data=f"q_audio|{msg.message_id}"),
        ],
    ]
    await safe_edit(msg, "\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons))

# ── /subs ───────────────────────────────────────────────────
async def cmd_subs(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    if not args:
        await update.message.reply_text(f"❌ Usage: <code>/subs &lt;url&gt; [lang]</code>\n\n{POWERED_BY}", parse_mode=ParseMode.HTML)
        return
    url = args[0]
    lang = args[1] if len(args) > 1 else None

    msg = await update.message.reply_text("🔍 <b>Checking subtitles...</b>", parse_mode=ParseMode.HTML)
    try:
        info = await extract_info(url, update.effective_user.id)
    except Exception as e:
        await safe_edit(msg, f"❌ Error: {str(e)[:200]}\n\n{POWERED_BY}")
        return

    subs = info.get("subtitles") or {}
    auto_subs = info.get("automatic_captions") or {}
    all_subs = {**auto_subs, **subs}

    if not all_subs:
        await safe_edit(msg, f"📭 No subtitles available for this video.\n\n{POWERED_BY}")
        return

    if not lang:
        langs = list(all_subs.keys())[:20]
        text = "📝 <b>Available Subtitle Languages:</b>\n\n"
        text += ", ".join(f"<code>{l}</code>" for l in langs)
        text += f"\n\nUse: <code>/subs {url} en</code>\n\n{POWERED_BY}"
        await safe_edit(msg, text)
        return

    task_dir = DOWNLOAD_DIR / str(uuid.uuid4())[:8]
    task_dir.mkdir(parents=True, exist_ok=True)
    try:
        opts = {
            "quiet": True,
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": [lang],
            "subtitlesformat": "srt",
            "outtmpl": str(task_dir / "%(title).60s.%(ext)s"),
            "ffmpeg_location": FFMPEG_PATH,
        }
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: yt_dlp.YoutubeDL(opts).download([url]))

        srt_files = list(task_dir.glob("*.srt")) + list(task_dir.glob("*.vtt"))
        if srt_files:
            for sf in srt_files:
                with open(sf, "rb") as fh:
                    await update.message.reply_document(
                        document=fh, filename=sf.name,
                        caption=f"📝 Subtitles ({lang})\n\n{POWERED_BY}",
                        parse_mode=ParseMode.HTML
                    )
            await safe_edit(msg, f"✅ Subtitles delivered!\n\n{POWERED_BY}")
        else:
            await safe_edit(msg, f"❌ Could not download subtitles for language: {lang}\n\n{POWERED_BY}")
    except Exception as e:
        await safe_edit(msg, f"❌ Error: {str(e)[:200]}\n\n{POWERED_BY}")
    finally:
        shutil.rmtree(task_dir, ignore_errors=True)

# ── /music — Music search command ──────────────────────────
async def cmd_music(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = " ".join(ctx.args) if ctx.args else ""
    if not query:
        await update.message.reply_text(
            f"🎵 <b>Music Search</b>\n\n"
            f"Usage: <code>/music &lt;song name&gt;</code>\n"
            f"Example: <code>/music Blinding Lights</code>\n\n"
            f"Or just type any song name directly!\n\n{POWERED_BY}",
            parse_mode=ParseMode.HTML
        )
        return
    await do_music_search(update, query)

async def do_music_search(update, query):
    """Search for music and show results with download buttons."""
    msg = await update.message.reply_text(
        f"🔍 <b>Searching:</b> <code>{query}</code>\n\n"
        f"🎵 Finding best results...\n\n{POWERED_BY}",
        parse_mode=ParseMode.HTML
    )

    try:
        results = await search_music(query, max_results=5)
    except Exception as e:
        await safe_edit(msg, f"❌ Search failed: {str(e)[:200]}\n\n{POWERED_BY}")
        return

    if not results:
        await safe_edit(msg, f"📭 No results found for: <code>{query}</code>\n\n{POWERED_BY}")
        return

    # Store results
    music_search_results[str(msg.message_id)] = results

    text = f"🎵 <b>Music Search Results</b>\n"
    text += f"🔍 Query: <code>{query}</code>\n\n"

    buttons = []
    for i, r in enumerate(results):
        text += f"<b>{i+1}.</b> {r['title']}\n"
        text += f"    👤 {r['channel']}  |  ⏱ {r['duration']}\n\n"
        buttons.append([
            InlineKeyboardButton(
                f"🎵 {i+1}. Audio (MP3)",
                callback_data=f"ms_audio_{i}|{msg.message_id}"
            ),
            InlineKeyboardButton(
                f"🎬 {i+1}. Video",
                callback_data=f"ms_video_{i}|{msg.message_id}"
            ),
        ])

    buttons.append([
        InlineKeyboardButton("📥 Download All as MP3", callback_data=f"ms_all|{msg.message_id}")
    ])

    text += f"\n{POWERED_BY}"
    await safe_edit(msg, text, reply_markup=InlineKeyboardMarkup(buttons))

# ── Core download workflow ──────────────────────────────────
async def do_download(bot, chat_id, user_id, url, quality, status_msg):
    """Execute the full download + send workflow."""
    is_audio = (quality == "audio")
    task_id = str(uuid.uuid4())[:8]
    task_dir = DOWNLOAD_DIR / task_id
    task_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Step 1: Get info
        await safe_edit(status_msg, f"🔍 <b>Analyzing...</b>\n\n{POWERED_BY}")
        try:
            info = await extract_info(url, user_id)
        except Exception as e:
            err = str(e)
            await safe_edit(status_msg,
                    f"❌ <b>Error:</b> Could not analyze URL.\n"
                    f"<code>{err[:300]}</code>\n\n{POWERED_BY}")
            return

        title = (info.get("title") or "download")[:60]
        duration = fmt_duration(info.get("duration"))

        # Step 2: Download with progress
        progress_data = {"pct": 0, "downloaded": 0, "total": 0, "speed": 0, "status": "downloading"}
        format_sel = build_format_selector(quality)

        await safe_edit(status_msg,
            f"⬇️ <b>Downloading:</b> {title}\n"
            f"🎯 Quality: <code>{quality}</code>\n\n"
            f"[□□□□□□□□□□] 0%\n\n{POWERED_BY}")

        # Progress updater task
        stop_progress = asyncio.Event()

        async def update_progress():
            last_text = ""
            while not stop_progress.is_set():
                await asyncio.sleep(2.5)
                if stop_progress.is_set():
                    break
                pct = progress_data.get("pct", 0)
                dl = progress_data.get("downloaded", 0)
                total = progress_data.get("total", 0)
                speed = progress_data.get("speed", 0)
                bar = progress_bar(pct)
                speed_str = fmt_size(speed) + "/s" if speed else "..."
                new_text = (
                    f"⬇️ <b>Downloading:</b> {title}\n"
                    f"🎯 Quality: <code>{quality}</code>\n\n"
                    f"[{bar}] {pct:.0f}%\n"
                    f"📦 {fmt_size(dl)} / {fmt_size(total)}  |  ⚡ {speed_str}\n\n{POWERED_BY}"
                )
                if new_text != last_text:
                    await safe_edit(status_msg, new_text)
                    last_text = new_text

        progress_task = asyncio.create_task(update_progress())

        # Run download
        loop = asyncio.get_event_loop()
        try:
            if is_audio:
                s = user_settings[user_id]
                result = await loop.run_in_executor(
                    None, _download_audio_sync,
                    url, s["audio_format"], s["audio_bitrate"],
                    task_dir, user_id, progress_data
                )
            else:
                result = await loop.run_in_executor(
                    None, _download_video_sync,
                    url, format_sel, task_dir, user_id, progress_data
                )
        except Exception as e:
            stop_progress.set()
            await progress_task
            err = str(e)
            if "unavailable" in err.lower() or "format" in err.lower():
                await safe_edit(status_msg,
                    f"⚠️ <b>Requested format not available.</b>\n"
                    f"Trying best alternative...\n\n{POWERED_BY}")
                try:
                    result = await loop.run_in_executor(
                        None, _download_video_sync,
                        url, "best", task_dir, user_id, progress_data
                    )
                except Exception as e2:
                    await safe_edit(status_msg,
                        f"❌ <b>Download failed</b>\n<code>{str(e2)[:300]}</code>\n\n{POWERED_BY}")
                    return
            else:
                await safe_edit(status_msg,
                    f"❌ <b>Download failed</b>\n<code>{err[:300]}</code>\n\n{POWERED_BY}")
                return

        stop_progress.set()
        await progress_task

        # Step 3: Processing
        await safe_edit(status_msg,
            f"⚙ <b>Processing & Optimizing for Telegram...</b>\n"
            f"  • Merging streams\n"
            f"  • Embedding metadata\n"
            f"  • Finalizing...\n\n{POWERED_BY}")

        await asyncio.sleep(1)

        # Find output file
        output_file = find_output_file(task_dir)
        if not output_file:
            await safe_edit(status_msg,
                f"❌ <b>Download completed but no output file found.</b>\n"
                f"🔧 Try a different quality.\n\n{POWERED_BY}")
            return

        file_size = output_file.stat().st_size

        # Smart fallback for oversized files
        if file_size > MAX_TG_FILE:
            await safe_edit(status_msg,
                f"📦 <b>File too large ({fmt_size(file_size)})</b>\n"
                f"Auto-fallback to lower quality...\n\n{POWERED_BY}")

            for fb_h in FALLBACK_HEIGHTS:
                shutil.rmtree(task_dir, ignore_errors=True)
                task_dir.mkdir(parents=True, exist_ok=True)
                fb_fmt = f"bestvideo[height<={fb_h}]+bestaudio/best[height<={fb_h}]/best"
                try:
                    await loop.run_in_executor(
                        None, _download_video_sync,
                        url, fb_fmt, task_dir, user_id, None
                    )
                    output_file = find_output_file(task_dir)
                    if output_file and output_file.stat().st_size <= MAX_TG_FILE:
                        file_size = output_file.stat().st_size
                        quality = f"{fb_h}p"
                        break
                except:
                    continue
            else:
                await safe_edit(status_msg,
                    f"❌ <b>File still too large even at lowest quality.</b>\n"
                    f"Cannot send via Telegram (2GB limit).\n\n{POWERED_BY}")
                return

        # Step 4: Upload to Telegram
        await safe_edit(status_msg,
            f"📤 <b>Uploading to Telegram...</b>\n"
            f"📂 {output_file.name}\n"
            f"📦 Size: {fmt_size(file_size)}\n\n{POWERED_BY}")

        try:
            await bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_DOCUMENT)
        except:
            pass

        caption = (
            f"✅ <b>Download Complete!</b>\n\n"
            f"📌 {title}\n"
            f"🎯 Quality: <code>{quality}</code>\n"
            f"📦 Size: <code>{fmt_size(file_size)}</code>\n"
            f"⏱ Duration: <code>{duration}</code>\n\n"
            f"{POWERED_BY}"
        )

        delivery_buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Subscribe Our Community", url="https://t.me/SPEED_AI_COMMUNITY")],
            [InlineKeyboardButton("👨‍💻 Creator: @SPEED_prime", url="https://t.me/SPEED_prime")],
        ])

        upload_start = time.time()
        with open(output_file, "rb") as fh:
            ext = output_file.suffix.lower()
            if is_audio or ext in ('.mp3', '.m4a', '.flac', '.opus', '.ogg', '.aac', '.wav'):
                await bot.send_audio(
                    chat_id=chat_id, audio=fh,
                    title=title, caption=caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=delivery_buttons,
                    read_timeout=600, write_timeout=600,
                )
            elif file_size < 50_000_000 and ext in ('.mp4', '.mkv', '.webm', '.mov'):
                try:
                    await bot.send_video(
                        chat_id=chat_id, video=fh,
                        caption=caption, parse_mode=ParseMode.HTML,
                        supports_streaming=True,
                        reply_markup=delivery_buttons,
                        read_timeout=600, write_timeout=600,
                    )
                except Exception:
                    fh.seek(0)
                    await bot.send_document(
                        chat_id=chat_id, document=fh,
                        filename=output_file.name, caption=caption,
                        parse_mode=ParseMode.HTML,
                        reply_markup=delivery_buttons,
                        read_timeout=600, write_timeout=600,
                    )
            else:
                await bot.send_document(
                    chat_id=chat_id, document=fh,
                    filename=output_file.name, caption=caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=delivery_buttons,
                    read_timeout=600, write_timeout=600,
                )

        upload_time = time.time() - upload_start
        completion_buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Subscribe Our Community", url="https://t.me/SPEED_AI_COMMUNITY")],
            [InlineKeyboardButton("👨‍💻 Creator: @SPEED_prime", url="https://t.me/SPEED_prime")],
        ])
        await safe_edit(status_msg,
            f"✅ <b>Task Completed!</b>\n"
            f"📂 {output_file.name}\n"
            f"📦 {fmt_size(file_size)} | ⏱ Upload: {upload_time:.1f}s\n\n{POWERED_BY}",
            reply_markup=completion_buttons)

    except Exception as e:
        log.error(f"Download error: {traceback.format_exc()}")
        await safe_edit(status_msg,
            f"❌ <b>Something went wrong</b>\n"
            f"<code>{str(e)[:300]}</code>\n\n"
            f"🔧 Try a different URL or quality.\n\n{POWERED_BY}")
    finally:
        shutil.rmtree(task_dir, ignore_errors=True)

# ── Show quality buttons ────────────────────────────────────
async def show_quality_buttons(update, url):
    """Show quality selection buttons for a URL."""
    msg = await update.message.reply_text(
        "🔍 <b>Analyzing link...</b>", parse_mode=ParseMode.HTML
    )

    try:
        info = await extract_info(url, update.effective_user.id)
    except Exception as e:
        err = str(e)
        await safe_edit(msg,
                f"❌ <b>Error:</b> Could not analyze URL.\n"
                f"<code>{err[:300]}</code>\n\n{POWERED_BY}")
        return

    title = (info.get("title") or "Unknown")[:60]
    duration = fmt_duration(info.get("duration"))

    # Check available resolutions
    formats = info.get("formats") or []
    max_height = 0
    for f in formats:
        h = f.get("height") or 0
        if h > max_height:
            max_height = h

    # Store URL keyed by message ID
    pending_urls[str(msg.message_id)] = url

    preview = (
        f"📋 <b>Preview</b>\n\n"
        f"📌 Title: <b>{title}</b>\n"
        f"⏱ Duration: <code>{duration}</code>\n"
        f"🌐 Platform: <code>{info.get('extractor', 'Unknown')}</code>\n"
        f"📹 Max Resolution: <code>{max_height}p</code>\n\n"
        f"🎯 <b>Choose quality:</b>"
    )

    # Build quality buttons - always show all options
    buttons = [
        [
            InlineKeyboardButton("📹 8K Ultra", callback_data=f"q_8k|{msg.message_id}"),
            InlineKeyboardButton("📹 4K Ultra", callback_data=f"q_4k|{msg.message_id}"),
        ],
        [
            InlineKeyboardButton("🖥 1080p", callback_data=f"q_1080p|{msg.message_id}"),
            InlineKeyboardButton("🖥 720p", callback_data=f"q_720p|{msg.message_id}"),
        ],
        [
            InlineKeyboardButton("📉 480p Saver", callback_data=f"q_480p|{msg.message_id}"),
            InlineKeyboardButton("🎵 Audio MP3", callback_data=f"q_audio|{msg.message_id}"),
        ],
    ]

    await safe_edit(msg, preview, reply_markup=InlineKeyboardMarkup(buttons))

# ── /download ───────────────────────────────────────────────
async def cmd_download(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    if not args:
        await update.message.reply_text(
            f"❌ Usage: <code>/download &lt;url&gt; [quality]</code>\n\n"
            f"Quality options: best, 8k, 4k, 1080p, 720p, 480p, audio\n\n{POWERED_BY}",
            parse_mode=ParseMode.HTML
        )
        return
    url = args[0]
    quality = args[1].lower() if len(args) > 1 else None

    if quality:
        msg = await update.message.reply_text(
            f"⬇️ <b>Starting download...</b>\n\n{POWERED_BY}",
            parse_mode=ParseMode.HTML
        )
        await do_download(ctx.bot, update.effective_chat.id, update.effective_user.id, url, quality, msg)
    else:
        await show_quality_buttons(update, url)

# ── /video ──────────────────────────────────────────────────
async def cmd_video(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    if not args:
        await update.message.reply_text(
            f"❌ Usage: <code>/video &lt;url&gt; [resolution]</code>\n"
            f"Resolutions: 8k, 4k, 1080p, 720p, 480p\n\n{POWERED_BY}",
            parse_mode=ParseMode.HTML
        )
        return
    url = args[0]
    quality = args[1].lower() if len(args) > 1 else "1080p"
    msg = await update.message.reply_text(
        f"⬇️ <b>Starting video download...</b>\n\n{POWERED_BY}",
        parse_mode=ParseMode.HTML
    )
    await do_download(ctx.bot, update.effective_chat.id, update.effective_user.id, url, quality, msg)

# ── /audio ──────────────────────────────────────────────────
async def cmd_audio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    if not args:
        await update.message.reply_text(
            f"❌ Usage: <code>/audio &lt;url&gt; [bitrate]</code>\n\n"
            f"Bitrate: 128, 192, 320, flac, opus\n\n{POWERED_BY}",
            parse_mode=ParseMode.HTML
        )
        return
    url = args[0]
    uid = update.effective_user.id
    if len(args) > 1:
        br = args[1].lower()
        if br in ("flac", "opus"):
            user_settings[uid]["audio_format"] = br
            user_settings[uid]["audio_bitrate"] = "0"
        else:
            user_settings[uid]["audio_bitrate"] = br.replace("kbps", "")
    msg = await update.message.reply_text(
        f"🎵 <b>Starting audio download...</b>\n\n{POWERED_BY}",
        parse_mode=ParseMode.HTML
    )
    await do_download(ctx.bot, update.effective_chat.id, uid, url, "audio", msg)

# ── /playlist ───────────────────────────────────────────────
async def cmd_playlist(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    if not args:
        await update.message.reply_text(
            f"❌ Usage: <code>/playlist &lt;url&gt;</code>\n\n{POWERED_BY}",
            parse_mode=ParseMode.HTML
        )
        return
    url = args[0]
    msg = await update.message.reply_text("🔍 <b>Analyzing playlist...</b>", parse_mode=ParseMode.HTML)

    try:
        opts = {
            "quiet": True, "no_warnings": True,
            "extract_flat": True, "skip_download": True,
            "ffmpeg_location": FFMPEG_PATH,
        }
        loop = asyncio.get_event_loop()
        info = await loop.run_in_executor(
            None, lambda: yt_dlp.YoutubeDL(opts).extract_info(url, download=False)
        )
    except Exception as e:
        await safe_edit(msg, f"❌ Error: {str(e)[:200]}\n\n{POWERED_BY}")
        return

    entries = list(info.get("entries") or [])
    count = len(entries)

    if count == 0:
        await safe_edit(msg, f"📭 No downloadable items found in playlist.\n\n{POWERED_BY}")
        return

    pending_urls[f"pl_{msg.message_id}"] = url

    buttons = [
        [InlineKeyboardButton(f"📥 Download All ({count})", callback_data=f"pl_all|{msg.message_id}")],
        [InlineKeyboardButton("📥 First 5", callback_data=f"pl_5|{msg.message_id}")],
        [InlineKeyboardButton("📥 First 10", callback_data=f"pl_10|{msg.message_id}")],
    ]

    await safe_edit(msg,
        f"📑 <b>Playlist: {info.get('title', 'Unknown')}</b>\n\n"
        f"📦 Total items: <b>{count}</b>\n\n"
        f"Choose how many to download:",
        reply_markup=InlineKeyboardMarkup(buttons))

# ── Callback handler ────────────────────────────────────────
async def callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    uid = query.from_user.id
    chat_id = query.message.chat_id
    bot = ctx.bot

    # Quality selection: q_QUALITY|msg_id
    if data.startswith("q_"):
        parts = data.split("|", 1)
        quality = parts[0].replace("q_", "")
        msg_id = parts[1] if len(parts) > 1 else ""

        url = pending_urls.get(msg_id) or pending_urls.get(f"info_{msg_id}")
        if not url:
            await safe_edit(query.message, f"❌ Session expired. Please send the link again.\n\n{POWERED_BY}")
            return

        await safe_edit(query.message,
            f"⬇️ <b>Starting download...</b>\n"
            f"🎯 Quality: <code>{quality}</code>\n\n{POWERED_BY}")

        await do_download(bot, chat_id, uid, url, quality, query.message)
        return

    # Music search results: ms_audio_N|msg_id or ms_video_N|msg_id or ms_all|msg_id
    if data.startswith("ms_"):
        parts = data.split("|", 1)
        action = parts[0]
        msg_id = parts[1] if len(parts) > 1 else ""

        results = music_search_results.get(msg_id)
        if not results:
            await safe_edit(query.message, f"❌ Session expired. Please search again.\n\n{POWERED_BY}")
            return

        if action == "ms_all":
            # Download all as audio
            await safe_edit(query.message, f"🎵 <b>Downloading all {len(results)} tracks as MP3...</b>\n\n{POWERED_BY}")
            for i, r in enumerate(results):
                try:
                    task_dir = DOWNLOAD_DIR / str(uuid.uuid4())[:8]
                    task_dir.mkdir(parents=True, exist_ok=True)
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(
                        None, _download_audio_sync,
                        r["url"], "mp3", "320", task_dir, uid, None
                    )
                    output_file = find_output_file(task_dir)
                    if output_file:
                        caption = f"🎵 [{i+1}/{len(results)}] <b>{r['title']}</b>\n👤 {r['channel']}\n\n{POWERED_BY}"
                        dl_buttons = InlineKeyboardMarkup([
                            [InlineKeyboardButton("📢 Subscribe Our Community", url="https://t.me/SPEED_AI_COMMUNITY")],
                        ])
                        with open(output_file, "rb") as fh:
                            await bot.send_audio(
                                chat_id=chat_id, audio=fh,
                                title=r["title"], caption=caption,
                                parse_mode=ParseMode.HTML,
                                reply_markup=dl_buttons,
                                read_timeout=600, write_timeout=600,
                            )
                    shutil.rmtree(task_dir, ignore_errors=True)
                except Exception as e:
                    log.error(f"Music download error: {e}")
                    shutil.rmtree(task_dir, ignore_errors=True)

            await safe_edit(query.message, f"✅ <b>All {len(results)} tracks downloaded!</b>\n\n{POWERED_BY}")
            return

        # Single track: ms_audio_N or ms_video_N
        try:
            idx = int(action.split("_")[-1])
            is_audio_dl = "audio" in action
        except:
            return

        if idx >= len(results):
            return

        r = results[idx]
        quality = "audio" if is_audio_dl else "best"
        await safe_edit(query.message,
            f"⬇️ <b>Downloading:</b> {r['title']}\n"
            f"👤 {r['channel']}  |  🎯 {quality}\n\n{POWERED_BY}")

        await do_download(bot, chat_id, uid, r["url"], quality, query.message)
        return

    # Settings toggles
    if data == "set_toggle_subs":
        user_settings[uid]["embed_subs"] = not user_settings[uid]["embed_subs"]
        await query.edit_message_text(
            f"✅ Subtitles embedding: <b>{'ON' if user_settings[uid]['embed_subs'] else 'OFF'}</b>\n\n{POWERED_BY}",
            parse_mode=ParseMode.HTML)
        return
    if data == "set_toggle_thumb":
        user_settings[uid]["embed_thumb"] = not user_settings[uid]["embed_thumb"]
        await query.edit_message_text(
            f"✅ Thumbnail embedding: <b>{'ON' if user_settings[uid]['embed_thumb'] else 'OFF'}</b>\n\n{POWERED_BY}",
            parse_mode=ParseMode.HTML)
        return
    if data == "set_quality":
        buttons = [
            [InlineKeyboardButton("📹 8K Ultra", callback_data="sq_8k"),
             InlineKeyboardButton("📹 4K Ultra", callback_data="sq_4k")],
            [InlineKeyboardButton("🖥 1080p", callback_data="sq_1080p"),
             InlineKeyboardButton("🖥 720p", callback_data="sq_720p")],
            [InlineKeyboardButton("📉 480p Saver", callback_data="sq_480p"),
             InlineKeyboardButton("🎵 Audio MP3", callback_data="sq_audio")],
        ]
        await query.edit_message_text("🎬 <b>Choose default quality:</b>",
            parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons))
        return
    if data.startswith("sq_"):
        q = data.replace("sq_", "")
        user_settings[uid]["quality"] = q
        await query.edit_message_text(f"✅ Default quality set to: <b>{q}</b>\n\n{POWERED_BY}",
            parse_mode=ParseMode.HTML)
        return
    if data == "set_audiofmt":
        buttons = [
            [InlineKeyboardButton("MP3", callback_data="saf_mp3"),
             InlineKeyboardButton("M4A", callback_data="saf_m4a")],
            [InlineKeyboardButton("FLAC", callback_data="saf_flac"),
             InlineKeyboardButton("Opus", callback_data="saf_opus")],
        ]
        await query.edit_message_text("🎵 <b>Choose audio format:</b>",
            parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons))
        return
    if data.startswith("saf_"):
        fmt = data.replace("saf_", "")
        user_settings[uid]["audio_format"] = fmt
        await query.edit_message_text(f"✅ Audio format set to: <b>{fmt.upper()}</b>\n\n{POWERED_BY}",
            parse_mode=ParseMode.HTML)
        return
    if data == "set_bitrate":
        buttons = [
            [InlineKeyboardButton("128kbps", callback_data="sbr_128"),
             InlineKeyboardButton("192kbps", callback_data="sbr_192")],
            [InlineKeyboardButton("320kbps", callback_data="sbr_320")],
        ]
        await query.edit_message_text("🔊 <b>Choose audio bitrate:</b>",
            parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons))
        return
    if data.startswith("sbr_"):
        br = data.replace("sbr_", "")
        user_settings[uid]["audio_bitrate"] = br
        await query.edit_message_text(f"✅ Audio bitrate set to: <b>{br}kbps</b>\n\n{POWERED_BY}",
            parse_mode=ParseMode.HTML)
        return

    # Playlist download
    if data.startswith("pl_"):
        parts = data.split("|", 1)
        mode = parts[0].replace("pl_", "")
        msg_id = parts[1] if len(parts) > 1 else ""
        url = pending_urls.get(f"pl_{msg_id}")
        if not url:
            await safe_edit(query.message, f"❌ Session expired. Please send the link again.\n\n{POWERED_BY}")
            return

        limit = None if mode == "all" else int(mode)
        await safe_edit(query.message, f"📥 <b>Starting playlist download...</b>\n\n{POWERED_BY}")

        try:
            opts = {"quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True, "ffmpeg_location": FFMPEG_PATH}
            loop = asyncio.get_event_loop()
            info = await loop.run_in_executor(None, lambda: yt_dlp.YoutubeDL(opts).extract_info(url, download=False))
            entries = list(info.get("entries") or [])
            if limit:
                entries = entries[:limit]

            for i, entry in enumerate(entries, 1):
                entry_url = entry.get("url") or entry.get("webpage_url")
                if not entry_url:
                    continue
                entry_title = (entry.get("title") or f"Track {i}")[:40]
                await safe_edit(query.message,
                    f"📥 <b>Playlist Progress:</b> {i}/{len(entries)}\n"
                    f"🎵 {entry_title}\n\n{POWERED_BY}")

                task_dir = DOWNLOAD_DIR / str(uuid.uuid4())[:8]
                task_dir.mkdir(parents=True, exist_ok=True)
                try:
                    await loop.run_in_executor(
                        None, _download_video_sync,
                        entry_url, "bestvideo+bestaudio/best", task_dir, uid, None
                    )
                    output_file = find_output_file(task_dir)
                    if output_file and output_file.stat().st_size <= MAX_TG_FILE:
                        caption = f"📥 [{i}/{len(entries)}] <b>{entry_title}</b>\n\n{POWERED_BY}"
                        pl_buttons = InlineKeyboardMarkup([
                            [InlineKeyboardButton("📢 Subscribe Our Community", url="https://t.me/SPEED_AI_COMMUNITY")],
                            [InlineKeyboardButton("👨‍💻 Creator: @SPEED_prime", url="https://t.me/SPEED_prime")],
                        ])
                        with open(output_file, "rb") as fh:
                            await bot.send_document(
                                chat_id=chat_id, document=fh,
                                filename=output_file.name, caption=caption,
                                parse_mode=ParseMode.HTML,
                                reply_markup=pl_buttons,
                                read_timeout=600, write_timeout=600,
                            )
                except Exception as e:
                    log.error(f"Playlist item {i} error: {e}")
                finally:
                    shutil.rmtree(task_dir, ignore_errors=True)

            await safe_edit(query.message,
                f"✅ <b>Playlist complete!</b> ({len(entries)} items)\n\n{POWERED_BY}")
        except Exception as e:
            await safe_edit(query.message, f"❌ Error: {str(e)[:200]}\n\n{POWERED_BY}")
        return

# ── Auto-detect URLs or music search in messages ──────────
async def handle_text_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # In groups, only respond to URLs, not music search
    if update.effective_chat.type in ("group", "supergroup"):
        text = update.message.text or ""
        urls = URL_REGEX.findall(text)
        if urls:
            url = urls[0]
            await show_quality_buttons(update, url)
        return
    text = update.message.text or ""
    urls = URL_REGEX.findall(text)
    if urls:
        # It's a URL — show quality buttons
        url = urls[0]
        await show_quality_buttons(update, url)
    else:
        # Not a URL — treat as music search
        query = text.strip()
        if len(query) >= 2:
            await do_music_search(update, query)


# ── Main ────────────────────────────────────────────────────
def main():
    log.info("Starting SPEED DOWNLOADER bot...")

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .read_timeout(60)
        .write_timeout(60)
        .connect_timeout(30)
        .pool_timeout(30)
        .build()
    )

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("ping", cmd_ping))
    app.add_handler(CommandHandler("settings", cmd_settings))
    app.add_handler(CommandHandler("download", cmd_download))
    app.add_handler(CommandHandler("video", cmd_video))
    app.add_handler(CommandHandler("audio", cmd_audio))
    app.add_handler(CommandHandler("playlist", cmd_playlist))
    app.add_handler(CommandHandler("info", cmd_info))
    app.add_handler(CommandHandler("subs", cmd_subs))
    app.add_handler(CommandHandler("music", cmd_music))

    # Callbacks (buttons)
    app.add_handler(CallbackQueryHandler(callback_handler))

    # Auto-detect URLs or music search in plain messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    log.info("Bot connected and polling! All systems ready.")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
