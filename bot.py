import os
import asyncio
import logging
import random
import json
from datetime import datetime

import firebase_admin
from firebase_admin import credentials, firestore
from zoneinfo import ZoneInfo

from google import genai
from google.genai import types

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters,
)

# =========================================================
# CONFIG
# =========================================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")
if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY is missing")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "RJteam1").strip().lstrip("@")
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID", "").strip()
TARGET_CHAT_ID = os.getenv("TARGET_CHAT_ID", "").strip()
AUTOPOST_FILE = os.getenv("AUTOPOST_FILE", "autoposts.json")
BD_TZ = ZoneInfo("Asia/Dhaka")
AUTOPOST_ENABLED = os.getenv("AUTOPOST_ENABLED", "true").strip().lower() == "true"

# =========================================================
# FIREBASE
# =========================================================
FIREBASE_SERVICE_ACCOUNT_JSON = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
FIREBASE_COLLECTION = os.getenv("AUTOPOST_FIREBASE_COLLECTION", "rj_bot_config").strip()
FIREBASE_DOCUMENT = os.getenv("AUTOPOST_FIREBASE_DOCUMENT", "autopost").strip()

def init_firebase():
    if not FIREBASE_SERVICE_ACCOUNT_JSON:
        logger.warning("FIREBASE_SERVICE_ACCOUNT_JSON is not set. Using local storage.")
        return None
    try:
        if not firebase_admin._apps:
            service_account = json.loads(FIREBASE_SERVICE_ACCOUNT_JSON)
            firebase_admin.initialize_app(credentials.Certificate(service_account))
        return firestore.client()
    except Exception as e:
        logger.error("Firebase initialization failed: %s", e, exc_info=True)
        return None

FIRESTORE_DB = init_firebase()

# =========================================================
# STORAGE
# =========================================================
def _local_load_data():
    default = {
        "posts": [], "next_id": 1, "enabled": AUTOPOST_ENABLED,
        "groups": [], "channels": [], "channel_messages": [
            "হাই সবাই 👋",
            "সবার কী অবস্থা? 😊",
            "কেমন আছেন সবাই? ❤️",
            "RJ Team Bangladesh থেকে শুভেচ্ছা! 🇧🇩",
            "আজকের দিনটি সুন্দর হোক। 🌸",
        ],
        "channel_enabled": False,
        "channel_message_index": 0,
    }
    try:
        if not os.path.exists(AUTOPOST_FILE):
            return default
        with open(AUTOPOST_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        for k, v in default.items():
            data.setdefault(k, v)
        return data
    except Exception as e:
        logger.error("Could not load storage: %s", e)
        return default

def load_data():
    local = _local_load_data()
    if FIRESTORE_DB is None:
        return local
    try:
        snap = FIRESTORE_DB.collection(FIREBASE_COLLECTION).document(FIREBASE_DOCUMENT).get()
        if snap.exists:
            data = snap.to_dict() or {}
            for k, v in local.items():
                data.setdefault(k, v)
            return data
        FIRESTORE_DB.collection(FIREBASE_COLLECTION).document(FIREBASE_DOCUMENT).set(local)
        return local
    except Exception as e:
        logger.error("Firebase load failed: %s", e, exc_info=True)
        return local

DATA = load_data()

def save_data():
    data = {
        "posts": DATA.get("posts", []),
        "next_id": int(DATA.get("next_id", 1)),
        "enabled": bool(DATA.get("enabled", True)),
        "groups": DATA.get("groups", []) or [],
        "channels": DATA.get("channels", []) or [],
        "channel_messages": DATA.get("channel_messages", []) or [],
        "channel_enabled": bool(DATA.get("channel_enabled", False)),
        "channel_message_index": int(DATA.get("channel_message_index", 0)),
    }
    try:
        tmp = AUTOPOST_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, AUTOPOST_FILE)
    except Exception as e:
        logger.error("Local save failed: %s", e)
    if FIRESTORE_DB is not None:
        try:
            FIRESTORE_DB.collection(FIREBASE_COLLECTION).document(FIREBASE_DOCUMENT).set(data)
        except Exception as e:
            logger.error("Firebase save failed: %s", e, exc_info=True)

def normalize_post_ids():
    posts = DATA.get("posts", []) or []
    for i, p in enumerate(posts, 1):
        p["id"] = i
    DATA["posts"] = posts
    DATA["next_id"] = len(posts) + 1

normalize_post_ids()
save_data()

# =========================================================
# ADMIN / HELPERS
# =========================================================
def is_admin(update: Update):
    user = update.effective_user
    if not user:
        return False
    if ADMIN_USER_ID and str(user.id) == ADMIN_USER_ID:
        return True
    username = (user.username or "").strip().lstrip("@")
    return bool(ADMIN_USERNAME) and username.lower() == ADMIN_USERNAME.lower()

async def admin_only(update):
    if is_admin(update):
        return True
    if update.message:
        await update.message.reply_text("⛔ এই command শুধু Admin ব্যবহার করতে পারবে।")
    return False

def parse_time(value):
    value = value.strip().upper().replace(".", "")
    for fmt in ("%I:%M %p", "%I %p", "%H:%M"):
        try:
            return datetime.strptime(value, fmt).strftime("%H:%M")
        except ValueError:
            pass
    return None

def display_time(hhmm):
    try:
        return datetime.strptime(hhmm, "%H:%M").strftime("%I:%M %p").lstrip("0")
    except Exception:
        return hhmm

# =========================================================
# GROUPS
# =========================================================
def groups():
    return DATA.setdefault("groups", [])

def save_group(chat_id, title="", username=""):
    try:
        chat_id = int(chat_id)
    except Exception:
        return False
    for g in groups():
        if int(g.get("chat_id", 0)) == chat_id:
            g["title"] = title or g.get("title") or f"Group {chat_id}"
            g["username"] = username or g.get("username", "")
            g["active"] = True
            save_data()
            return False
    groups().append({
        "chat_id": chat_id,
        "title": title or f"Group {chat_id}",
        "username": username or "",
        "active": True,
    })
    save_data()
    return True

def active_group_ids():
    return [int(g["chat_id"]) for g in groups() if g.get("active", True)]

def group_by_ref(ref):
    try:
        n = int(ref)
    except Exception:
        return None
    if n >= 1 and n <= len(groups()):
        return int(groups()[n-1]["chat_id"])
    for g in groups():
        if int(g.get("chat_id", 0)) == n:
            return n
    return None

def group_text():
    if not groups():
        return "👥 GROUPS\n\nTotal: 0\n/addgroup দিয়ে বর্তমান গ্রুপ যোগ করুন।"
    out = [f"👥 GROUPS\n\nTotal: {len(groups())}\n"]
    for i, g in enumerate(groups(), 1):
        state = "🟢 Active" if g.get("active", True) else "⏸️ Paused"
        out.append(f"{i}. {g.get('title','Group')}\n🆔 {g['chat_id']}\n{state}")
    return "\n\n".join(out)

# =========================================================
# CHANNELS — NO CODE-LEVEL COUNT LIMIT
# Bot must be admin in each channel and have permission to post.
# =========================================================
def channels():
    return DATA.setdefault("channels", [])

def save_channel(chat_id, title="", username=""):
    try:
        chat_id = int(chat_id)
    except Exception:
        return False
    for c in channels():
        if int(c.get("chat_id", 0)) == chat_id:
            c["title"] = title or c.get("title") or f"Channel {chat_id}"
            c["username"] = username or c.get("username", "")
            c["active"] = True
            save_data()
            return False
    channels().append({
        "chat_id": chat_id,
        "title": title or f"Channel {chat_id}",
        "username": username or "",
        "active": True,
    })
    save_data()
    return True

def active_channel_ids():
    return [int(c["chat_id"]) for c in channels() if c.get("active", True)]

def channel_by_ref(ref):
    try:
        n = int(ref)
    except Exception:
        return None
    if 1 <= n <= len(channels()):
        return int(channels()[n-1]["chat_id"])
    for c in channels():
        if int(c.get("chat_id", 0)) == n:
            return n
    return None

def channel_text():
    if not channels():
        return "📢 CHANNELS\n\nTotal: 0\nচ্যানেলে /addchannel দিয়ে যোগ করুন।"
    out = [f"📢 CHANNELS\n\nTotal: {len(channels())}\n"]
    for i, c in enumerate(channels(), 1):
        state = "🟢 Active" if c.get("active", True) else "⏸️ Paused"
        uname = f"\n🔗 @{c['username']}" if c.get("username") else ""
        out.append(f"{i}. {c.get('title','Channel')}{uname}\n🆔 {c['chat_id']}\n{state}")
    return "\n\n".join(out)

def channel_message_list():
    return DATA.setdefault("channel_messages", [])

# =========================================================
# SCHEDULED AUTO POSTS
# =========================================================
def add_post(hhmm, ptype, text="", photo_file_id=None, target_groups=None):
    normalize_post_ids()
    post_id = len(DATA["posts"]) + 1
    DATA["posts"].append({
        "id": post_id,
        "time": hhmm,
        "type": ptype,
        "text": text or "",
        "photo_file_id": photo_file_id,
        "last_sent_by_group": {},
        "target_groups": target_groups or [],
    })
    DATA["next_id"] = post_id + 1
    save_data()
    return post_id

def delete_post(pid):
    try:
        pid = int(pid)
    except Exception:
        return False
    old = len(DATA["posts"])
    DATA["posts"] = [p for p in DATA["posts"] if int(p.get("id", -1)) != pid]
    if len(DATA["posts"]) == old:
        return False
    normalize_post_ids()
    save_data()
    return True

def find_post(pid):
    for p in DATA.get("posts", []):
        if int(p.get("id", -1)) == int(pid):
            return p
    return None

# =========================================================
# CHANNEL 1-MINUTE ROTATING MESSAGE
# =========================================================
async def channel_auto_worker(application):
    logger.info("Channel auto-message worker started.")
    last_minute = None
    while True:
        try:
            if DATA.get("channel_enabled", False) and active_channel_ids() and channel_message_list():
                now = datetime.now(BD_TZ)
                minute_key = now.strftime("%Y-%m-%d %H:%M")
                if minute_key != last_minute:
                    last_minute = minute_key
                    messages = channel_message_list()
                    idx = int(DATA.get("channel_message_index", 0)) % len(messages)
                    msg = str(messages[idx]).strip()
                    DATA["channel_message_index"] = (idx + 1) % len(messages)
                    save_data()

                    for chat_id in active_channel_ids():
                        try:
                            await application.bot.send_message(
                                chat_id=chat_id,
                                text=msg[:4096],
                                disable_web_page_preview=True,
                            )
                            logger.info("Channel auto message sent: %s -> %s", chat_id, msg[:60])
                        except Exception as e:
                            logger.error("Channel auto message failed | %s | %s", chat_id, e)
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("Channel worker error: %s", e, exc_info=True)
            await asyncio.sleep(10)

async def autopost_worker(application):
    logger.info("Scheduled group auto-post worker started.")
    while True:
        try:
            if DATA.get("enabled", True):
                now = datetime.now(BD_TZ)
                hhmm = now.strftime("%H:%M")
                today = now.strftime("%Y-%m-%d")
                default_groups = active_group_ids()

                if not default_groups and TARGET_CHAT_ID:
                    try:
                        legacy = int(TARGET_CHAT_ID)
                        save_group(legacy, "Legacy Target Group")
                        default_groups = active_group_ids()
                    except Exception:
                        pass

                for post in DATA.get("posts", []):
                    if post.get("time") != hhmm:
                        continue
                    sent = post.setdefault("last_sent_by_group", {})
                    targets = post.get("target_groups") or default_groups
                    for chat_id in targets:
                        key = str(chat_id)
                        if sent.get(key) == today:
                            continue
                        try:
                            if post.get("type") == "photo" and post.get("photo_file_id"):
                                await application.bot.send_photo(
                                    chat_id=int(chat_id),
                                    photo=post["photo_file_id"],
                                    caption=(post.get("text") or "")[:1024] or None,
                                )
                            else:
                                await application.bot.send_message(
                                    chat_id=int(chat_id),
                                    text=(post.get("text") or "")[:4096],
                                    disable_web_page_preview=True,
                                )
                            sent[key] = today
                            save_data()
                        except Exception as e:
                            logger.error("Scheduled post failed | %s | %s", chat_id, e)
            await asyncio.sleep(20)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("Autopost worker error: %s", e, exc_info=True)
            await asyncio.sleep(20)

async def post_init(application):
    application.bot_data["autopost_task"] = asyncio.create_task(autopost_worker(application))
    application.bot_data["channel_task"] = asyncio.create_task(channel_auto_worker(application))

async def post_shutdown(application):
    for key in ("autopost_task", "channel_task"):
        task = application.bot_data.get(key)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

# =========================================================
# GEMINI
# =========================================================
client = genai.Client(api_key=GEMINI_API_KEY)
GEMINI_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-2.5-flash",
    "gemini-3.5-flash-lite",
]
GEMINI_SEMAPHORE = asyncio.Semaphore(3)

SYSTEM_PROMPT = """
তুমি RJ Team Bangladesh-এর বন্ধুসুলভ Telegram AI Assistant।
ব্যবহারকারীর প্রশ্নের উত্তর বাংলায় দেবে। English বা Banglish হলেও
বাংলায় উত্তর দেবে। সাধারণ প্রশ্নে সংক্ষিপ্ত ও স্বাভাবিক উত্তর দাও।
মজার SMS হলে হালকা মজার reply, emotional হলে সহানুভূতিশীল reply।
গুরুতর বিষয়ে মজা করবে না। অপ্রয়োজনীয় দীর্ঘ explanation দেবে না।
"""

def transient(s):
    s = s.lower()
    return any(x in s for x in [
        "429","500","502","503","504","resource_exhausted",
        "unavailable","timeout","rate limit","too many requests"
    ])

async def generate_gemini(prompt, system_instruction=None):
    async with GEMINI_SEMAPHORE:
        for mi, model in enumerate(GEMINI_MODELS):
            attempts = 1 if mi == 0 else 2
            for attempt in range(attempts):
                try:
                    config = types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        max_output_tokens=500,
                        automatic_function_calling=types.AutomaticFunctionCallingConfig(
                            disable=True
                        ),
                    )
                    response = await client.aio.models.generate_content(
                        model=model, contents=prompt, config=config
                    )
                    answer = (response.text or "").strip()
                    if answer:
                        return answer
                except Exception as e:
                    logger.error("Gemini %s attempt %s: %s", model, attempt + 1, e)
                    if transient(str(e)) and attempt + 1 < attempts:
                        await asyncio.sleep(2 ** (attempt + 1) + random.uniform(.2, .8))
                    else:
                        break
    return None

# =========================================================
# BASIC COMMANDS
# =========================================================
ABOUT_TEXT = """
🤖 About AI Assistant

আমি RJ Team Bangladesh Hacker Community-এর পক্ষ থেকে
তোমার AI Assistant। 😊

💬 সাধারণ প্রশ্ন → উত্তর
😂 মজার SMS → মজার reply
❤️ Emotional SMS → emotional reply
🌐 Translation → অনুবাদ

👑 Owner: @RJteam1
📢 Channel: @RJteam123890
"""

async def start(update, context):
    if not update.message:
        return
    kb = [
        [InlineKeyboardButton("ℹ️ About", callback_data="about"),
         InlineKeyboardButton("🌐 Translate", callback_data="translate_help")],
        [InlineKeyboardButton("📢 Channel", url="https://t.me/RJteam123890"),
         InlineKeyboardButton("👑 Owner", url="https://t.me/RJteam1")],
    ]
    await update.message.reply_text(
        "👋 হ্যালো বন্ধু! ❤️\n\nআমি তোমার AI Assistant। 🤖\n\n"
        "যেকোনো SMS পাঠাও, আমি বাংলায় উত্তর দেব।",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def help_command(update, context):
    if not update.message:
        return
    await update.message.reply_text(
        "🤖 RJ Team Bot Help\n\n"
        "💬 AI: যেকোনো SMS পাঠাও\n"
        "🌐 /translate Hello\n"
        "ℹ️ /about\n\n"
        "👥 GROUP\n"
        "/addgroup\n/groups\n/delgroup 2\n/pausegroup 2\n/renamegroup 2 নতুন নাম\n"
        "/status\n/broadcast মেসেজ\n\n"
        "📢 CHANNEL\n"
        "/addchannel - যে channel-এ bot admin, সেখান থেকে দিন\n"
        "/channels - channel list\n"
        "/delchannel 2\n"
        "/pausechannel 2\n"
        "/channelon\n/channeloff\n"
        "/channelmsg নতুন মেসেজ\n"
        "/channelmsgs - saved messages\n"
        "/clearchannelmsgs\n"
        "/channeltest\n\n"
        "⏱️ Channel Auto Message প্রতি ১ মিনিটে ১টি করে saved message পাঠায়।\n\n"
        "📅 AUTO POST\n"
        "/autopost on / off\n"
        "/autopost list\n"
        "/autopost add 8:30 PM পোস্ট\n"
        "/autopost addphoto 8:30 PM Caption (photo-তে reply)\n"
        "/autopost delete 1"
    )

async def about(update, context):
    if not update.message:
        return
    await update.message.reply_text(
        ABOUT_TEXT,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("👑 Owner", url="https://t.me/RJteam1"),
            InlineKeyboardButton("📢 Channel", url="https://t.me/RJteam123890")
        ]]),
        disable_web_page_preview=True,
    )

async def translate_text(text):
    return await generate_gemini(
        f"""Translate naturally. Detect source language.
If no target language is specified, translate to Bangla.
Return only the translation.

Text:
{text}""",
        "You are a professional translation assistant. Return only the translation."
    )

async def translate(update, context):
    if not update.message:
        return
    if not context.args:
        await update.message.reply_text("ব্যবহার: /translate Hello, how are you?")
        return
    result = await translate_text(" ".join(context.args))
    await update.message.reply_text("🌐 Translation:\n\n" + (result or "❌ Translation ব্যর্থ হয়েছে।"))

async def handle_message(update, context):
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    if not text:
        return
    answer = await generate_gemini(text, SYSTEM_PROMPT)
    answer = answer or "😅 AI সার্ভার এখন ব্যস্ত। একটু পরে আবার চেষ্টা করো। ❤️"
    context.user_data["last_ai_reply"] = answer
    await update.message.reply_text(
        answer,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🌐 Translate", callback_data="translate_last")
        ]]),
        disable_web_page_preview=True,
    )

# =========================================================
# GROUP COMMANDS
# =========================================================
async def addgroup(update, context):
    if not await admin_only(update): return
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        await update.message.reply_text("❌ গ্রুপের ভিতর থেকে /addgroup দিন।")
        return
    save_group(chat.id, chat.title or "Group", getattr(chat, "username", "") or "")
    await update.message.reply_text("✅ Group saved.\n\n" + group_text())

async def groups_command(update, context):
    if await admin_only(update):
        await update.message.reply_text(group_text())

async def delgroup(update, context):
    if not await admin_only(update): return
    if not context.args:
        await update.message.reply_text("ব্যবহার: /delgroup 2")
        return
    cid = group_by_ref(context.args[0])
    if cid is None:
        await update.message.reply_text("❌ Group পাওয়া যায়নি।")
        return
    DATA["groups"] = [g for g in groups() if int(g["chat_id"]) != cid]
    save_data()
    await update.message.reply_text("🗑️ Group deleted.\n\n" + group_text())

async def pausegroup(update, context):
    if not await admin_only(update): return
    if not context.args:
        await update.message.reply_text("ব্যবহার: /pausegroup 2")
        return
    cid = group_by_ref(context.args[0])
    if cid is None:
        await update.message.reply_text("❌ Group পাওয়া যায়নি।")
        return
    for g in groups():
        if int(g["chat_id"]) == cid:
            g["active"] = not g.get("active", True)
    save_data()
    await update.message.reply_text(group_text())

async def renamegroup(update, context):
    if not await admin_only(update): return
    if len(context.args) < 2:
        await update.message.reply_text("ব্যবহার: /renamegroup 2 নতুন নাম")
        return
    cid = group_by_ref(context.args[0])
    if cid is None:
        await update.message.reply_text("❌ Group পাওয়া যায়নি।")
        return
    name = " ".join(context.args[1:]).strip()
    for g in groups():
        if int(g["chat_id"]) == cid:
            g["title"] = name
    save_data()
    await update.message.reply_text("✅ নাম পরিবর্তন হয়েছে।")

async def broadcast(update, context):
    if not await admin_only(update): return
    text = " ".join(context.args).strip()
    if not text and update.message.reply_to_message:
        text = update.message.reply_to_message.text or update.message.reply_to_message.caption or ""
    if not text:
        await update.message.reply_text("ব্যবহার: /broadcast আপনার মেসেজ")
        return
    ok = fail = 0
    for cid in active_group_ids():
        try:
            await context.bot.send_message(cid, text[:4096])
            ok += 1
        except Exception:
            fail += 1
    await update.message.reply_text(f"📢 Broadcast complete\n\n✅ Sent: {ok}\n❌ Failed: {fail}")

# =========================================================
# CHANNEL COMMANDS
# =========================================================
async def addchannel(update, context):
    if not await admin_only(update): return
    chat = update.effective_chat
    if not chat or chat.type != "channel":
        await update.message.reply_text(
            "❌ Channel-এর নিজের পোস্ট/কোনো message-এর reply হিসেবে /addchannel দিন।\n"
            "Bot-কে ওই channel-এ Admin করে posting permission দিন।"
        )
        return
    save_channel(chat.id, chat.title or "Channel", getattr(chat, "username", "") or "")
    await update.message.reply_text("⚠️ Channel-এ সাধারণত command message পাঠানো যায় না।\n"
                                    "Channel-এ bot admin করার পর channel ID/username দিয়ে /addchannel ব্যবহার করুন।")

async def addchannel_ref(update, context):
    if not await admin_only(update): return
    if not context.args:
        await update.message.reply_text(
            "ব্যবহার:\n/addchannel @YourChannel\nঅথবা\n/addchannel -1001234567890\n\n"
            "Bot-কে আগে Channel Admin করুন।"
        )
        return
    ref = context.args[0]
    cid = None
    if ref.startswith("@"):
        try:
            chat = await context.bot.get_chat(ref)
            cid = chat.id
        except Exception as e:
            await update.message.reply_text("❌ Channel পাওয়া/অ্যাক্সেস করা যায়নি। Bot-কে আগে Admin করুন।")
            return
    else:
        try:
            cid = int(ref)
            chat = await context.bot.get_chat(cid)
        except Exception:
            await update.message.reply_text("❌ Channel ID ভুল বা bot-এর access নেই।")
            return
    save_channel(cid, getattr(chat, "title", None) or str(ref), getattr(chat, "username", "") or "")
    await update.message.reply_text(
        "✅ Channel Auto Message-এর জন্য যোগ হয়েছে।\n\n" + channel_text()
    )

async def channels_command(update, context):
    if await admin_only(update):
        await update.message.reply_text(channel_text())

async def delchannel(update, context):
    if not await admin_only(update): return
    if not context.args:
        await update.message.reply_text("ব্যবহার: /delchannel 2")
        return
    cid = channel_by_ref(context.args[0])
    if cid is None:
        await update.message.reply_text("❌ Channel পাওয়া যায়নি।")
        return
    DATA["channels"] = [c for c in channels() if int(c["chat_id"]) != cid]
    save_data()
    await update.message.reply_text("🗑️ Channel removed.\n\n" + channel_text())

async def pausechannel(update, context):
    if not await admin_only(update): return
    if not context.args:
        await update.message.reply_text("ব্যবহার: /pausechannel 2")
        return
    cid = channel_by_ref(context.args[0])
    if cid is None:
        await update.message.reply_text("❌ Channel পাওয়া যায়নি।")
        return
    for c in channels():
        if int(c["chat_id"]) == cid:
            c["active"] = not c.get("active", True)
    save_data()
    await update.message.reply_text(channel_text())

async def channelon(update, context):
    if await admin_only(update):
        DATA["channel_enabled"] = True
        save_data()
        await update.message.reply_text(
            "✅ Channel Auto Message ON 🟢\nপ্রতি ১ মিনিটে saved message থেকে ১টি করে পাঠানো হবে।"
        )

async def channeloff(update, context):
    if await admin_only(update):
        DATA["channel_enabled"] = False
        save_data()
        await update.message.reply_text("⛔ Channel Auto Message OFF 🔴")

async def channelmsg(update, context):
    if not await admin_only(update): return
    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text(
            "ব্যবহার: /channelmsg হাই সবাই 👋\n\n"
            "প্রতি মিনিটে পরের saved message পাঠানো হবে।"
        )
        return
    channel_message_list().append(text[:4096])
    save_data()
    await update.message.reply_text(
        f"✅ Channel message saved.\n🆔 Message ID: {len(channel_message_list())}\n\n"
        "এখন /channelon দিলে প্রতি মিনিটে পাঠানো শুরু হবে।"
    )

async def channelmsgs(update, context):
    if not await admin_only(update): return
    msgs = channel_message_list()
    if not msgs:
        await update.message.reply_text("📢 Saved channel messages: 0")
        return
    out = ["📢 SAVED CHANNEL MESSAGES\n"]
    for i, m in enumerate(msgs, 1):
        out.append(f"{i}. {m}")
    await update.message.reply_text("\n\n".join(out[:30])[:4096])

async def clearchannelmsgs(update, context):
    if not await admin_only(update): return
    DATA["channel_messages"] = []
    DATA["channel_message_index"] = 0
    save_data()
    await update.message.reply_text("🗑️ সব channel auto messages delete হয়েছে।")

async def channeltest(update, context):
    if not await admin_only(update): return
    if not active_channel_ids():
        await update.message.reply_text("❌ কোনো active channel নেই।")
        return
    ok = fail = 0
    for cid in active_channel_ids():
        try:
            await context.bot.send_message(cid, "🧪 RJ Team Channel Auto Message Test সফল হয়েছে।")
            ok += 1
        except Exception as e:
            fail += 1
            logger.error("Channel test failed %s: %s", cid, e)
    await update.message.reply_text(f"🧪 Channel test\n\n✅ Sent: {ok}\n❌ Failed: {fail}")

# =========================================================
# AUTOPOST COMMAND
# =========================================================
async def autopost(update, context):
    if not await admin_only(update): return
    args = context.args
    if not args:
        await update.message.reply_text(
            "/autopost on\n/autopost off\n/autopost list\n"
            "/autopost add 8:30 PM পোস্ট\n"
            "/autopost addphoto 8:30 PM Caption (photo-তে reply)\n"
            "/autopost delete 1"
        )
        return
    action = args[0].lower()
    if action == "on":
        DATA["enabled"] = True; save_data()
        await update.message.reply_text("✅ Auto Post ON 🟢"); return
    if action == "off":
        DATA["enabled"] = False; save_data()
        await update.message.reply_text("⛔ Auto Post OFF 🔴"); return
    if action == "list":
        normalize_post_ids()
        if not DATA["posts"]:
            await update.message.reply_text("📋 Saved posts: 0"); return
        out = [f"📋 SAVED POSTS\nTotal: {len(DATA['posts'])}\n"]
        for p in DATA["posts"]:
            out.append(
                f"🆔 ID {p['id']}\n⏰ {display_time(p['time'])}\n"
                f"📌 {p['type']}\n📝 {p.get('text') or '(empty)'}"
            )
        await update.message.reply_text("\n\n".join(out)[:4096]); return
    if action == "delete":
        if len(args) < 2 or not args[1].isdigit():
            await update.message.reply_text("ব্যবহার: /autopost delete 1"); return
        await update.message.reply_text(
            "🗑️ Deleted." if delete_post(int(args[1])) else "❌ ID পাওয়া যায়নি।"
        ); return
    if action == "add":
        if len(args) < 3:
            await update.message.reply_text("ব্যবহার: /autopost add 8:30 PM পোস্ট"); return
        if len(args) >= 3 and args[2].upper() in ("AM", "PM"):
            t = parse_time(args[1] + " " + args[2]); start = 3
        else:
            t = parse_time(args[1]); start = 2
        if not t:
            await update.message.reply_text("❌ সময় ভুল।"); return
        text = " ".join(args[start:]).strip()
        pid = add_post(t, "text", text=text)
        await update.message.reply_text(f"✅ Saved ID {pid} | ⏰ {display_time(t)}"); return
    if action == "addphoto":
        if len(args) < 2:
            await update.message.reply_text("Photo-তে reply করে /autopost addphoto 8:30 PM Caption"); return
        if len(args) >= 3 and args[2].upper() in ("AM", "PM"):
            t = parse_time(args[1] + " " + args[2]); start = 3
        else:
            t = parse_time(args[1]); start = 2
        r = update.message.reply_to_message
        if not t or not r or not r.photo:
            await update.message.reply_text("❌ Photo-তে reply করে command দিন।"); return
        caption = " ".join(args[start:]).strip() or (r.caption or "")
        pid = add_post(t, "photo", caption, r.photo[-1].file_id)
        await update.message.reply_text(f"✅ Photo saved ID {pid} | ⏰ {display_time(t)}"); return
    await update.message.reply_text("❌ Unknown /autopost command")

# =========================================================
# STATUS / SHORT COMMANDS
# =========================================================
async def status(update, context):
    if not await admin_only(update): return
    await update.message.reply_text(
        "📊 RJ BOT STATUS\n\n"
        f"🤖 Scheduled Auto Post: {'🟢 ON' if DATA.get('enabled') else '🔴 OFF'}\n"
        f"📢 Channel 1-Minute Auto Message: {'🟢 ON' if DATA.get('channel_enabled') else '🔴 OFF'}\n"
        f"👥 Groups: {len(groups())} | Active: {len(active_group_ids())}\n"
        f"📢 Channels: {len(channels())} | Active: {len(active_channel_ids())}\n"
        f"💬 Channel Messages: {len(channel_message_list())}\n"
        f"📋 Scheduled Posts: {len(DATA.get('posts', []))}\n"
        "💾 Storage: Firebase + local backup"
    )

async def list_cmd(update, context):
    await autopost(update, context)

async def delete_cmd(update, context):
    if await admin_only(update):
        if not context.args or not context.args[0].isdigit():
            await update.message.reply_text("ব্যবহার: /delete 1"); return
        await update.message.reply_text(
            "🗑️ Deleted." if delete_post(int(context.args[0])) else "❌ ID পাওয়া যায়নি।"
        )

# =========================================================
# BUTTONS
# =========================================================
async def button_handler(update, context):
    q = update.callback_query
    if not q: return
    await q.answer()
    if q.data == "about":
        await q.message.reply_text(ABOUT_TEXT)
    elif q.data == "translate_help":
        await q.message.reply_text("🌐 /translate Hello, how are you?")
    elif q.data == "translate_last":
        text = context.user_data.get("last_ai_reply")
        if not text:
            await q.message.reply_text("❌ আগের reply পাওয়া যায়নি।"); return
        result = await translate_text(text)
        await q.message.reply_text("🌐 Translation:\n\n" + (result or "❌ ব্যর্থ হয়েছে।"))

async def error_handler(update, context):
    logger.error("Telegram error: %s", context.error, exc_info=True)

# =========================================================
# MAIN
# =========================================================
def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # Core
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("about", about))
    app.add_handler(CommandHandler("translate", translate))

    # Groups
    app.add_handler(CommandHandler("addgroup", addgroup))
    app.add_handler(CommandHandler("groups", groups_command))
    app.add_handler(CommandHandler("delgroup", delgroup))
    app.add_handler(CommandHandler("pausegroup", pausegroup))
    app.add_handler(CommandHandler("renamegroup", renamegroup))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CommandHandler("status", status))

    # Channels
    app.add_handler(CommandHandler("addchannel", addchannel_ref))
    app.add_handler(CommandHandler("channels", channels_command))
    app.add_handler(CommandHandler("delchannel", delchannel))
    app.add_handler(CommandHandler("pausechannel", pausechannel))
    app.add_handler(CommandHandler("channelon", channelon))
    app.add_handler(CommandHandler("channeloff", channeloff))
    app.add_handler(CommandHandler("channelmsg", channelmsg))
    app.add_handler(CommandHandler("channelmsgs", channelmsgs))
    app.add_handler(CommandHandler("clearchannelmsgs", clearchannelmsgs))
    app.add_handler(CommandHandler("channeltest", channeltest))

    # Scheduled posts
    app.add_handler(CommandHandler("autopost", autopost))
    app.add_handler(CommandHandler("list", list_cmd))
    app.add_handler(CommandHandler("delete", delete_cmd))

    # Buttons / AI
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)

    port = int(os.getenv("PORT", "10000"))
    render_url = os.getenv("RENDER_EXTERNAL_URL")

    if render_url:
        webhook_url = render_url.rstrip("/") + "/telegram/" + BOT_TOKEN
        logger.info("Starting Render webhook...")
        app.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path="telegram/" + BOT_TOKEN,
            webhook_url=webhook_url,
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES,
        )
    else:
        logger.info("Starting Telegram polling...")
        app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
