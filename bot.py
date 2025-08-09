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
import random
import threading
from datetime import datetime
from typing import Union, Dict, Any, Optional
from flask import Flask, request, send_file, jsonify
from werkzeug.utils import secure_filename
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Document, InputFile
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
from telegram.error import BadRequest, TelegramError, RetryAfter
from telegram.request import BaseRequest

# --- Basic Setup ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Flask App for File Handling ---
app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 150 * 1024 * 1024  # 150MB max file size
UPLOAD_FOLDER = 'data/temp'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Ensure upload folder exists
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "OK"}), 200

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400
    
    if file:
        filename = secure_filename(file.filename)
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(file_path)
        return jsonify({"success": True, "file_path": file_path}), 200
    
    return jsonify({"error": "File upload failed"}), 500

# --- Configuration ---
# Load configuration from environment variable or use default
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8109732136:AAGoVJURJtbUJuqcN84ciC5We2Ni3W4OMYM")

# Load authorized users from file or use default
USERS_FILE = "data/users.json"
DATA_DIR = "data"
BOTS_DIR = "data/bots"
TEMP_DIR = "data/temp"
WELCOME_MEDIA = "welcome.gif"  # Change to welcome.jpg if using an image

# Create directories if they don't exist
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(BOTS_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)

# Load user configuration
try:
    with open(USERS_FILE, 'r') as f:
        users_config = json.load(f)
        AUTHORIZED_USERS = users_config.get("authorized_users", [5431714552, 6392830471])
        MAX_BOTS_PER_USER = users_config.get("bot_settings", {}).get("max_bots_per_user", 5)
        MAX_BOT_FILE_SIZE = users_config.get("bot_settings", {}).get("max_bot_file_size", 10485760)  # 10MB
        ALLOWED_FILE_TYPES = users_config.get("bot_settings", {}).get("allowed_file_types", [".py"])
        MIRROR_MAX_SIZE = users_config.get("bot_settings", {}).get("mirror_max_size", 157286400)  # 150MB
except (FileNotFoundError, json.JSONDecodeError):
    # Create default config if file doesn't exist or is invalid
    AUTHORIZED_USERS = [5431714552, 6392830471]
    MAX_BOTS_PER_USER = 5
    MAX_BOT_FILE_SIZE = 10485760  # 10MB
    ALLOWED_FILE_TYPES = [".py"]
    MIRROR_MAX_SIZE = 157286400  # 150MB
    
    default_config = {
        "authorized_users": AUTHORIZED_USERS,
        "bot_settings": {
            "max_bots_per_user": MAX_BOTS_PER_USER,
            "max_bot_file_size": MAX_BOT_FILE_SIZE,
            "allowed_file_types": ALLOWED_FILE_TYPES,
            "mirror_max_size": MIRROR_MAX_SIZE
        }
    }
    
    with open(USERS_FILE, 'w') as f:
        json.dump(default_config, f, indent=4)

# --- Global State ---
# Format: { 'bot_name': {'process': subprocess.Popen, 'start_time': datetime, 'token': str, 'bot_dir': str} }
running_bots = {}

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
    MIRROR = "🪞"
    EDIT = "✏️"
    REACT = "😊"
    STAR = "⭐"
    HEART = "❤️"
    THUMB_UP = "👍"
    FIRE = "🔥"
    COOL = "😎"
    THINKING = "🤔"
    EYES = "👀"
    WAVE = "👋"

# --- Loading Animation Messages ---
LOADING_MESSAGES = [
    f"{EMOJI.LOADING} Processing your request...",
    f"{EMOJI.LOADING} Working on it...",
    f"{EMOJI.LOADING} Almost there...",
    f"{EMOJI.LOADING} Just a moment...",
    f"{EMOJI.LOADING} Please wait..."
]

# --- Reaction Emojis ---
REACTION_EMOJIS = [
    EMOJI.STAR, EMOJI.HEART, EMOJI.THUMB_UP, EMOJI.COOL, 
    EMOJI.THINKING, EMOJI.EYES, EMOJI.FIRE, EMOJI.WAVE
]

# --- Keyboard Generation Functions ---
def get_main_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton(f"{EMOJI.UPLOAD} Upload New Bot", callback_data='upload_start')],
        [InlineKeyboardButton(f"{EMOJI.CLIPBOARD} My Bots", callback_data='list_bots')],
        [InlineKeyboardButton(f"{EMOJI.BAR_CHART} Statistics", callback_data='stats')],
        [InlineKeyboardButton(f"{EMOJI.MIRROR} Mirror File", callback_data='mirror_start')],
        [InlineKeyboardButton(f"{EMOJI.GEAR} Settings", callback_data='settings')],
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
            InlineKeyboardButton(f"{EMOJI.EDIT} Edit Code", callback_data=f'bot_action:edit:{bot_name}'),
            InlineKeyboardButton(f"{EMOJI.DELETE} Delete", callback_data=f'bot_action:delete_confirm:{bot_name}'),
        ],
        [
            InlineKeyboardButton(f"{EMOJI.DOWNLOAD} Download Code", callback_data=f'bot_action:download:{bot_name}'),
            InlineKeyboardButton(f"{EMOJI.LOGS} View Logs", callback_data=f'bot_action:logs:{bot_name}')
        ],
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

def get_settings_keyboard():
    keyboard = [
        [InlineKeyboardButton(f"{EMOJI.ROBOT} Manage Authorized Users", callback_data='settings:users')],
        [InlineKeyboardButton(f"{EMOJI.GEAR} Bot Settings", callback_data='settings:bots')],
        [InlineKeyboardButton(f"{EMOJI.REACT} Reaction Settings", callback_data='settings:reactions')],
        [InlineKeyboardButton(f"{EMOJI.BACK} Main Menu", callback_data='main_menu')]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_edit_bot_keyboard(bot_name):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"{EMOJI.MEMO} Edit Bot Code", callback_data=f'edit_bot:code:{bot_name}'),
            InlineKeyboardButton(f"{EMOJI.PACKAGE} Edit Requirements", callback_data=f'edit_bot:req:{bot_name}'),
        ],
        [
            InlineKeyboardButton(f"{EMOJI.KEY} Edit Token", callback_data=f'edit_bot:token:{bot_name}'),
            InlineKeyboardButton(f"{EMOJI.RESTART} Apply & Restart", callback_data=f'edit_bot:restart:{bot_name}'),
        ],
        [InlineKeyboardButton(f"{EMOJI.BACK} Back to Bot Actions", callback_data=f'select_bot:{bot_name}')]
    ])

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
                    # If editing fails, send a new message
                    await update.callback_query.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
        else:
            await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Error in edit_or_reply_message: {e}")

async def send_loading_message(update: Update):
    """Send a loading message with random animation text."""
    loading_text = random.choice(LOADING_MESSAGES)
    if update.callback_query:
        await update.callback_query.answer(loading_text)
        return await update.callback_query.edit_message_text(loading_text, parse_mode=ParseMode.MARKDOWN)
    else:
        return await update.message.reply_text(loading_text, parse_mode=ParseMode.MARKDOWN)

async def add_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add a random reaction to the user's message."""
    try:
        if update.message and update.message.text:
            reaction = random.choice(REACTION_EMOJIS)
            await update.message.react(reaction)
    except Exception as e:
        logger.error(f"Error adding reaction: {e}")

def create_bot_directory(bot_name: str) -> str:
    """Create a directory for the bot files."""
    bot_dir = os.path.join(BOTS_DIR, bot_name)
    os.makedirs(bot_dir, exist_ok=True)
    return bot_dir

def start_bot_subprocess(bot_name: str, bot_token: str, bot_code: str, requirements_content: str = None) -> Union[dict, None]:
    """Handles the logic of starting a bot in a subprocess."""
    try:
        # Create a directory for the bot
        bot_dir = create_bot_directory(bot_name)
        
        # Create the bot file
        bot_file_path = os.path.join(bot_dir, "bot.py")
        
        # Inject the token into the script
        modified_script = bot_code.replace("TOKEN = \"\"", f"TOKEN = \"{bot_token}\"")
        modified_script = modified_script.replace("TOKEN = ''", f"TOKEN = \"{bot_token}\"")
        modified_script = modified_script.replace("TOKEN=os.getenv(\"BOT_TOKEN\")", f"TOKEN = \"{bot_token}\"")
        
        with open(bot_file_path, 'w') as f:
            f.write(modified_script)
        
        # Create requirements.txt if provided
        if requirements_content:
            requirements_path = os.path.join(bot_dir, "requirements.txt")
            with open(requirements_path, 'w') as f:
                f.write(requirements_content)
        
        # Install requirements if they exist
        if requirements_content:
            try:
                subprocess.run(
                    ['pip', 'install', '-r', os.path.join(bot_dir, "requirements.txt")],
                    check=True,
                    capture_output=True,
                    text=True
                )
            except subprocess.CalledProcessError as e:
                logger.error(f"Failed to install requirements for {bot_name}: {e}")
                return None
        
        # Start the bot process
        process = subprocess.Popen(
            ['python', bot_file_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=bot_dir
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
        if process.poll() is None:  # Check if the process is still running
            process.terminate()
            # If terminate doesn't work, try kill
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
        
        # Read the original bot code
        bot_code = ""
        bot_file_path = os.path.join(bot_dir, "bot.py")
        if os.path.exists(bot_file_path):
            with open(bot_file_path, 'r') as f:
                bot_code = f.read()
        
        # Read requirements if they exist
        requirements_content = None
        requirements_path = os.path.join(bot_dir, "requirements.txt")
        if os.path.exists(requirements_path):
            with open(requirements_path, 'r') as f:
                requirements_content = f.read()
        
        stop_bot_process(bot_name)
        time.sleep(2)  # Give it time to terminate
        
        # Start a new one
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
        
        # Check if there's any output to read
        if process.poll() is None:  # Process is still running
            # Read any available output without blocking
            import select
            if select.select([process.stdout], [], [], 0)[0]:
                output = process.stdout.read(4096).decode('utf-8', errors='ignore')
                running_bots[bot_name]['logs'] += output
        else:
            # Process has finished, read any remaining output
            output, _ = process.communicate()
            running_bots[bot_name]['logs'] += output.decode('utf-8', errors='ignore')

async def download_file(update: Update, document: Document) -> Optional[str]:
    """Download a file from Telegram and return its content."""
    try:
        file = await update.message.effective_user.bot.get_file(document.file_id)
        
        # Create a temporary file path
        temp_file_path = os.path.join(TEMP_DIR, f"temp_{int(time.time())}_{document.file_name}")
        
        # Download the file
        await file.download_to_drive(custom_path=temp_file_path)
        
        # Read the content
        with open(temp_file_path, 'rb') as f:
            content = f.read()
        
        # Clean up the temporary file
        try:
            os.unlink(temp_file_path)
        except:
            pass
        
        return content.decode('utf-8')
    except Exception as e:
        logger.error(f"Error downloading file: {e}")
        return None

async def mirror_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mirror a file from the user to the bot."""
    if not update.message.document:
        await update.message.reply_text(f"{EMOJI.CANCEL} Please send a file to mirror.")
        return
    
    document = update.message.document
    
    # Check file size
    if document.file_size > MIRROR_MAX_SIZE:
        await update.message.reply_text(f"{EMOJI.CANCEL} File is too large. Maximum size is {MIRROR_MAX_SIZE/1024/1024}MB.")
        return
    
    loading_msg = await update.message.reply_text(f"{EMOJI.LOADING} Mirroring your file...")
    
    try:
        # Get the file
        file = await update.message.effective_user.bot.get_file(document.file_id)
        
        # Create a temporary file path
        temp_file_path = os.path.join(TEMP_DIR, f"mirror_{int(time.time())}_{document.file_name}")
        
        # Download the file
        await file.download_to_drive(custom_path=temp_file_path)
        
        # Send the file back
        with open(temp_file_path, 'rb') as f:
            await update.message.reply_document(
                document=f,
                caption=f"{EMOJI.SUCCESS} Here's your mirrored file: {document.file_name}"
            )
        
        # Clean up the temporary file
        try:
            os.unlink(temp_file_path)
        except:
            pass
        
        await loading_msg.delete()
        
    except Exception as e:
        logger.error(f"Error mirroring file: {e}")
        await loading_msg.edit_text(f"{EMOJI.CANCEL} Failed to mirror the file. Please try again.")

# --- Core Command Handlers ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the main menu with a welcome message and image/GIF."""
    user_id = update.effective_user.id
    
    # Check if user is authorized
    if user_id not in AUTHORIZED_USERS:
        await update.message.reply_text("You are not authorized to use this bot.")
        return
    
    welcome_message = f"""
{EMOJI.SPARKLES} *Welcome to BotHoster Pro!* {EMOJI.SPARKLES}
I can host and manage your Python Telegram bots.
{EMOJI.GEAR} Use the menu below to get started.
"""
    
    # Try to send a GIF or image with the welcome message
    try:
        if os.path.exists(WELCOME_MEDIA):
            with open(WELCOME_MEDIA, 'rb') as media:
                if WELCOME_MEDIA.endswith('.gif'):
                    await update.message.reply_animation(
                        animation=media,
                        caption=welcome_message,
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=get_main_menu_keyboard()
                    )
                else:
                    await update.message.reply_photo(
                        photo=media,
                        caption=welcome_message,
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=get_main_menu_keyboard()
                    )
        else:
            # If media file doesn't exist, just send text
            await update.message.reply_text(
                welcome_message,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=get_main_menu_keyboard()
            )
    except Exception as e:
        logger.error(f"Error sending welcome media: {e}")
        # If media fails, just send text
        await update.message.reply_text(
            welcome_message,
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
`/mirror` - Mirror a file up to 150MB.
`/edit <bot_name>` - Edit an existing bot.
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
        # Update the status before displaying
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
    
    status_msg = await update.message.reply_text(f"{EMOJI.LOADING} Restarting `{bot_name}`...")
    
    if restart_bot_process(bot_name):
        await status_msg.edit_text(f"{EMOJI.SUCCESS} Bot `{bot_name}` has been restarted successfully.")
    else:
        await status_msg.edit_text(f"{EMOJI.CANCEL} Failed to restart `{bot_name}`.")

async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stops a specified bot."""
    if len(context.args) < 1:
        await update.message.reply_text("Please provide a bot name. Usage: `/stop <bot_name>`", parse_mode=ParseMode.MARKDOWN)
        return
    
    bot_name = context.args[0]
    if bot_name not in running_bots:
        await update.message.reply_text(f"Bot `{bot_name}` not found.", parse_mode=ParseMode.MARKDOWN)
        return
    
    if stop_bot_process(bot_name):
        await update.message.reply_text(f"{EMOJI.SUCCESS} Bot `{bot_name}` has been stopped.")
    else:
        await update.message.reply_text(f"{EMOJI.CANCEL} Failed to stop `{bot_name}`.")

async def logs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows logs for a specified bot."""
    if len(context.args) < 1:
        await update.message.reply_text("Please provide a bot name. Usage: `/logs <bot_name>`", parse_mode=ParseMode.MARKDOWN)
        return
    
    bot_name = context.args[0]
    if bot_name not in running_bots:
        await update.message.reply_text(f"Bot `{bot_name}` not found.", parse_mode=ParseMode.MARKDOWN)
        return
    
    # Update logs before showing
    update_bot_logs(bot_name)
    
    log_content = running_bots[bot_name]['logs']
    
    if not log_content:
        log_output = "No logs available."
    # Telegram message limit is 4096 chars
    elif len(log_content) > 3800:
        log_output = f"...\n{log_content[-3800:]}"
    else:
        log_output = log_content
    
    await update.message.reply_text(f"{EMOJI.LOGS} *Logs for `{bot_name}`:*\n\n```\n{log_output}\n```", parse_mode=ParseMode.MARKDOWN)

async def mirror_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Starts the file mirroring process."""
    user_id = update.effective_user.id
    
    # Check if user is authorized
    if user_id not in AUTHORIZED_USERS:
        await update.message.reply_text("You are not authorized to use this bot.")
        return
    
    await update.message.reply_text(
        f"{EMOJI.MIRROR} *File Mirroring*\n\n"
        f"Please send me a file (up to {MIRROR_MAX_SIZE/1024/1024}MB) that you want to mirror.",
        parse_mode=ParseMode.MARKDOWN
    )

async def edit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Edits an existing bot."""
    if len(context.args) < 1:
        await update.message.reply_text("Please provide a bot name. Usage: `/edit <bot_name>`", parse_mode=ParseMode.MARKDOWN)
        return
    
    bot_name = context.args[0]
    if bot_name not in running_bots:
        await update.message.reply_text(f"Bot `{bot_name}` not found.", parse_mode=ParseMode.MARKDOWN)
        return
    
    # Show the edit menu
    info = running_bots[bot_name]
    update_bot_logs(bot_name)
    process_status = info['process'].poll()
    status_emoji = EMOJI.GREEN_CIRCLE if process_status is None else EMOJI.RED_CIRCLE
    
    uptime = datetime.now() - info['start_time'] if process_status is None else "N/A"
    
    text = f"""
{EMOJI.GEAR} *Editing Bot: `{bot_name}`*
Status: {status_emoji} *{'Running' if status_emoji == EMOJI.GREEN_CIRCLE else 'Stopped'}*
Uptime: `{str(uptime).split('.')[0]}`
What would you like to edit?
"""
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=get_edit_bot_keyboard(bot_name))

async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows the settings menu."""
    await edit_or_reply_message(update, f"{EMOJI.GEAR} *Settings*\n\nChoose an option to configure:", reply_markup=get_settings_keyboard())

# --- Conversation Handler for Bot Upload ---
(ASK_BOT_NAME, GET_BOT_FILE, GET_TOKEN, GET_REQUIREMENTS) = range(4)

async def upload_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the bot upload conversation."""
    if update.callback_query:
        await update.callback_query.answer()
        user_id = update.callback_query.from_user.id
    else:
        user_id = update.message.from_user.id
    
    # Check if user is authorized
    if user_id not in AUTHORIZED_USERS:
        await update.message.reply_text("You are not authorized to use this bot.")
        return ConversationHandler.END
    
    # Check if user has reached the maximum number of bots
    user_bots = sum(1 for bot_name in running_bots.keys())  # Simplified for this example
    if user_bots >= MAX_BOTS_PER_USER:
        await update.message.reply_text(f"{EMOJI.WARNING} You've reached the maximum number of bots ({MAX_BOTS_PER_USER}). Please delete some bots first.")
        return ConversationHandler.END
    
    welcome_text = f"""
{EMOJI.ROBOT} Let's upload a new bot!
First, what do you want to name this bot? (e.g., `MyAwesomeBot`)
Please use letters, numbers, and underscores only.
"""
    if update.callback_query:
        await update.callback_query.edit_message_text(welcome_text, parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(welcome_text, parse_mode=ParseMode.MARKDOWN)
    
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
        parse_mode=ParseMode.MARKDOWN
    )
    return GET_BOT_FILE

async def receive_bot_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives bot.py and asks for the bot token."""
    document = update.message.document
    if not document.file_name.endswith('.py'):
        await update.message.reply_text(f"{EMOJI.CANCEL} That's not a Python file. Please send a `.py` file.")
        return GET_BOT_FILE
    
    # Check file size
    if document.file_size > MAX_BOT_FILE_SIZE:
        await update.message.reply_text(f"{EMOJI.CANCEL} File is too large. Maximum size is {MAX_BOT_FILE_SIZE/1024/1024}MB.")
        return GET_BOT_FILE
    
    bot_name = context.user_data['bot_name']
    
    loading_msg = await send_loading_message(update)
    
    # Get the file content
    bot_code = await download_file(update, document)
    
    if not bot_code:
        await loading_msg.edit_text(f"{EMOJI.CANCEL} Failed to download the file. Please try again.")
        return GET_BOT_FILE
    
    context.user_data['bot_code'] = bot_code
    
    await loading_msg.edit_text(
        f"{EMOJI.SUCCESS} Bot file received!\n\n"
        f"{EMOJI.KEY} Now, please send me the Telegram token for `{bot_name}`.",
        parse_mode=ParseMode.MARKDOWN
    )
    return GET_TOKEN

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
        f"{EMOJI.PACKAGE} Does your bot require any additional packages? (Do you have a requirements.txt file?)",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return GET_REQUIREMENTS

async def receive_requirements_and_run(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives requirements.txt and runs the bot."""
    if update.callback_query:
        await update.callback_query.answer()
        has_requirements = update.callback_query.data == 'has_requirements'
    else:
        # This shouldn't happen in our flow, but just in case
        has_requirements = False
    
    bot_name = context.user_data['bot_name']
    bot_token = context.user_data['bot_token']
    bot_code = context.user_data['bot_code']
    
    requirements_content = None
    
    if has_requirements:
        await update.callback_query.edit_message_text(
            f"{EMOJI.PACKAGE} Please send me your requirements.txt file.",
            parse_mode=ParseMode.MARKDOWN
        )
        return GET_REQUIREMENTS  # Wait for the file
    
    # If no requirements, start the bot
    loading_msg = await send_loading_message(update)
    
    # Start the bot subprocess
    bot_info = start_bot_subprocess(bot_name, bot_token, bot_code, requirements_content)
    
    if bot_info:
        running_bots[bot_name] = bot_info
        await loading_msg.edit_text(
            f"{EMOJI.PARTY} Hooray! Your bot `{bot_name}` is now running!",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_back_to_main_menu_keyboard()
        )
    else:
        await loading_msg.edit_text(
            f"{EMOJI.CANCEL} A critical error occurred while trying to start your bot. "
            "Please check the hoster bot's console logs for more details.",
            reply_markup=get_back_to_main_menu_keyboard()
        )
        
    context.user_data.clear()
    return ConversationHandler.END

async def receive_requirements_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives requirements.txt and runs the bot."""
    document = update.message.document
    if not document.file_name.endswith('.txt'):
        await update.message.reply_text(f"{EMOJI.CANCEL} That's not a text file. Please send a `requirements.txt` file.")
        return GET_REQUIREMENTS
    
    loading_msg = await send_loading_message(update)
    
    # Get the file content
    requirements_content = await download_file(update, document)
    
    if not requirements_content:
        await loading_msg.edit_text(f"{EMOJI.CANCEL} Failed to download the requirements file. Please try again.")
        return GET_REQUIREMENTS
    
    bot_name = context.user_data['bot_name']
    bot_token = context.user_data['bot_token']
    bot_code = context.user_data['bot_code']
    
    # Start the bot subprocess
    bot_info = start_bot_subprocess(bot_name, bot_token, bot_code, requirements_content)
    
    if bot_info:
        running_bots[bot_name] = bot_info
        await loading_msg.edit_text(
            f"{EMOJI.PARTY} Hooray! Your bot `{bot_name}` is now running with its requirements!",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_back_to_main_menu_keyboard()
        )
    else:
        await loading_msg.edit_text(
            f"{EMOJI.CANCEL} A critical error occurred while trying to start your bot. "
            "Please check the hoster bot's console logs for more details.",
            reply_markup=get_back_to_main_menu_keyboard()
        )
        
    context.user_data.clear()
    return ConversationHandler.END

async def cancel_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels and ends the upload conversation."""
    await update.message.reply_text(
        f"{EMOJI.CANCEL} Upload process cancelled.",
        reply_markup=get_main_menu_keyboard()
    )
    
    context.user_data.clear()
    return ConversationHandler.END

# --- Conversation Handler for Bot Editing ---
(EDIT_SELECT_ACTION, EDIT_GET_CODE, EDIT_GET_REQ, EDIT_GET_TOKEN) = range(4)

async def edit_bot_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the bot editing conversation."""
    await update.callback_query.answer()
    
    _, action, bot_name = update.callback_query.data.split(':')
    
    context.user_data['edit_bot_name'] = bot_name
    
    if action == 'code':
        await update.callback_query.edit_message_text(
            f"{EMOJI.MEMO} Please send me the updated Python code for `{bot_name}`.",
            parse_mode=ParseMode.MARKDOWN
        )
        return EDIT_GET_CODE
    
    elif action == 'req':
        await update.callback_query.edit_message_text(
            f"{EMOJI.PACKAGE} Please send me the updated requirements.txt for `{bot_name}`.",
            parse_mode=ParseMode.MARKDOWN
        )
        return EDIT_GET_REQ
    
    elif action == 'token':
        await update.callback_query.edit_message_text(
            f"{EMOJI.KEY} Please send me the updated token for `{bot_name}`.",
            parse_mode=ParseMode.MARKDOWN
        )
        return EDIT_GET_TOKEN
    
    elif action == 'restart':
        await update.callback_query.edit_message_text(
            f"{EMOJI.LOADING} Applying changes and restarting `{bot_name}`...",
            parse_mode=ParseMode.MARKDOWN
        )
        
        if restart_bot_process(bot_name):
            await update.callback_query.edit_message_text(
                f"{EMOJI.SUCCESS} Bot `{bot_name}` has been successfully restarted!",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=get_edit_bot_keyboard(bot_name)
            )
        else:
            await update.callback_query.edit_message_text(
                f"{EMOJI.CANCEL} Failed to restart `{bot_name}`.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=get_edit_bot_keyboard(bot_name)
            )
        
        return ConversationHandler.END
    
    return ConversationHandler.END

async def edit_bot_receive_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives updated bot code."""
    bot_name = context.user_data['edit_bot_name']
    
    if update.message.document and update.message.document.file_name.endswith('.py'):
        loading_msg = await send_loading_message(update)
        
        # Get the file content
        bot_code = await download_file(update, update.message.document)
        
        if not bot_code:
            await loading_msg.edit_text(f"{EMOJI.CANCEL} Failed to download the file. Please try again.")
            return EDIT_GET_CODE
        
        # Save the updated code
        bot_dir = running_bots[bot_name]['bot_dir']
        bot_file_path = os.path.join(bot_dir, "bot.py")
        
        with open(bot_file_path, 'w') as f:
            f.write(bot_code)
        
        await loading_msg.edit_text(
            f"{EMOJI.SUCCESS} Bot code updated! Use 'Apply & Restart' to apply the changes.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_edit_bot_keyboard(bot_name)
        )
        
    else:
        # If not a document, treat as text code
        bot_code = update.message.text
        
        # Save the updated code
        bot_dir = running_bots[bot_name]['bot_dir']
        bot_file_path = os.path.join(bot_dir, "bot.py")
        
        with open(bot_file_path, 'w') as f:
            f.write(bot_code)
        
        await update.message.reply_text(
            f"{EMOJI.SUCCESS} Bot code updated! Use 'Apply & Restart' to apply the changes.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_edit_bot_keyboard(bot_name)
        )
    
    return ConversationHandler.END

async def edit_bot_receive_req(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives updated requirements.txt."""
    bot_name = context.user_data['edit_bot_name']
    
    if update.message.document and update.message.document.file_name.endswith('.txt'):
        loading_msg = await send_loading_message(update)
        
        # Get the file content
        requirements_content = await download_file(update, update.message.document)
        
        if not requirements_content:
            await loading_msg.edit_text(f"{EMOJI.CANCEL} Failed to download the file. Please try again.")
            return EDIT_GET_REQ
        
        # Save the updated requirements
        bot_dir = running_bots[bot_name]['bot_dir']
        requirements_path = os.path.join(bot_dir, "requirements.txt")
        
        with open(requirements_path, 'w') as f:
            f.write(requirements_content)
        
        await loading_msg.edit_text(
            f"{EMOJI.SUCCESS} Requirements updated! Use 'Apply & Restart' to apply the changes.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_edit_bot_keyboard(bot_name)
        )
        
    else:
        # If not a document, treat as text requirements
        requirements_content = update.message.text
        
        # Save the updated requirements
        bot_dir = running_bots[bot_name]['bot_dir']
        requirements_path = os.path.join(bot_dir, "requirements.txt")
        
        with open(requirements_path, 'w') as f:
            f.write(requirements_content)
        
        await update.message.reply_text(
            f"{EMOJI.SUCCESS} Requirements updated! Use 'Apply & Restart' to apply the changes.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_edit_bot_keyboard(bot_name)
        )
    
    return ConversationHandler.END

async def edit_bot_receive_token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives updated bot token."""
    bot_name = context.user_data['edit_bot_name']
    bot_token = update.message.text.strip()
    
    # Save the updated token
    running_bots[bot_name]['token'] = bot_token
    
    await update.message.reply_text(
        f"{EMOJI.SUCCESS} Token updated! Use 'Apply & Restart' to apply the changes.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_edit_bot_keyboard(bot_name)
    )
    
    return ConversationHandler.END

async def cancel_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels and ends the edit conversation."""
    bot_name = context.user_data.get('edit_bot_name', 'unknown')
    
    await update.message.reply_text(
        f"{EMOJI.CANCEL} Edit process cancelled for `{bot_name}`.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_edit_bot_keyboard(bot_name)
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
    await edit_or_reply_message(update, welcome_message, get_main_menu_keyboard())

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
    
    _, action, bot_name = query.data.split(':')
    
    if action == 'delete_confirm':
        text = f"{EMOJI.QUESTION} Are you sure you want to delete `{bot_name}`? This will stop the bot and remove it from the list."
        await edit_or_reply_message(update, text, reply_markup=get_delete_confirmation_keyboard(bot_name))
        return
    
    if bot_name not in running_bots:
        await edit_or_reply_message(update, f"{EMOJI.CANCEL} Bot not found.", reply_markup=get_back_to_main_menu_keyboard())
        return
    
    if action == 'stop':
        await query.edit_message_text(f"{EMOJI.LOADING} Stopping `{bot_name}`...", parse_mode=ParseMode.MARKDOWN)
        stop_bot_process(bot_name)
        await asyncio.sleep(1) # Give it time to terminate
        await query.edit_message_text(f"{EMOJI.STOP} Bot `{bot_name}` has been stopped.", parse_mode=ParseMode.MARKDOWN, reply_markup=get_bot_actions_keyboard(bot_name))
    
    elif action == 'restart':
        await query.edit_message_text(f"{EMOJI.LOADING} Restarting `{bot_name}`...", parse_mode=ParseMode.MARKDOWN)
        
        if restart_bot_process(bot_name):
            await query.edit_message_text(f"{EMOJI.RESTART} Bot `{bot_name}` has been successfully restarted!", parse_mode=ParseMode.MARKDOWN, reply_markup=get_bot_actions_keyboard(bot_name))
        else:
            await query.edit_message_text(f"{EMOJI.CANCEL} Failed to restart `{bot_name}`.", parse_mode=ParseMode.MARKDOWN, reply_markup=get_bot_actions_keyboard(bot_name))
    
    elif action == 'logs':
        await query.edit_message_text(f"{EMOJI.LOADING} Fetching logs for `{bot_name}`...", parse_mode=ParseMode.MARKDOWN)
        
        # Update logs before showing
        update_bot_logs(bot_name)
        
        log_content = running_bots[bot_name]['logs']
        
        if not log_content:
            log_output = "No logs available."
        # Telegram message limit is 4096 chars
        elif len(log_content) > 3800:
            log_output = f"...\n{log_content[-3800:]}"
        else:
            log_output = log_content
        
        await query.edit_message_text(f"{EMOJI.LOGS} *Logs for `{bot_name}`:*\n\n```\n{log_output}\n```", parse_mode=ParseMode.MARKDOWN, reply_markup=get_bot_actions_keyboard(bot_name))
    
    elif action == 'download':
        await query.edit_message_text(f"{EMOJI.LOADING} Preparing your bot files for download...", parse_mode=ParseMode.MARKDOWN)
        
        try:
            bot_dir = running_bots[bot_name]['bot_dir']
            
            # Create a temporary zip file
            zip_buffer = io.BytesIO()
            
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                # Add bot.py
                bot_file_path = os.path.join(bot_dir, "bot.py")
                if os.path.exists(bot_file_path):
                    zip_file.write(bot_file_path, "bot.py")
                
                # Add requirements.txt if it exists
                requirements_path = os.path.join(bot_dir, "requirements.txt")
                if os.path.exists(requirements_path):
                    zip_file.write(requirements_path, "requirements.txt")
            
            zip_buffer.seek(0)
            
            await query.message.reply_document(
                document=zip_buffer,
                filename=f"{bot_name}_source_code.zip",
                caption=f"{EMOJI.DOWNLOAD} Here's the source code for `{bot_name}`"
            )
            
            # Show the bot actions menu again
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
            await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=get_bot_actions_keyboard(bot_name))
            
        except Exception as e:
            logger.error(f"Error preparing bot files for download: {e}")
            await query.edit_message_text(f"{EMOJI.CANCEL} Failed to prepare files for download. Please try again.", parse_mode=ParseMode.MARKDOWN, reply_markup=get_bot_actions_keyboard(bot_name))
    
    elif action == 'edit':
        # Show the edit menu
        info = running_bots[bot_name]
        update_bot_logs(bot_name)
        process_status = info['process'].poll()
        status_emoji = EMOJI.GREEN_CIRCLE if process_status is None else EMOJI.RED_CIRCLE
        
        uptime = datetime.now() - info['start_time'] if process_status is None else "N/A"
        
        text = f"""
{EMOJI.GEAR} *Editing Bot: `{bot_name}`*
Status: {status_emoji} *{'Running' if status_emoji == EMOJI.GREEN_CIRCLE else 'Stopped'}*
Uptime: `{str(uptime).split('.')[0]}`
What would you like to edit?
"""
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=get_edit_bot_keyboard(bot_name))
    
    elif action == 'delete_final':
        bot_dir = running_bots[bot_name]['bot_dir']
        stop_bot_process(bot_name)
        del running_bots[bot_name]
        
        # Remove the bot directory
        try:
            shutil.rmtree(bot_dir)
        except Exception as e:
            logger.error(f"Error removing bot directory {bot_dir}: {e}")
        
        await query.edit_message_text(f"{EMOJI.SUCCESS} Bot `{bot_name}` has been removed.", parse_mode=ParseMode.MARKDOWN, reply_markup=get_back_to_main_menu_keyboard())

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
    for bot_name in list(running_bots.keys()):
        bot_dir = running_bots[bot_name]['bot_dir']
        stop_bot_process(bot_name)
        del running_bots[bot_name]
        
        # Remove the bot directory
        try:
            shutil.rmtree(bot_dir)
        except Exception as e:
            logger.error(f"Error removing bot directory {bot_dir}: {e}")
    
    await edit_or_reply_message(update, f"{EMOJI.SUCCESS} All hosted bots have been removed.", reply_markup=get_back_to_main_menu_keyboard())

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
Maximum bot file size: *{MAX_BOT_FILE_SIZE/1024/1024}MB*
Allowed file types: *{', '.join(ALLOWED_FILE_TYPES)}*
Maximum mirror file size: *{MIRROR_MAX_SIZE/1024/1024}MB*

To change these settings, please edit the `users.json` file directly.
"""
        await update.callback_query.edit_message_text(
            settings_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_back_to_main_menu_keyboard()
        )
    
    elif section == 'reactions':
        settings_text = f"""
{EMOJI.REACT} *Reaction Settings*\n\n
The bot will automatically react to messages with random emojis.
Current reaction emojis: {', '.join(REACTION_EMOJIS)}

To change these settings, please edit the `REACTION_EMOJIS` list in the bot code.
"""
        await update.callback_query.edit_message_text(
            settings_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_back_to_main_menu_keyboard()
        )

async def mirror_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Starts the mirror process from a button."""
    await update.callback_query.answer()
    await mirror_command(update, context)

# --- Main Function ---
def run_flask():
    """Run the Flask app in a separate thread."""
    app.run(host='0.0.0.0', port=10000, threaded=True)

def main():
    """Run the bot."""
    # Start Flask in a separate thread
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    
    # Create the application
    application = Application.builder().token(TOKEN).build()
    
    # Conversation handler for the upload process
    upload_conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(upload_start, pattern='^upload_start$'), 
            CommandHandler('upload', upload_start)
        ],
        states={
            ASK_BOT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_bot_file)],
            GET_BOT_FILE: [MessageHandler(filters.Document.PY, receive_bot_file)],
            GET_TOKEN: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_token_and_ask_requirements)],
            GET_REQUIREMENTS: [
                CallbackQueryHandler(receive_requirements_and_run, pattern='^(has_requirements|no_requirements)$'),
                MessageHandler(filters.Document.TXT, receive_requirements_file)
            ],
        },
        fallbacks=[CommandHandler('cancel', cancel_upload)],
        allow_reentry=True
    )
    
    # Conversation handler for the edit process
    edit_conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(edit_bot_start, pattern='^edit_bot:'),
            CommandHandler('edit', edit_command)
        ],
        states={
            EDIT_GET_CODE: [MessageHandler(filters.TEXT | filters.Document.PY, edit_bot_receive_code)],
            EDIT_GET_REQ: [MessageHandler(filters.TEXT | filters.Document.TXT, edit_bot_receive_req)],
            EDIT_GET_TOKEN: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_bot_receive_token)],
        },
        fallbacks=[CommandHandler('cancel', cancel_edit)],
        allow_reentry=True
    )
    
    application.add_handler(upload_conv_handler)
    application.add_handler(edit_conv_handler)
    
    # Regular command handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("list", list_bots_command))
    application.add_handler(CommandHandler("restart", restart_command))
    application.add_handler(CommandHandler("stop", stop_command))
    application.add_handler(CommandHandler("logs", logs_command))
    application.add_handler(CommandHandler("mirror", mirror_command))
    application.add_handler(CommandHandler("settings", settings_command))
    
    # Message handler for mirroring files
    application.add_handler(MessageHandler(filters.Document.ALL & ~filters.COMMAND, mirror_file))
    
    # Callback query handlers for menu navigation and actions
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
    application.add_handler(CallbackQueryHandler(mirror_start_callback, pattern='^mirror_start$'))
    
    # Auto-reaction to all text messages
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, add_reaction))
    
    # Log all errors
    application.add_error_handler(lambda update, context: logger.error(f"Update {update} caused error {context.error}"))
    
    # Start the bot
    application.run_polling()

if __name__ == "__main__":
    main()
