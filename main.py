import asyncio
import os
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import Optional

from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# ===== Настройки окружения =====
BOT_TOKEN = os.getenv("BOT_TOKEN")
COOKIE_FILE = "путь до файла с куки"
DEFAULT_COOKIEFILE = Path(COOKIE_FILE).expanduser() if COOKIE_FILE else None

# Фильтр YouTube ссылок (watch, shorts, youtu.be)
YOUTUBE_REGEX = re.compile(
    r"(https?://)?(www\.)?("
    r"youtube\.com/(watch\?v=|shorts/)[\w-]{11}"
    r"|youtu\.be/[\w-]{11}"
    r")"
)

# Порог для подсказки о размере, чисто информационно
MAX_MB_HINT = 45


# ===== Скачивание/конвертация =====
def _download_audio_sync(url: str, outdir: Path, cookiefile: Optional[Path]):
    import yt_dlp

    outtmpl = str(outdir / "%(title).200B [%(id)s].%(ext)s")
    ydl_opts = {
        "quiet": True,
        "noprogress": True,
        "format": "bestaudio/best",
        "outtmpl": outtmpl,
        "noplaylist": True,
        "writethumbnail": True,
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "128"}
        ],
        "postprocessor_args": ["-id3v2_version", "3"],
        # немного устойчивости к временным ошибкам
        "retries": 3,
        "extractor_retries": 3,
        "sleep_interval_requests": 0.5,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        },
    }

    # если cookies есть — используем их и ОСТАЁМСЯ на web-клиенте (без android/ios/tv)
    if cookiefile and cookiefile.exists():
        ydl_opts["cookiefile"] = str(cookiefile)
        # можно явно закрепить web, но это и так дефолт:
        ydl_opts.setdefault("extractor_args", {"youtube": {"player_client": ["web"]}})
    else:
        # без cookies иногда помогает переключение клиентов
        ydl_opts.setdefault("extractor_args", {"youtube": {"player_client": ["web", "tv"]}})
        # (убрали android/ios, чтобы не ловить несовместимость с cookies в будущем)

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

    video_id = info.get("id")
    mp3 = next(outdir.glob(f"*{video_id}*.mp3"), None)
    if not mp3:
        raise RuntimeError("Не найден итоговый MP3 после обработки.")
    thumb = None
    for ext in (".jpg", ".png", ".webp"):
        t = next(outdir.glob(f"*{video_id}*{ext}"), None)
        if t:
            thumb = t
            break
    return mp3, thumb, info


async def download_audio(url: str, cookiefile: Optional[Path], loop: asyncio.AbstractEventLoop):
    with tempfile.TemporaryDirectory(prefix="ytmp3_") as td:
        outdir = Path(td)
        executor = ThreadPoolExecutor(max_workers=1)
        try:
            audio_path, thumb_path, info = await loop.run_in_executor(
                executor, partial(_download_audio_sync, url, outdir, cookiefile)
            )
            # Переносим готовое в стабильную tmp-папку, чтобы жить после выхода из контекста
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


# ===== Хэндлеры =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "Пришлите ссылку на YouTube (включая shorts) — я пришлю MP3 128 kbps.\n"
        "Если YouTube требует вход/капчу, бот использует cookies с сервера."
    )
    await update.message.reply_text(msg)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)


async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    m = YOUTUBE_REGEX.search(text)
    if not m:
        await update.message.reply_text("Отправьте корректную ссылку на YouTube (watch/shorts/youtu.be).")
        return

    url = m.group(0)
    status = await update.message.reply_text("Скачиваю аудио… ⏳")

    try:
        loop = asyncio.get_event_loop()
        audio_path, thumb_path, info = await download_audio(url, DEFAULT_COOKIEFILE, loop)

        title = info.get("title") or "Аудио из видео"
        duration = info.get("duration")
        size_mb = audio_path.stat().st_size / (1024 * 1024)

        caption = title
        if isinstance(duration, int):
            caption += f" • {duration // 60}:{duration % 60:02d}"
        if size_mb > MAX_MB_HINT:
            caption += f"\n(≈ {size_mb:.1f} MB)"

        await status.edit_text("Отправляю файл…")

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
                await update.message.reply_document(
                    document=InputFile(f, filename=audio_path.name),
                    caption=caption,
                )

        try:
            await status.delete()
        except Exception:
            pass

    except Exception as e:
        hint = (
            "\n\n(Если видите такие ошибки часто, обновите cookies на сервере "
            "и перезапустите бота.)"
        )
        try:
            await status.edit_text("Не удалось скачать аудио. " + hint)
        except Exception:
            pass
        print("ERROR:", repr(e))


def main():
    if not BOT_TOKEN:
        raise RuntimeError("Не задан BOT_TOKEN в переменных окружения.")
    if not DEFAULT_COOKIEFILE:
        print("WARNING: COOKIE_FILE не задан — попробую без cookies (может упираться в капчу/логин).")
    elif not DEFAULT_COOKIEFILE.exists():
        print(f"WARNING: COOKIE_FILE указан, но файл не найден: {DEFAULT_COOKIEFILE} — попробую без cookies.")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))

    print("Bot is running. Press Ctrl+C to stop.")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
