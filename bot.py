import asyncio
import os
import sys
import sqlite3
import logging
import time
import re
import shutil
import requests
from concurrent.futures import ThreadPoolExecutor

import instaloader
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.utils.media_group import MediaGroupBuilder
from dotenv import load_dotenv
from yt_dlp import YoutubeDL

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
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ══════════════════════════════════════════
#  BOT VA DISPATCHER
# ══════════════════════════════════════════
bot = Bot(token=TOKEN)
dp = Dispatcher()

# ══════════════════════════════════════════
#  TEZLIK: Thread pool (parallel yuklash)
# ══════════════════════════════════════════
executor = ThreadPoolExecutor(max_workers=4)

# ══════════════════════════════════════════
#  RATE LIMITING
# ══════════════════════════════════════════
COOLDOWN_SECONDS = 5  # 10 dan 5 ga tushirildi — tezroq
user_links: dict[int, str] = {}
user_cooldowns: dict[int, float] = {}

def is_rate_limited(user_id: int) -> bool:
    last_time = user_cooldowns.get(user_id, 0)
    now = time.time()
    if now - last_time < COOLDOWN_SECONDS:
        return True
    user_cooldowns[user_id] = now
    return False

# ══════════════════════════════════════════
#  MA'LUMOTLAR BAZASI
# ══════════════════════════════════════════
TELEGRAM_FILE_LIMIT = 50 * 1024 * 1024

db = sqlite3.connect("users.db")
cursor = db.cursor()
cursor.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY)")
db.commit()

# ══════════════════════════════════════════
#  INSTALOADER
# ══════════════════════════════════════════
L = instaloader.Instaloader(
    download_video_thumbnails=False,
    save_metadata=False,
    download_comments=False
)

# ══════════════════════════════════════════
#  YORDAMCHI FUNKSIYALAR
# ══════════════════════════════════════════
def clean_query(text: str) -> str:
    if not text:
        return "Musiqa"
    text = re.sub(r'\(.*?\)|\[.*?\]', '', text)
    bad_words = ['official', 'video', 'lyrics', '1080p', '4k', 'hd', 'full', 'clip', 'klip']
    text = text.lower()
    for word in bad_words:
        text = text.replace(word, '')
    return text.strip()


def is_facebook_link(text: str) -> bool:
    if not text:
        return False
    fb_patterns = ['facebook.com', 'fb.watch', 'fb.com', 'm.facebook.com', 'web.facebook.com']
    return any(p in text.lower() for p in fb_patterns)


def is_facebook_photo_url(url: str) -> bool:
    """Facebook rasm linki ekanligini URL pattern bo'yicha aniqlaydi."""
    url_lower = url.lower()
    photo_patterns = ['/photo/', '/photos/', 'photo.php', '/photo.php',
                      '/photo?', 'photo_id=', '/image', 'fbid=']
    return any(p in url_lower for p in photo_patterns)


def is_facebook_video_url(url: str) -> bool:
    """Facebook video linki ekanligini URL pattern bo'yicha aniqlaydi."""
    url_lower = url.lower()
    video_patterns = ['/videos/', '/video/', '/watch', '/reel/', 'fb.watch',
                      'video_id=', '/watch/', 'story_fbid=']
    return any(p in url_lower for p in video_patterns)


def detect_media_type(url: str) -> str:
    """Linkdagi kontent turini aniqlaydi: 'video', 'photo', yoki 'unknown'."""
    # 1. Avval URL patternni tekshirish (tez va ishonchli)
    if is_facebook_photo_url(url):
        return 'photo'
    if is_facebook_video_url(url):
        return 'video'

    # 2. URL pattern aniqlanmasa, yt-dlp bilan tekshirish
    try:
        ydl_opts = {'quiet': True, 'no_warnings': True, 'skip_download': True}
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if info and info.get('formats'):
                return 'video'
            return 'photo'  # format yo'q = rasm
    except Exception:
        # yt-dlp yuklolmasa — ehtimol rasm
        return 'photo'


def download_photo_from_url(url: str) -> str | None:
    """Linkdan rasmni yuklab oladi (og:image orqali)."""
    photo_path = f'photo_{int(time.time())}.jpg'

    # Facebook mobile URL ga o'tkazish (kamroq cheklov)
    mobile_url = url.replace('www.facebook.com', 'm.facebook.com')
    mobile_url = mobile_url.replace('web.facebook.com', 'm.facebook.com')

    # Turli User-Agentlar bilan sinash
    user_agents = [
        # Facebook o'zining crawleri — eng ishonchli
        'facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)',
        # Telegram link preview bot
        'TelegramBot (like TwitterBot)',
        # Google bot
        'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)',
        # Oddiy brauzer
        'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148',
    ]

    for ua in user_agents:
        try:
            headers = {'User-Agent': ua, 'Accept': 'text/html,*/*', 'Accept-Language': 'en-US,en;q=0.9'}
            resp = requests.get(mobile_url, headers=headers, timeout=15, allow_redirects=True)
            if resp.status_code != 200:
                continue

            html = resp.text
            img_url = None

            # og:image qidirish (bir necha xil formatda)
            patterns = [
                r'<meta\s+property=["\']og:image["\']\s+content=["\']([^"\']+)["\']',
                r'content=["\']([^"\']+)["\']\s+property=["\']og:image["\']',
                r'"og:image"\s*:\s*"([^"]+)"',
                r'"image"\s*:\s*\{"uri"\s*:\s*"([^"]+)"',
                r'"photo_image"\s*:\s*\{"uri"\s*:\s*"([^"]+)"',
            ]

            for pattern in patterns:
                match = re.search(pattern, html)
                if match:
                    img_url = match.group(1).replace('&amp;', '&').replace('\\/', '/')
                    break

            if img_url:
                logger.info(f"Rasm URL topildi: {img_url[:100]}...")
                img_resp = requests.get(img_url, headers={'User-Agent': user_agents[0]}, timeout=15)
                if img_resp.status_code == 200 and len(img_resp.content) > 1000:
                    with open(photo_path, 'wb') as f:
                        f.write(img_resp.content)
                    logger.info(f"Rasm yuklandi: {len(img_resp.content)} bayt")
                    return photo_path

        except Exception as e:
            logger.warning(f"Rasm yuklash urinishi xato (UA: {ua[:30]}): {e}")
            continue

    logger.warning(f"Barcha urinishlar muvaffaqiyatsiz: {url}")
    return None


def safe_remove(path) -> None:
    try:
        if path and os.path.exists(path):
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                os.remove(path)
    except OSError as e:
        logger.warning(f"Fayl o'chirishda xato: {path} — {e}")


def check_file_size(path: str) -> bool:
    try:
        return os.path.getsize(path) <= TELEGRAM_FILE_LIMIT
    except OSError:
        return False


def download_media(url: str, mode: str) -> tuple[str, str, str]:
    """Media yuklaydi — bitta YoutubeDL chaqiruvi bilan (tezroq)."""
    file_name = f'res_{mode}_{int(time.time())}'
    ydl_opts = {
        'outtmpl': f'{file_name}.%(ext)s',
        'quiet': True,
        'no_warnings': True,
        'merge_output_format': 'mp4',
        'concurrent_fragment_downloads': 4,  # Tezlik: parallel fragment yuklash
        'buffersize': 1024 * 64,  # Tezlik: katta buffer
    }

    if mode in ['convert_mp3', 'original_music']:
        ydl_opts.update({
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192'
            }]
        })
    elif mode == '720p':
        ydl_opts['format'] = 'bestvideo[height<=720]+bestaudio/best/best'
    else:
        ydl_opts['format'] = 'bestvideo[height<=480]+bestaudio/best/best'

    with YoutubeDL(ydl_opts) as ydl:
        if mode == 'original_music':
            # Avval asl link ma'lumotlarini olish
            info_pre = ydl.extract_info(url, download=False)
            title = info_pre.get('title', 'musiqa')
            artist = info_pre.get('artist', "Noma'lum ijrochi")
            track = info_pre.get('track', title)
            q = (f"{artist} {track} official audio"
                 if artist != "Noma'lum ijrochi"
                 else f"{clean_query(title)} audio")
            result = ydl.extract_info(f"ytsearch1:{q}", download=True)
            info = result['entries'][0]
        else:
            info = ydl.extract_info(url, download=True)
            title = info.get('title', 'musiqa')
            artist = info.get('artist', "Noma'lum ijrochi")
            track = info.get('track', title)

        path = ydl.prepare_filename(info)

        if 'mp3' in mode or 'music' in mode:
            if not path.endswith('.mp3'):
                path = os.path.splitext(path)[0] + '.mp3'
        elif path.endswith('.webm'):
            new_path = path.replace('.webm', '.mp4')
            if os.path.exists(path):
                os.rename(path, new_path)
            path = new_path

        return path, artist, track




# ══════════════════════════════════════════
#  KLAVIATURA
# ══════════════════════════════════════════
def get_main_menu():
    builder = InlineKeyboardBuilder()
    builder.row(
        types.InlineKeyboardButton(text="🎥 Video", callback_data="btn_video"),
        types.InlineKeyboardButton(text="🎵 MP3", callback_data="btn_audio_choice")
    )
    builder.row(types.InlineKeyboardButton(text="👨‍💻 Admin", url=ADMIN_URL))
    return builder.as_markup()


# ══════════════════════════════════════════
#  HANDLERLAR
# ══════════════════════════════════════════
@dp.message(CommandStart())
async def start(message: types.Message):
    logger.info(f"Start handler triggered for user {message.from_user.id}")
    try:
        cursor.execute(
            "INSERT OR IGNORE INTO users (user_id) VALUES (?)",
            (message.from_user.id,)
        )
        db.commit()
    except sqlite3.Error as e:
        logger.error(f"DB xato (start): {e}")
    
    try:
        await message.answer(f"Salom {message.from_user.full_name}! 👋\nLink yuboring!")
        logger.info(f"Start message sent to user {message.from_user.id}")
    except Exception as e:
        logger.error(f"Error sending start message: {e}")


# --- INSTAGRAM ---
@dp.message(F.text.contains("instagram.com/p/"))
async def handle_insta(message: types.Message):
    temp_dir = "temp_insta"
    try:
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
        shortcode = message.text.split("/")[-2]
        loop = asyncio.get_running_loop()
        post = await loop.run_in_executor(
            executor, instaloader.Post.from_shortcode, L.context, shortcode
        )

        if post.is_video:
            user_links[message.from_user.id] = message.text
            await message.answer("Nima yuklamoqchisiz?", reply_markup=get_main_menu())
        else:
            status = await message.answer("📸 Rasm yuklanmoqda...")
            await loop.run_in_executor(executor, L.download_post, post, temp_dir)
            files = [
                os.path.join(temp_dir, f)
                for f in os.listdir(temp_dir)
                if f.endswith(('.jpg', '.png', '.jpeg'))
            ]
            files.sort()
            cap = f"Tayyor! ✅\nManba: {BOT_USERNAME}"
            if len(files) == 1:
                await message.answer_photo(types.FSInputFile(files[0]), caption=cap)
            else:
                album = MediaGroupBuilder(caption=cap)
                for f in files:
                    album.add_photo(media=types.FSInputFile(f))
                await message.answer_media_group(media=album.build())
            await status.delete()
    except Exception as e:
        logger.error(f"Instagram xato ({message.text}): {e}")
        user_links[message.from_user.id] = message.text
        await message.answer("Nima yuklamoqchisiz?", reply_markup=get_main_menu())
    finally:
        safe_remove(temp_dir)


# --- FACEBOOK va BOSHQA LINKLAR (AVTO-ANIQLASH) ---
@dp.message(F.text.contains("http"))
async def handle_any_link(message: types.Message):
    url = message.text.strip()
    user_id = message.from_user.id
    user_links[user_id] = url

    # Facebook yoki boshqa link uchun media turini aniqlash
    if is_facebook_link(url):
        status = await message.answer("🔍 Facebook kontent aniqlanmoqda...")
        try:
            loop = asyncio.get_running_loop()
            media_type = await loop.run_in_executor(executor, detect_media_type, url)

            if media_type == 'photo':
                # RASM — avtomatik yuklash
                await status.edit_text("📸 Rasm yuklanmoqda...")
                path = await loop.run_in_executor(executor, download_photo_from_url, url)
                if path and os.path.exists(path):
                    cap = f"Tayyor! ✅\nManba: {BOT_USERNAME}"
                    await message.answer_photo(types.FSInputFile(path), caption=cap)
                    await status.delete()
                    safe_remove(path)
                    logger.info(f"FB rasm yuklandi: user={user_id}")
                    return
                else:
                    await status.edit_text("❌ Rasm yuklab bo'lmadi.")
                    return
            else:
                # VIDEO — menyu ko'rsatish
                await status.edit_text(
                    "📘 Facebook video aniqlandi!\nNima yuklamoqchisiz?",
                    reply_markup=get_main_menu()
                )
                return
        except Exception as e:
            logger.error(f"FB aniqlash xato: {e}")
            await status.edit_text(
                "Nima yuklamoqchisiz?", reply_markup=get_main_menu()
            )
            return

    # Boshqa linklar (YouTube va h.k.) — darhol menyu
    await message.answer("Nima yuklamoqchisiz?", reply_markup=get_main_menu())


# --- MP3 menyu ---
@dp.callback_query(F.data == "btn_audio_choice")
async def audio_menu(callback: types.CallbackQuery):
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(
        text="✂️ Videoni MP3 ga o'girish", callback_data="dl_convert_mp3"
    ))
    builder.row(types.InlineKeyboardButton(
        text="🔍 Videodagi musiqani topish", callback_data="dl_original_music"
    ))
    builder.row(types.InlineKeyboardButton(
        text="⬅️ Orqaga", callback_data="back_to_main"
    ))
    await callback.message.edit_text(
        "MP3 yuklash turini tanlang:", reply_markup=builder.as_markup()
    )


# --- Video menyu ---
@dp.callback_query(F.data == "btn_video")
async def video_menu(callback: types.CallbackQuery):
    builder = InlineKeyboardBuilder()
    builder.row(
        types.InlineKeyboardButton(text="🎥 720p", callback_data="dl_720p"),
        types.InlineKeyboardButton(text="📱 480p", callback_data="dl_480p")
    )
    builder.row(types.InlineKeyboardButton(
        text="⬅️ Orqaga", callback_data="back_to_main"
    ))
    await callback.message.edit_text(
        "Video sifatini tanlang:", reply_markup=builder.as_markup()
    )


@dp.callback_query(F.data == "back_to_main")
async def back_main(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "Nima yuklamoqchisiz?", reply_markup=get_main_menu()
    )




# --- VIDEO/AUDIO YUKLASH ---
@dp.callback_query(F.data.startswith("dl_"))
async def start_dl(callback: types.CallbackQuery):
    user_id = callback.from_user.id

    if is_rate_limited(user_id):
        await callback.answer(
            f"⏳ Iltimos, {COOLDOWN_SECONDS} soniya kuting!", show_alert=True
        )
        return

    mode = callback.data.split("_", 1)[1]
    url = user_links.get(user_id)

    if not url:
        await callback.message.edit_text("❌ Link topilmadi. Iltimos, qaytadan link yuboring.")
        return

    msg = await callback.message.edit_text("⏳ Yuklanmoqda...")
    path = None

    try:
        loop = asyncio.get_running_loop()
        path, artist, track = await loop.run_in_executor(executor, download_media, url, mode)

        if not check_file_size(path):
            await callback.message.answer(
                "❌ Fayl hajmi 50MB dan katta! Telegram yuborishga ruxsat bermaydi.\n"
                "Pastroq sifat tanlang yoki MP3 ga o'giring."
            )
            await msg.delete()
            return

        input_f = types.FSInputFile(path)
        cap = f"Tayyor! ✅\nManba: {BOT_USERNAME}"

        if 'mp3' in mode or 'music' in mode:
            await callback.message.answer_audio(
                input_f, caption=cap, title=track, performer=artist
            )
        else:
            await callback.message.answer_video(input_f, caption=cap)

        await msg.delete()
        logger.info(f"Yuklandi: {mode} | user={user_id} | url={url}")

    except Exception as e:
        logger.error(f"Yuklash xato: mode={mode}, url={url}, xato={e}")
        if mode == 'original_music':
            builder = InlineKeyboardBuilder()
            builder.row(types.InlineKeyboardButton(
                text="✂️ Videoni MP3 ga o'girib ber", callback_data="dl_convert_mp3"
            ))
            builder.row(types.InlineKeyboardButton(
                text="⬅️ Orqaga", callback_data="back_to_main"
            ))
            await callback.message.edit_text(
                f"⚠️ Musiqa topilmadi. MP3 qilib beraymi?\n\nManba: {BOT_USERNAME}",
                reply_markup=builder.as_markup()
            )
        else:
            await callback.message.answer("❌ Xatolik yuz berdi! Qaytadan urinib ko'ring.")
    finally:
        safe_remove(path)


# ══════════════════════════════════════════
#  ISHGA TUSHURISH
# ══════════════════════════════════════════
async def main():
    logger.info("🤖 Bot ishga tushdi!")
    try:
        await dp.start_polling(bot)
    finally:
        executor.shutdown(wait=False)
        db.close()
        logger.info("🛑 Bot to'xtatildi.")


if __name__ == "__main__":
    asyncio.run(main())
