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
import subprocess
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
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8043941196:AAEOiKxtEqUoyDkwHX8plH1R3OW4SrLFa-U")
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

# Store edit sessions
edit_sessions = {}  # user_id -> {"action": ..., "waiting_for": ..., "params": ...}

# Store video info for background song button
pending_video_info = {}  # msg_id -> {"url": ..., "info": ...}

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
        "socket_timeout": 120,
        "retries": 10,
        "fragment_retries": 10,
        "file_access_retries": 10,
        "extractor_retries": 10,
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
    media_exts = {'.mp4', '.mkv', '.webm', '.mp3', '.m4a', '.flac', '.opus', '.ogg', '.wav', '.aac', '.avi', '.mov', '.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'}
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
        "<code>/edit</code> — Edit videos (reply to video with /edit)\n\n"
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

    if not info:
        await safe_edit(msg,
            f"❌ <b>Error:</b> Could not extract info from this URL.\n"
            f"The platform may require login or the link is invalid.\n\n{POWERED_BY}")
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
        [
            InlineKeyboardButton("🖼 Photo/Thumbnail", callback_data=f"q_photo|{msg.message_id}"),
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
    is_photo = (quality == "photo")
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

        if not info:
            await safe_edit(status_msg,
                f"❌ <b>Error:</b> Could not extract info from this URL.\n"
                f"The platform may require login or the link is invalid.\n\n{POWERED_BY}")
            return

        title = (info.get("title") or "download")[:60]
        duration = fmt_duration(info.get("duration"))

        # Photo download: get thumbnail/image
        if is_photo:
            thumb_url = info.get("thumbnail") or info.get("thumbnails", [{}])[-1].get("url")
            if not thumb_url:
                await safe_edit(status_msg, f"❌ <b>No photo/thumbnail found for this URL.</b>\n\n{POWERED_BY}")
                return
            await safe_edit(status_msg, f"📥 <b>Downloading photo...</b>\n\n{POWERED_BY}")
            import urllib.request
            photo_path = task_dir / f"{title}.jpg"
            try:
                req = urllib.request.Request(thumb_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=30) as resp, open(photo_path, "wb") as out:
                    out.write(resp.read())
            except Exception as e:
                await safe_edit(status_msg, f"❌ <b>Failed to download photo:</b> <code>{str(e)[:200]}</code>\n\n{POWERED_BY}")
                return
            caption = (
                f"🖼 <b>Photo Downloaded!</b>\n\n"
                f"📌 {title}\n\n"
                f"{POWERED_BY}"
            )
            delivery_buttons = InlineKeyboardMarkup([
                [InlineKeyboardButton("📢 Subscribe Our Community", url="https://t.me/SPEED_AI_COMMUNITY")],
                [InlineKeyboardButton("👨\u200d💻 Creator: @SPEED_prime", url="https://t.me/SPEED_prime")],
            ])
            with open(photo_path, "rb") as fh:
                await bot.send_photo(
                    chat_id=chat_id, photo=fh,
                    caption=caption, parse_mode=ParseMode.HTML,
                    reply_markup=delivery_buttons,
                )
            await safe_edit(status_msg, f"✅ <b>Photo sent!</b>\n\n{POWERED_BY}")
            shutil.rmtree(task_dir, ignore_errors=True)
            return

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
                    read_timeout=1200, write_timeout=1200,
                )
            elif ext in ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'):
                await bot.send_photo(
                    chat_id=chat_id, photo=fh,
                    caption=caption, parse_mode=ParseMode.HTML,
                    read_timeout=1200, write_timeout=1200,
                )
            elif file_size < MAX_TG_FILE and ext in ('.mp4', '.mkv', '.webm', '.mov'):
                try:
                    await bot.send_video(
                        chat_id=chat_id, video=fh,
                        caption=caption, parse_mode=ParseMode.HTML,
                        supports_streaming=True,
                        reply_markup=delivery_buttons,
                        read_timeout=1200, write_timeout=1200,               )
                except Exception:
                    fh.seek(0)
                    await bot.send_document(
                        chat_id=chat_id, document=fh,
                        filename=output_file.name, caption=caption,
                        parse_mode=ParseMode.HTML,
                        reply_markup=delivery_buttons,
                        read_timeout=1200, write_timeout=1200,
                    )
            else:
                await bot.send_document(
                    chat_id=chat_id, document=fh,
                    filename=output_file.name, caption=caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=delivery_buttons,
                    read_timeout=1200, write_timeout=1200,
                )

        upload_time = time.time() - upload_start

        # After video download, check for background music and offer download button
        if not is_audio and not is_photo and ext in ('.mp4', '.mkv', '.webm', '.mov', '.avi'):
            track_name = info.get("track") or info.get("alt_title") or ""
            artist_name = info.get("artist") or info.get("creator") or ""
            album_name = info.get("album") or ""

            # Build search query from metadata
            if track_name and artist_name:
                music_query = f"{artist_name} - {track_name}"
            elif track_name:
                music_query = track_name
            elif artist_name:
                music_query = artist_name
            else:
                music_query = ""

            if music_query:
                # Store info for later when user clicks the button
                song_key = f"song_{status_msg.message_id}"
                pending_video_info[song_key] = {
                    "url": url,
                    "query": music_query,
                    "track": track_name,
                    "artist": artist_name,
                    "album": album_name,
                    "thumbnail": info.get("thumbnail") or "",
                }
                song_label = f"{artist_name} - {track_name}" if artist_name and track_name else music_query
                completion_buttons = InlineKeyboardMarkup([
                    [InlineKeyboardButton(f"🎵 Download Song: {song_label[:45]}", callback_data=f"findsong|{song_key}")],
                    [InlineKeyboardButton("📢 Subscribe Our Community", url="https://t.me/SPEED_AI_COMMUNITY")],
                    [InlineKeyboardButton("👨‍💻 Creator: @SPEED_prime", url="https://t.me/SPEED_prime")],
                ])
            else:
                completion_buttons = InlineKeyboardMarkup([
                    [InlineKeyboardButton("📢 Subscribe Our Community", url="https://t.me/SPEED_AI_COMMUNITY")],
                    [InlineKeyboardButton("👨‍💻 Creator: @SPEED_prime", url="https://t.me/SPEED_prime")],
                ])
        else:
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

    if not info:
        await safe_edit(msg,
            f"❌ <b>Error:</b> Could not extract info from this URL.\n"
            f"The platform may require login or the link is invalid.\n\n{POWERED_BY}")
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
        [
            InlineKeyboardButton("🖼 Photo/Thumbnail", callback_data=f"q_photo|{msg.message_id}"),
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
                                read_timeout=1200, write_timeout=1200,
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

    # Video edit callbacks
    if data.startswith("edit_"):
        action = data.replace("edit_", "")
        session = edit_sessions.get(uid)
        if not session:
            await safe_edit(query.message, f"❌ No edit session. Reply to a video with /edit first.\n\n{POWERED_BY}")
            return

        session["action"] = action

        # Actions that need user input
        if action == "trim":
            session["waiting_for"] = "trim_times"
            await safe_edit(query.message,
                "✂️ <b>Trim Video</b>\n\n"
                "Send start and end time separated by space:\n"
                "<code>00:00:10 00:00:30</code>\n"
                "or in seconds: <code>10 30</code>\n\n" + POWERED_BY)
            return

        elif action == "speed":
            session["waiting_for"] = "speed_value"
            await safe_edit(query.message,
                "⏩ <b>Change Speed</b>\n\n"
                "Send speed value (0.25 to 4.0):\n"
                "<code>0.5</code> = slow-mo\n"
                "<code>1.0</code> = normal\n"
                "<code>2.0</code> = 2x fast\n\n" + POWERED_BY)
            return

        elif action == "text":
            session["waiting_for"] = "text_content"
            await safe_edit(query.message,
                "📝 <b>Add Text / Watermark</b>\n\n"
                "Send the text you want to add on the video:\n\n" + POWERED_BY)
            return

        elif action == "music":
            session["waiting_for"] = "music_file"
            await safe_edit(query.message,
                "🎵 <b>Add Background Music</b>\n\n"
                "Send an audio file (MP3, M4A, etc.) to add as background music:\n\n" + POWERED_BY)
            return

        elif action == "resize":
            session["waiting_for"] = "resize_value"
            await safe_edit(query.message,
                "📐 <b>Resize Video</b>\n\n"
                "Send the new size:\n"
                "<code>1920x1080</code> (Full HD)\n"
                "<code>1280x720</code> (HD)\n"
                "<code>640x360</code> (SD)\n\n" + POWERED_BY)
            return

        elif action == "convert":
            buttons = [
                [InlineKeyboardButton("MP4", callback_data="editfmt_mp4"),
                 InlineKeyboardButton("MKV", callback_data="editfmt_mkv")],
                [InlineKeyboardButton("AVI", callback_data="editfmt_avi"),
                 InlineKeyboardButton("WEBM", callback_data="editfmt_webm")],
                [InlineKeyboardButton("MOV", callback_data="editfmt_mov")],
            ]
            await safe_edit(query.message, "🔄 <b>Choose output format:</b>\n\n" + POWERED_BY,
                reply_markup=InlineKeyboardMarkup(buttons))
            return

        elif action == "filter":
            buttons = [
                [InlineKeyboardButton("🖤 Grayscale", callback_data="editflt_grayscale"),
                 InlineKeyboardButton("🌫 Blur", callback_data="editflt_blur")],
                [InlineKeyboardButton("🔪 Sharpen", callback_data="editflt_sharpen"),
                 InlineKeyboardButton("☀️ Bright", callback_data="editflt_bright")],
                [InlineKeyboardButton("🌗 Contrast", callback_data="editflt_contrast"),
                 InlineKeyboardButton("📼 Vintage", callback_data="editflt_vintage")],
                [InlineKeyboardButton("🔃 Negative", callback_data="editflt_negative"),
                 InlineKeyboardButton("🪞 Mirror", callback_data="editflt_mirror")],
                [InlineKeyboardButton("↕️ Flip", callback_data="editflt_flip")],
            ]
            await safe_edit(query.message, "🎨 <b>Choose a filter:</b>\n\n" + POWERED_BY,
                reply_markup=InlineKeyboardMarkup(buttons))
            return

        # Actions that process immediately (no extra input needed)
        elif action in ("mute", "compress", "gif", "reverse", "screenshot"):
            status = query.message
            await safe_edit(status, f"⚙️ <b>Processing: {EDIT_FEATURES.get(action, action)}...</b>\n\n{POWERED_BY}")
            await process_edit(bot, chat_id, uid, session, status)
            return

        elif action == "merge":
            await safe_edit(query.message,
                "🔗 <b>Merge Videos</b>\n\n"
                "To merge videos, send all videos first, then reply to each one with /edit and choose an action.\n"
                "Merge feature coming soon!\n\n" + POWERED_BY)
            return

        return

    # Edit format selection
    if data.startswith("editfmt_"):
        fmt = data.replace("editfmt_", "")
        session = edit_sessions.get(uid)
        if not session:
            await safe_edit(query.message, f"❌ No edit session.\n\n{POWERED_BY}")
            return
        session["params"]["format"] = fmt
        session["waiting_for"] = None
        await safe_edit(query.message, f"🔄 <b>Converting to {fmt.upper()}...</b>\n\n{POWERED_BY}")
        await process_edit(bot, chat_id, uid, session, query.message)
        return

    # Edit filter selection
    if data.startswith("editflt_"):
        filt = data.replace("editflt_", "")
        session = edit_sessions.get(uid)
        if not session:
            await safe_edit(query.message, f"❌ No edit session.\n\n{POWERED_BY}")
            return
        session["params"]["filter"] = filt
        session["waiting_for"] = None
        await safe_edit(query.message, f"🎨 <b>Applying filter...</b>\n\n{POWERED_BY}")
        await process_edit(bot, chat_id, uid, session, query.message)
        return

    # Find song button clicked — search and show results
    if data.startswith("findsong|"):
        song_key = data.split("|", 1)[1]
        song_data = pending_video_info.get(song_key)
        if not song_data:
            await safe_edit(query.message, f"❌ Session expired. Please send the link again.\n\n{POWERED_BY}")
            return

        await safe_edit(query.message,
            f"🔍 <b>Searching for background music...</b>\n"
            f"🎵 {song_data['query']}\n\n{POWERED_BY}")

        try:
            music_results = await search_music(song_data['query'], max_results=5)
        except Exception:
            music_results = []

        if not music_results:
            await safe_edit(query.message,
                f"❌ <b>Could not find the song.</b>\n\n{POWERED_BY}")
            return

        # Store results for download callback
        bg_key = f"bg_{query.message.message_id}"
        music_search_results[bg_key] = music_results

        # Build song info text
        song_info = f"🎵 <b>Background Music Found!</b>\n\n"
        if song_data.get('artist'):
            song_info += f"🎤 Artist: <b>{song_data['artist']}</b>\n"
        if song_data.get('track'):
            song_info += f"🎶 Track: <b>{song_data['track']}</b>\n"
        if song_data.get('album'):
            song_info += f"💿 Album: <b>{song_data['album']}</b>\n"
        song_info += f"\n🔎 Found {len(music_results)} results:\n"
        for i, r in enumerate(music_results):
            song_info += f"  {i+1}. <b>{r['title']}</b> — {r['channel']} ({r['duration']})\n"

        # Build buttons
        buttons = []
        for i, r in enumerate(music_results):
            buttons.append([InlineKeyboardButton(
                f"🎵 {i+1}. {r['title'][:40]}",
                callback_data=f"bg_audio_{i}|{bg_key}"
            )])
        buttons.append([InlineKeyboardButton("📢 Subscribe Our Community", url="https://t.me/SPEED_AI_COMMUNITY")])
        buttons.append([InlineKeyboardButton("👨‍💻 Creator: @SPEED_prime", url="https://t.me/SPEED_prime")])

        # Send with cover art if available
        thumb_url = song_data.get('thumbnail', '')
        if thumb_url:
            try:
                await bot.send_photo(
                    chat_id=chat_id, photo=thumb_url,
                    caption=song_info, parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup(buttons),
                )
                await safe_edit(query.message,
                    f"✅ <b>Song results sent above!</b>\n\n{POWERED_BY}")
            except Exception:
                await safe_edit(query.message, song_info,
                    reply_markup=InlineKeyboardMarkup(buttons))
        else:
            await safe_edit(query.message, song_info,
                reply_markup=InlineKeyboardMarkup(buttons))
        return

    # Download background music from video
    if data.startswith("bg_audio_"):
        parts = data.split("|", 1)
        action = parts[0]
        bg_key = parts[1] if len(parts) > 1 else ""

        results = music_search_results.get(bg_key)
        if not results:
            await safe_edit(query.message, f"❌ Session expired. Please send the link again.\n\n{POWERED_BY}")
            return

        try:
            idx = int(action.replace("bg_audio_", ""))
        except:
            return

        if idx >= len(results):
            return

        r = results[idx]
        await safe_edit(query.message,
            f"🎵 <b>Downloading:</b> {r['title']}\n"
            f"🎤 {r['channel']}\n\n{POWERED_BY}")

        task_dir = DOWNLOAD_DIR / str(uuid.uuid4())[:8]
        task_dir.mkdir(parents=True, exist_ok=True)
        try:
            loop = asyncio.get_event_loop()
            s = user_settings[uid]
            await loop.run_in_executor(
                None, _download_audio_sync,
                r["url"], s["audio_format"], s["audio_bitrate"],
                task_dir, uid, None
            )
            output_file = find_output_file(task_dir)
            if output_file:
                file_size = output_file.stat().st_size
                caption = (
                    f"🎵 <b>Background Music Downloaded!</b>\n\n"
                    f"🎶 {r['title']}\n"
                    f"🎤 {r['channel']}\n"
                    f"📦 Size: <code>{fmt_size(file_size)}</code>\n\n"
                    f"{POWERED_BY}"
                )
                dl_buttons = InlineKeyboardMarkup([
                    [InlineKeyboardButton("📢 Subscribe Our Community", url="https://t.me/SPEED_AI_COMMUNITY")],
                    [InlineKeyboardButton("👨‍💻 Creator: @SPEED_prime", url="https://t.me/SPEED_prime")],
                ])
                with open(output_file, "rb") as fh:
                    await bot.send_audio(
                        chat_id=chat_id, audio=fh,
                        title=r['title'], caption=caption,
                        parse_mode=ParseMode.HTML,
                        reply_markup=dl_buttons,
                        read_timeout=1200, write_timeout=1200,
                    )
                await safe_edit(query.message,
                    f"✅ <b>Background music sent!</b>\n"
                    f"🎶 {r['title']} — {r['channel']}\n\n{POWERED_BY}")
            else:
                await safe_edit(query.message,
                    f"❌ <b>Could not download this track.</b>\n\n{POWERED_BY}")
        except Exception as e:
            log.error(f"BG music download error: {e}")
            await safe_edit(query.message,
                f"❌ <b>Download failed:</b> <code>{str(e)[:200]}</code>\n\n{POWERED_BY}")
        finally:
            shutil.rmtree(task_dir, ignore_errors=True)
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
                                read_timeout=1200, write_timeout=1200,
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
    # Check if user is in an edit session waiting for text input
    uid = update.effective_user.id
    if uid in edit_sessions and edit_sessions[uid].get("waiting_for"):
        handled = await handle_edit_text_input(update, ctx)
        if handled:
            return

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


# ── /edit command ───────────────────────────────────────────
EDIT_FEATURES = {
    "trim": "✂️ Trim / Cut Video",
    "speed": "⏩ Change Speed",
    "text": "📝 Add Text / Watermark",
    "music": "🎵 Add Background Music",
    "mute": "🔇 Remove Audio",
    "compress": "📦 Compress Video",
    "convert": "🔄 Convert Format",
    "resize": "📐 Resize / Crop",
    "filter": "🎨 Add Filters",
    "gif": "🎬 Create GIF",
    "reverse": "⏪ Reverse Video",
    "screenshot": "📸 Extract Screenshots",
    "merge": "🔗 Merge Videos",
}

async def cmd_edit(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Show video editing menu. User should reply to a video with /edit."""
    msg = update.message
    uid = update.effective_user.id

    # Check if replying to a video
    reply = msg.reply_to_message
    if not reply or not (reply.video or reply.document or reply.animation):
        await msg.reply_text(
            "🎬 <b>Video Editor</b>\n\n"
            "To edit a video, reply to a video message with /edit\n\n"
            "<b>Available features:</b>\n"
            "✂️ Trim / Cut\n"
            "⏩ Change Speed (slow-mo, fast)\n"
            "📝 Add Text / Watermark\n"
            "🎵 Add Background Music\n"
            "🔇 Remove Audio\n"
            "📦 Compress Video\n"
            "🔄 Convert Format\n"
            "📐 Resize / Crop\n"
            "🎨 Add Filters\n"
            "🎬 Create GIF\n"
            "⏪ Reverse Video\n"
            "📸 Extract Screenshots\n"
            "🔗 Merge Videos\n\n"
            f"{POWERED_BY}",
            parse_mode=ParseMode.HTML)
        return

    # Store the file_id for later processing
    if reply.video:
        file_id = reply.video.file_id
    elif reply.animation:
        file_id = reply.animation.file_id
    elif reply.document:
        file_id = reply.document.file_id
    else:
        await msg.reply_text("❌ Please reply to a video file.\n\n" + POWERED_BY, parse_mode=ParseMode.HTML)
        return

    edit_sessions[uid] = {"file_id": file_id, "action": None, "waiting_for": None, "params": {}}

    buttons = []
    row = []
    for i, (key, label) in enumerate(EDIT_FEATURES.items()):
        row.append(InlineKeyboardButton(label, callback_data=f"edit_{key}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    await msg.reply_text(
        "🎬 <b>Video Editor</b>\n\n"
        "Choose an editing feature:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons))


async def handle_edit_video_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle video/document messages when user is in an edit session waiting for input."""
    uid = update.effective_user.id
    session = edit_sessions.get(uid)
    if not session or session.get("waiting_for") != "music_file":
        return

    msg = update.message
    if msg.audio or msg.voice:
        file_id = (msg.audio or msg.voice).file_id
    elif msg.document:
        file_id = msg.document.file_id
    else:
        await msg.reply_text("❌ Please send an audio file.\n\n" + POWERED_BY, parse_mode=ParseMode.HTML)
        return

    session["params"]["music_file_id"] = file_id
    session["waiting_for"] = None

    status = await msg.reply_text("🎵 <b>Adding background music...</b>\n\n" + POWERED_BY, parse_mode=ParseMode.HTML)
    await process_edit(ctx.bot, update.effective_chat.id, uid, session, status)


async def handle_edit_text_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle text input for edit sessions (trim times, speed, text, etc)."""
    uid = update.effective_user.id
    session = edit_sessions.get(uid)
    if not session or not session.get("waiting_for"):
        return False  # Not in edit mode

    text = (update.message.text or "").strip()
    waiting = session["waiting_for"]

    if waiting == "trim_times":
        # Expected: "00:00:10 00:00:30" or "10 30"
        parts = text.split()
        if len(parts) != 2:
            await update.message.reply_text("❌ Send start and end time separated by space.\nExample: <code>00:00:10 00:00:30</code> or <code>10 30</code>\n\n" + POWERED_BY, parse_mode=ParseMode.HTML)
            return True
        session["params"]["start"] = parts[0]
        session["params"]["end"] = parts[1]
        session["waiting_for"] = None
        status = await update.message.reply_text("✂️ <b>Trimming video...</b>\n\n" + POWERED_BY, parse_mode=ParseMode.HTML)
        await process_edit(ctx.bot, update.effective_chat.id, uid, session, status)
        return True

    elif waiting == "speed_value":
        try:
            speed = float(text)
            if speed < 0.25 or speed > 4.0:
                raise ValueError
        except:
            await update.message.reply_text("❌ Send a speed between 0.25 and 4.0\nExample: <code>0.5</code> (slow-mo) or <code>2.0</code> (fast)\n\n" + POWERED_BY, parse_mode=ParseMode.HTML)
            return True
        session["params"]["speed"] = speed
        session["waiting_for"] = None
        status = await update.message.reply_text("⏩ <b>Changing video speed...</b>\n\n" + POWERED_BY, parse_mode=ParseMode.HTML)
        await process_edit(ctx.bot, update.effective_chat.id, uid, session, status)
        return True

    elif waiting == "text_content":
        session["params"]["text"] = text
        session["waiting_for"] = None
        status = await update.message.reply_text("📝 <b>Adding text overlay...</b>\n\n" + POWERED_BY, parse_mode=ParseMode.HTML)
        await process_edit(ctx.bot, update.effective_chat.id, uid, session, status)
        return True

    elif waiting == "resize_value":
        session["params"]["size"] = text
        session["waiting_for"] = None
        status = await update.message.reply_text("📐 <b>Resizing video...</b>\n\n" + POWERED_BY, parse_mode=ParseMode.HTML)
        await process_edit(ctx.bot, update.effective_chat.id, uid, session, status)
        return True

    return False


async def process_edit(bot, chat_id, uid, session, status_msg):
    """Process the video edit using FFmpeg."""
    action = session["action"]
    params = session["params"]
    file_id = session["file_id"]

    task_dir = DOWNLOAD_DIR / str(uuid.uuid4())[:8]
    task_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Download the video from Telegram
        await safe_edit(status_msg, f"📥 <b>Downloading video from Telegram...</b>\n\n{POWERED_BY}")
        tg_file = await bot.get_file(file_id)
        input_path = task_dir / "input_video.mp4"
        await tg_file.download_to_drive(str(input_path))

        output_path = task_dir / "output.mp4"
        ffmpeg = FFMPEG_PATH

        await safe_edit(status_msg, f"⚙️ <b>Processing: {EDIT_FEATURES.get(action, action)}...</b>\n\n{POWERED_BY}")

        loop = asyncio.get_event_loop()

        if action == "trim":
            start = params.get("start", "0")
            end = params.get("end", "10")
            cmd = [ffmpeg, "-y", "-i", str(input_path), "-ss", start, "-to", end, "-c", "copy", str(output_path)]

        elif action == "speed":
            speed = params.get("speed", 1.0)
            video_filter = f"setpts={1/speed}*PTS"
            audio_filter = f"atempo={speed}" if 0.5 <= speed <= 2.0 else f"atempo={min(2.0, speed)},atempo={speed/2.0}" if speed > 2.0 else f"atempo={max(0.5, speed)}"
            cmd = [ffmpeg, "-y", "-i", str(input_path), "-filter:v", video_filter, "-filter:a", audio_filter, str(output_path)]

        elif action == "text":
            text = params.get("text", "SPEED AI")
            # Escape special chars for FFmpeg drawtext
            text_escaped = text.replace("'", "\\'").replace(":", "\\:")
            cmd = [ffmpeg, "-y", "-i", str(input_path), "-vf",
                   f"drawtext=text='{text_escaped}':fontsize=36:fontcolor=white:borderw=2:bordercolor=black:x=(w-text_w)/2:y=h-th-20",
                   "-codec:a", "copy", str(output_path)]

        elif action == "music":
            music_file_id = params.get("music_file_id")
            if not music_file_id:
                await safe_edit(status_msg, f"❌ No audio file provided.\n\n{POWERED_BY}")
                return
            music_file = await bot.get_file(music_file_id)
            music_path = task_dir / "bg_music.mp3"
            await music_file.download_to_drive(str(music_path))
            cmd = [ffmpeg, "-y", "-i", str(input_path), "-i", str(music_path),
                   "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=first:dropout_transition=2",
                   "-c:v", "copy", str(output_path)]

        elif action == "mute":
            cmd = [ffmpeg, "-y", "-i", str(input_path), "-an", "-c:v", "copy", str(output_path)]

        elif action == "compress":
            cmd = [ffmpeg, "-y", "-i", str(input_path), "-vcodec", "libx264", "-crf", "28",
                   "-preset", "fast", "-acodec", "aac", "-b:a", "128k", str(output_path)]

        elif action == "convert":
            fmt = params.get("format", "mp4")
            output_path = task_dir / f"output.{fmt}"
            cmd = [ffmpeg, "-y", "-i", str(input_path), str(output_path)]

        elif action == "resize":
            size = params.get("size", "1280x720")
            cmd = [ffmpeg, "-y", "-i", str(input_path), "-vf", f"scale={size.replace('x', ':')}",
                   "-c:a", "copy", str(output_path)]

        elif action == "filter":
            filt = params.get("filter", "grayscale")
            filter_map = {
                "grayscale": "colorchannelmixer=.3:.4:.3:0:.3:.4:.3:0:.3:.4:.3",
                "blur": "boxblur=5:1",
                "sharpen": "unsharp=5:5:1.0:5:5:0.0",
                "bright": "eq=brightness=0.1",
                "contrast": "eq=contrast=1.5",
                "vintage": "curves=vintage",
                "negative": "negate",
                "mirror": "hflip",
                "flip": "vflip",
            }
            vf = filter_map.get(filt, filt)
            cmd = [ffmpeg, "-y", "-i", str(input_path), "-vf", vf, "-c:a", "copy", str(output_path)]

        elif action == "gif":
            output_path = task_dir / "output.gif"
            cmd = [ffmpeg, "-y", "-i", str(input_path), "-vf", "fps=15,scale=480:-1:flags=lanczos",
                   "-t", "10", str(output_path)]

        elif action == "reverse":
            cmd = [ffmpeg, "-y", "-i", str(input_path), "-vf", "reverse", "-af", "areverse", str(output_path)]

        elif action == "screenshot":
            output_path = task_dir / "frame_%03d.jpg"
            cmd = [ffmpeg, "-y", "-i", str(input_path), "-vf", "fps=1", "-frames:v", "5", str(output_path)]

        else:
            await safe_edit(status_msg, f"❌ Unknown edit action.\n\n{POWERED_BY}")
            return

        # Run FFmpeg
        result = await loop.run_in_executor(None, lambda: subprocess.run(
            cmd, capture_output=True, text=True, timeout=600
        ))

        if result.returncode != 0:
            err = result.stderr[:300] if result.stderr else "Unknown error"
            await safe_edit(status_msg, f"❌ <b>FFmpeg error:</b>\n<code>{err}</code>\n\n{POWERED_BY}")
            return

        # Send result
        if action == "screenshot":
            # Send all extracted frames
            frames = sorted(task_dir.glob("frame_*.jpg"))
            if not frames:
                await safe_edit(status_msg, f"❌ No frames extracted.\n\n{POWERED_BY}")
                return
            for frame in frames[:5]:
                with open(frame, "rb") as fh:
                    await bot.send_photo(chat_id=chat_id, photo=fh,
                        caption=f"📸 {frame.name}\n\n{POWERED_BY}", parse_mode=ParseMode.HTML)
            await safe_edit(status_msg, f"✅ <b>{len(frames)} screenshots extracted!</b>\n\n{POWERED_BY}")
        elif action == "gif":
            if output_path.exists() and output_path.stat().st_size > 0:
                with open(output_path, "rb") as fh:
                    await bot.send_animation(chat_id=chat_id, animation=fh,
                        caption=f"🎬 <b>GIF Created!</b>\n\n{POWERED_BY}", parse_mode=ParseMode.HTML,
                        read_timeout=1200, write_timeout=1200)
                await safe_edit(status_msg, f"✅ <b>GIF created and sent!</b>\n\n{POWERED_BY}")
            else:
                await safe_edit(status_msg, f"❌ Failed to create GIF.\n\n{POWERED_BY}")
        else:
            if output_path.exists() and output_path.stat().st_size > 0:
                file_size = output_path.stat().st_size
                caption = (
                    f"✅ <b>{EDIT_FEATURES.get(action, action)} Complete!</b>\n\n"
                    f"📦 Size: <code>{fmt_size(file_size)}</code>\n\n"
                    f"{POWERED_BY}"
                )
                edit_buttons = InlineKeyboardMarkup([
                    [InlineKeyboardButton("📢 Subscribe Our Community", url="https://t.me/SPEED_AI_COMMUNITY")],
                    [InlineKeyboardButton("👨‍💻 Creator: @SPEED_prime", url="https://t.me/SPEED_prime")],
                ])
                with open(output_path, "rb") as fh:
                    if file_size <= 50_000_000:
                        await bot.send_video(chat_id=chat_id, video=fh,
                            caption=caption, parse_mode=ParseMode.HTML,
                            reply_markup=edit_buttons,
                            read_timeout=1200, write_timeout=1200)
                    else:
                        await bot.send_document(chat_id=chat_id, document=fh,
                            filename=output_path.name, caption=caption,
                            parse_mode=ParseMode.HTML,
                            reply_markup=edit_buttons,
                            read_timeout=1200, write_timeout=1200)
                await safe_edit(status_msg, f"✅ <b>Edit complete!</b>\n\n{POWERED_BY}")
            else:
                await safe_edit(status_msg, f"❌ Processing failed. No output file.\n\n{POWERED_BY}")

    except Exception as e:
        log.error(f"Edit error: {traceback.format_exc()}")
        await safe_edit(status_msg, f"❌ <b>Edit failed:</b> <code>{str(e)[:300]}</code>\n\n{POWERED_BY}")
    finally:
        edit_sessions.pop(uid, None)
        shutil.rmtree(task_dir, ignore_errors=True)


# ── Register bot commands in Telegram menu ─────────────────
async def post_init(application):
    """Register all bot commands in Telegram menu so users see them when typing /"""
    commands = [
        BotCommand("start", "Start the bot and see welcome message"),
        BotCommand("help", "Show all available commands and usage"),
        BotCommand("download", "Download video/audio from any URL"),
        BotCommand("video", "Download video from URL"),
        BotCommand("audio", "Download audio from URL"),
        BotCommand("music", "Search and download music by name"),
        BotCommand("playlist", "Download entire playlist"),
        BotCommand("info", "Get media info from URL"),
        BotCommand("settings", "Customize your download preferences"),
        BotCommand("subs", "Download subtitles from URL"),
        BotCommand("ping", "Check if bot is online"),
        BotCommand("edit", "Edit videos (reply to a video with /edit)"),
    ]
    await application.bot.set_my_commands(commands)
    log.info("Bot commands registered in Telegram menu.")

# ── Main ────────────────────────────────────────────────────
def main():
    log.info("Starting SPEED DOWNLOADER bot...")

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .read_timeout(1200)
        .write_timeout(1200)
        .connect_timeout(60)
        .pool_timeout(60)
        .post_init(post_init)
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
    app.add_handler(CommandHandler("edit", cmd_edit))

    # Callbacks (buttons)
    app.add_handler(CallbackQueryHandler(callback_handler))

    # Handle video/audio messages for edit sessions
    app.add_handler(MessageHandler(filters.AUDIO | filters.VOICE | filters.Document.ALL, handle_edit_video_message))

    # Auto-detect URLs or music search in plain messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    log.info("Bot connected and polling! All systems ready.")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
