import os
import subprocess
import logging
import asyncio
import json
import time
import signal
import tempfile
import shutil
from datetime import datetime
from typing import Union, Dict, Any, Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Document, Bot, InputFile
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
import requests

# --- Basic Setup ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Configuration ---
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8109732136:AAGoVJURJtbUJuqcN84ciC5We2Ni3W4OMYM")
USERS_FILE = "data/users.json"
DATA_DIR = "data"
BOTS_DIR = "data/bots"
MIRROR_DIR = "data/mirror"

# Create directories if they don't exist
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(BOTS_DIR, exist_ok=True)
os.makedirs(MIRROR_DIR, exist_ok=True)

# Load user configuration
try:
    with open(USERS_FILE, 'r') as f:
        users_config = json.load(f)
        AUTHORIZED_USERS = users_config.get("authorized_users", [5431714552, 6392830471])
        MAX_BOTS_PER_USER = users_config.get("bot_settings", {}).get("max_bots_per_user", 5)
        MAX_BOT_FILE_SIZE = users_config.get("bot_settings", {}).get("max_bot_file_size", 10485760)  # 10MB
        MAX_MIRROR_FILE_SIZE = users_config.get("bot_settings", {}).get("max_mirror_file_size", 157286400) # 150MB
        ALLOWED_FILE_TYPES = users_config.get("bot_settings", {}).get("allowed_file_types", [".py"])
except (FileNotFoundError, json.JSONDecodeError):
    AUTHORIZED_USERS = [5431714552, 6392830471]
    MAX_BOTS_PER_USER = 5
    MAX_BOT_FILE_SIZE = 10485760  # 10MB
    MAX_MIRROR_FILE_SIZE = 157286400 # 150MB
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
running_bots = {}
ANIMATION_URL = "https://c.tenor.com/25ykirk3P4YAAAAd/tenor.gif" # Replace with your image URL
START_IMAGE_URL = "https://c.tenor.com/HJvV2wKvDDQAAAAd/tenor.gif" # Replace with your start image URL

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

# --- Keyboard Generation Functions ---
def get_main_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton(f"{EMOJI.UPLOAD} Upload New Bot", callback_data='upload_start')],
        [InlineKeyboardButton(f"{EMOJI.CLIPBOARD} My Bots", callback_data='list_bots')],
        [InlineKeyboardButton(f"{EMOJI.BAR_CHART} Statistics", callback_data='stats')],
        [InlineKeyboardButton(f"{EMOJI.MIRROR} Mirror File", callback_data='mirror_start')]
    ]
    if running_bots:
        keyboard.append([InlineKeyboardButton(f"{EMOJI.DELETE} Delete All Bots", callback_data='delete_all_confirm')])
    
    keyboard.append([InlineKeyboardButton(f"{EMOJI.QUESTION} Help", callback_data='help')])
    return InlineKeyboardMarkup(keyboard)

def get_bot_actions_keyboard(bot_name):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"{EMOJI.STOP} Stop", callback_data=f'bot_action:stop:{bot_name}'),
            InlineKeyboardButton(f"{EMOJI.RESTART} Restart", callback_data=f'bot_action:restart:{bot_name}'),
        ],
        [
            InlineKeyboardButton(f"{EMOJI.WRENCH} Edit Code", callback_data=f'bot_action:edit_code:{bot_name}'),
            InlineKeyboardButton(f"{EMOJI.KEY} Edit Token", callback_data=f'bot_action:edit_token:{bot_name}')
        ],
        [
            InlineKeyboardButton(f"{EMOJI.DELETE} Delete", callback_data=f'bot_action:delete_confirm:{bot_name}'),
            InlineKeyboardButton(f"{EMOJI.DOWNLOAD} Download Code", callback_data=f'bot_action:download:{bot_name}')
        ],
        [InlineKeyboardButton(f"{EMOJI.LOGS} View Logs", callback_data=f'bot_action:logs:{bot_name}')],
        [InlineKeyboardButton(f"{EMOJI.BACK} Back to Bot List", callback_data='list_bots')]
    ])

def get_delete_confirmation_keyboard(bot_name):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"{EMOJI.DELETE} Yes, Delete", callback_data=f'bot_action:delete_final:{bot_name}'),
            InlineKeyboardButton(f"{EMOJI.CANCEL} No, Cancel", callback_data=f'select_bot:{bot_name}')
        ],
        [InlineKeyboardButton(f"{EMOJI.BACK} Main Menu", callback_data='main_menu')]
    ])

def get_delete_all_confirmation_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"{EMOJI.DELETE} Yes, Delete All Bots", callback_data=f'delete_all_final'),
            InlineKeyboardButton(f"{EMOJI.CANCEL} No, Cancel", callback_data='main_menu')
        ],
    ])

def get_back_to_main_menu_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton(f"{EMOJI.BACK} Main Menu", callback_data='main_menu')]])

def get_cancel_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton(f"{EMOJI.CANCEL} Cancel", callback_data='cancel_upload')]])

def get_settings_keyboard():
    keyboard = [
        [InlineKeyboardButton(f"{EMOJI.ROBOT} Manage Authorized Users", callback_data='settings:users')],
        [InlineKeyboardButton(f"{EMOJI.GEAR} Bot Settings", callback_data='settings:bots')],
        [InlineKeyboardButton(f"{EMOJI.BACK} Main Menu", callback_data='main_menu')]
    ]
    return InlineKeyboardMarkup(keyboard)

# --- Helper Functions ---
async def edit_or_reply_message(update: Update, text: str, reply_markup: InlineKeyboardMarkup = None):
    """Edits the message if it's a callback query, otherwise sends a new message."""
    try:
        if update.callback_query:
            try:
                await update.callback_query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
            except BadRequest as e:
                if "Message is not modified" not in str(e):
                    logger.warning(f"Could not edit message: {e}")
                    await update.callback_query.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
        else:
            await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Error in edit_or_reply_message: {e}")

async def send_loading_animation(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    """Sends a loading animation (GIF or image) with a text message."""
    return await context.bot.send_photo(
        chat_id=update.effective_chat.id,
        photo=ANIMATION_URL,
        caption=text,
        parse_mode=ParseMode.MARKDOWN
    )

def create_bot_directory(bot_name: str) -> str:
    """Create a directory for the bot files."""
    bot_dir = os.path.join(BOTS_DIR, bot_name)
    os.makedirs(bot_dir, exist_ok=True)
    return bot_dir

def start_bot_subprocess(bot_name: str, bot_token: str, bot_code: str, requirements_content: str = None) -> Union[dict, None]:
    """Handles the logic of starting a bot in a subprocess."""
    try:
        bot_dir = create_bot_directory(bot_name)
        bot_file_path = os.path.join(bot_dir, "bot.py")
        
        modified_script = bot_code.replace("TOKEN = \"\"", f"TOKEN = \"{bot_token}\"")
        modified_script = modified_script.replace("TOKEN = ''", f"TOKEN = \"{bot_token}\"")
        modified_script = modified_script.replace("TOKEN=os.getenv(\"BOT_TOKEN\")", f"TOKEN = \"{bot_token}\"")
        
        with open(bot_file_path, 'w') as f:
            f.write(modified_script)
        
        if requirements_content:
            requirements_path = os.path.join(bot_dir, "requirements.txt")
            with open(requirements_path, 'w') as f:
                f.write(requirements_content)
        
        if requirements_content:
            try:
                subprocess.run(
                    ['pip', 'install', '-r', os.path.join(bot_dir, "requirements.txt")],
                    check=True,
                    capture_output=True,
                    text=True,
                    cwd=bot_dir
                )
            except subprocess.CalledProcessError as e:
                logger.error(f"Failed to install requirements for {bot_name}: {e.stderr}")
                return None
        
        process = subprocess.Popen(
            ['python', bot_file_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=bot_dir,
            text=True
        )
        
        return {
            'process': process,
            'start_time': datetime.now(),
            'token': bot_token,
            'bot_dir': bot_dir,
            'logs': ""
        }
    except Exception as e:
        logger.error(f"Failed to start subprocess for {bot_name}: {e}")
        return None

def stop_bot_process(bot_name):
    """Gracefully terminates a bot process."""
    if bot_name in running_bots:
        process = running_bots[bot_name]['process']
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            logger.info(f"Terminated process for bot {bot_name}")
        return True
    return False

def restart_bot_process(bot_name):
    """Restarts a bot process."""
    if bot_name in running_bots:
        bot_token = running_bots[bot_name]['token']
        bot_dir = running_bots[bot_name]['bot_dir']
        
        bot_code = ""
        bot_file_path = os.path.join(bot_dir, "bot.py")
        if os.path.exists(bot_file_path):
            with open(bot_file_path, 'r') as f:
                bot_code = f.read()
        
        requirements_content = None
        requirements_path = os.path.join(bot_dir, "requirements.txt")
        if os.path.exists(requirements_path):
            with open(requirements_path, 'r') as f:
                requirements_content = f.read()
        
        stop_bot_process(bot_name)
        time.sleep(2)
        
        if bot_code:
            new_bot_info = start_bot_subprocess(bot_name, bot_token, bot_code, requirements_content)
            if new_bot_info:
                running_bots[bot_name] = new_bot_info
                return True
    return False

def update_bot_logs(bot_name):
    """Update the logs for a bot from its process output."""
    if bot_name in running_bots:
        process = running_bots[bot_name]['process']
        if process.poll() is None:
            output = process.stdout.read(4096)
            if output:
                running_bots[bot_name]['logs'] += output
        else:
            if process.stdout:
                output = process.stdout.read()
                if output:
                    running_bots[bot_name]['logs'] += output
            if process.stderr:
                err_output = process.stderr.read()
                if err_output:
                    running_bots[bot_name]['logs'] += f"\n[ERROR]\n{err_output}"

async def download_file(bot: Bot, document: Document, destination_path: str) -> bool:
    """Download a file from Telegram."""
    try:
        file = await bot.get_file(document.file_id)
        
        # Download the file using requests library
        response = requests.get(file.file_path)
        response.raise_for_status()

        with open(destination_path, 'wb') as f:
            f.write(response.content)
        
        return True
    except Exception as e:
        logger.error(f"Error downloading file: {e}")
        return False

# --- Core Command Handlers ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the main menu with a welcome message."""
    user_id = update.effective_user.id
    if user_id not in AUTHORIZED_USERS:
        await update.message.reply_text("You are not authorized to use this bot.")
        return
    
    welcome_message = f"""
{EMOJI.SPARKLES} *Welcome to BotHoster Pro!* {EMOJI.SPARKLES}
I can host and manage your Python Telegram bots.
{EMOJI.GEAR} Use the menu below to get started.
"""
    await context.bot.send_photo(
        chat_id=update.effective_chat.id,
        photo=START_IMAGE_URL,
        caption=welcome_message,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_main_menu_keyboard()
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the help message."""
    help_text = f"""
{EMOJI.QUESTION} *BotHoster Pro Help* {EMOJI.QUESTION}
{EMOJI.ROCKET} *Available Commands:*
`/start` - Show the main menu.
`/upload` - Begin the process to upload a new bot.
`/list` - Show your running and stopped bots.
`/stats` - View hosting statistics.
`/restart <bot_name>` - Restart a specific bot.
`/stop <bot_name>` - Stop a specific bot.
`/logs <bot_name>` - View logs of a specific bot.
`/edit <bot_name>` - Modify an existing bot's code or token.
`/mirror` - Mirror a file to a new URL.
`/help` - Show this help message.
`/cancel` - Cancel the current operation (like bot upload).
{EMOJI.INFO} The easiest way to manage your bots is by using the interactive buttons!
"""
    await edit_or_reply_message(update, help_text, get_main_menu_keyboard())

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows statistics about hosted bots."""
    if update.callback_query:
        await update.callback_query.answer("Crunching the numbers...")
    
    total_bots = len(running_bots)
    running_count = sum(1 for bot in running_bots.values() if bot['process'].poll() is None)
    stopped_count = total_bots - running_count
    
    stats_text = f"""
{EMOJI.BAR_CHART} *Hosting Statistics*
{EMOJI.ROBOT} Total Bots Managed: *{total_bots}*
{EMOJI.GREEN_CIRCLE} Bots Currently Running: *{running_count}*
{EMOJI.RED_CIRCLE} Bots Stopped: *{stopped_count}*
"""
    await edit_or_reply_message(update, stats_text, get_main_menu_keyboard())

async def list_bots_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lists all managed bots with buttons to interact with them."""
    if update.callback_query:
        await update.callback_query.answer("Fetching your bots...")
        
    if not running_bots:
        await edit_or_reply_message(update, f"{EMOJI.CLIPBOARD} You haven't uploaded any bots yet.", reply_markup=get_main_menu_keyboard())
        return
    
    keyboard = []
    for bot_name, info in running_bots.items():
        update_bot_logs(bot_name)
        status_emoji = EMOJI.GREEN_CIRCLE if info['process'].poll() is None else EMOJI.RED_CIRCLE
        
        keyboard.append([InlineKeyboardButton(f"{status_emoji} {bot_name}", callback_data=f"select_bot:{bot_name}")])
    
    keyboard.append([InlineKeyboardButton(f"{EMOJI.BACK} Main Menu", callback_data='main_menu')])
    
    await edit_or_reply_message(update, f"{EMOJI.CLIPBOARD} *Your Bots*\n\nSelect a bot to manage:", reply_markup=InlineKeyboardMarkup(keyboard))

async def restart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Restarts a specified bot."""
    if len(context.args) < 1:
        await update.message.reply_text("Please provide a bot name. Usage: `/restart <bot_name>`", parse_mode=ParseMode.MARKDOWN)
        return
    
    bot_name = context.args[0]
    if bot_name not in running_bots:
        await update.message.reply_text(f"Bot `{bot_name}` not found.", parse_mode=ParseMode.MARKDOWN)
        return
    
    loading_msg = await send_loading_animation(update, context, f"{EMOJI.LOADING} Restarting `{bot_name}`...")
    
    if restart_bot_process(bot_name):
        await loading_msg.edit_caption(f"{EMOJI.SUCCESS} Bot `{bot_name}` has been restarted successfully.")
    else:
        await loading_msg.edit_caption(f"{EMOJI.CANCEL} Failed to restart `{bot_name}`.")
        
async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stops a specified bot."""
    if len(context.args) < 1:
        await update.message.reply_text("Please provide a bot name. Usage: `/stop <bot_name>`", parse_mode=ParseMode.MARKDOWN)
        return
    
    bot_name = context.args[0]
    if bot_name not in running_bots:
        await update.message.reply_text(f"Bot `{bot_name}` not found.", parse_mode=ParseMode.MARKDOWN)
        return
    
    loading_msg = await send_loading_animation(update, context, f"{EMOJI.LOADING} Stopping `{bot_name}`...")
    if stop_bot_process(bot_name):
        await loading_msg.edit_caption(f"{EMOJI.SUCCESS} Bot `{bot_name}` has been stopped.")
    else:
        await loading_msg.edit_caption(f"{EMOJI.CANCEL} Failed to stop `{bot_name}`.")

async def logs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows logs for a specified bot."""
    if len(context.args) < 1:
        await update.message.reply_text("Please provide a bot name. Usage: `/logs <bot_name>`", parse_mode=ParseMode.MARKDOWN)
        return
    
    bot_name = context.args[0]
    if bot_name not in running_bots:
        await update.message.reply_text(f"Bot `{bot_name}` not found.", parse_mode=ParseMode.MARKDOWN)
        return
    
    loading_msg = await send_loading_animation(update, context, f"{EMOJI.LOADING} Fetching logs for `{bot_name}`...")
    update_bot_logs(bot_name)
    log_content = running_bots[bot_name]['logs']
    
    if not log_content:
        log_output = "No logs available."
    elif len(log_content) > 3800:
        log_output = f"...\n{log_content[-3800:]}"
    else:
        log_output = log_content
    
    await loading_msg.edit_caption(f"{EMOJI.LOGS} *Logs for `{bot_name}`:*\n\n```\n{log_output}\n```", parse_mode=ParseMode.MARKDOWN)

async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows the settings menu."""
    await edit_or_reply_message(update, f"{EMOJI.GEAR} *Settings*\n\nChoose an option to configure:", reply_markup=get_settings_keyboard())

# --- Conversation Handlers for Bot Upload & Edit ---
(ASK_BOT_NAME, GET_BOT_FILE, GET_TOKEN, GET_REQUIREMENTS) = range(4)
(ASK_EDIT_BOT_NAME, ASK_EDIT_OPTION, GET_NEW_CODE, GET_NEW_TOKEN) = range(4, 8)
(ASK_MIRROR_FILE, ASK_MIRROR_URL) = range(8, 10)

async def upload_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the bot upload conversation."""
    user_id = update.callback_query.from_user.id if update.callback_query else update.message.from_user.id
    if user_id not in AUTHORIZED_USERS:
        await edit_or_reply_message(update, "You are not authorized to use this bot.")
        return ConversationHandler.END
    
    user_bots = sum(1 for bot_name in running_bots.keys())
    if user_bots >= MAX_BOTS_PER_USER:
        await edit_or_reply_message(update, f"{EMOJI.WARNING} You've reached the maximum number of bots ({MAX_BOTS_PER_USER}). Please delete some bots first.")
        return ConversationHandler.END
    
    welcome_text = f"""
{EMOJI.ROBOT} Let's upload a new bot!
First, what do you want to name this bot? (e.g., `MyAwesomeBot`)
Please use letters, numbers, and underscores only.
"""
    await edit_or_reply_message(update, welcome_text, reply_markup=get_cancel_keyboard())
    return ASK_BOT_NAME

async def ask_bot_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives bot name and asks for the bot.py file."""
    bot_name = update.message.text.strip()
    if not bot_name.replace('_', '').isalnum():
        await update.message.reply_text(f"{EMOJI.CANCEL} Invalid name. Please use only letters, numbers, and underscores. Try again.")
        return ASK_BOT_NAME
    if bot_name in running_bots:
        await update.message.reply_text(f"{EMOJI.CANCEL} A bot with this name already exists. Please choose another name.")
        return ASK_BOT_NAME
        
    context.user_data['bot_name'] = bot_name
    
    await update.message.reply_text(
        f"{EMOJI.SUCCESS} Great! Bot will be named `{bot_name}`.\n\n"
        f"{EMOJI.SNAKE} Now, please send me your Python file (e.g., `bot.py`).",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_cancel_keyboard()
    )
    return GET_BOT_FILE

async def receive_bot_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives bot.py and asks for the bot token."""
    document = update.message.document
    if not document or not document.file_name.endswith('.py'):
        await update.message.reply_text(f"{EMOJI.CANCEL} That's not a Python file. Please send a `.py` file.", reply_markup=get_cancel_keyboard())
        return GET_BOT_FILE
    
    if document.file_size > MAX_BOT_FILE_SIZE:
        await update.message.reply_text(f"{EMOJI.CANCEL} File is too large. Maximum size is {MAX_BOT_FILE_SIZE/1024/1024:.2f}MB.", reply_markup=get_cancel_keyboard())
        return GET_BOT_FILE
    
    bot_name = context.user_data['bot_name']
    loading_msg = await send_loading_animation(update, context, f"{EMOJI.LOADING} Downloading your bot file...")
    
    try:
        temp_dir = tempfile.mkdtemp()
        temp_file_path = os.path.join(temp_dir, document.file_name)
        
        if not await download_file(context.bot, document, temp_file_path):
            await loading_msg.edit_caption(f"{EMOJI.CANCEL} Failed to download the file. Please try again.")
            shutil.rmtree(temp_dir)
            return GET_BOT_FILE

        with open(temp_file_path, 'r', encoding='utf-8') as f:
            bot_code = f.read()

        context.user_data['bot_code'] = bot_code
        shutil.rmtree(temp_dir)

        await loading_msg.edit_caption(
            f"{EMOJI.SUCCESS} Bot file received!\n\n"
            f"{EMOJI.KEY} Now, please send me the Telegram token for `{bot_name}`.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_cancel_keyboard()
        )
        return GET_TOKEN
    except Exception as e:
        logger.error(f"Error in receive_bot_file: {e}")
        await loading_msg.edit_caption(f"{EMOJI.CANCEL} An error occurred while processing the file. Please try again.")
        return ConversationHandler.END

async def receive_token_and_ask_requirements(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives token and asks for requirements.txt."""
    bot_token = update.message.text.strip()
    bot_name = context.user_data['bot_name']
    
    context.user_data['bot_token'] = bot_token
    
    keyboard = [
        [InlineKeyboardButton(f"{EMOJI.SUCCESS} Yes, I have requirements.txt", callback_data='has_requirements')],
        [InlineKeyboardButton(f"{EMOJI.CANCEL} No, continue without requirements", callback_data='no_requirements')]
    ]
    
    await update.message.reply_text(
        f"{EMOJI.SUCCESS} Token received!\n\n"
        f"{EMOJI.PACKAGE} Does your bot require any additional packages? (Do you have a `requirements.txt` file?)",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return GET_REQUIREMENTS

async def receive_requirements_and_run(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles the final step of the upload process."""
    if update.callback_query:
        await update.callback_query.answer()
        has_requirements = update.callback_query.data == 'has_requirements'
        if has_requirements:
            await update.callback_query.edit_message_text(
                f"{EMOJI.PACKAGE} Please send me your `requirements.txt` file.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=get_cancel_keyboard()
            )
            return GET_REQUIREMENTS
    
    bot_name = context.user_data['bot_name']
    bot_token = context.user_data['bot_token']
    bot_code = context.user_data['bot_code']
    requirements_content = context.user_data.get('requirements_content')
    
    status_msg = await send_loading_animation(update, context, f"{EMOJI.LOADING} Starting your bot...")
    
    bot_info = start_bot_subprocess(bot_name, bot_token, bot_code, requirements_content)
    
    if bot_info:
        running_bots[bot_name] = bot_info
        await status_msg.edit_caption(
            f"{EMOJI.PARTY} Hooray! Your bot `{bot_name}` is now running!",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_back_to_main_menu_keyboard()
        )
    else:
        await status_msg.edit_caption(
            f"{EMOJI.CANCEL} A critical error occurred while trying to start your bot. "
            "Please check the hoster bot's console logs for more details or review your bot code.",
            reply_markup=get_back_to_main_menu_keyboard()
        )
        
    context.user_data.clear()
    return ConversationHandler.END

async def receive_requirements_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives requirements.txt and stores its content."""
    document = update.message.document
    if not document or not document.file_name.endswith('.txt'):
        await update.message.reply_text(f"{EMOJI.CANCEL} That's not a text file. Please send a `requirements.txt` file.", reply_markup=get_cancel_keyboard())
        return GET_REQUIREMENTS
    
    loading_msg = await send_loading_animation(update, context, f"{EMOJI.LOADING} Downloading requirements file...")
    
    try:
        temp_dir = tempfile.mkdtemp()
        temp_file_path = os.path.join(temp_dir, document.file_name)
        
        if not await download_file(context.bot, document, temp_file_path):
            await loading_msg.edit_caption(f"{EMOJI.CANCEL} Failed to download the file. Please try again.")
            shutil.rmtree(temp_dir)
            return GET_REQUIREMENTS

        with open(temp_file_path, 'r', encoding='utf-8') as f:
            requirements_content = f.read()
        
        context.user_data['requirements_content'] = requirements_content
        shutil.rmtree(temp_dir)
        
        await loading_msg.edit_caption(
            f"{EMOJI.SUCCESS} Requirements file received!\n\n"
            f"{EMOJI.LOADING} Now, attempting to start your bot...",
            parse_mode=ParseMode.MARKDOWN
        )
        
        return await receive_requirements_and_run(update, context)

    except Exception as e:
        logger.error(f"Error in receive_requirements_file: {e}")
        await loading_msg.edit_caption(f"{EMOJI.CANCEL} An error occurred while processing the requirements file. Please try again.")
        return ConversationHandler.END

async def cancel_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels and ends the upload conversation."""
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            f"{EMOJI.CANCEL} Upload process cancelled.",
            reply_markup=get_main_menu_keyboard()
        )
    else:
        await update.message.reply_text(
            f"{EMOJI.CANCEL} Upload process cancelled.",
            reply_markup=get_main_menu_keyboard()
        )
    
    context.user_data.clear()
    return ConversationHandler.END

# --- Edit Bot Conversation Handlers ---
async def edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the edit conversation."""
    if len(context.args) < 1:
        await update.message.reply_text("Please provide a bot name. Usage: `/edit <bot_name>`", parse_mode=ParseMode.MARKDOWN)
        return ConversationHandler.END
    
    bot_name = context.args[0]
    if bot_name not in running_bots:
        await update.message.reply_text(f"Bot `{bot_name}` not found.", parse_mode=ParseMode.MARKDOWN)
        return ConversationHandler.END

    context.user_data['bot_name_to_edit'] = bot_name
    
    keyboard = [
        [InlineKeyboardButton(f"{EMOJI.SNAKE} Edit Code", callback_data='edit_option:code')],
        [InlineKeyboardButton(f"{EMOJI.KEY} Edit Token", callback_data='edit_option:token')],
        [InlineKeyboardButton(f"{EMOJI.BACK} Cancel", callback_data='cancel_edit')]
    ]
    
    await update.message.reply_text(
        f"{EMOJI.GEAR} What would you like to edit for bot `{bot_name}`?",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return ASK_EDIT_OPTION

async def ask_for_new_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Asks for the new bot code."""
    await update.callback_query.answer()
    context.user_data['edit_option'] = 'code'
    await update.callback_query.edit_message_text(
        f"{EMOJI.CODE} Please send me the new Python file for `{context.user_data['bot_name_to_edit']}`.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_cancel_keyboard()
    )
    return GET_NEW_CODE

async def ask_for_new_token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Asks for the new bot token."""
    await update.callback_query.answer()
    context.user_data['edit_option'] = 'token'
    await update.callback_query.edit_message_text(
        f"{EMOJI.KEY} Please send me the new Telegram token for `{context.user_data['bot_name_to_edit']}`.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_cancel_keyboard()
    )
    return GET_NEW_TOKEN

async def receive_new_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives new bot code, saves it, and restarts the bot."""
    document = update.message.document
    if not document or not document.file_name.endswith('.py'):
        await update.message.reply_text(f"{EMOJI.CANCEL} That's not a Python file. Please send a `.py` file.", reply_markup=get_cancel_keyboard())
        return GET_NEW_CODE
    
    bot_name = context.user_data['bot_name_to_edit']
    loading_msg = await send_loading_animation(update, context, f"{EMOJI.LOADING} Updating code for `{bot_name}`...")
    
    try:
        temp_dir = tempfile.mkdtemp()
        temp_file_path = os.path.join(temp_dir, document.file_name)
        if not await download_file(context.bot, document, temp_file_path):
            await loading_msg.edit_caption(f"{EMOJI.CANCEL} Failed to download the file. Please try again.")
            shutil.rmtree(temp_dir)
            return GET_NEW_CODE

        bot_dir = running_bots[bot_name]['bot_dir']
        new_bot_code_path = os.path.join(bot_dir, "bot.py")
        shutil.copyfile(temp_file_path, new_bot_code_path)
        shutil.rmtree(temp_dir)
        
        await loading_msg.edit_caption(f"{EMOJI.LOADING} Restarting `{bot_name}` with the new code...")
        
        if restart_bot_process(bot_name):
            await loading_msg.edit_caption(f"{EMOJI.SUCCESS} Bot `{bot_name}` has been successfully updated and restarted!", reply_markup=get_back_to_main_menu_keyboard())
        else:
            await loading_msg.edit_caption(f"{EMOJI.CANCEL} Failed to restart `{bot_name}` with the new code. Please check the logs.", reply_markup=get_back_to_main_menu_keyboard())
    except Exception as e:
        logger.error(f"Error updating bot code: {e}")
        await loading_msg.edit_caption(f"{EMOJI.CANCEL} An error occurred while updating the bot. Please try again.", reply_markup=get_back_to_main_menu_keyboard())

    context.user_data.clear()
    return ConversationHandler.END

async def receive_new_token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives new bot token, updates it, and restarts the bot."""
    new_token = update.message.text.strip()
    bot_name = context.user_data['bot_name_to_edit']
    
    loading_msg = await send_loading_animation(update, context, f"{EMOJI.LOADING} Updating token for `{bot_name}`...")

    try:
        running_bots[bot_name]['token'] = new_token
        
        await loading_msg.edit_caption(f"{EMOJI.LOADING} Restarting `{bot_name}` with the new token...")
        
        if restart_bot_process(bot_name):
            await loading_msg.edit_caption(f"{EMOJI.SUCCESS} Bot `{bot_name}` has been successfully updated and restarted!", reply_markup=get_back_to_main_menu_keyboard())
        else:
            await loading_msg.edit_caption(f"{EMOJI.CANCEL} Failed to restart `{bot_name}` with the new token. Please check the logs.", reply_markup=get_back_to_main_menu_keyboard())
    except Exception as e:
        logger.error(f"Error updating bot token: {e}")
        await loading_msg.edit_caption(f"{EMOJI.CANCEL} An error occurred while updating the bot. Please try again.", reply_markup=get_back_to_main_menu_keyboard())

    context.user_data.clear()
    return ConversationHandler.END

async def cancel_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels and ends the edit conversation."""
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        f"{EMOJI.CANCEL} Edit process cancelled.",
        reply_markup=get_main_menu_keyboard()
    )
    context.user_data.clear()
    return ConversationHandler.END

# --- Mirror Command Handlers ---
async def mirror_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the mirror conversation."""
    if update.callback_query:
        await update.callback_query.answer()
    
    await edit_or_reply_message(update,
        f"{EMOJI.MIRROR} *Mirror File*\n\n"
        f"Send me a file up to {MAX_MIRROR_FILE_SIZE/1024/1024:.2f}MB and I will provide you with a permanent URL.",
        reply_markup=get_cancel_keyboard()
    )
    return ASK_MIRROR_FILE

async def receive_mirror_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives a file to be mirrored and saves it."""
    document = update.message.document or update.message.video or update.message.audio or update.message.photo[-1]
    
    if not document:
        await update.message.reply_text(f"{EMOJI.CANCEL} Please send a file or media to mirror.")
        return ASK_MIRROR_FILE
    
    if document.file_size > MAX_MIRROR_FILE_SIZE:
        await update.message.reply_text(f"{EMOJI.CANCEL} File is too large. Maximum size is {MAX_MIRROR_FILE_SIZE/1024/1024:.2f}MB.", reply_markup=get_cancel_keyboard())
        return ASK_MIRROR_FILE
    
    loading_msg = await send_loading_animation(update, context, f"{EMOJI.LOADING} Downloading your file...")

    try:
        file_extension = os.path.splitext(document.file_name)[1] if document.file_name else ".dat"
        file_name = f"{document.file_unique_id}{file_extension}"
        file_path = os.path.join(MIRROR_DIR, file_name)

        if not await download_file(context.bot, document, file_path):
            await loading_msg.edit_caption(f"{EMOJI.CANCEL} Failed to download the file. Please try again.")
            return ASK_MIRROR_FILE
        
        file_url = f"https://your-bot-host-url/mirror/{file_name}" # Replace with your actual host URL
        
        await loading_msg.edit_caption(
            f"{EMOJI.SUCCESS} File mirrored successfully!\n\n"
            f"Here is your direct link: \n`{file_url}`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_back_to_main_menu_keyboard()
        )
    except Exception as e:
        logger.error(f"Error mirroring file: {e}")
        await loading_msg.edit_caption(f"{EMOJI.CANCEL} An error occurred while mirroring the file. Please try again.")
    
    return ConversationHandler.END

async def cancel_mirror(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels and ends the mirror conversation."""
    await update.message.reply_text(
        f"{EMOJI.CANCEL} Mirror process cancelled.",
        reply_markup=get_main_menu_keyboard()
    )
    context.user_data.clear()
    return ConversationHandler.END

# --- Callback Query Handlers (Button Presses) ---
async def main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles 'main_menu' button press to show the main menu."""
    await update.callback_query.answer()
    welcome_message = f"""
{EMOJI.SPARKLES} *Welcome to BotHoster Pro!* {EMOJI.SPARKLES}
I can host and manage your Python Telegram bots.
{EMOJI.GEAR} Use the menu below to get started.
"""
    await update.callback_query.edit_message_caption(
        caption=welcome_message,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_main_menu_keyboard()
    )

async def select_bot_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows the action menu for a specifically selected bot."""
    await update.callback_query.answer()
    bot_name = update.callback_query.data.split(':')[1]
    
    if bot_name not in running_bots:
        await edit_or_reply_message(update, f"{EMOJI.CANCEL} Bot not found. It might have been removed.", reply_markup=get_back_to_main_menu_keyboard())
        return
        
    info = running_bots[bot_name]
    update_bot_logs(bot_name)
    process_status = info['process'].poll()
    status_emoji = EMOJI.GREEN_CIRCLE if process_status is None else EMOJI.RED_CIRCLE
    
    uptime = datetime.now() - info['start_time'] if process_status is None else "N/A"
    
    text = f"""
{EMOJI.GEAR} *Managing Bot: `{bot_name}`*
Status: {status_emoji} *{'Running' if status_emoji == EMOJI.GREEN_CIRCLE else 'Stopped'}*
Uptime: `{str(uptime).split('.')[0]}`
What would you like to do?
"""
    await edit_or_reply_message(update, text, reply_markup=get_bot_actions_keyboard(bot_name))

async def bot_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles actions for a bot (stop, restart, logs, delete, download, edit)."""
    query = update.callback_query
    await query.answer()
    
    _, action, bot_name = query.data.split(':', 2)
    
    if action == 'delete_confirm':
        text = f"{EMOJI.QUESTION} Are you sure you want to delete `{bot_name}`? This will stop the bot and remove it from the list."
        await edit_or_reply_message(update, text, reply_markup=get_delete_confirmation_keyboard(bot_name))
        return
    
    if bot_name not in running_bots:
        await edit_or_reply_message(update, f"{EMOJI.CANCEL} Bot not found.", reply_markup=get_back_to_main_menu_keyboard())
        return
    
    if action == 'stop':
        loading_msg = await send_loading_animation(update, context, f"{EMOJI.LOADING} Stopping `{bot_name}`...")
        stop_bot_process(bot_name)
        await asyncio.sleep(1)
        await loading_msg.edit_caption(f"{EMOJI.STOP} Bot `{bot_name}` has been stopped.", parse_mode=ParseMode.MARKDOWN, reply_markup=get_bot_actions_keyboard(bot_name))
    
    elif action == 'restart':
        loading_msg = await send_loading_animation(update, context, f"{EMOJI.LOADING} Restarting `{bot_name}`...")
        if restart_bot_process(bot_name):
            await loading_msg.edit_caption(f"{EMOJI.RESTART} Bot `{bot_name}` has been successfully restarted!", parse_mode=ParseMode.MARKDOWN, reply_markup=get_bot_actions_keyboard(bot_name))
        else:
            await loading_msg.edit_caption(f"{EMOJI.CANCEL} Failed to restart `{bot_name}`.", parse_mode=ParseMode.MARKDOWN, reply_markup=get_bot_actions_keyboard(bot_name))
    
    elif action == 'logs':
        await query.edit_message_text(f"{EMOJI.LOADING} Fetching logs for `{bot_name}`...", parse_mode=ParseMode.MARKDOWN)
        update_bot_logs(bot_name)
        log_content = running_bots[bot_name]['logs']
        
        if not log_content:
            log_output = "No logs available."
        elif len(log_content) > 3800:
            log_output = f"...\n{log_content[-3800:]}"
        else:
            log_output = log_content
        
        await query.edit_message_text(f"{EMOJI.LOGS} *Logs for `{bot_name}`:*\n\n```\n{log_output}\n```", parse_mode=ParseMode.MARKDOWN, reply_markup=get_bot_actions_keyboard(bot_name))
    
    elif action == 'download':
        await query.edit_message_text(f"{EMOJI.LOADING} Preparing your bot files for download...", parse_mode=ParseMode.MARKDOWN)
        
        try:
            bot_dir = running_bots[bot_name]['bot_dir']
            import zipfile
            import io
            
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                bot_file_path = os.path.join(bot_dir, "bot.py")
                if os.path.exists(bot_file_path):
                    zip_file.write(bot_file_path, "bot.py")
                
                requirements_path = os.path.join(bot_dir, "requirements.txt")
                if os.path.exists(requirements_path):
                    zip_file.write(requirements_path, "requirements.txt")
            
            zip_buffer.seek(0)
            
            await query.message.reply_document(
                document=zip_buffer,
                filename=f"{bot_name}_source_code.zip",
                caption=f"{EMOJI.DOWNLOAD} Here's the source code for `{bot_name}`"
            )
            
            info = running_bots[bot_name]
            process_status = info['process'].poll()
            status_emoji = EMOJI.GREEN_CIRCLE if process_status is None else EMOJI.RED_CIRCLE
            uptime = datetime.now() - info['start_time'] if process_status is None else "N/A"
            text = f"""
{EMOJI.GEAR} *Managing Bot: `{bot_name}`*
Status: {status_emoji} *{'Running' if status_emoji == EMOJI.GREEN_CIRCLE else 'Stopped'}*
Uptime: `{str(uptime).split('.')[0]}`
What would you like to do?
"""
            await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=get_bot_actions_keyboard(bot_name))
            
        except Exception as e:
            logger.error(f"Error preparing bot files for download: {e}")
            await query.edit_message_text(f"{EMOJI.CANCEL} Failed to prepare files for download. Please try again.", parse_mode=ParseMode.MARKDOWN, reply_markup=get_bot_actions_keyboard(bot_name))
    
    elif action == 'delete_final':
        bot_dir = running_bots[bot_name]['bot_dir']
        stop_bot_process(bot_name)
        del running_bots[bot_name]
        
        try:
            shutil.rmtree(bot_dir)
        except Exception as e:
            logger.error(f"Error removing bot directory {bot_dir}: {e}")
        
        await query.edit_message_text(f"{EMOJI.SUCCESS} Bot `{bot_name}` has been removed.", parse_mode=ParseMode.MARKDOWN, reply_markup=get_back_to_main_menu_keyboard())
        
    elif action in ['edit_code', 'edit_token']:
        await query.answer()
        context.user_data['bot_name_to_edit'] = bot_name
        if action == 'edit_code':
            await ask_for_new_code(update, context)
        elif action == 'edit_token':
            await ask_for_new_token(update, context)

async def delete_all_bots_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Asks for confirmation to delete all bots."""
    await update.callback_query.answer()
    if not running_bots:
        await edit_or_reply_message(update, f"{EMOJI.CANCEL} There are no bots to delete.", reply_markup=get_main_menu_keyboard())
        return
    text = f"{EMOJI.QUESTION} Are you absolutely sure you want to remove all hosted bots? This action cannot be undone."
    await edit_or_reply_message(update, text, reply_markup=get_delete_all_confirmation_keyboard())

async def delete_all_bots_final(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stops all bots and removes them from the list."""
    await update.callback_query.answer()
    loading_msg = await send_loading_animation(update, context, f"{EMOJI.LOADING} Deleting all bots...")
    
    for bot_name in list(running_bots.keys()):
        bot_dir = running_bots[bot_name]['bot_dir']
        stop_bot_process(bot_name)
        del running_bots[bot_name]
        
        try:
            shutil.rmtree(bot_dir)
        except Exception as e:
            logger.error(f"Error removing bot directory {bot_dir}: {e}")
    
    await loading_msg.edit_caption(f"{EMOJI.SUCCESS} All hosted bots have been removed.", reply_markup=get_back_to_main_menu_keyboard())

async def settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles settings menu callbacks."""
    await update.callback_query.answer()
    
    if not update.callback_query.data:
        return
    
    parts = update.callback_query.data.split(':')
    if len(parts) < 2:
        return
    
    section = parts[1]
    
    if section == 'users':
        await update.callback_query.edit_message_text(
            f"{EMOJI.ROBOT} *Authorized Users*\n\n"
            f"Current authorized users: {', '.join(str(uid) for uid in AUTHORIZED_USERS)}\n\n"
            f"To add or remove users, please edit the `users.json` file directly.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_back_to_main_menu_keyboard()
        )
    
    elif section == 'bots':
        settings_text = f"""
{EMOJI.GEAR} *Bot Settings*\n\n
Maximum bots per user: *{MAX_BOTS_PER_USER}*
Maximum bot file size: *{MAX_BOT_FILE_SIZE/1024/1024:.2f}MB*
Allowed file types: *{', '.join(ALLOWED_FILE_TYPES)}*

To change these settings, please edit the `users.json` file directly.
"""
        await update.callback_query.edit_message_text(
            settings_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_back_to_main_menu_keyboard()
        )

# --- Autoreact Message Handler ---
async def autoreact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await update.message.set_reaction("👍") # You can change the reaction emoji here

# --- Main Function ---
def main():
    """Run the bot."""
    application = Application.builder().token(TOKEN).build()
    
    upload_conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(upload_start, pattern='^upload_start$'), 
            CommandHandler('upload', upload_start)
        ],
        states={
            ASK_BOT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_bot_file)],
            GET_BOT_FILE: [MessageHandler(filters.Document.ALL & filters.ATTACHMENT, receive_bot_file)],
            GET_TOKEN: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_token_and_ask_requirements)],
            GET_REQUIREMENTS: [
                CallbackQueryHandler(receive_requirements_and_run, pattern='^(has_requirements|no_requirements)$'),
                MessageHandler(filters.Document.ALL & filters.ATTACHMENT, receive_requirements_file)
            ],
        },
        fallbacks=[CommandHandler('cancel', cancel_upload), CallbackQueryHandler(cancel_upload, pattern='^cancel_upload$')],
        allow_reentry=True
    )

    edit_conv_handler = ConversationHandler(
        entry_points=[CommandHandler('edit', edit_start)],
        states={
            ASK_EDIT_OPTION: [CallbackQueryHandler(ask_for_new_code, pattern='^edit_option:code$'),
                              CallbackQueryHandler(ask_for_new_token, pattern='^edit_option:token$')],
            GET_NEW_CODE: [MessageHandler(filters.Document.ALL & filters.ATTACHMENT, receive_new_code)],
            GET_NEW_TOKEN: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_new_token)]
        },
        fallbacks=[CallbackQueryHandler(cancel_edit, pattern='^cancel_edit$')],
        allow_reentry=True
    )
    
    mirror_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(mirror_start, pattern='^mirror_start$'), CommandHandler('mirror', mirror_start)],
        states={
            ASK_MIRROR_FILE: [MessageHandler(filters.ALL, receive_mirror_file)]
        },
        fallbacks=[CommandHandler('cancel', cancel_mirror), CallbackQueryHandler(cancel_mirror, pattern='^cancel_upload$')],
        allow_reentry=True
    )
    
    application.add_handler(upload_conv_handler)
    application.add_handler(edit_conv_handler)
    application.add_handler(mirror_conv_handler)
    
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("list", list_bots_command))
    application.add_handler(CommandHandler("restart", restart_command))
    application.add_handler(CommandHandler("stop", stop_command))
    application.add_handler(CommandHandler("logs", logs_command))
    application.add_handler(CommandHandler("settings", settings_command))
    
    application.add_handler(CallbackQueryHandler(main_menu_callback, pattern='^main_menu$'))
    application.add_handler(CallbackQueryHandler(select_bot_callback, pattern='^select_bot:'))
    application.add_handler(CallbackQueryHandler(bot_action_callback, pattern='^bot_action:'))
    application.add_handler(CallbackQueryHandler(stats_command, pattern='^stats$'))
    application.add_handler(CallbackQueryHandler(list_bots_command, pattern='^list_bots$'))
    application.add_handler(CallbackQueryHandler(help_command, pattern='^help$'))
    application.add_handler(CallbackQueryHandler(delete_all_bots_confirm, pattern='^delete_all_confirm$'))
    application.add_handler(CallbackQueryHandler(delete_all_bots_final, pattern='^delete_all_final$'))
    application.add_handler(CallbackQueryHandler(settings_callback, pattern='^settings:'))
    application.add_handler(CallbackQueryHandler(settings_command, pattern='^settings$'))
    
    application.add_handler(MessageHandler(filters.ALL, autoreact))
    
    application.add_error_handler(lambda update, context: logger.error(f"Update {update} caused error {context.error}"))
    
    application.run_polling()

if __name__ == "__main__":
    main()

