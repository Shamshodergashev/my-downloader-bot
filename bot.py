import asyncio, os, sys, sqlite3, logging, time, re, shutil, requests, threading
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

# --- FFMPEG (Render uchun shart!) ---
static_ffmpeg.add_paths()

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_URL = os.getenv("ADMIN_URL", "https://t.me/Ergashev_Shamshod")
bot = Bot(token=TOKEN)
dp = Dispatcher()
executor = ThreadPoolExecutor(max_workers=4)

# --- HEALTH CHECK (Render o'chirmasligi uchun) ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers()
        self.wfile.write(b"Bot is running!")
    def log_message(self, f, *a): return

def run_health_check():
    port = int(os.environ.get("PORT", 10000))
    HTTPServer(('0.0.0.0', port), HealthCheckHandler).serve_forever()

# --- UTILS ---
COOLDOWN_SECONDS = 5
user_links = {}; user_cooldowns = {}
def is_rate_limited(uid):
    now = time.time()
    if now - user_cooldowns.get(uid, 0) < COOLDOWN_SECONDS: return True
    user_cooldowns[uid] = now; return False

db = sqlite3.connect("users.db")
cursor = db.cursor()
cursor.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY)")
db.commit()
L = instaloader.Instaloader(download_video_thumbnails=False, save_metadata=False, download_comments=False)

def download_media(url, mode):
    file_name = f'res_{mode}_{int(time.time())}'
    opts = {'outtmpl':f'{file_name}.%(ext)s','quiet':True,'no_warnings':True,'merge_output_format':'mp4'}
    if 'mp3' in mode or 'music' in mode:
        opts.update({'format':'bestaudio/best','postprocessors':[{'key':'FFmpegExtractAudio','preferredcodec':'mp3','preferredquality':'192'}]})
    elif mode == '720p': opts['format'] = 'bestvideo[height<=720]+bestaudio/best/best'
    else: opts['format'] = 'bestvideo[height<=480]+bestaudio/best/best'
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        p = ydl.prepare_filename(info)
        if ('mp3' in mode or 'music' in mode) and not p.endswith('.mp3'): p = os.path.splitext(p)[0] + '.mp3'
        return p, info.get('artist','Noma\'lum'), info.get('title','video')

# --- HANDLERS ---
@dp.message(CommandStart())
async def start(m):
    cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (m.from_user.id,))
    db.commit()
    await m.answer(f"Salom {m.from_user.full_name}! 👋 Link yuboring!")

@dp.message(F.text.contains("http"))
async def handle_link(m):
    user_links[m.from_user.id] = m.text.strip()
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="🎥 Video", callback_data="dl_480p"), types.InlineKeyboardButton(text="🎵 MP3", callback_data="dl_convert_mp3"))
    await m.answer("Nima yuklamoqchisiz?", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("dl_"))
async def dl(c):
    uid = c.from_user.id
    if is_rate_limited(uid): return await c.answer("⏳ 5 soniya kuting!", show_alert=True)
    mode, url = c.data.split("_", 1)[1], user_links.get(uid)
    if not url: return await c.message.edit_text("❌ Link topilmadi.")
    status = await c.message.edit_text("⏳ Yuklanmoqda...")
    path = None
    try:
        path, art, trk = await asyncio.get_running_loop().run_in_executor(executor, download_media, url, mode)
        if os.path.exists(path):
            input_f = types.FSInputFile(path)
            if 'mp3' in mode: await c.message.answer_audio(input_f, title=trk, performer=art)
            else: await c.message.answer_video(input_f)
            await status.delete()
        else: raise Exception("Fayl topilmadi")
    except Exception as e:
        await c.message.answer(f"❌ Xatolik: {str(e)[:100]}...")
        await status.delete()
    finally:
        if path and os.path.exists(path): os.remove(path)

async def main():
    threading.Thread(target=run_health_check, daemon=True).start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
