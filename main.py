import asyncio
import os
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import Optional, Tuple, List
from urllib.parse import urlparse

from telegram import Update, InputFile, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# ===== Окружение =====
BOT_TOKEN = os.getenv("DOWNLOADER_BOT_TOKEN")

# Cookie-файлы (Netscape формат)
YT_COOKIE_FILE = "cookies.txt"
IG_COOKIE_FILE = os.getenv("IG_COOKIE_FILE", "")
YT_COOKIES = Path(YT_COOKIE_FILE).expanduser() if YT_COOKIE_FILE else None
IG_COOKIES = Path(IG_COOKIE_FILE).expanduser() if IG_COOKIE_FILE else None

# ===== Кнопки (подсказки, не «режимы») =====
BTN_IG = "🟣 Загрузить видео из Instagram"
BTN_YT = "🔴 Загрузить звук из видео YouTube"
KB = ReplyKeyboardMarkup([[KeyboardButton(BTN_IG)], [KeyboardButton(BTN_YT)]], resize_keyboard=True)

# ===== Регексы =====
YOUTUBE_REGEX = re.compile(
    r"(https?://(?:www\.)?(?:youtube\.com/(?:watch\?v=|shorts/)[\w-]{11}|youtu\.be/[\w-]{11}))",
    re.IGNORECASE,
)
INSTAGRAM_REGEX = re.compile(
    r"(https?://(?:www\.)?instagram\.com/(?:reel|p|tv)/[A-Za-z0-9_\-]+/?(?:\?[^\s#]*)?)",
    re.IGNORECASE,
)

MAX_MB_HINT = 45


# ========== YouTube: загрузка аудио ==========
def _yt_download_audio_sync(url: str, outdir: Path, cookiefile: Optional[Path]) -> Tuple[Path, Optional[Path], dict]:
    import yt_dlp
    outtmpl = str(outdir / "%(title).200B [%(id)s].%(ext)s")
    ydl_opts = {
        "quiet": True,
        "noprogress": True,
        "format": "bestaudio/best",
        "outtmpl": outtmpl,
        "noplaylist": True,
        "writethumbnail": True,
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "128"}],
        "postprocessor_args": ["-id3v2_version", "3"],
        "retries": 3,
        "extractor_retries": 3,
        "sleep_interval_requests": 0.5,
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        },
        "extractor_args": {"youtube": {"player_client": ["web"]}},  # с cookies остаёмся на web
    }
    if cookiefile and cookiefile.exists():
        ydl_opts["cookiefile"] = str(cookiefile)

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

    vid = info.get("id")
    mp3 = next(outdir.glob(f"*{vid}*.mp3"), None)
    if not mp3:
        raise RuntimeError("Не найден итоговый MP3 после обработки.")
    thumb = None
    for ext in (".jpg", ".png", ".webp"):
        t = next(outdir.glob(f"*{vid}*{ext}"), None)
        if t:
            thumb = t
            break
    return mp3, thumb, info


async def yt_download_audio(url: str, cookiefile: Optional[Path], loop: asyncio.AbstractEventLoop):
    with tempfile.TemporaryDirectory(prefix="ytmp3_") as td:
        outdir = Path(td)
        executor = ThreadPoolExecutor(max_workers=1)
        try:
            audio_path, thumb_path, info = await loop.run_in_executor(
                executor, partial(_yt_download_audio_sync, url, outdir, cookiefile)
            )
            final_dir = Path(tempfile.gettempdir()) / f"ytmp3_done_{os.getpid()}"
            final_dir.mkdir(exist_ok=True)
            audio_dst = final_dir / audio_path.name
            audio_path.replace(audio_dst)

            thumb_dst = None
            if thumb_path and thumb_path.exists():
                thumb_dst = final_dir / thumb_path.name
                thumb_path.replace(thumb_dst)

            return audio_dst, thumb_dst, info
        finally:
            executor.shutdown(wait=True)


# ========== Instagram: загрузка видео ==========
def _ig_download_video_sync(url: str, outdir: Path, cookiefile: Optional[Path]) -> Tuple[Path, dict]:
    import yt_dlp
    outtmpl = str(outdir / "%(title).200B [%(id)s].%(ext)s")
    ydl_opts = {
        "quiet": True,
        "noprogress": True,
        "format": "bestvideo*+bestaudio/best",
        "merge_output_format": "mp4",
        "outtmpl": outtmpl,
        "retries": 3,
        "extractor_retries": 3,
        "sleep_interval_requests": 0.5,
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        },
    }
    if cookiefile and cookiefile.exists():
        ydl_opts["cookiefile"] = str(cookiefile)

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

    vid = info.get("id")
    video = next(outdir.glob(f"*{vid}*.mp4"), None)
    if not video:
        video = next(outdir.glob(f"*{vid}*"), None)
    if not video:
        raise RuntimeError("Не найден итоговый видеофайл после загрузки.")
    return video, info


async def ig_download_video(url: str, cookiefile: Optional[Path], loop: asyncio.AbstractEventLoop):
    with tempfile.TemporaryDirectory(prefix="igvid_") as td:
        outdir = Path(td)
        executor = ThreadPoolExecutor(max_workers=1)
        try:
            video_path, info = await loop.run_in_executor(
                executor, partial(_ig_download_video_sync, url, outdir, cookiefile)
            )
            final_dir = Path(tempfile.gettempdir()) / f"igvid_done_{os.getpid()}"
            final_dir.mkdir(exist_ok=True)
            video_dst = final_dir / video_path.name
            video_path.replace(video_dst)
            return video_dst, info
        finally:
            executor.shutdown(wait=True)


# ===== Вспомогательное =====
def extract_links(text: str) -> List[str]:
    """Достаём все IG/YT ссылки из текста (в любом количестве, без «режимов»)."""
    links = []
    links += [m[0] if isinstance(m, tuple) else m for m in YOUTUBE_REGEX.findall(text or "")]
    links += [m[0] if isinstance(m, tuple) else m for m in INSTAGRAM_REGEX.findall(text or "")]
    return links


def canonical_ig(url: str) -> str:
    """Приводим IG-ссылку к каноническому виду (обрезаем query/fragment)."""
    m = re.search(r"(instagram\.com/(reel|p|tv)/([A-Za-z0-9_\-]+))", url, re.IGNORECASE)
    if not m:
        return url
    return f"https://www.{m.group(1)}/"


# ===== UI / Handlers =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Пришлите ссылку (Instagram/YouTube) — бот сам поймёт и скачает.\n"
        "Кнопки ниже — просто подсказки.",
        reply_markup=KB,
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()

    # Если нажали кнопку — просто покажем короткую подсказку
    if text == BTN_IG:
        await update.message.reply_text("Пришлите ссылку на Instagram (reel/p/tv) — пришлю видео.", reply_markup=KB)
        return
    if text == BTN_YT:
        await update.message.reply_text("Пришлите ссылку на YouTube (watch/shorts/youtu.be) — пришлю MP3.", reply_markup=KB)
        return

    # Любой другой текст — пытаемся вытащить ссылки и обработать их по очереди
    links = extract_links(text)
    if not links:
        await update.message.reply_text("Скиньте ссылку на Instagram или YouTube — обработаю автоматически.", reply_markup=KB)
        return

    loop = asyncio.get_event_loop()
    for url in links[:5]:  # на всякий случай ограничим пачку
        if "instagram.com" in url.lower():
            url = canonical_ig(url)
            status = await update.message.reply_text("Скачиваю видео из Instagram… ⏳")
            try:
                video_path, info = await ig_download_video(url, IG_COOKIES, loop)
                title = info.get("title") or "Видео из Instagram"
                size_mb = video_path.stat().st_size / (1024 * 1024)
                caption = title + (f"\n(≈ {size_mb:.1f} MB)" if size_mb > MAX_MB_HINT else "")
                await status.edit_text("Отправляю видео…")
                try:
                    with video_path.open("rb") as f:
                        await update.message.reply_video(
                            video=InputFile(f, filename=video_path.name),
                            caption=caption,
                            supports_streaming=True,
                        )
                except Exception:
                    with video_path.open("rb") as f:
                        await update.message.reply_document(document=InputFile(f, filename=video_path.name), caption=caption)
                await status.delete()
            except Exception as e:
                try:
                    await status.edit_text("Не удалось скачать видео. Проверьте доступность/куки Instagram.")
                except Exception:
                    pass
                print("IG ERROR:", repr(e))
        else:
            # YouTube
            status = await update.message.reply_text("Скачиваю аудио с YouTube… ⏳")
            try:
                audio_path, thumb_path, info = await yt_download_audio(url, YT_COOKIES, loop)
                title = info.get("title") or "Аудио из видео"
                duration = info.get("duration")
                size_mb = audio_path.stat().st_size / (1024 * 1024)

                caption = title
                if isinstance(duration, int):
                    caption += f" • {duration // 60}:{duration % 60:02d}"
                if size_mb > MAX_MB_HINT:
                    caption += f"\n(≈ {size_mb:.1f} MB)"

                await status.edit_text("Отправляю MP3…")
                try:
                    with audio_path.open("rb") as f:
                        thumb = None
                        if thumb_path and thumb_path.exists():
                            thumb = InputFile(thumb_path.open("rb"), filename=thumb_path.name)
                        await update.message.reply_audio(
                            audio=InputFile(f, filename=audio_path.name),
                            thumbnail=thumb,
                            caption=caption,
                            title=title,
                            duration=duration if isinstance(duration, int) else None,
                        )
                except Exception:
                    with audio_path.open("rb") as f:
                        await update.message.reply_document(document=InputFile(f, filename=audio_path.name), caption=caption)
                await status.delete()
            except Exception as e:
                try:
                    await status.edit_text("Не удалось скачать аудио. Обновите cookies YouTube и попробуйте ещё раз.")
                except Exception:
                    pass
                print("YT ERROR:", repr(e))


def main():
    if not BOT_TOKEN:
        raise RuntimeError("Не задан BOT_TOKEN в переменных окружения.")
    if YT_COOKIES and not YT_COOKIES.exists():
        print(f"WARNING: YT_COOKIE_FILE указан, но не найден: {YT_COOKIES}")
    if IG_COOKIES and not IG_COOKIES.exists():
        print(f"WARNING: IG_COOKIE_FILE указан, но не найден: {IG_COOKIES}")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    # Все текстовые сообщения — в единый обработчик
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    print("Bot is running. Press Ctrl+C to stop.")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
