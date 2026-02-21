import asyncio
import os
import sys
import sqlite3
import logging
import time
import re
import shutil
import requests
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from concurrent.futures import ThreadPoolExecutor

import instaloader
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.utils.media_group import MediaGroupBuilder
from dotenv import load_dotenv
from yt_dlp import YoutubeDL
import static_ffmpeg

# ══════════════════════════════════════════
#  FFMPEG SOZLAMALARI (Render uchun)
# ══════════════════════════════════════════
static_ffmpeg.add_paths()

# ══════════════════════════════════════════
#  SOZLAMALAR (.env dan yuklash)
# ══════════════════════════════════════════
load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_URL = os.getenv("ADMIN_URL", "https://t.me/Ergashev_Shamshod")
BOT_USERNAME = os.getenv("BOT_USERNAME", "@Godzilla_downloadbot")

if not TOKEN:
    print("❌ XATO: BOT_TOKEN topilmadi! .env faylingizni tekshiring.")
    sys.exit(1)

# ══════════════════════════════════════════
#  LOGGING
# ══════════════════════════════════════════
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", handlers=[logging.FileHandler("bot.log", encoding="utf-8"), logging.StreamHandler()])
logger = logging.getLogger(__name__)

# ══════════════════════════════════════════
#  BOT VA DISPATCHER
# ══════════════════════════════════════════
bot = Bot(token=TOKEN)
dp = Dispatcher()
executor = ThreadPoolExecutor(max_workers=4)

# --- RENDER HEALTH CHECK SERVER ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(b"Bot is running!")
    def log_message(self, format, *args):
        return

def run_health_check():
    port = int(os.environ.get("PORT", 10000))
    try:
        HTTPServer(('0.0.0.0', port), HealthCheckHandler).serve_forever()
    except: pass

# --- COOLDOWN ---
COOLDOWN_SECONDS = 5
user_links: dict[int, str] = {}
user_cooldowns: dict[int, float] = {}

def is_rate_limited(user_id: int) -> bool:
    last_time = user_cooldowns.get(user_id, 0)
    now = time.time()
    if now - last_time < COOLDOWN_SECONDS: return True
    user_cooldowns[user_id] = now
    return False

# --- DATABASE ---
TELEGRAM_FILE_LIMIT = 50 * 1024 * 1024
db = sqlite3.connect("users.db")
cursor = db.cursor()
cursor.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY)")
db.commit()

L = instaloader.Instaloader(download_video_thumbnails=False, save_metadata=False, download_comments=False)

# --- UTILS ---
def clean_query(text: str) -> str:
    if not text: return "Musiqa"
    text = re.sub(r'\(.*?\)|\[.*?\]', '', text)
    bad_words = ['official', 'video', 'lyrics', '1080p', '4k', 'hd', 'full', 'clip', 'klip']
    text = text.lower()
    for word in bad_words: text = text.replace(word, '')
    return text.strip()

def is_facebook_link(text: str) -> bool:
    fb_patterns = ['facebook.com', 'fb.watch', 'fb.com', 'm.facebook.com', 'web.facebook.com']
    return any(p in text.lower() for p in fb_patterns) if text else False

def is_facebook_photo_url(url: str) -> bool:
    url_l = url.lower()
    return any(p in url_l for p in ['/photo/', '/photos/', 'photo.php', '/image', 'fbid='])

def is_facebook_video_url(url: str) -> bool:
    url_l = url.lower()
    return any(p in url_l for p in ['/videos/', '/video/', '/watch', '/reel/', 'fb.watch'])

def detect_media_type(url: str) -> str:
    if is_facebook_photo_url(url): return 'photo'
    if is_facebook_video_url(url): return 'video'
    try:
        ydl_opts = {'quiet': True, 'no_warnings': True, 'skip_download': True}
        with YoutubeDL(ydl_opts) as ydl:
            if ydl.extract_info(url, download=False).get('formats'): return 'video'
            return 'photo'
    except: return 'photo'

def download_photo_from_url(url: str) -> str | None:
    path = f'photo_{int(time.time())}.jpg'
    m_url = url.replace('www.', 'm.').replace('web.', 'm.')
    try:
        resp = requests.get(m_url, headers={'User-Agent': 'Mozilla/5.0...'}, timeout=15)
        if resp.status_code == 200:
            match = re.search(r'property=["\']og:image["\']\s+content=["\']([^"\']+)["\']', resp.text)
            if match:
                img_url = match.group(1).replace('&amp;', '&').replace('\\/', '/')
                img_resp = requests.get(img_url, timeout=15)
                if img_resp.status_code == 200:
                    with open(path, 'wb') as f: f.write(img_resp.content)
                    return path
    except: pass
    return None

def safe_remove(path):
    try:
        if path and os.path.exists(path):
            if os.path.isdir(path): shutil.rmtree(path)
            else: os.remove(path)
    except: pass

def download_media(url: str, mode: str) -> tuple[str, str, str]:
    file_name = f'res_{mode}_{int(time.time())}'
    ydl_opts = {'outtmpl': f'{file_name}.%(ext)s', 'quiet': True, 'no_warnings': True, 'merge_output_format': 'mp4'}
    if 'mp3' in mode or 'music' in mode:
        ydl_opts.update({'format': 'bestaudio/best', 'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}]})
    elif mode == '720p': ydl_opts['format'] = 'bestvideo[height<=720]+bestaudio/best/best'
    else: ydl_opts['format'] = 'bestvideo[height<=480]+bestaudio/best/best'

    with YoutubeDL(ydl_opts) as ydl:
        if mode == 'original_music':
            info_p = ydl.extract_info(url, download=False)
            q = f"{info_p.get('artist', '')} {info_p.get('track', info_p.get('title', ''))} audio"
            info = ydl.extract_info(f"ytsearch1:{q}", download=True)['entries'][0]
        else: info = ydl.extract_info(url, download=True)
        p = ydl.prepare_filename(info)
        if ('mp3' in mode or 'music' in mode) and not p.endswith('.mp3'): p = os.path.splitext(p)[0] + '.mp3'
        return p, info.get('artist', 'Noma\'lum'), info.get('track', info.get('title', 'video'))

# --- INTERFACE ---
def get_main_menu():
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="🎥 Video", callback_data="btn_video"), types.InlineKeyboardButton(text="🎵 MP3", callback_data="btn_audio_choice"))
    builder.row(types.InlineKeyboardButton(text="👨‍💻 Admin", url=ADMIN_URL))
    return builder.as_markup()

@dp.message(CommandStart())
async def start(message: types.Message):
    cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (message.from_user.id,))
    db.commit()
    await message.answer(f"Salom {message.from_user.full_name}! 👋\nLink yuboring!")

@dp.message(F.text.contains("instagram.com/"))
async def handle_insta(message: types.Message):
    try:
        post = await asyncio.get_running_loop().run_in_executor(executor, instaloader.Post.from_shortcode, L.context, message.text.split("/")[-2])
        if post.is_video:
            user_links[message.from_user.id] = message.text
            await message.answer("Nima yuklamoqchisiz?", reply_markup=get_main_menu())
        else:
            status = await message.answer("📸 Yuklanmoqda...")
            await asyncio.get_running_loop().run_in_executor(executor, L.download_post, post, "temp_insta")
            await status.edit_text("Instagram rasm yuklandi!")
    except:
        user_links[message.from_user.id] = message.text
        await message.answer("Nima yuklamoqchisiz?", reply_markup=get_main_menu())

@dp.message(F.text.contains("http"))
async def handle_any(message: types.Message):
    user_links[message.from_user.id] = message.text.strip()
    await message.answer("Nima yuklamoqchisiz?", reply_markup=get_main_menu())

@dp.callback_query(F.data == "btn_audio_choice")
async def audio_choice(callback: types.CallbackQuery):
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="✂️ MP3 ga o'girish", callback_data="dl_convert_mp3"), types.InlineKeyboardButton(text="🔍 Musiqani topish", callback_data="dl_original_music"))
    builder.row(types.InlineKeyboardButton(text="⬅️ Orqaga", callback_data="back_to_main"))
    await callback.message.edit_text("MP3 yuklash turi:", reply_markup=builder.as_markup())

@dp.callback_query(F.data == "btn_video")
async def video_choice(callback: types.CallbackQuery):
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="🎥 720p", callback_data="dl_720p"), types.InlineKeyboardButton(text="📱 480p", callback_data="dl_480p"))
    builder.row(types.InlineKeyboardButton(text="⬅️ Orqaga", callback_data="back_to_main"))
    await callback.message.edit_text("Video sifati:", reply_markup=builder.as_markup())

@dp.callback_query(F.data == "back_to_main")
async def back_main(callback: types.CallbackQuery):
    await callback.message.edit_text("Nima yuklamoqchisiz?", reply_markup=get_main_menu())

@dp.callback_query(F.data.startswith("dl_"))
async def start_dl(callback: types.CallbackQuery):
    u_id = callback.from_user.id
    if is_rate_limited(u_id): return await callback.answer("⏳ 5 soniya kuting!", show_alert=True)
    mode, url = callback.data.split("_", 1)[1], user_links.get(u_id)
    if not url: return await callback.message.edit_text("❌ Link topilmadi.")
    status = await callback.message.edit_text("⏳ Yuklanmoqda...")
    path = None
    try:
        path, artist, track = await asyncio.get_running_loop().run_in_executor(executor, download_media, url, mode)
        if os.path.exists(path):
            input_f, cap = types.FSInputFile(path), f"Tayyor! ✅\nManba: {BOT_USERNAME}"
            if 'mp3' in mode or 'music' in mode: await callback.message.answer_audio(input_f, caption=cap, title=track, performer=artist)
            else: await callback.message.answer_video(input_f, caption=cap)
            await status.delete()
        else: raise Exception("Fayl topilmadi")
    except Exception as e:
        if "confirm you're not a bot" in str(e):
            await callback.message.answer("⚠️ YouTube hozircha botlarni bloklamoqda.\nInstagram yoki TikTok link yuboring!")
        else: await callback.message.answer(f"❌ Xatolik!")
        await status.delete()
    finally: safe_remove(path)

# --- MAIN ---
async def main():
    logger.info("🤖 Bot ishga tushdi!")
    threading.Thread(target=run_health_check, daemon=True).start()
    try: await dp.start_polling(bot)
    finally:
        executor.shutdown(wait=False)
        db.close()

if __name__ == "__main__":
    asyncio.run(main())
