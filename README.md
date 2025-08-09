# 🎵 YouTube → MP3 Telegram Bot

Telegram-бот: присылаете ссылку на YouTube (включая Shorts), бот возвращает аудио MP3 (128 kbps).  
Работает на `python-telegram-bot`, `yt-dlp` и `ffmpeg`.

⚠️ Используйте только для загрузки контента, на который у вас есть права.

---

## 🚀 Возможности
- Поддержка ссылок:
  - `https://youtube.com/watch?v=VIDEO_ID`
  - `https://youtube.com/shorts/VIDEO_ID`
  - `https://youtu.be/VIDEO_ID`
- Конвертация в MP3 (128 kbps).
- Работа с cookies для обхода ограничений YouTube.

---

## 📦 Требования
- Python 3.10+
- Установленный `ffmpeg` на локальной машине
- Токен Telegram-бота от [@BotFather](https://t.me/BotFather)
- `cookies.txt` в формате Netscape для стабильной загрузки  
  (можно получить вручную через расширение Chrome **[Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc)**)
