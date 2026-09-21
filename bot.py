import os
import re
import json
import threading
from datetime import time
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, HTTPServer

import firebase_admin
from firebase_admin import credentials, firestore

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")

TARGET_CHAT_RAW = (
    os.getenv("TARGET_CHAT_ID")
    or os.getenv("CHANNEL_USERNAME")
    or "@RJteam123890"
)

ADMIN_USERNAME = os.getenv(
    "ADMIN_USERNAME",
    "RJteam1"
).strip().lstrip("@")

TZ = ZoneInfo("Asia/Dhaka")

PORT = int(os.getenv("PORT", "10000"))

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN environment variable is missing."
    )


# =========================================================
# TARGET CHAT
# =========================================================

def parse_target_chat(value):
    value = str(value).strip()

    if re.fullmatch(r"-?\d+", value):
        return int(value)

    return value


TARGET_CHAT = parse_target_chat(TARGET_CHAT_RAW)


# =========================================================
# FIREBASE
# =========================================================

def init_firebase():

    # Option 1:
    # Firebase service account JSON as environment variable
    firebase_json = os.getenv("FIREBASE_SERVICE_ACCOUNT")

    if firebase_json:

        try:
            service_account_info = json.loads(firebase_json)

            cred = credentials.Certificate(
                service_account_info
            )

        except Exception as e:
            raise RuntimeError(
                f"Invalid FIREBASE_SERVICE_ACCOUNT: {e}"
            )

    # Option 2:
    # Local JSON file
    else:

        json_file = os.getenv(
            "FIREBASE_CREDENTIALS",
            "firebase-service-account.json"
        )

        if not os.path.exists(json_file):
            raise RuntimeError(
                "Firebase credentials missing. "
                "Set FIREBASE_SERVICE_ACCOUNT or "
                "upload firebase-service-account.json."
            )

        cred = credentials.Certificate(json_file)

    if not firebase_admin._apps:
        firebase_admin.initialize_app(cred)

    return firestore.client()


db = init_firebase()

POSTS_COLLECTION = "scheduled_posts"


# =========================================================
# RENDER HEALTH SERVER
# =========================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):

        self.send_response(200)

        self.send_header(
            "Content-Type",
            "text/plain"
        )

        self.end_headers()

        self.wfile.write(
            b"Bot is running."
        )

    def log_message(self, format, *args):
        return


def start_health_server():

    server = HTTPServer(
        ("0.0.0.0", PORT),
        HealthHandler
    )

    server.serve_forever()


threading.Thread(
    target=start_health_server,
    daemon=True
).start()


# =========================================================
# TIME SYSTEM
# =========================================================

def normalize_time(value):

    value = value.strip().upper()

    # 12-hour format
    match = re.fullmatch(
        r"(\d{1,2}):(\d{2})\s*(AM|PM)",
        value
    )

    if match:

        hour = int(match.group(1))
        minute = int(match.group(2))
        ampm = match.group(3)

        if hour < 1 or hour > 12:
            raise ValueError(
                "Hour must be between 1 and 12."
            )

        if minute < 0 or minute > 59:
            raise ValueError(
                "Minute must be between 00 and 59."
            )

        if ampm == "AM":

            if hour == 12:
                hour = 0

        else:

            if hour != 12:
                hour += 12

        return f"{hour:02d}:{minute:02d}"

    # 24-hour format
    match = re.fullmatch(
        r"(\d{1,2}):(\d{2})",
        value
    )

    if match:

        hour = int(match.group(1))
        minute = int(match.group(2))

        if hour < 0 or hour > 23:
            raise ValueError(
                "Hour must be between 00 and 23."
            )

        if minute < 0 or minute > 59:
            raise ValueError(
                "Minute must be between 00 and 59."
            )

        return f"{hour:02d}:{minute:02d}"

    raise ValueError(
        "Invalid time. Example: 06:00 PM"
    )


def display_time(value):

    hour, minute = map(
        int,
        value.split(":")
    )

    ampm = "AM"

    if hour >= 12:
        ampm = "PM"

    display_hour = hour % 12

    if display_hour == 0:
        display_hour = 12

    return (
        f"{display_hour:02d}:"
        f"{minute:02d} {ampm}"
    )


# =========================================================
# ADMIN CHECK
# =========================================================

async def is_admin(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not user:
        return False

    # Group / Supergroup
    if (
        update.effective_chat
        and update.effective_chat.type
        in ("group", "supergroup")
    ):

        try:

            member = await context.bot.get_chat_member(
                update.effective_chat.id,
                user.id
            )

            if member.status in (
                "administrator",
                "creator"
            ):
                return True

        except Exception as e:

            print(
                "Admin check error:",
                e
            )

        return False

    # Private chat
    username = (
        user.username or ""
    ).strip().lstrip("@")

    return (
        username.lower()
        == ADMIN_USERNAME.lower()
    )


# =========================================================
# FIRESTORE HELPERS
# =========================================================

def get_post(post_id):

    doc = (
        db.collection(POSTS_COLLECTION)
        .document(str(post_id))
        .get()
    )

    if not doc.exists:
        return None

    data = doc.to_dict()

    data["id"] = doc.id

    return data


def get_all_posts():

    docs = (
        db.collection(POSTS_COLLECTION)
        .stream()
    )

    posts = []

    for doc in docs:

        data = doc.to_dict()

        data["id"] = doc.id

        posts.append(data)

    return posts


def create_post(
    post_time,
    photo_id,
    caption
):

    doc_ref = (
        db.collection(POSTS_COLLECTION)
        .document()
    )

    doc_ref.set({

        "post_time": post_time,

        "photo_id": photo_id,

        "caption": caption,

        "enabled": True,

    })

    return doc_ref.id


# =========================================================
# SEND POST
# =========================================================

async def send_post(
    context: ContextTypes.DEFAULT_TYPE
):

    post_id = context.job.data["id"]

    post = get_post(post_id)

    if not post:
        return

    post_time = post.get(
        "post_time",
        "00:00"
    )

    photo_id = post.get(
        "photo_id"
    )

    caption = post.get(
        "caption",
        ""
    )

    enabled = post.get(
        "enabled",
        True
    )

    if not enabled:
        return

    try:

        if photo_id:

            await context.bot.send_photo(

                chat_id=TARGET_CHAT,

                photo=photo_id,

                caption=caption

            )

        else:

            await context.bot.send_message(

                chat_id=TARGET_CHAT,

                text=caption

            )

        print(
            f"POST SUCCESS | "
            f"ID={post_id} | "
            f"TIME={display_time(post_time)}"
        )

    except Exception as e:

        print(
            f"POST ERROR | "
            f"ID={post_id} | "
            f"ERROR={e}"
        )


# =========================================================
# SCHEDULE ONE POST
# =========================================================

def schedule_post(
    application,
    post_id,
    post_time
):

    hour, minute = map(
        int,
        post_time.split(":")
    )

    job_name = f"post_{post_id}"

    # Remove old job
    old_jobs = (
        application.job_queue
        .get_jobs_by_name(job_name)
    )

    for job in old_jobs:

        job.schedule_removal()

    # Daily schedule
    application.job_queue.run_daily(

        send_post,

        time=time(
            hour=hour,
            minute=minute,
            tzinfo=TZ
        ),

        days=tuple(range(7)),

        data={
            "id": post_id
        },

        name=job_name
    )

    print(
        f"SCHEDULED | "
        f"ID={post_id} | "
        f"TIME={display_time(post_time)}"
    )


# =========================================================
# LOAD ALL SAVED POSTS
# =========================================================

def schedule_all(application):

    posts = get_all_posts()

    for post in posts:

        if not post.get(
            "enabled",
            True
        ):
            continue

        schedule_post(

            application,

            post["id"],

            post["post_time"]

        )


# =========================================================
# START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not await is_admin(
        update,
        context
    ):
        return

    await update.message.reply_text(

        "🤖 Auto Post Bot চালু আছে!\n\n"

        "🕐 Time format:\n"
        "06:00 AM = সকাল ৬টা\n"
        "12:00 PM = দুপুর ১২টা\n"
        "06:00 PM = সন্ধ্যা ৬টা\n"
        "11:00 PM = রাত ১১টা\n\n"

        "📝 Text example:\n"
        "06:00 PM|🌙 শুভ সন্ধ্যা 🌙\n\n"

        "📷 Photo example:\n"
        "Photo পাঠিয়ে caption লিখুন:\n"
        "06:00 PM|🌙 শুভ সন্ধ্যা 🌙"
    )


# =========================================================
# HELP
# =========================================================

async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not await is_admin(
        update,
        context
    ):
        return

    await update.message.reply_text(

        "📚 Commands:\n\n"

        "/start - Bot start\n"
        "/help - Help\n"
        "/list - Saved posts\n"
        "/test - Test post\n"
        "/status - Bot status\n"
        "/delete ID - Delete post\n"
        "/clear - Delete all posts\n"
        "/on - Enable all posts\n"
        "/off - Disable all posts\n\n"

        "🕐 Time examples:\n"
        "06:00 AM\n"
        "12:00 PM\n"
        "06:00 PM\n"
        "11:30 PM"
    )


# =========================================================
# SAVE TEXT POST
# =========================================================

async def text_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not await is_admin(
        update,
        context
    ):
        return

    if (
        not update.message
        or not update.message.text
    ):
        return

    text = update.message.text.strip()

    if "|" not in text:
        return

    time_part, caption = text.split(
        "|",
        1
    )

    caption = caption.strip()

    if not caption:

        await update.message.reply_text(
            "❌ Caption খালি রাখা যাবে না।"
        )

        return

    try:

        post_time = normalize_time(
            time_part
        )

    except ValueError:

        await update.message.reply_text(

            "❌ Time ভুল।\n\n"

            "Example:\n"
            "06:00 AM|সুপ্রভাত 🌞\n"
            "06:00 PM|শুভ সন্ধ্যা 🌙"

        )

        return

    post_id = create_post(
        post_time,
        None,
        caption
    )

    schedule_post(
        context.application,
        post_id,
        post_time
    )

    await update.message.reply_text(

        f"✅ Post saved!\n\n"

        f"🆔 ID: {post_id}\n"
        f"⏰ Time: {display_time(post_time)}\n"
        f"📅 প্রতিদিন এই সময়ে পোস্ট হবে।\n"
        f"🔥 Firebase-এ save হয়েছে।"
    )


# =========================================================
# SAVE PHOTO POST
# =========================================================

async def photo_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not await is_admin(
        update,
        context
    ):
        return

    if (
        not update.message
        or not update.message.photo
    ):
        return

    caption = (
        update.message.caption
        or ""
    )

    if "|" not in caption:

        await update.message.reply_text(

            "❌ Caption format ভুল।\n\n"

            "Example:\n"
            "06:00 PM|শুভ সন্ধ্যা 🌙"

        )

        return

    time_part, post_caption = caption.split(
        "|",
        1
    )

    post_caption = post_caption.strip()

    if not post_caption:

        await update.message.reply_text(
            "❌ Caption খালি রাখা যাবে না।"
        )

        return

    try:

        post_time = normalize_time(
            time_part
        )

    except ValueError:

        await update.message.reply_text(

            "❌ Time ভুল।\n\n"

            "Example:\n"
            "06:00 AM|সুপ্রভাত 🌞\n"
            "06:00 PM|শুভ সন্ধ্যা 🌙"

        )

        return

    photo_id = (
        update.message
        .photo[-1]
        .file_id
    )

    post_id = create_post(

        post_time,

        photo_id,

        post_caption

    )

    schedule_post(
        context.application,
        post_id,
        post_time
    )

    await update.message.reply_text(

        f"✅ Photo post saved!\n\n"

        f"🆔 ID: {post_id}\n"
        f"⏰ Time: {display_time(post_time)}\n"
        f"📅 প্রতিদিন এই সময়ে পোস্ট হবে।\n"
        f"🔥 Firebase-এ save হয়েছে।"
    )


# =========================================================
# LIST
# =========================================================

async def list_posts(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not await is_admin(
        update,
        context
    ):
        return

    posts = get_all_posts()

    if not posts:

        await update.message.reply_text(
            "📭 কোনো post save করা নেই।"
        )

        return

    posts.sort(
        key=lambda x: x.get(
            "post_time",
            "99:99"
        )
    )

    lines = [
        "📋 Saved Posts:\n"
    ]

    for post in posts:

        post_id = post["id"]

        post_time = post.get(
            "post_time",
            "00:00"
        )

        photo_id = post.get(
            "photo_id"
        )

        caption = post.get(
            "caption",
            ""
        )

        enabled = post.get(
            "enabled",
            True
        )

        status = (
            "ON"
            if enabled
            else "OFF"
        )

        post_type = (
            "📷 Photo"
            if photo_id
            else "📝 Text"
        )

        lines.append(

            f"🆔 {post_id}\n"
            f"⏰ {display_time(post_time)}\n"
            f"{post_type}\n"
            f"🔘 {status}\n"
            f"📄 {caption[:80]}\n"

        )

    await update.message.reply_text(
        "\n".join(lines)
    )


# =========================================================
# DELETE
# =========================================================

async def delete_post(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not await is_admin(
        update,
        context
    ):
        return

    if not context.args:

        await update.message.reply_text(
            "Example: /delete POST_ID"
        )

        return

    post_id = context.args[0]

    job_name = f"post_{post_id}"

    for job in (
        context.application
        .job_queue
        .get_jobs_by_name(job_name)
    ):

        job.schedule_removal()

    doc_ref = (
        db.collection(POSTS_COLLECTION)
        .document(str(post_id))
    )

    doc = doc_ref.get()

    if not doc.exists:

        await update.message.reply_text(
            "❌ এই ID পাওয়া যায়নি।"
        )

        return

    doc_ref.delete()

    await update.message.reply_text(

        f"✅ Post {post_id} deleted."

    )


# =========================================================
# CLEAR
# =========================================================

async def clear_posts(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not await is_admin(
        update,
        context
    ):
        return

    for job in (
        context.application
        .job_queue
        .jobs()
    ):

        job.schedule_removal()

    posts = get_all_posts()

    for post in posts:

        db.collection(
            POSTS_COLLECTION
        ).document(
            str(post["id"])
        ).delete()

    await update.message.reply_text(
        "🗑️ সব scheduled post delete করা হয়েছে।"
    )


# =========================================================
# ON
# =========================================================

async def on_posts(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not await is_admin(
        update,
        context
    ):
        return

    posts = get_all_posts()

    for post in posts:

        db.collection(
            POSTS_COLLECTION
        ).document(
            str(post["id"])
        ).update({
            "enabled": True
        })

        schedule_post(

            context.application,

            post["id"],

            post["post_time"]

        )

    await update.message.reply_text(
        "✅ সব post ON করা হয়েছে।"
    )


# =========================================================
# OFF
# =========================================================

async def off_posts(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not await is_admin(
        update,
        context
    ):
        return

    for job in (
        context.application
        .job_queue
        .jobs()
    ):

        job.schedule_removal()

    posts = get_all_posts()

    for post in posts:

        db.collection(
            POSTS_COLLECTION
        ).document(
            str(post["id"])
        ).update({
            "enabled": False
        })

    await update.message.reply_text(
        "⛔ সব automatic post OFF করা হয়েছে।"
    )


# =========================================================
# TEST
# =========================================================

async def test_post(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not await is_admin(
        update,
        context
    ):
        return

    try:

        await context.bot.send_message(

            chat_id=TARGET_CHAT,

            text="✅ Auto Post Bot Test Message"

        )

        await update.message.reply_text(
            "✅ Test message successfully sent."
        )

    except Exception as e:

        await update.message.reply_text(

            f"❌ Test failed:\n{e}"

        )


# =========================================================
# STATUS
# =========================================================

async def status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not await is_admin(
        update,
        context
    ):
        return

    posts = get_all_posts()

    active = sum(
        1
        for post in posts
        if post.get("enabled", True)
    )

    await update.message.reply_text(

        f"🤖 Bot: ONLINE\n"
        f"🌏 Timezone: Bangladesh (Asia/Dhaka)\n"
        f"🎯 Target: {TARGET_CHAT}\n"
        f"📅 Active posts: {active}\n"
        f"🔥 Database: Firebase Firestore\n"
        f"⏰ Time format: 12-hour AM/PM"

    )


# =========================================================
# MAIN
# =========================================================

def main():

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # Commands
    application.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    application.add_handler(
        CommandHandler(
            "help",
            help_command
        )
    )

    application.add_handler(
        CommandHandler(
            "list",
            list_posts
        )
    )

    application.add_handler(
        CommandHandler(
            "delete",
            delete_post
        )
    )

    application.add_handler(
        CommandHandler(
            "clear",
            clear_posts
        )
    )

    application.add_handler(
        CommandHandler(
            "on",
            on_posts
        )
    )

    application.add_handler(
        CommandHandler(
            "off",
            off_posts
        )
    )

    application.add_handler(
        CommandHandler(
            "test",
            test_post
        )
    )

    application.add_handler(
        CommandHandler(
            "status",
            status
        )
    )

    # Photo
    application.add_handler(
        MessageHandler(
            filters.PHOTO,
            photo_message
        )
    )

    # Text
    application.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            text_message
        )
    )

    # Load Firebase schedules
    schedule_all(application)

    print(
        "==================================="
    )

    print(
        "🤖 Auto Post Bot Started"
    )

    print(
        "🔥 Firebase Firestore"
    )

    print(
        "🌏 Timezone: Asia/Dhaka"
    )

    print(
        "🎯 Target:",
        TARGET_CHAT
    )

    print(
        "⏰ Time format: 12-hour AM/PM"
    )

    print(
        "==================================="
    )

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
