import os
import subprocess
import logging
import asyncio
import json
import time
import signal
import tempfile
import shutil
import zipfile
import io
import requests
import base64
import math
from datetime import datetime
from typing import Union, Dict, Any, Optional

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Document,
    Bot,
    InputFile
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError

# --- Basic Setup ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Configuration ---
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8109732136:AAGoVJURJtbUJuqcN84ciC5We2Ni3W4OMYM")
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL") 
PANTRY_API_KEY = os.environ.get("PANTRY_API_KEY", "1355fa66-95d4-40e0-9508-4d92a74531fe")
PANTRY_URL = f"https://getpantry.cloud/apiv1/pantry/{PANTRY_API_KEY}"

USERS_FILE = "data/users.json"
DATA_DIR = "data"
BOTS_DIR = "data/bots"

# --- Create directories if they don't exist ---
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(BOTS_DIR, exist_ok=True)

# --- Load user configuration ---
try:
    with open(USERS_FILE, 'r') as f:
        users_config = json.load(f)
        AUTHORIZED_USERS = users_config.get("authorized_users", [5431714552, 6392830471])
        MAX_BOTS_PER_USER = users_config.get("bot_settings", {}).get("max_bots_per_user", 5)
        MAX_BOT_FILE_SIZE = users_config.get("bot_settings", {}).get("max_bot_file_size", 10485760)  # 10MB
        MAX_MIRROR_FILE_SIZE = users_config.get("bot_settings", {}).get("max_mirror_file_size", 2097152000) # 2 GB
        ALLOWED_FILE_TYPES = users_config.get("bot_settings", {}).get("allowed_file_types", [".py"])
except (FileNotFound, json.JSONDecodeError):
    AUTHORIZED_USERS = [5431714552, 6392830471]
    MAX_BOTS_PER_USER = 5
    MAX_BOT_FILE_SIZE = 10485760
    MAX_MIRROR_FILE_SIZE = 2097152000 # 2 GB
    ALLOWED_FILE_TYPES = [".py"]
    
    default_config = {
        "authorized_users": AUTHORIZED_USERS,
        "bot_settings": {
            "max_bots_per_user": MAX_BOTS_PER_USER,
            "max_bot_file_size": MAX_BOT_FILE_SIZE,
            "max_mirror_file_size": MAX_MIRROR_FILE_SIZE,
            "allowed_file_types": ALLOWED_FILE_TYPES
        }
    }
    with open(USERS_FILE, 'w') as f:
        json.dump(default_config, f, indent=4)

# --- Global State ---
running_bots: Dict[str, Dict[str, Any]] = {}

# --- URLs ---
LOADING_ANIMATION_URL = "https://c.tenor.com/25ykirk3P4YAAAAd/tenor.gif" 
START_IMAGE_URL = "https://c.tenor.com/25ykirk3P4YAAAAd/tenor.gif"

# --- UI Elements (Emojis & Keyboards) ---
class EMOJI:
    SPARKLES = "✨"
    ROBOT = "🤖"
    CLIPBOARD = "📋"
    BAR_CHART = "📊"
    QUESTION = "❓"
    UPLOAD = "📤"
    SNAKE = "🐍"
    MEMO = "📝"
    KEY = "🔑"
    BACK = "⬅️"
    STOP = "⏹️"
    RESTART = "🔄"
    LOGS = "📄"
    CANCEL = "❌"
    SUCCESS = "✅"
    LOADING = "⏳"
    ROCKET = "🚀"
    PACKAGE = "📦"
    PARTY = "🎉"
    INFO = "ℹ️"
    GREEN_CIRCLE = "🟢"
    RED_CIRCLE = "🔴"
    GEAR = "⚙️"
    DELETE = "🗑️"
    DOWNLOAD = "⬇️"
    WARNING = "⚠️"
    FILE = "📄"
    CODE = "👨‍💻"
    WRENCH = "🔧"
    MIRROR = "🪞"
    STORAGE = "💾"

# --- Keyboard Generation Functions ---
def get_main_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton(f"{EMOJI.UPLOAD} Upload New Bot", callback_data='upload_start')],
        [InlineKeyboardButton(f"{EMOJI.CLIPBOARD} My Bots", callback_data='list_bots')],
        [InlineKeyboardButton(f"{EMOJI.BAR_CHART} Statistics & Storage", callback_data='stats')],
        [InlineKeyboardButton(f"{EMOJI.MIRROR} Mirror File", callback_data='mirror_start')],
        [InlineKeyboardButton(f"{EMOJI.GEAR} Settings", callback_data='settings'), InlineKeyboardButton(f"{EMOJI.QUESTION} Help", callback_data='help')]
    ]
    if running_bots:
        keyboard.append([InlineKeyboardButton(f"{EMOJI.DELETE} Delete All Bots", callback_data='delete_all_confirm')])
    return InlineKeyboardMarkup(keyboard)

def get_stats_keyboard():
    keyboard = [
        [InlineKeyboardButton(f"{EMOJI.MIRROR} Manage Mirror", callback_data='manage_mirror')],
        [InlineKeyboardButton(f"{EMOJI.BACK} Main Menu", callback_data='main_menu')]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_mirror_management_keyboard():
    # Pantry's free tier has no "delete all" function.
    keyboard = [[InlineKeyboardButton(f"{EMOJI.BACK} Back to Stats", callback_data='stats')]]
    return InlineKeyboardMarkup(keyboard)

def get_bot_actions_keyboard(bot_name: str):
    is_running = running_bots.get(bot_name) and running_bots[bot_name]['process'].poll() is None
    first_row = [InlineKeyboardButton(f"{EMOJI.RESTART} Restart", callback_data=f'bot_action:restart:{bot_name}')]
    if is_running:
        first_row.insert(0, InlineKeyboardButton(f"{EMOJI.STOP} Stop", callback_data=f'bot_action:stop:{bot_name}'))

    return InlineKeyboardMarkup([
        first_row,
        [
            InlineKeyboardButton(f"{EMOJI.DELETE} Delete", callback_data=f'bot_action:delete_confirm:{bot_name}'),
            InlineKeyboardButton(f"{EMOJI.DOWNLOAD} Download Code", callback_data=f'bot_action:download:{bot_name}')
        ],
        [InlineKeyboardButton(f"{EMOJI.LOGS} View Logs", callback_data=f'bot_action:logs:{bot_name}')],
        [InlineKeyboardButton(f"{EMOJI.BACK} Back to Bot List", callback_data='list_bots')]
    ])

def get_delete_confirmation_keyboard(bot_name: str):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"{EMOJI.DELETE} Yes, I'm sure", callback_data=f'bot_action:delete_final:{bot_name}'),
            InlineKeyboardButton(f"{EMOJI.CANCEL} No, Cancel", callback_data=f'select_bot:{bot_name}')
        ]
    ])

def get_delete_all_confirmation_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"{EMOJI.WARNING} Yes, Delete All", callback_data='delete_all_final'),
            InlineKeyboardButton(f"{EMOJI.CANCEL} No, Cancel", callback_data='main_menu')
        ]
    ])

def get_back_to_main_menu_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton(f"{EMOJI.BACK} Main Menu", callback_data='main_menu')]])

def get_cancel_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton(f"{EMOJI.CANCEL} Cancel", callback_data='cancel_operation')]])

# --- Helper Functions ---
async def edit_or_reply_message(update: Update, text: str, reply_markup: Optional[InlineKeyboardMarkup] = None, photo_url: Optional[str] = None):
    try:
        if update.callback_query:
            query = update.callback_query
            # If the original message has a photo and the new one doesn't, or vice-versa, we must delete and send a new one.
            has_photo_orig = bool(query.message.photo)
            has_photo_new = bool(photo_url)

            if has_photo_orig != has_photo_new:
                await query.message.delete()
                if has_photo_new:
                    await query.message.chat.send_photo(photo=photo_url, caption=text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
                else:
                    await query.message.chat.send_message(text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup, disable_web_page_preview=True)
                return

            # If message type is consistent, we can edit.
            if photo_url:
                await query.edit_message_media(media=InputMediaPhoto(media=photo_url, caption=text, parse_mode=ParseMode.MARKDOWN), reply_markup=reply_markup)
            else:
                await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup, disable_web_page_preview=True)
        else:
            if photo_url:
                await update.message.reply_photo(photo=photo_url, caption=text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
            else:
                await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup, disable_web_page_preview=True)
    except BadRequest as e:
        if "Message is not modified" in str(e):
            await query.answer()
        else:
            logger.error(f"Error in edit_or_reply_message: {e}")
            await update.effective_chat.send_message(text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup, disable_web_page_preview=True)

async def send_loading_animation(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str):
    """Sends a loading animation with a text message."""
    return await context.bot.send_animation(
        chat_id=chat_id,
        animation=LOADING_ANIMATION_URL,
        caption=text,
        parse_mode=ParseMode.MARKDOWN
    )

def create_bot_directory(bot_name: str) -> str:
    bot_dir = os.path.join(BOTS_DIR, bot_name)
    os.makedirs(bot_dir, exist_ok=True)
    return bot_dir

def start_bot_subprocess(bot_name: str, bot_token: str, bot_code: str, requirements_content: Optional[str] = None) -> Optional[Dict[str, Any]]:
    try:
        bot_dir = create_bot_directory(bot_name)
        bot_file_path = os.path.join(bot_dir, "bot.py")

        modified_code = bot_code.replace("TOKEN = \"\"", f"TOKEN = \"{bot_token}\"")
        modified_code = modified_code.replace("TOKEN = ''", f"TOKEN = \"{bot_token}\"")
        modified_code = modified_code.replace("TOKEN=os.getenv(\"BOT_TOKEN\")", f"TOKEN = \"{bot_token}\"")
        
        with open(bot_file_path, 'w', encoding='utf-8') as f:
            f.write(modified_code)
        
        if requirements_content:
            requirements_path = os.path.join(bot_dir, "requirements.txt")
            with open(requirements_path, 'w', encoding='utf-8') as f:
                f.write(requirements_content)
            
            logger.info(f"Installing requirements for {bot_name}...")
            pip_process = subprocess.run(
                ['pip', 'install', '-r', requirements_path],
                capture_output=True, text=True, cwd=bot_dir
            )
            if pip_process.returncode != 0:
                logger.error(f"Failed to install requirements for {bot_name}. Stderr: {pip_process.stderr}")
        
        process = subprocess.Popen(
            ['python3', 'bot.py'],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=bot_dir,
            text=True,
            encoding='utf-8',
            errors='replace',
            preexec_fn=os.setsid 
        )
        
        logger.info(f"Started subprocess for bot '{bot_name}' with PID {process.pid}.")
        
        return {
            'process': process,
            'start_time': datetime.now(),
            'token': bot_token,
            'bot_dir': bot_dir,
            'logs': ""
        }
    except Exception as e:
        logger.error(f"Failed to start subprocess for {bot_name}: {e}", exc_info=True)
        return None

def stop_bot_process(bot_name: str) -> bool:
    if bot_name in running_bots:
        process = running_bots[bot_name]['process']
        if process.poll() is None:
            logger.info(f"Stopping process group for bot {bot_name} with PGID {process.pid}...")
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                process.wait(timeout=5)
                logger.info(f"Terminated process group for bot {bot_name}.")
            except subprocess.TimeoutExpired:
                logger.warning(f"Process group for {bot_name} did not terminate in time. Killing...")
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except ProcessLookupError:
                logger.info(f"Process for bot {bot_name} already terminated.")
        return True
    return False

def restart_bot_process(bot_name: str) -> bool:
    if bot_name in running_bots:
        bot_info = running_bots[bot_name]
        bot_token = bot_info['token']
        bot_dir = bot_info['bot_dir']
        
        bot_code_path = os.path.join(bot_dir, "bot.py")
        if not os.path.exists(bot_code_path):
            logger.error(f"Cannot restart {bot_name}: bot.py not found in {bot_dir}")
            return False
            
        with open(bot_code_path, 'r', encoding='utf-8') as f:
            bot_code = f.read()
            
        requirements_content = None
        requirements_path = os.path.join(bot_dir, "requirements.txt")
        if os.path.exists(requirements_path):
            with open(requirements_path, 'r', encoding='utf-8') as f:
                requirements_content = f.read()
        
        logger.info(f"Attempting to restart bot: {bot_name}")
        stop_bot_process(bot_name)
        time.sleep(2)
        
        new_bot_info = start_bot_subprocess(bot_name, bot_token, bot_code, requirements_content)
        if new_bot_info:
            running_bots[bot_name] = new_bot_info
            return True
    return False

def update_bot_logs(bot_name: str):
    if bot_name in running_bots:
        process = running_bots[bot_name]['process']
        if process.stdout:
            fd = process.stdout.fileno()
            fl = os.fcntl.fcntl(fd, os.fcntl.F_GETFL)
            os.fcntl.fcntl(fd, os.fcntl.F_SETFL, fl | os.O_NONBLOCK)
            try:
                output = process.stdout.read()
                if output:
                    running_bots[bot_name]['logs'] += output
            except (TypeError, IOError):
                pass

async def download_file(bot: Bot, file_id: str, destination_path: str) -> bool:
    try:
        file = await bot.get_file(file_id)
        await file.download_to_drive(destination_path)
        return True
    except Exception as e:
        logger.error(f"Error downloading file {file_id}: {e}")
        return False

def get_dir_size(path='.'):
    """Calculates the size of a directory."""
    total = 0
    with os.scandir(path) as it:
        for entry in it:
            if entry.is_file():
                total += entry.stat().st_size
            elif entry.is_dir():
                total += get_dir_size(entry.path)
    return total

def format_bytes(size):
    """Formats bytes into KB, MB, GB, etc."""
    if size == 0:
        return "0B"
    size_name = ("B", "KB", "MB", "GB", "TB")
    i = int(math.floor(math.log(size, 1024)))
    p = math.pow(1024, i)
    s = round(size / p, 2)
    return f"{s} {size_name[i]}"

# --- Authorization Decorator ---
from functools import wraps

def authorized_only(func):
    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        if user_id not in AUTHORIZED_USERS:
            logger.warning(f"Unauthorized access attempt by user {user_id}.")
            if update.message:
                await update.message.reply_text("⛔ You are not authorized to use this bot.")
            elif update.callback_query:
                await update.callback_query.answer("⛔ You are not authorized.", show_alert=True)
            return
        return await func(update, context, *args, **kwargs)
    return wrapped

# --- Core Command Handlers ---
@authorized_only
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_message = f"""
{EMOJI.SPARKLES} *Welcome to BotHoster Pro!* {EMOJI.SPARKLES}
I can host and manage your Python Telegram bots.
{EMOJI.GEAR} Use the menu below to get started.
"""
    if update.message:
        await update.message.reply_photo(photo=START_IMAGE_URL, caption=welcome_message, parse_mode=ParseMode.MARKDOWN, reply_markup=get_main_menu_keyboard())

@authorized_only
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    help_text = f"""
{EMOJI.QUESTION} *BotHoster Pro Help* {EMOJI.QUESTION}
This bot allows you to host other Telegram bots directly from this chat.
{EMOJI.ROCKET} *Features:*
- `{EMOJI.UPLOAD} Upload New Bot`: Start a conversation to upload a new bot.
- `{EMOJI.CLIPBOARD} My Bots`: View, manage, and see logs for your bots.
- `{EMOJI.MIRROR} Mirror File`: Upload a file and get a direct public link.
- `{EMOJI.BAR_CHART} Stats & Storage`: View bot counts and server disk usage.
- `{EMOJI.GEAR} Settings`: View the current bot hosting limits.
{EMOJI.WARNING} *Disclaimer:*
Running custom code can be risky. Ensure you trust the code you are uploading.
"""
    await edit_or_reply_message(update, help_text, get_main_menu_keyboard())

@authorized_only
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer("Crunching the numbers...")
    
    total_bots = len(running_bots)
    running_count = sum(1 for bot in running_bots.values() if bot['process'].poll() is None)
    
    # Get directory and disk stats
    bots_dir_size = get_dir_size(BOTS_DIR)
    total, used, free = shutil.disk_usage("/")

    stats_text = f"""
{EMOJI.BAR_CHART} *Hosting Statistics*
{EMOJI.ROBOT} Total Bots Managed: *{total_bots}*
{EMOJI.GREEN_CIRCLE} Bots Running: *{running_count}*

{EMOJI.STORAGE} *Server Storage*
{EMOJI.SNAKE} Bots Folder Size: `{format_bytes(bots_dir_size)}`
{EMOJI.MIRROR} Mirror Storage: *Using getpantry.cloud*
---
Disk Total: `{format_bytes(total)}`
Disk Used: `{format_bytes(used)}`
Disk Free: `{format_bytes(free)}`
"""
    await edit_or_reply_message(update, stats_text, get_stats_keyboard())

@authorized_only
async def list_bots_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer("Fetching your bots...")
        
    if not running_bots:
        await edit_or_reply_message(update, f"{EMOJI.CLIPBOARD} You haven't uploaded any bots yet.", reply_markup=get_main_menu_keyboard())
        return
    
    keyboard = []
    for bot_name, info in running_bots.items():
        status_emoji = EMOJI.GREEN_CIRCLE if info['process'].poll() is None else EMOJI.RED_CIRCLE
        keyboard.append([InlineKeyboardButton(f"{status_emoji} {bot_name}", callback_data=f"select_bot:{bot_name}")])
    
    keyboard.append([InlineKeyboardButton(f"{EMOJI.BACK} Main Menu", callback_data='main_menu')])
    await edit_or_reply_message(update, f"{EMOJI.CLIPBOARD} *Your Bots*\n\nSelect a bot to manage:", reply_markup=InlineKeyboardMarkup(keyboard))


# --- Conversation Handlers States ---
(ASK_BOT_NAME, GET_BOT_FILE, GET_TOKEN, GET_REQUIREMENTS, ASK_MIRROR_FILE) = range(5)

# --- Upload Bot Conversation ---
@authorized_only
async def upload_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    user_bots_count = len(running_bots)
    if user_bots_count >= MAX_BOTS_PER_USER:
        await query.message.reply_text(f"{EMOJI.WARNING} You have reached the maximum of *{MAX_BOTS_PER_USER}* bots.", reply_markup=get_back_to_main_menu_keyboard())
        return ConversationHandler.END
    
    await query.message.delete()
    await query.message.chat.send_message(
        f"{EMOJI.ROBOT} Let's upload a new bot!\n\nFirst, what do you want to name it? (e.g., `MyAwesomeBot`).",
        reply_markup=get_cancel_keyboard()
    )
    return ASK_BOT_NAME

async def ask_bot_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    bot_name = update.message.text.strip()
    if not bot_name or not bot_name.replace('_', '').isalnum():
        await update.message.reply_text(f"{EMOJI.CANCEL} Invalid name. Please use only letters, numbers, and underscores. Try again.", reply_markup=get_cancel_keyboard())
        return ASK_BOT_NAME
    if bot_name in running_bots:
        await update.message.reply_text(f"{EMOJI.CANCEL} A bot with this name already exists. Please choose another name.", reply_markup=get_cancel_keyboard())
        return ASK_BOT_NAME
        
    context.user_data['bot_name'] = bot_name
    await update.message.reply_text(f"{EMOJI.SUCCESS} Great! Bot will be named `{bot_name}`.\n\n{EMOJI.SNAKE} Now, please send your Python file (e.g., `bot.py`).", parse_mode=ParseMode.MARKDOWN, reply_markup=get_cancel_keyboard())
    return GET_BOT_FILE

async def receive_bot_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    document = update.message.document
    if not document or not any(document.file_name.lower().endswith(ft) for ft in ALLOWED_FILE_TYPES):
        await update.message.reply_text(f"{EMOJI.CANCEL} Invalid file type. Please send a file with one of the allowed extensions: {', '.join(ALLOWED_FILE_TYPES)}.", reply_markup=get_cancel_keyboard())
        return GET_BOT_FILE
    if document.file_size > MAX_BOT_FILE_SIZE:
        await update.message.reply_text(f"{EMOJI.CANCEL} File is too large. Maximum size is {MAX_BOT_FILE_SIZE/1024/1024:.2f}MB.", reply_markup=get_cancel_keyboard())
        return GET_BOT_FILE

    loading_msg = await send_loading_animation(context, update.effective_chat.id, f"{EMOJI.LOADING} Downloading your bot file...")
    
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_file_path = os.path.join(temp_dir, document.file_name)
        if not await download_file(context.bot, document.file_id, temp_file_path):
            await loading_msg.edit_caption(f"{EMOJI.CANCEL} Failed to download the file. Please try again.", reply_markup=get_cancel_keyboard())
            return GET_BOT_FILE

        with open(temp_file_path, 'r', encoding='utf-8') as f:
            context.user_data['bot_code'] = f.read()

    bot_name = context.user_data['bot_name']
    await loading_msg.edit_caption(f"{EMOJI.SUCCESS} Bot file received!\n\n{EMOJI.KEY} Now, please send me the Telegram token for `{bot_name}`.", parse_mode=ParseMode.MARKDOWN, reply_markup=get_cancel_keyboard())
    return GET_TOKEN

async def receive_token_and_ask_requirements(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['bot_token'] = update.message.text.strip()
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{EMOJI.PACKAGE} Yes, upload requirements.txt", callback_data='has_requirements')],
        [InlineKeyboardButton(f"{EMOJI.ROCKET} No, run without it", callback_data='no_requirements')]
    ])
    await update.message.reply_text(f"{EMOJI.SUCCESS} Token received!\n\nDoes your bot have any external Python package dependencies (a `requirements.txt` file)?", reply_markup=keyboard)
    return GET_REQUIREMENTS

async def handle_requirements_decision(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    decision = query.data

    if decision == 'has_requirements':
        await query.edit_message_text(f"{EMOJI.PACKAGE} Please send me your `requirements.txt` file.", reply_markup=get_cancel_keyboard())
        return GET_REQUIREMENTS
    else:
        context.user_data['requirements_content'] = None
        await query.edit_message_text(f"{EMOJI.LOADING} Understood. Preparing to launch your bot...")
        return await finalize_and_run_bot(update, context)

async def receive_requirements_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    document = update.message.document
    if not document or not document.file_name.lower().endswith('.txt'):
        await update.message.reply_text(f"{EMOJI.CANCEL} That doesn't look like a `requirements.txt` file. Please send a `.txt` file.", reply_markup=get_cancel_keyboard())
        return GET_REQUIREMENTS

    loading_msg = await send_loading_animation(context, update.effective_chat.id, f"{EMOJI.LOADING} Downloading requirements file...")

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_file_path = os.path.join(temp_dir, document.file_name)
        if not await download_file(context.bot, document.file_id, temp_file_path):
            await loading_msg.edit_caption(f"{EMOJI.CANCEL} Failed to download the requirements file. Please try again.", reply_markup=get_cancel_keyboard())
            return GET_REQUIREMENTS
        
        with open(temp_file_path, 'r', encoding='utf-8') as f:
            context.user_data['requirements_content'] = f.read()

    await loading_msg.delete()
    return await finalize_and_run_bot(update, context)

async def finalize_and_run_bot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """The final step of launching the bot."""
    bot_name = context.user_data['bot_name']
    bot_token = context.user_data['bot_token']
    bot_code = context.user_data['bot_code']
    requirements_content = context.user_data.get('requirements_content')
    
    chat_id = update.effective_chat.id
    if update.callback_query:
        await update.callback_query.message.delete()
    
    status_msg = await send_loading_animation(context, chat_id, f"{EMOJI.LOADING} Finalizing setup and starting `{bot_name}`...")
    
    bot_info = start_bot_subprocess(bot_name, bot_token, bot_code, requirements_content)
    
    if bot_info:
        running_bots[bot_name] = bot_info
        await status_msg.edit_caption(f"{EMOJI.PARTY} Hooray! Your bot `{bot_name}` is now running!", parse_mode=ParseMode.MARKDOWN, reply_markup=get_back_to_main_menu_keyboard())
    else:
        await status_msg.edit_caption(f"{EMOJI.CANCEL} A critical error occurred while starting your bot. Please check your code and token, then try again.", reply_markup=get_back_to_main_menu_keyboard())
        
    context.user_data.clear()
    return ConversationHandler.END

async def cancel_operation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message_text = f"{EMOJI.CANCEL} Operation cancelled."
    query = update.callback_query
    if query:
        await query.answer()
        await query.message.delete()
        await query.message.chat.send_message(message_text)
        await start_command(update, context)
    else:
        await update.message.reply_text(message_text)
        await start_command(update, context)

    context.user_data.clear()
    return ConversationHandler.END


# --- Mirror File Conversation & Management ---
@authorized_only
async def mirror_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if not PANTRY_API_KEY or not RENDER_EXTERNAL_URL:
        await edit_or_reply_message(update, f"{EMOJI.WARNING} Mirror service is not fully configured.", get_back_to_main_menu_keyboard())
        return ConversationHandler.END

    await query.message.delete()
    await query.message.chat.send_message(f"{EMOJI.MIRROR} *File Mirror*\n\nSend me a file up to {MAX_MIRROR_FILE_SIZE/1024/1024:.0f}MB to mirror. The file will be stored in getpantry.cloud.", parse_mode=ParseMode.MARKDOWN, reply_markup=get_cancel_keyboard())
    return ASK_MIRROR_FILE

async def receive_mirror_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.message
    file_source = message.document or message.video or message.audio or (message.photo[-1] if message.photo else None)
    
    if not file_source:
        await message.reply_text(f"{EMOJI.CANCEL} Please send a file or media to mirror.", reply_markup=get_cancel_keyboard())
        return ASK_MIRROR_FILE
    
    # Pantry's limit is approximately 500 KB per basket
    # For now, we will enforce the 2 GB limit as requested, but with a warning.
    # The proxy server will handle the actual download from Telegram, then stream to Pantry.
    # We will trust the Render's server space for a temporary download and upload.
    if file_source.file_size > MAX_MIRROR_FILE_SIZE:
        await message.reply_text(f"{EMOJI.CANCEL} File is too large. Maximum size is {MAX_MIRROR_FILE_SIZE/1024/1024:.0f}MB.", reply_markup=get_cancel_keyboard())
        return ASK_MIRROR_FILE

    loading_msg = await send_loading_animation(context, message.chat_id, f"{EMOJI.LOADING} Downloading file and uploading to Pantry...")

    try:
        # Download the file to a temporary location to handle large files
        with tempfile.NamedTemporaryFile(delete=False) as temp_file:
            temp_path = temp_file.name
            await context.bot.get_file(file_source.file_id).download_to_drive(custom_path=temp_path)

        file_name = getattr(file_source, 'file_name', f"{file_source.file_unique_id}.dat")
        file_key = f"mirror_{file_source.file_unique_id}_{file_name}"
        
        # Prepare to stream the file to Pantry
        with open(temp_path, 'rb') as f:
            encoded_content = base64.b64encode(f.read()).decode('utf-8')

        # Pantry can store up to 500KB per basket. You'll need to create a new basket for each file part.
        # This is a major limitation for large files. To keep this simple, we'll assume files are small enough for one basket.
        # For a 2 GB limit, a different storage service like S3 is required. We'll proceed with Pantry but add a warning.

        payload = {
            "name": file_name,
            "content": encoded_content,
            "mime_type": file_source.mime_type
        }

        # Check against Pantry's practical limit before sending.
        if len(json.dumps(payload)) > 500 * 1024:
            await loading_msg.edit_caption(f"{EMOJI.WARNING} Pantry's limit is ~500 KB per file. This file is too big. Please use a different service for larger files.", reply_markup=get_back_to_main_menu_keyboard())
            os.remove(temp_path)
            return ConversationHandler.END

        headers = {"Content-Type": "application/json"}
        response = requests.post(f"{PANTRY_URL}/basket/{file_key}", json=payload, headers=headers)
        
        os.remove(temp_path)

        if response.status_code == 200:
            mirror_url = f"{RENDER_EXTERNAL_URL}/mirror/{file_key}"
            await loading_msg.edit_caption(
                f"{EMOJI.SUCCESS} *File Mirrored Successfully!*\n\n"
                f"Here is your direct link:\n`{mirror_url}`",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=get_back_to_main_menu_keyboard()
            )
        else:
            logger.error(f"Pantry upload failed with status code {response.status_code}: {response.text}")
            await loading_msg.edit_caption(f"{EMOJI.CANCEL} An error occurred while uploading to Pantry. Status: {response.status_code}", reply_markup=get_back_to_main_menu_keyboard())
    except Exception as e:
        logger.error(f"Error mirroring file to Pantry: {e}", exc_info=True)
        await loading_msg.edit_caption(f"{EMOJI.CANCEL} An unexpected error occurred while mirroring the file. Please try again.")

    return ConversationHandler.END

@authorized_only
async def manage_mirror_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    text = f"""
{EMOJI.MIRROR} *Mirror Management*

You are currently using getpantry.cloud for file storage.
Pantry is a key-value store and does not provide a way to list or delete files via its API.
You must manually manage files via the Pantry web interface.
"""
    await edit_or_reply_message(update, text, get_mirror_management_keyboard())

@authorized_only
async def delete_all_mirror_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer(text="Pantry does not support this feature via the API.", show_alert=True)
    await edit_or_reply_message(update, f"{EMOJI.WARNING} Pantry does not support deleting files in bulk via the API. Please manage your storage directly on the getpantry.cloud website.", reply_markup=get_mirror_management_keyboard())

@authorized_only
async def delete_all_mirror_final_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer(text="Pantry does not support this feature via the API.", show_alert=True)
    await edit_or_reply_message(update, f"{EMOJI.WARNING} Pantry does not support deleting files in bulk via the API. Please manage your storage directly on the getpantry.cloud website.", reply_markup=get_mirror_management_keyboard())

@authorized_only
async def settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    settings_text = f"""
{EMOJI.GEAR} *BotHoster Pro Settings*
These settings are configured in the `users.json` file.

{EMOJI.ROBOT} *Authorization*
- Authorized User IDs: `{', '.join(map(str, AUTHORIZED_USERS))}`

{EMOJI.WRENCH} *Limits & Rules*
- Max Bots Per User: *{MAX_BOTS_PER_USER}*
- Max Bot Script Size: *{MAX_BOT_FILE_SIZE/1024/1024:.1f} MB*
- Max Mirror File Size: *{MAX_MIRROR_FILE_SIZE/1024/1024:.0f} MB* (Note: Pantry has a 500KB file limit.)
"""
    await edit_or_reply_message(update, settings_text, get_main_menu_keyboard())


# ... (main function and other handlers are not changed)
def main():
    """Initializes and runs the bot application."""
    application = Application.builder().token(TOKEN).build()
    
    upload_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(upload_start, pattern='^upload_start$')],
        states={
            ASK_BOT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_bot_file)],
            GET_BOT_FILE: [MessageHandler(filters.Document.ALL, receive_bot_file)],
            GET_TOKEN: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_token_and_ask_requirements)],
            GET_REQUIREMENTS: [
                CallbackQueryHandler(handle_requirements_decision, pattern='^(has_requirements|no_requirements)$'),
                MessageHandler(filters.Document.ALL, receive_requirements_file),
            ],
        },
        fallbacks=[CallbackQueryHandler(cancel_operation, pattern='^cancel_operation$'), CommandHandler('cancel', cancel_operation)],
        per_user=True, per_chat=True
    )

    mirror_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(mirror_start, pattern='^mirror_start$')],
        states={
            ASK_MIRROR_FILE: [MessageHandler(filters.ALL & ~filters.COMMAND, receive_mirror_file)]
        },
        fallbacks=[CallbackQueryHandler(cancel_operation, pattern='^cancel_operation$'), CommandHandler('cancel', cancel_operation)],
        per_user=True, per_chat=True
    )

    application.add_handler(upload_conv_handler)
    application.add_handler(mirror_conv_handler)
    
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("list", list_bots_command))

    application.add_handler(CallbackQueryHandler(main_menu_callback, pattern='^main_menu$'))
    application.add_handler(CallbackQueryHandler(list_bots_command, pattern='^list_bots$'))
    application.add_handler(CallbackQueryHandler(stats_command, pattern='^stats$'))
    application.add_handler(CallbackQueryHandler(help_command, pattern='^help$'))
    application.add_handler(CallbackQueryHandler(settings_callback, pattern='^settings$'))

    application.add_handler(CallbackQueryHandler(manage_mirror_callback, pattern='^manage_mirror$'))
    application.add_handler(CallbackQueryHandler(delete_all_mirror_confirm_callback, pattern='^delete_all_mirror_confirm$'))
    application.add_handler(CallbackQueryHandler(delete_all_mirror_final_callback, pattern='^delete_all_mirror_final$'))

    application.add_handler(CallbackQueryHandler(select_bot_callback, pattern=r'^select_bot:'))
    application.add_handler(CallbackQueryHandler(bot_action_callback, pattern=r'^bot_action:'))
    
    application.add_handler(CallbackQueryHandler(delete_all_bots_confirm, pattern='^delete_all_confirm$'))
    application.add_handler(CallbackQueryHandler(delete_all_bots_final, pattern='^delete_all_final$'))
    
    logger.info("Bot is starting...")
    application.run_polling()
