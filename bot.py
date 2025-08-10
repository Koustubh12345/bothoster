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
import math
import fcntl
import psutil
from datetime import datetime, timedelta
from typing import Union, Dict, Any, Optional, List, Tuple
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Document,
    Bot,
    InputFile,
    InputMediaPhoto,
    InputMediaAnimation
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
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    logger.error("TELEGRAM_BOT_TOKEN environment variable not set!")
    exit(1)

RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL", "https://your-app-name.onrender.com")
USERS_FILE = "data/users.json"
DATA_DIR = "data"
BOTS_DIR = "data/bots"
MIRROR_DIR = "data/mirror"
TEMPLATES_DIR = "data/templates"
LOGS_DIR = "data/logs"

# --- Create directories if they don't exist ---
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(BOTS_DIR, exist_ok=True)
os.makedirs(MIRROR_DIR, exist_ok=True)
os.makedirs(TEMPLATES_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)

# --- Load user configuration ---
try:
    with open(USERS_FILE, 'r') as f:
        users_config = json.load(f)
        AUTHORIZED_USERS = users_config.get("authorized_users", [5431714552, 6392830471])
        MAX_BOTS_PER_USER = users_config.get("bot_settings", {}).get("max_bots_per_user", 5)
        MAX_BOT_FILE_SIZE = users_config.get("bot_settings", {}).get("max_bot_file_size", 10485760)  # 10MB
        MAX_MIRROR_FILE_SIZE = users_config.get("bot_settings", {}).get("max_mirror_file_size", 104857600)  # 100MB
        ALLOWED_FILE_TYPES = users_config.get("bot_settings", {}).get("allowed_file_types", [".py"])
        AUTO_RESTART_BOTS = users_config.get("bot_settings", {}).get("auto_restart_bots", True)
        LOG_RETENTION_DAYS = users_config.get("bot_settings", {}).get("log_retention_days", 7)
except (FileNotFoundError, json.JSONDecodeError):
    AUTHORIZED_USERS = [5431714552, 6392830471]
    MAX_BOTS_PER_USER = 5
    MAX_BOT_FILE_SIZE = 10485760  # 10MB
    MAX_MIRROR_FILE_SIZE = 104857600  # 100MB
    ALLOWED_FILE_TYPES = [".py"]
    AUTO_RESTART_BOTS = True
    LOG_RETENTION_DAYS = 7
    
    default_config = {
        "authorized_users": AUTHORIZED_USERS,
        "bot_settings": {
            "max_bots_per_user": MAX_BOTS_PER_USER,
            "max_bot_file_size": MAX_BOT_FILE_SIZE,
            "max_mirror_file_size": MAX_MIRROR_FILE_SIZE,
            "allowed_file_types": ALLOWED_FILE_TYPES,
            "auto_restart_bots": AUTO_RESTART_BOTS,
            "log_retention_days": LOG_RETENTION_DAYS
        }
    }
    with open(USERS_FILE, 'w') as f:
        json.dump(default_config, f, indent=4)

# --- Bot Templates ---
BOT_TEMPLATES = {
    "echo_bot": {
        "name": "Echo Bot",
        "description": "A simple bot that echoes back messages",
        "file": "templates/echo_bot.py"
    },
    "poll_bot": {
        "name": "Poll Bot",
        "description": "Create and manage polls",
        "file": "templates/poll_bot.py"
    },
    "inline_bot": {
        "name": "Inline Bot",
        "description": "Bot with inline query capabilities",
        "file": "templates/inline_bot.py"
    },
    "admin_bot": {
        "name": "Admin Bot",
        "description": "Group administration bot",
        "file": "templates/admin_bot.py"
    },
    "file_bot": {
        "name": "File Bot",
        "description": "File storage and sharing bot",
        "file": "templates/file_bot.py"
    }
}

# --- Global State ---
running_bots: Dict[str, Dict[str, Any]] = {}
bot_monitor_task = None

# --- URLs ---
LOADING_ANIMATION_URL = "https://media.tenor.com/25ykirk3P4YAAAAd/loading-gif.gif"
START_IMAGE_URL = "https://media.tenor.com/25ykirk3P4YAAAAd/loading-gif.gif"

# --- UI Elements (Emojis & Keyboards) ---
class EMOJI:
    SPARKLES = "\u2728"
    ROBOT = "\ud83e\udd16"
    CLIPBOARD = "\ud83d\udccb"
    BAR_CHART = "\ud83d\udcca"
    QUESTION = "\u2753"
    UPLOAD = "\ud83d\udce4"
    SNAKE = "\ud83d\udc0d"
    MEMO = "\ud83d\udcdd"
    KEY = "\ud83d\udd11"
    BACK = "\u2b05\ufe0f"
    STOP = "\u23f9\ufe0f"
    RESTART = "\ud83d\udd04"
    LOGS = "\ud83d\udcc4"
    CANCEL = "\u274c"
    SUCCESS = "\u2705"
    LOADING = "\u23f3"
    ROCKET = "\ud83d\ude80"
    PACKAGE = "\ud83d\udce6"
    PARTY = "\ud83c\udf89"
    INFO = "\u2139\ufe0f"
    GREEN_CIRCLE = "\ud83d\udfe2"
    RED_CIRCLE = "\ud83d\udd34"
    GEAR = "\u2699\ufe0f"
    DELETE = "\ud83d\uddd1\ufe0f"
    DOWNLOAD = "\u2b07\ufe0f"
    WARNING = "\u26a0\ufe0f"
    FILE = "\ud83d\udcc4"
    CODE = "\ud83d\udc68\u200d\ud83d\udcbb"
    WRENCH = "\ud83d\udd27"
    MIRROR = "\ud83e\ude9e"
    STORAGE = "\ud83d\udcbe"
    TEMPLATE = "\ud83d\udcdd"
    HEALTH = "\u2764\ufe0f"
    SEARCH = "\ud83d\udd0d"
    FILTER = "\ud83d\udd0e"
    BACKUP = "\ud83d\udcbe"
    RESTORE = "\u267b\ufe0f"
    PLAY_ALL = "\u25b6\ufe0f"
    STOP_ALL = "\u23f9\ufe0f"
    CLEAN = "\ud83e\uddf9"
    STAR = "\u2b50"

# --- Keyboard Generation Functions ---
def get_main_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton(f"{EMOJI.UPLOAD} Upload New Bot", callback_data='upload_start'),
         InlineKeyboardButton(f"{EMOJI.TEMPLATE} Use Template", callback_data='template_list')],
        [InlineKeyboardButton(f"{EMOJI.CLIPBOARD} My Bots", callback_data='list_bots')],
        [InlineKeyboardButton(f"{EMOJI.BAR_CHART} Statistics & Storage", callback_data='stats')],
        [InlineKeyboardButton(f"{EMOJI.MIRROR} Mirror File", callback_data='mirror_start')],
        [InlineKeyboardButton(f"{EMOJI.GEAR} Settings", callback_data='settings'), 
         InlineKeyboardButton(f"{EMOJI.QUESTION} Help", callback_data='help')]
    ]
    if running_bots:
        keyboard.append([
            InlineKeyboardButton(f"{EMOJI.PLAY_ALL} Start All", callback_data='start_all_bots'),
            InlineKeyboardButton(f"{EMOJI.STOP_ALL} Stop All", callback_data='stop_all_bots')
        ])
        keyboard.append([InlineKeyboardButton(f"{EMOJI.DELETE} Delete All Bots", callback_data='delete_all_confirm')])
    return InlineKeyboardMarkup(keyboard)

def get_stats_keyboard():
    keyboard = [
        [InlineKeyboardButton(f"{EMOJI.HEALTH} System Health", callback_data='system_health')],
        [InlineKeyboardButton(f"{EMOJI.MIRROR} Manage Mirror", callback_data='manage_mirror')],
        [InlineKeyboardButton(f"{EMOJI.CLEAN} Clean Logs", callback_data='clean_logs')],
        [InlineKeyboardButton(f"{EMOJI.BACK} Main Menu", callback_data='main_menu')]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_mirror_management_keyboard(mirror_size_gb: float):
    keyboard = []
    if mirror_size_gb > 0:
        keyboard.append([InlineKeyboardButton(f"{EMOJI.SEARCH} Browse Files", callback_data='browse_mirror')])
        keyboard.append([InlineKeyboardButton(f"{EMOJI.DELETE} Delete All Mirrored Files", callback_data='delete_all_mirror_confirm')])
    keyboard.append([InlineKeyboardButton(f"{EMOJI.BACK} Back to Stats", callback_data='stats')])
    return InlineKeyboardMarkup(keyboard)

def get_delete_all_mirror_confirmation_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"{EMOJI.WARNING} Yes, Delete All", callback_data='delete_all_mirror_final'),
            InlineKeyboardButton(f"{EMOJI.CANCEL} No, Cancel", callback_data='manage_mirror')
        ]
    ])

def get_bot_actions_keyboard(bot_name: str):
    is_running = running_bots.get(bot_name) and running_bots[bot_name]['process'].poll() is None
    first_row = [InlineKeyboardButton(f"{EMOJI.RESTART} Restart", callback_data=f'bot_action:restart:{bot_name}')]
    if is_running:
        first_row.insert(0, InlineKeyboardButton(f"{EMOJI.STOP} Stop", callback_data=f'bot_action:stop:{bot_name}'))
    else:
        first_row.insert(0, InlineKeyboardButton(f"{EMOJI.PLAY_ALL} Start", callback_data=f'bot_action:start:{bot_name}'))
    return InlineKeyboardMarkup([
        first_row,
        [
            InlineKeyboardButton(f"{EMOJI.DELETE} Delete", callback_data=f'bot_action:delete_confirm:{bot_name}'),
            InlineKeyboardButton(f"{EMOJI.DOWNLOAD} Download Code", callback_data=f'bot_action:download:{bot_name}')
        ],
        [InlineKeyboardButton(f"{EMOJI.LOGS} View Logs", callback_data=f'bot_action:logs:{bot_name}'),
         InlineKeyboardButton(f"{EMOJI.HEALTH} Resource Usage", callback_data=f'bot_action:resources:{bot_name}')],
        [InlineKeyboardButton(f"{EMOJI.BACKUP} Backup Bot", callback_data=f'bot_action:backup:{bot_name}'),
         InlineKeyboardButton(f"{EMOJI.CODE} Edit Code", callback_data=f'bot_action:edit:{bot_name}')],
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

def get_template_list_keyboard():
    keyboard = []
    for template_id, template_info in BOT_TEMPLATES.items():
        keyboard.append([InlineKeyboardButton(f"{EMOJI.TEMPLATE} {template_info['name']}", callback_data=f'select_template:{template_id}')])
    keyboard.append([InlineKeyboardButton(f"{EMOJI.BACK} Main Menu", callback_data='main_menu')])
    return InlineKeyboardMarkup(keyboard)

def get_template_action_keyboard(template_id: str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{EMOJI.ROCKET} Use This Template", callback_data=f'use_template:{template_id}')],
        [InlineKeyboardButton(f"{EMOJI.BACK} Back to Templates", callback_data='template_list')]
    ])

def get_edit_code_keyboard(bot_name: str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{EMOJI.SUCCESS} Save Changes", callback_data=f'save_code:{bot_name}')],
        [InlineKeyboardButton(f"{EMOJI.CANCEL} Cancel", callback_data=f'select_bot:{bot_name}')]
    ])

# --- Helper Functions ---
async def edit_or_reply_message(update: Update, text: str, reply_markup: Optional[InlineKeyboardMarkup] = None, photo_url: Optional[str] = None, use_animation: bool = False):
    try:
        if update.callback_query:
            query = update.callback_query
            # If the original message has a photo and the new one doesn't, or vice-versa, we must delete and send a new one.
            has_photo_orig = bool(query.message.photo or query.message.animation)
            has_photo_new = bool(photo_url)
            if has_photo_orig != has_photo_new:
                await query.message.delete()
                if has_photo_new:
                    if use_animation:
                        await query.message.chat.send_animation(animation=photo_url, caption=text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
                    else:
                        await query.message.chat.send_photo(photo=photo_url, caption=text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
                else:
                    await query.message.chat.send_message(text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup, disable_web_page_preview=True)
                return
            # If message type is consistent, we can edit.
            if photo_url:
                if use_animation and query.message.animation:
                    await query.edit_message_media(
                        media=InputMediaAnimation(media=photo_url, caption=text, parse_mode=ParseMode.MARKDOWN),
                        reply_markup=reply_markup
                    )
                elif query.message.photo:
                    await query.edit_message_media(
                        media=InputMediaPhoto(media=photo_url, caption=text, parse_mode=ParseMode.MARKDOWN),
                        reply_markup=reply_markup
                    )
                else:
                    # Can't edit from text to media, so delete and resend
                    await query.message.delete()
                    if use_animation:
                        await query.message.chat.send_animation(animation=photo_url, caption=text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
                    else:
                        await query.message.chat.send_photo(photo=photo_url, caption=text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
            else:
                await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup, disable_web_page_preview=True)
        else:
            if photo_url:
                if use_animation:
                    await update.message.reply_animation(animation=photo_url, caption=text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
                else:
                    await update.message.reply_photo(photo=photo_url, caption=text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
            else:
                await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup, disable_web_page_preview=True)
    except BadRequest as e:
        if "Message is not modified" in str(e):
            if update.callback_query:
                await update.callback_query.answer()
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

def create_log_file(bot_name: str) -> str:
    """Create a log file for the bot and return the path."""
    log_dir = os.path.join(LOGS_DIR, bot_name)
    os.makedirs(log_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = os.path.join(log_dir, f"{timestamp}.log")
    return log_file

def start_bot_subprocess(bot_name: str, bot_token: str, bot_code: str, requirements_content: Optional[str] = None) -> Optional[Dict[str, Any]]:
    try:
        bot_dir = create_bot_directory(bot_name)
        bot_file_path = os.path.join(bot_dir, "bot.py")
        log_file_path = create_log_file(bot_name)
        
        # Ensure the bot code has the token set
        if "TOKEN = " in bot_code:
            modified_code = bot_code.replace("TOKEN = \"\"", f"TOKEN = \"{bot_token}\"")
            modified_code = modified_code.replace("TOKEN = ''", f"TOKEN = \"{bot_token}\"")
            modified_code = modified_code.replace("TOKEN=os.getenv(\"BOT_TOKEN\")", f"TOKEN = \"{bot_token}\"")
        else:
            # If no TOKEN variable is found, add it at the top of the file
            modified_code = f"TOKEN = \"{bot_token}\"\n{bot_code}"
        
        with open(bot_file_path, 'w', encoding='utf-8') as f:
            f.write(modified_code)
        
        if requirements_content:
            requirements_path = os.path.join(bot_dir, "requirements.txt")
            with open(requirements_path, 'w', encoding='utf-8') as f:
                f.write(requirements_content)
            
            logger.info(f"Installing requirements for {bot_name}...")
            with open(log_file_path, 'a') as log_file:
                log_file.write(f"--- Installing requirements at {datetime.now().isoformat()} ---\n")
                pip_process = subprocess.run(
                    ['pip', 'install', '-r', requirements_path],
                    capture_output=True, text=True, cwd=bot_dir
                )
                log_file.write(pip_process.stdout)
                if pip_process.returncode != 0:
                    log_file.write(f"ERROR: {pip_process.stderr}\n")
                    logger.error(f"Failed to install requirements for {bot_name}. Stderr: {pip_process.stderr}")
        
        # Open log file for the process
        log_file = open(log_file_path, 'a')
        log_file.write(f"--- Bot started at {datetime.now().isoformat()} ---\n")
        
        process = subprocess.Popen(
            ['python3', 'bot.py'],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=bot_dir,
            text=True,
            encoding='utf-8',
            errors='replace',
            preexec_fn=os.setsid,
            bufsize=1
        )
        
        logger.info(f"Started subprocess for bot '{bot_name}' with PID {process.pid}.")
        
        # Make stdout non-blocking
        if process.stdout:
            fd = process.stdout.fileno()
            fl = fcntl.fcntl(fd, fcntl.F_GETFL)
            fcntl.fcntl(fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)
        
        return {
            'process': process,
            'start_time': datetime.now(),
            'token': bot_token,
            'bot_dir': bot_dir,
            'logs': "",
            'log_file': log_file,
            'log_file_path': log_file_path,
            'restart_count': 0,
            'last_restart': None,
            'cpu_usage': 0.0,
            'memory_usage': 0.0
        }
    except Exception as e:
        logger.error(f"Failed to start subprocess for {bot_name}: {e}", exc_info=True)
        return None

def stop_bot_process(bot_name: str) -> bool:
    if bot_name in running_bots:
        process = running_bots[bot_name]['process']
        log_file = running_bots[bot_name].get('log_file')
        
        if process.poll() is None:
            logger.info(f"Stopping process group for bot {bot_name} with PGID {process.pid}...")
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                process.wait(timeout=5)
                logger.info(f"Terminated process group for bot {bot_name}.")
                
                # Log the termination
                if log_file and not log_file.closed:
                    log_file.write(f"--- Bot stopped at {datetime.now().isoformat()} ---\n")
                    log_file.flush()
            except subprocess.TimeoutExpired:
                logger.warning(f"Process group for {bot_name} did not terminate in time. Killing...")
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                if log_file and not log_file.closed:
                    log_file.write(f"--- Bot forcefully killed at {datetime.now().isoformat()} ---\n")
                    log_file.flush()
            except ProcessLookupError:
                logger.info(f"Process for bot {bot_name} already terminated.")
        
        # Close the log file if it's open
        if log_file and not log_file.closed:
            log_file.close()
            
        return True
    return False

def start_bot_process(bot_name: str) -> bool:
    """Start a previously stopped bot."""
    if bot_name in running_bots:
        bot_info = running_bots[bot_name]
        bot_token = bot_info['token']
        bot_dir = bot_info['bot_dir']
        
        bot_code_path = os.path.join(bot_dir, "bot.py")
        if not os.path.exists(bot_code_path):
            logger.error(f"Cannot start {bot_name}: bot.py not found in {bot_dir}")
            return False
            
        with open(bot_code_path, 'r', encoding='utf-8') as f:
            bot_code = f.read()
            
        requirements_content = None
        requirements_path = os.path.join(bot_dir, "requirements.txt")
        if os.path.exists(requirements_path):
            with open(requirements_path, 'r', encoding='utf-8') as f:
                requirements_content = f.read()
        
        logger.info(f"Starting bot: {bot_name}")
        
        new_bot_info = start_bot_subprocess(bot_name, bot_token, bot_code, requirements_content)
        if new_bot_info:
            # Preserve some info from the old bot_info
            new_bot_info['restart_count'] = bot_info.get('restart_count', 0)
            running_bots[bot_name] = new_bot_info
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
            # Increment restart count
            new_bot_info['restart_count'] = bot_info.get('restart_count', 0) + 1
            new_bot_info['last_restart'] = datetime.now()
            running_bots[bot_name] = new_bot_info
            return True
    return False

def update_bot_logs(bot_name: str):
    if bot_name in running_bots:
        bot_info = running_bots[bot_name]
        process = bot_info['process']
        log_file = bot_info.get('log_file')
        
        if process.stdout:
            try:
                output = process.stdout.read()
                if output:
                    running_bots[bot_name]['logs'] += output
                    # Also write to the log file
                    if log_file and not log_file.closed:
                        log_file.write(output)
                        log_file.flush()
            except (TypeError, IOError):
                pass

def get_bot_logs(bot_name: str, max_lines: int = 100) -> str:
    """Get logs for a bot, either from memory or from log files."""
    if bot_name in running_bots:
        # First update the in-memory logs
        update_bot_logs(bot_name)
        logs = running_bots[bot_name]['logs']
        
        # If we don't have enough logs in memory, read from the log file
        if not logs or len(logs.splitlines()) < max_lines:
            log_file_path = running_bots[bot_name].get('log_file_path')
            if log_file_path and os.path.exists(log_file_path):
                try:
                    with open(log_file_path, 'r', encoding='utf-8', errors='replace') as f:
                        file_logs = f.read()
                        if file_logs:
                            logs = file_logs
                except Exception as e:
                    logger.error(f"Error reading log file for {bot_name}: {e}")
        
        # Limit to the last max_lines
        log_lines = logs.splitlines()
        if len(log_lines) > max_lines:
            return "\n".join(log_lines[-max_lines:])
        return logs
    
    # If the bot is not in running_bots, try to find its log files
    log_dir = os.path.join(LOGS_DIR, bot_name)
    if os.path.exists(log_dir):
        log_files = sorted([f for f in os.listdir(log_dir) if f.endswith('.log')], reverse=True)
        if log_files:
            latest_log = os.path.join(log_dir, log_files[0])
            try:
                with open(latest_log, 'r', encoding='utf-8', errors='replace') as f:
                    logs = f.read()
                    log_lines = logs.splitlines()
                    if len(log_lines) > max_lines:
                        return "\n".join(log_lines[-max_lines:])
                    return logs
            except Exception as e:
                logger.error(f"Error reading log file for {bot_name}: {e}")
    
    return "No logs available."

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

def get_system_health():
    """Get system health information."""
    cpu_percent = psutil.cpu_percent(interval=1)
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    
    return {
        'cpu_percent': cpu_percent,
        'memory_percent': memory.percent,
        'memory_used': format_bytes(memory.used),
        'memory_total': format_bytes(memory.total),
        'disk_percent': disk.percent,
        'disk_used': format_bytes(disk.used),
        'disk_total': format_bytes(disk.total),
        'boot_time': datetime.fromtimestamp(psutil.boot_time()).strftime("%Y-%m-%d %H:%M:%S")
    }

def get_bot_resource_usage(bot_name: str):
    """Get resource usage for a specific bot."""
    if bot_name in running_bots:
        bot_info = running_bots[bot_name]
        process = bot_info['process']
        
        if process.poll() is None:  # Process is still running
            try:
                proc = psutil.Process(process.pid)
                cpu_percent = proc.cpu_percent(interval=0.5)
                memory_info = proc.memory_info()
                
                # Update the stored values
                running_bots[bot_name]['cpu_usage'] = cpu_percent
                running_bots[bot_name]['memory_usage'] = memory_info.rss
                
                return {
                    'cpu_percent': cpu_percent,
                    'memory_used': format_bytes(memory_info.rss),
                    'memory_percent': proc.memory_percent(),
                    'threads': proc.num_threads(),
                    'status': proc.status(),
                    'running_time': str(datetime.now() - bot_info['start_time']).split('.')[0]
                }
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                return {
                    'cpu_percent': 0,
                    'memory_used': '0B',
                    'memory_percent': 0,
                    'threads': 0,
                    'status': 'Unknown',
                    'running_time': 'N/A'
                }
    
    return {
        'cpu_percent': 0,
        'memory_used': '0B',
        'memory_percent': 0,
        'threads': 0,
        'status': 'Not running',
        'running_time': 'N/A'
    }

def clean_old_logs(days=None):
    """Clean log files older than specified days."""
    if days is None:
        days = LOG_RETENTION_DAYS
        
    cutoff_date = datetime.now() - timedelta(days=days)
    cleaned_count = 0
    
    for bot_name in os.listdir(LOGS_DIR):
        bot_log_dir = os.path.join(LOGS_DIR, bot_name)
        if os.path.isdir(bot_log_dir):
            for log_file in os.listdir(bot_log_dir):
                if log_file.endswith('.log'):
                    log_path = os.path.join(bot_log_dir, log_file)
                    file_time = datetime.fromtimestamp(os.path.getmtime(log_path))
                    if file_time < cutoff_date:
                        os.remove(log_path)
                        cleaned_count += 1
    
    return cleaned_count

def create_bot_template_files():
    """Create template files if they don't exist."""
    os.makedirs(TEMPLATES_DIR, exist_ok=True)
    
    # Echo Bot Template
    echo_bot_path = os.path.join(TEMPLATES_DIR, "echo_bot.py")
    if not os.path.exists(echo_bot_path):
        with open(echo_bot_path, 'w') as f:
            f.write("""
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Set your Telegram Bot Token here
TOKEN = ""

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    \"\"\"Send a message when the command /start is issued.\"\"\"
    await update.message.reply_text('Hi! I am an Echo Bot. I will echo back any message you send me.')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    \"\"\"Send a message when the command /help is issued.\"\"\"
    await update.message.reply_text('Send me any message and I will echo it back to you!')

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    \"\"\"Echo the user message.\"\"\"
    await update.message.reply_text(update.message.text)

def main() -> None:
    \"\"\"Start the bot.\"\"\"
    # Create the Application
    application = Application.builder().token(TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))
    
    # Run the bot until the user presses Ctrl-C
    application.run_polling()

if __name__ == '__main__':
    main()
""")
    
    # Poll Bot Template
    poll_bot_path = os.path.join(TEMPLATES_DIR, "poll_bot.py")
    if not os.path.exists(poll_bot_path):
        with open(poll_bot_path, 'w') as f:
            f.write("""
import logging
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, Poll
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, PollAnswerHandler

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Set your Telegram Bot Token here
TOKEN = ""

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    \"\"\"Send a message when the command /start is issued.\"\"\"
    await update.message.reply_text(
        'Welcome to the Poll Bot! Use /poll to create a new poll.'
    )

async def poll_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    \"\"\"Sends a predefined poll\"\"\"
    questions = ["Good", "Really good", "Fantastic", "Great"]
    message = await context.bot.send_poll(
        update.effective_chat.id,
        "How are you?",
        questions,
        is_anonymous=False,
        allows_multiple_answers=False,
    )
    
    # Save some info about the poll the bot_data for later use in receive_poll_answer
    payload = {
        message.poll.id: {
            "questions": questions,
            "message_id": message.message_id,
            "chat_id": update.effective_chat.id,
            "answers": 0,
        }
    }
    context.bot_data.update(payload)

async def receive_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    \"\"\"Summarize a users poll vote\"\"\"
    answer = update.poll_answer
    poll_id = answer.poll_id
    
    try:
        questions = context.bot_data[poll_id]["questions"]
    except KeyError:
        return
    
    selected_options = answer.option_ids
    answer_string = ""
    for question_id in selected_options:
        if question_id != selected_options[-1]:
            answer_string += questions[question_id] + " and "
        else:
            answer_string += questions[question_id]
    
    await context.bot.send_message(
        context.bot_data[poll_id]["chat_id"],
        f"{update.effective_user.mention_html()} feels {answer_string}!",
        parse_mode='HTML'
    )
    
    context.bot_data[poll_id]["answers"] += 1
    
    # Close poll after three participants voted
    if context.bot_data[poll_id]["answers"] == 3:
        await context.bot.stop_poll(
            context.bot_data[poll_id]["chat_id"], context.bot_data[poll_id]["message_id"]
        )

def main() -> None:
    \"\"\"Start the bot.\"\"\"
    # Create the Application
    application = Application.builder().token(TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("poll", poll_command))
    application.add_handler(PollAnswerHandler(receive_poll_answer))
    
    # Run the bot until the user presses Ctrl-C
    application.run_polling()

if __name__ == '__main__':
    main()
""")
    
    # Inline Bot Template
    inline_bot_path = os.path.join(TEMPLATES_DIR, "inline_bot.py")
    if not os.path.exists(inline_bot_path):
        with open(inline_bot_path, 'w') as f:
            f.write("""
import logging
from telegram import Update, InlineQueryResultArticle, InputTextMessageContent
from telegram.ext import Application, CommandHandler, InlineQueryHandler, ContextTypes

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Set your Telegram Bot Token here
TOKEN = ""

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    \"\"\"Send a message when the command /start is issued.\"\"\"
    await update.message.reply_text(
        'Hi! I am an Inline Bot. Type @YourBotUsername in any chat followed by a query to use me.'
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    \"\"\"Send a message when the command /help is issued.\"\"\"
    await update.message.reply_text('Type @YourBotUsername followed by your query in any chat.')

async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    \"\"\"Handle the inline query.\"\"\"
    query = update.inline_query.query
    
    if not query:
        return
    
    results = [
        InlineQueryResultArticle(
            id="1",
            title="Uppercase",
            input_message_content=InputTextMessageContent(query.upper()),
            description=f"Convert '{query}' to uppercase"
        ),
        InlineQueryResultArticle(
            id="2",
            title="Lowercase",
            input_message_content=InputTextMessageContent(query.lower()),
            description=f"Convert '{query}' to lowercase"
        ),
        InlineQueryResultArticle(
            id="3",
            title="Reverse",
            input_message_content=InputTextMessageContent(query[::-1]),
            description=f"Reverse '{query}'"
        ),
    ]
    
    await update.inline_query.answer(results)

def main() -> None:
    \"\"\"Start the bot.\"\"\"
    # Create the Application
    application = Application.builder().token(TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(InlineQueryHandler(inline_query))
    
    # Run the bot until the user presses Ctrl-C
    application.run_polling()

if __name__ == '__main__':
    main()
""")
    
    # Admin Bot Template
    admin_bot_path = os.path.join(TEMPLATES_DIR, "admin_bot.py")
    if not os.path.exists(admin_bot_path):
        with open(admin_bot_path, 'w') as f:
            f.write("""
import logging
from telegram import Update, ChatPermissions
from telegram.ext import Application, CommandHandler, ContextTypes, filters
from telegram.constants import ParseMode

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Set your Telegram Bot Token here
TOKEN = ""

# List of user IDs who can use admin commands
ADMIN_USER_IDS = [123456789]  # Replace with your Telegram user ID

def is_admin(user_id):
    \"\"\"Check if the user is an admin.\"\"\"
    return user_id in ADMIN_USER_IDS

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    \"\"\"Send a message when the command /start is issued.\"\"\"
    await update.message.reply_text(
        'Welcome to the Admin Bot! Use /help to see available commands.'
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    \"\"\"Send a message when the command /help is issued.\"\"\"
    user_id = update.effective_user.id
    
    basic_commands = \"\"\"
*Basic Commands:*
/start - Start the bot
/help - Show this help message
\"\"\"
    
    admin_commands = \"\"\"
*Admin Commands:*
/ban <user_id> - Ban a user from the group
/unban <user_id> - Unban a user
/mute <user_id> - Mute a user
/unmute <user_id> - Unmute a user
/kick <user_id> - Kick a user from the group
/pin - Pin the replied message
/unpin - Unpin the most recent pinned message
\"\"\"
    
    if is_admin(user_id):
        await update.message.reply_text(basic_commands + admin_commands, parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(basic_commands, parse_mode=ParseMode.MARKDOWN)

async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    \"\"\"Ban a user from the group.\"\"\"
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("You don't have permission to use this command.")
        return
    
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Please provide a valid user ID to ban.")
        return
    
    target_user_id = int(context.args[0])
    chat_id = update.effective_chat.id
    
    try:
        await context.bot.ban_chat_member(chat_id, target_user_id)
        await update.message.reply_text(f"User {target_user_id} has been banned.")
    except Exception as e:
        await update.message.reply_text(f"Failed to ban user: {e}")

async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    \"\"\"Unban a user from the group.\"\"\"
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("You don't have permission to use this command.")
        return
    
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Please provide a valid user ID to unban.")
        return
    
    target_user_id = int(context.args[0])
    chat_id = update.effective_chat.id
    
    try:
        await context.bot.unban_chat_member(chat_id, target_user_id)
        await update.message.reply_text(f"User {target_user_id} has been unbanned.")
    except Exception as e:
        await update.message.reply_text(f"Failed to unban user: {e}")

async def mute_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    \"\"\"Mute a user in the group.\"\"\"
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("You don't have permission to use this command.")
        return
    
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Please provide a valid user ID to mute.")
        return
    
    target_user_id = int(context.args[0])
    chat_id = update.effective_chat.id
    
    try:
        await context.bot.restrict_chat_member(
            chat_id, 
            target_user_id,
            permissions=ChatPermissions(
                can_send_messages=False,
                can_send_media_messages=False,
                can_send_other_messages=False
            )
        )
        await update.message.reply_text(f"User {target_user_id} has been muted.")
    except Exception as e:
        await update.message.reply_text(f"Failed to mute user: {e}")

async def unmute_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    \"\"\"Unmute a user in the group.\"\"\"
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("You don't have permission to use this command.")
        return
    
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Please provide a valid user ID to unmute.")
        return
    
    target_user_id = int(context.args[0])
    chat_id = update.effective_chat.id
    
    try:
        await context.bot.restrict_chat_member(
            chat_id, 
            target_user_id,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_media_messages=True,
                can_send_other_messages=True,
                can_send_polls=True,
                can_add_web_page_previews=True
            )
        )
        await update.message.reply_text(f"User {target_user_id} has been unmuted.")
    except Exception as e:
        await update.message.reply_text(f"Failed to unmute user: {e}")

async def kick_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    \"\"\"Kick a user from the group.\"\"\"
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("You don't have permission to use this command.")
        return
    
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Please provide a valid user ID to kick.")
        return
    
    target_user_id = int(context.args[0])
    chat_id = update.effective_chat.id
    
    try:
        await context.bot.ban_chat_member(chat_id, target_user_id)
        await context.bot.unban_chat_member(chat_id, target_user_id)
        await update.message.reply_text(f"User {target_user_id} has been kicked.")
    except Exception as e:
        await update.message.reply_text(f"Failed to kick user: {e}")

async def pin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    \"\"\"Pin the replied message.\"\"\"
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("You don't have permission to use this command.")
        return
    
    if update.message.reply_to_message:
        chat_id = update.effective_chat.id
        message_id = update.message.reply_to_message.message_id
        
        try:
            await context.bot.pin_chat_message(chat_id, message_id)
            await update.message.reply_text("Message pinned successfully.")
        except Exception as e:
            await update.message.reply_text(f"Failed to pin message: {e}")
    else:
        await update.message.reply_text("Please reply to a message to pin it.")

async def unpin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    \"\"\"Unpin the most recent pinned message.\"\"\"
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("You don't have permission to use this command.")
        return
    
    chat_id = update.effective_chat.id
    
    try:
        await context.bot.unpin_chat_message(chat_id)
        await update.message.reply_text("Most recent pinned message unpinned successfully.")
    except Exception as e:
        await update.message.reply_text(f"Failed to unpin message: {e}")

def main() -> None:
    \"\"\"Start the bot.\"\"\"
    # Create the Application
    application = Application.builder().token(TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("ban", ban_command))
    application.add_handler(CommandHandler("unban", unban_command))
    application.add_handler(CommandHandler("mute", mute_command))
    application.add_handler(CommandHandler("unmute", unmute_command))
    application.add_handler(CommandHandler("kick", kick_command))
    application.add_handler(CommandHandler("pin", pin_command))
    application.add_handler(CommandHandler("unpin", unpin_command))
    
    # Run the bot until the user presses Ctrl-C
    application.run_polling()

if __name__ == '__main__':
    main()
""")
    
    # File Bot Template
    file_bot_path = os.path.join(TEMPLATES_DIR, "file_bot.py")
    if not os.path.exists(file_bot_path):
        with open(file_bot_path, 'w') as f:
            f.write("""
import logging
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from telegram.constants import ParseMode

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Set your Telegram Bot Token here
TOKEN = ""

# Directory to store files
FILES_DIR = "files"
os.makedirs(FILES_DIR, exist_ok=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    \"\"\"Send a message when the command /start is issued.\"\"\"
    await update.message.reply_text(
        'Welcome to the File Storage Bot! Send me any file to store it, or use /list to see your stored files.'
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    \"\"\"Send a message when the command /help is issued.\"\"\"
    help_text = \"\"\"
*File Storage Bot Commands:*
/start - Start the bot
/help - Show this help message
/list - List all stored files
/search <query> - Search for files by name

*How to use:*
1. Send any file to store it
2. Use /list to see all your files
3. Click on a file name to download it
4. Use the delete button to remove a file
\"\"\"
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)

async def store_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    \"\"\"Store a file sent by the user.\"\"\"
    user_id = update.effective_user.id
    user_dir = os.path.join(FILES_DIR, str(user_id))
    os.makedirs(user_dir, exist_ok=True)
    
    file = None
    if update.message.document:
        file = update.message.document
    elif update.message.photo:
        file = update.message.photo[-1]  # Get the largest photo
    elif update.message.video:
        file = update.message.video
    elif update.message.audio:
        file = update.message.audio
    elif update.message.voice:
        file = update.message.voice
    
    if file:
        file_id = file.file_id
        file_name = file.file_name if hasattr(file, 'file_name') else f"{file_id}.file"
        
        # Download the file
        new_file = await context.bot.get_file(file_id)
        file_path = os.path.join(user_dir, file_name)
        await new_file.download_to_drive(file_path)
        
        await update.message.reply_text(
            f"File *{file_name}* has been stored successfully!\\n"
            f"Use /list to see all your files.",
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await update.message.reply_text("Please send a file to store.")

async def list_files(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    \"\"\"List all files stored by the user.\"\"\"
    user_id = update.effective_user.id
    user_dir = os.path.join(FILES_DIR, str(user_id))
    
    if not os.path.exists(user_dir) or not os.listdir(user_dir):
        await update.message.reply_text("You haven't stored any files yet.")
        return
    
    files = os.listdir(user_dir)
    keyboard = []
    
    for file_name in files:
        keyboard.append([
            InlineKeyboardButton(f"📄 {file_name}", callback_data=f"get_file:{file_name}"),
            InlineKeyboardButton("🗑️", callback_data=f"delete_file:{file_name}")
        ])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Your stored files:", reply_markup=reply_markup)

async def search_files(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    \"\"\"Search for files by name.\"\"\"
    if not context.args:
        await update.message.reply_text("Please provide a search query. Example: /search document")
        return
    
    query = ' '.join(context.args).lower()
    user_id = update.effective_user.id
    user_dir = os.path.join(FILES_DIR, str(user_id))
    
    if not os.path.exists(user_dir) or not os.listdir(user_dir):
        await update.message.reply_text("You haven't stored any files yet.")
        return
    
    files = os.listdir(user_dir)
    matching_files = [f for f in files if query in f.lower()]
    
    if not matching_files:
        await update.message.reply_text(f"No files found matching '{query}'.")
        return
    
    keyboard = []
    for file_name in matching_files:
        keyboard.append([
            InlineKeyboardButton(f"📄 {file_name}", callback_data=f"get_file:{file_name}"),
            InlineKeyboardButton("🗑️", callback_data=f"delete_file:{file_name}")
        ])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(f"Files matching '{query}':", reply_markup=reply_markup)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    \"\"\"Handle button callbacks.\"\"\"
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = update.effective_user.id
    user_dir = os.path.join(FILES_DIR, str(user_id))
    
    if data.startswith("get_file:"):
        file_name = data.split(":", 1)[1]
        file_path = os.path.join(user_dir, file_name)
        
        if os.path.exists(file_path):
            await query.message.reply_document(document=open(file_path, 'rb'), filename=file_name)
        else:
            await query.message.reply_text(f"File {file_name} not found.")
    
    elif data.startswith("delete_file:"):
        file_name = data.split(":", 1)[1]
        file_path = os.path.join(user_dir, file_name)
        
        if os.path.exists(file_path):
            os.remove(file_path)
            await query.message.reply_text(f"File {file_name} has been deleted.")
            
            # Update the file list
            await list_files(update, context)
        else:
            await query.message.reply_text(f"File {file_name} not found.")

def main() -> None:
    \"\"\"Start the bot.\"\"\"
    # Create the Application
    application = Application.builder().token(TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("list", list_files))
    application.add_handler(CommandHandler("search", search_files))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(MessageHandler(
        filters.ATTACHMENT | filters.Document.ALL | filters.PHOTO | filters.VIDEO | filters.AUDIO | filters.VOICE,
        store_file
    ))
    
    # Run the bot until the user presses Ctrl-C
    application.run_polling()

if __name__ == '__main__':
    main()
""")

async def monitor_bots():
    """Monitor running bots and restart them if they crash."""
    while True:
        for bot_name in list(running_bots.keys()):
            try:
                bot_info = running_bots[bot_name]
                process = bot_info['process']
                
                # Update logs
                update_bot_logs(bot_name)
                
                # Check if process is still running
                if process.poll() is not None:  # Process has terminated
                    logger.warning(f"Bot {bot_name} has crashed or stopped unexpectedly.")
                    
                    # Check if we should auto-restart
                    if AUTO_RESTART_BOTS:
                        logger.info(f"Attempting to auto-restart {bot_name}...")
                        if restart_bot_process(bot_name):
                            logger.info(f"Successfully auto-restarted {bot_name}.")
                        else:
                            logger.error(f"Failed to auto-restart {bot_name}.")
                
                # Update resource usage
                if process.poll() is None:  # Only if process is running
                    try:
                        proc = psutil.Process(process.pid)
                        bot_info['cpu_usage'] = proc.cpu_percent(interval=0.1)
                        bot_info['memory_usage'] = proc.memory_info().rss
                    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                        pass
            except Exception as e:
                logger.error(f"Error monitoring bot {bot_name}: {e}")
                
        await asyncio.sleep(30)  # Check every 30 seconds

# --- Authorization Decorator ---
from functools import wraps

def authorized_only(func):
    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        if user_id not in AUTHORIZED_USERS:
            logger.warning(f"Unauthorized access attempt by user {user_id}.")
            if update.message:
                await update.message.reply_text("🛡️ You are not authorized to use this bot.")
            elif update.callback_query:
                await update.callback_query.answer("🛡️ You are not authorized.", show_alert=True)
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
    # Using animation instead of photo for GIF
    if update.message:
        await update.message.reply_animation(
            animation=LOADING_ANIMATION_URL, 
            caption=welcome_message, 
            parse_mode=ParseMode.MARKDOWN, 
            reply_markup=get_main_menu_keyboard()
        )

@authorized_only
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # This handler is now triggered by a button, so we handle the callback query
    query = update.callback_query
    if query:
        await query.answer()
    
    help_text = f"""
{EMOJI.QUESTION} *BotHoster Pro Help* {EMOJI.QUESTION}
This bot allows you to host other Telegram bots directly from this chat.

{EMOJI.ROCKET} *Features:*
- `{EMOJI.UPLOAD} Upload New Bot`: Start a conversation to upload a new bot.
- `{EMOJI.TEMPLATE} Use Template`: Create a bot from pre-made templates.
- `{EMOJI.CLIPBOARD} My Bots`: View, manage, and see logs for your bots.
- `{EMOJI.MIRROR} Mirror File`: Upload a file and get a direct public link.
- `{EMOJI.BAR_CHART} Stats & Storage`: View bot counts and server disk usage.
- `{EMOJI.GEAR} Settings`: View the current bot hosting limits.

{EMOJI.PLAY_ALL} *Bot Management:*
- Start/Stop/Restart individual bots
- View logs and resource usage
- Download or edit bot code
- Backup your bots
- Start or stop all bots at once

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
    mirror_dir_size = get_dir_size(MIRROR_DIR)
    logs_dir_size = get_dir_size(LOGS_DIR)
    total, used, free = shutil.disk_usage("/")
    
    stats_text = f"""
{EMOJI.BAR_CHART} *Hosting Statistics*
{EMOJI.ROBOT} Total Bots Managed: *{total_bots}*
{EMOJI.GREEN_CIRCLE} Bots Running: *{running_count}*
{EMOJI.STORAGE} *Server Storage*
{EMOJI.SNAKE} Bots Folder Size: `{format_bytes(bots_dir_size)}`
{EMOJI.MIRROR} Mirror Folder Size: `{format_bytes(mirror_dir_size)}`
{EMOJI.LOGS} Logs Folder Size: `{format_bytes(logs_dir_size)}`
---
Disk Total: `{format_bytes(total)}`
Disk Used: `{format_bytes(used)}`
Disk Free: `{format_bytes(free)}`
"""
    await edit_or_reply_message(update, stats_text, get_stats_keyboard())

@authorized_only
async def system_health_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer("Checking system health...")
    
    health = get_system_health()
    
    health_text = f"""
{EMOJI.HEALTH} *System Health*
{EMOJI.BAR_CHART} *CPU Usage:* `{health['cpu_percent']}%`
{EMOJI.STORAGE} *Memory:* `{health['memory_used']} / {health['memory_total']} ({health['memory_percent']}%)`
{EMOJI.STORAGE} *Disk:* `{health['disk_used']} / {health['disk_total']} ({health['disk_percent']}%)`
{EMOJI.ROCKET} *System Uptime:* `{health['boot_time']}`
{EMOJI.ROBOT} *Running Bots:* `{sum(1 for bot in running_bots.values() if bot['process'].poll() is None)}`
"""
    
    # Add info about top resource-consuming bots
    if running_bots:
        health_text += "\n*Top Resource-Using Bots:*\n"
        
        # Get resource usage for all running bots
        bot_resources = []
        for bot_name, bot_info in running_bots.items():
            if bot_info['process'].poll() is None:  # Only if process is running
                try:
                    proc = psutil.Process(bot_info['process'].pid)
                    cpu = proc.cpu_percent(interval=0.1)
                    memory = proc.memory_info().rss
                    bot_resources.append((bot_name, cpu, memory))
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    pass
        
        # Sort by CPU usage and show top 3
        if bot_resources:
            bot_resources.sort(key=lambda x: x[1], reverse=True)
            for i, (bot_name, cpu, memory) in enumerate(bot_resources[:3]):
                health_text += f"- `{bot_name}`: CPU `{cpu:.1f}%`, Memory `{format_bytes(memory)}`\n"
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{EMOJI.BACK} Back to Stats", callback_data='stats')]
    ])
    
    await edit_or_reply_message(update, health_text, keyboard)

@authorized_only
async def list_bots_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer("Fetching your bots...")
        
    if not running_bots:
        await edit_or_reply_message(update, f"{EMOJI.CLIPBOARD} You haven't uploaded any bots yet.", reply_markup=get_main_menu_keyboard())
        return
    
    keyboard = []
    
    # Add search and filter options
    keyboard.append([
        InlineKeyboardButton(f"{EMOJI.SEARCH} Search", callback_data='search_bots'),
        InlineKeyboardButton(f"{EMOJI.FILTER} Filter", callback_data='filter_bots')
    ])
    
    # Add bot list
    for bot_name, info in running_bots.items():
        status_emoji = EMOJI.GREEN_CIRCLE if info['process'].poll() is None else EMOJI.RED_CIRCLE
        keyboard.append([InlineKeyboardButton(f"{status_emoji} {bot_name}", callback_data=f"select_bot:{bot_name}")])
    
    # Add batch operations
    if running_bots:
        keyboard.append([
            InlineKeyboardButton(f"{EMOJI.PLAY_ALL} Start All", callback_data='start_all_bots'),
            InlineKeyboardButton(f"{EMOJI.STOP_ALL} Stop All", callback_data='stop_all_bots')
        ])
    
    keyboard.append([InlineKeyboardButton(f"{EMOJI.BACK} Main Menu", callback_data='main_menu')])
    await edit_or_reply_message(update, f"{EMOJI.CLIPBOARD} *Your Bots*\n\nSelect a bot to manage:", reply_markup=InlineKeyboardMarkup(keyboard))

@authorized_only
async def start_all_bots_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Starting all bots...")
    
    loading_msg = await send_loading_animation(context, query.message.chat_id, f"{EMOJI.LOADING} Starting all bots...")
    
    started_count = 0
    failed_count = 0
    
    for bot_name in running_bots:
        if running_bots[bot_name]['process'].poll() is not None:  # Bot is not running
            if start_bot_process(bot_name):
                started_count += 1
            else:
                failed_count += 1
    
    result_text = f"{EMOJI.SUCCESS} Started {started_count} bots."
    if failed_count > 0:
        result_text += f"\n{EMOJI.WARNING} Failed to start {failed_count} bots."
    
    await loading_msg.edit_caption(result_text, reply_markup=get_main_menu_keyboard())

@authorized_only
async def stop_all_bots_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Stopping all bots...")
    
    loading_msg = await send_loading_animation(context, query.message.chat_id, f"{EMOJI.LOADING} Stopping all bots...")
    
    stopped_count = 0
    
    for bot_name in list(running_bots.keys()):
        if running_bots[bot_name]['process'].poll() is None:  # Bot is running
            if stop_bot_process(bot_name):
                stopped_count += 1
    
    await loading_msg.edit_caption(f"{EMOJI.SUCCESS} Stopped {stopped_count} bots.", reply_markup=get_main_menu_keyboard())

@authorized_only
async def clean_logs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Cleaning old logs...")
    
    loading_msg = await send_loading_animation(context, query.message.chat_id, f"{EMOJI.LOADING} Cleaning old logs...")
    
    cleaned_count = clean_old_logs()
    
    await loading_msg.edit_caption(
        f"{EMOJI.SUCCESS} Cleaned {cleaned_count} old log files.\n\n"
        f"Log retention policy: {LOG_RETENTION_DAYS} days",
        reply_markup=get_stats_keyboard()
    )

# --- Conversation Handlers States ---
(ASK_BOT_NAME, GET_BOT_FILE, GET_TOKEN, GET_REQUIREMENTS, ASK_MIRROR_FILE, EDIT_CODE) = range(6)

# --- Upload Bot Conversation ---
@authorized_only
async def upload_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the bot upload conversation."""
    query = update.callback_query
    await query.answer()
    
    user_bots_count = len(running_bots)
    if user_bots_count >= MAX_BOTS_PER_USER:
        await query.message.reply_text(f"{EMOJI.WARNING} You have reached the maximum of *{MAX_BOTS_PER_USER}* bots.", reply_markup=get_back_to_main_menu_keyboard())
        return ConversationHandler.END
    
    # Delete old message and send a new one to prevent edit error
    await query.message.delete()
    await query.message.chat.send_message(
        f"{EMOJI.ROBOT} Let's upload a new bot!\n\nFirst, what do you want to name it? (e.g., `MyAwesomeBot`).",
        parse_mode=ParseMode.MARKDOWN,
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
        await query.edit_message_text(f"{EMOJI.PACKAGE} Please send me your `requirements.txt` file.", parse_mode=ParseMode.MARKDOWN, reply_markup=get_cancel_keyboard())
        return GET_REQUIREMENTS
    else: # no_requirements
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
    
    # This might be triggered by a message or a callback, handle both
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
        # To be safe, delete and send new, as we don't know the original message type
        await query.message.delete()
        await query.message.chat.send_message(message_text)
        await start_command(update, context) # Resend the start message
    else:
        await update.message.reply_text(message_text)
        await start_command(update, context)
    context.user_data.clear()
    return ConversationHandler.END

# --- Bot Template Handlers ---
@authorized_only
async def template_list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    # Create template files if they don't exist
    create_bot_template_files()
    
    template_text = f"{EMOJI.TEMPLATE} *Bot Templates*\n\nChoose a template to create a new bot quickly:"
    await edit_or_reply_message(update, template_text, get_template_list_keyboard())

@authorized_only
async def select_template_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    template_id = query.data.split(':', 1)[1]
    if template_id not in BOT_TEMPLATES:
        await edit_or_reply_message(
            update,
            f"{EMOJI.CANCEL} Template not found.",
            get_template_list_keyboard()
        )
        return

    template_info = BOT_TEMPLATES[template_id]
    template_path = os.path.join(os.getcwd(), template_info['file'])

    if not os.path.exists(template_path):
        await edit_or_reply_message(
            update,
            f"{EMOJI.CANCEL} Template file not found. Please try again later.",
            get_template_list_keyboard()
        )
        return

    with open(template_path, 'r') as f:
        template_code = f.read()

    template_preview = template_code[:500] + "..." if len(template_code) > 500 else template_code

    template_text = f"""
{EMOJI.TEMPLATE} {template_info['name']}
{template_info['description']}

Code Preview:

{template_preview}
"""
    await edit_or_reply_message(update, template_text, get_template_action_keyboard(template_id))


@authorized_only
async def use_template_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    template_id = query.data.split(':', 1)[1]
    if template_id not in BOT_TEMPLATES:
        await edit_or_reply_message(
            update,
            f"{EMOJI.CANCEL} Template not found.",
            get_template_list_keyboard()
        )
        return

    template_info = BOT_TEMPLATES[template_id]
    template_path = os.path.join(os.getcwd(), template_info['file'])

    if not os.path.exists(template_path):
        await edit_or_reply_message(
            update,
            f"{EMOJI.CANCEL} Template file not found. Please try again later.",
            get_template_list_keyboard()
        )
        return

    with open(template_path, 'r') as f:
        template_code = f.read()

    # Use template_code however your bot logic requires
    await edit_or_reply_message(
        update,
        f"{EMOJI.CHECK} Template '{template_info['name']}' applied successfully!"
    )
# Store the template code in user_data
context.user_data['bot_code'] = template_code

# Continue with the bot creation flow
user_bots_count = len(running_bots)
if user_bots_count >= MAX_BOTS_PER_USER:
await edit_or_reply_message(update, f"{EMOJI.WARNING} You have reached the maximum of *{MAX_BOTS_PER_USER}* bots.", get_back_to_main_menu_keyboard())
return

# Delete old message and send a new one
await query.message.delete()
await query.message.chat.send_message(
f"{EMOJI.ROBOT} Let's create a new bot using the *{template_info['name']}* template!\n\n"
f"First, what do you want to name it? (e.g., `My{template_info['name'].replace(' ', '')}`).",
parse_mode=ParseMode.MARKDOWN,
reply_markup=get_cancel_keyboard()
)
return ASK_BOT_NAME
--- Mirror File Conversation & Management ---
@authorized_only
async def mirror_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
query = update.callback_query
await query.answer()

if not RENDER_EXTERNAL_URL:
await edit_or_reply_message(update, f"{EMOJI.WARNING} Mirror service is not configured.", get_back_to_main_menu_keyboard())
return ConversationHandler.END

await query.message.delete()
await query.message.chat.send_message(
f"{EMOJI.MIRROR} *File Mirror*\n\nSend me any file (up to {MAX_MIRROR_FILE_SIZE/1024/1024:.0f}MB).",
parse_mode=ParseMode.MARKDOWN,
reply_markup=get_cancel_keyboard()
)
return ASK_MIRROR_FILE
async def receive_mirror_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
message = update.message
file_source = message.document or message.video or message.audio or (message.photo[-1] if message.photo else None)

if not file_source:
await message.reply_text(f"{EMOJI.CANCEL} Please send a file or media to mirror.", reply_markup=get_cancel_keyboard())
return ASK_MIRROR_FILE

if file_source.file_size > MAX_MIRROR_FILE_SIZE:
await message.reply_text(f"{EMOJI.CANCEL} File is too large. Maximum size is {MAX_MIRROR_FILE_SIZE/1024/1024:.0f}MB.", reply_markup=get_cancel_keyboard())
return ASK_MIRROR_FILE

loading_msg = await send_loading_animation(context, message.chat_id, f"{EMOJI.LOADING} Downloading your file...")

try:
file_name = getattr(file_source, 'file_name', f"{file_source.file_unique_id}.dat")
sanitized_filename = f"{file_source.file_unique_id}_{os.path.basename(file_name)}"
file_path = os.path.join(MIRROR_DIR, sanitized_filename)

if not await download_file(context.bot, file_source.file_id, file_path):
await loading_msg.edit_caption(f"{EMOJI.CANCEL} Failed to download the file. Please try again.", reply_markup=get_cancel_keyboard())
return ASK_MIRROR_FILE

file_url = f"{RENDER_EXTERNAL_URL}/mirror/{sanitized_filename}"

await loading_msg.edit_caption(
f"{EMOJI.SUCCESS} *File Mirrored Successfully!*\n\n"
f"Here is your direct link:\n`{file_url}`",
parse_mode=ParseMode.MARKDOWN,
reply_markup=get_back_to_main_menu_keyboard()
)
except Exception as e:
logger.error(f"Error mirroring file: {e}", exc_info=True)
await loading_msg.edit_caption(f"{EMOJI.CANCEL} An error occurred while mirroring the file. Please try again.")

return ConversationHandler.END
@authorized_only
async def manage_mirror_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
query = update.callback_query
await query.answer()

mirror_size = get_dir_size(MIRROR_DIR)
text = f"""
{EMOJI.MIRROR} Mirror Management
You are currently using {format_bytes(mirror_size)} of storage for mirrored files.
Maximum file size: {format_bytes(MAX_MIRROR_FILE_SIZE)}
Remember that this storage is temporary and will be wiped on server restarts or redeploys.
"""
await edit_or_reply_message(update, text, get_mirror_management_keyboard(mirror_size))

@authorized_only
async def browse_mirror_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
query = update.callback_query
await query.answer()

if not os.path.exists(MIRROR_DIR) or not os.listdir(MIRROR_DIR):
await edit_or_reply_message(update, f"{EMOJI.MIRROR} No mirrored files found.", get_mirror_management_keyboard(0))
return

files = os.listdir(MIRROR_DIR)
files.sort(key=lambda x: os.path.getmtime(os.path.join(MIRROR_DIR, x)), reverse=True)

text = f"{EMOJI.MIRROR} *Mirrored Files*\n\n"

for i, file_name in enumerate(files[:10], 1):
file_path = os.path.join(MIRROR_DIR, file_name)
file_size = os.path.getsize(file_path)
file_date = datetime.fromtimestamp(os.path.getmtime(file_path)).strftime("%Y-%m-%d %H:%M")
file_url = f"{RENDER_EXTERNAL_URL}/mirror/{file_name}"

text += f"{i}. [{file_name}]({file_url}) - `{format_bytes(file_size)}` - {file_date}\n"

if len(files) > 10:
text += f"\n_...and {len(files) - 10} more files._"

keyboard = InlineKeyboardMarkup([
[InlineKeyboardButton(f"{EMOJI.BACK} Back to Mirror Management", callback_data='manage_mirror')]
])

await edit_or_reply_message(update, text, keyboard)
@authorized_only
async def delete_all_mirror_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
query = update.callback_query
await query.answer()

text = f"{EMOJI.WARNING} Are you sure you want to delete all mirrored files? This action cannot be undone."
await edit_or_reply_message(update, text, get_delete_all_mirror_confirmation_keyboard())
@authorized_only
async def delete_all_mirror_final_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
query = update.callback_query
await query.answer("Deleting files...")

try:
shutil.rmtree(MIRROR_DIR)
os.makedirs(MIRROR_DIR)
text = f"{EMOJI.SUCCESS} All mirrored files have been deleted."
except Exception as e:
logger.error(f"Error deleting mirror directory: {e}")
text = f"{EMOJI.CANCEL} An error occurred while deleting files."

await edit_or_reply_message(update, text, get_stats_keyboard())
--- Bot Edit Code Handlers ---
@authorized_only
async def edit_bot_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
query = update.callback_query
await query.answer()

_, action, bot_name = query.data.split(':', 2)

if bot_name not in running_bots:
await edit_or_reply_message(update, f"{EMOJI.CANCEL} Bot not found.", get_back_to_main_menu_keyboard())
return ConversationHandler.END

bot_dir = running_bots[bot_name]['bot_dir']
bot_file_path = os.path.join(bot_dir, "bot.py")

if not os.path.exists(bot_file_path):
await edit_or_reply_message(update, f"{EMOJI.CANCEL} Bot file not found.", get_back_to_main_menu_keyboard())
return ConversationHandler.END

with open(bot_file_path, 'r', encoding='utf-8') as f:
bot_code = f.read()

# Store the bot name and code in user_data
context.user_data['edit_bot_name'] = bot_name
context.user_data['edit_bot_code'] = bot_code

# Send the code as a document for editing
with tempfile.NamedTemporaryFile(suffix='.py', delete=False) as temp_file:
temp_file_path = temp_file.name
with open(temp_file_path, 'w', encoding='utf-8') as f:
f.write(bot_code)

await query.message.reply_document(
document=open(temp_file_path, 'rb'),
filename=f"{bot_name}.py",
caption=f"{EMOJI.CODE} Here's the code for `{bot_name}`.\n\nEdit it and send it back to update the bot.",
parse_mode=ParseMode.MARKDOWN,
reply_markup=get_edit_code_keyboard(bot_name)
)

os.unlink(temp_file_path)

return EDIT_CODE
async def receive_edited_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
document = update.message.document
if not document or not any(document.file_name.lower().endswith(ft) for ft in ALLOWED_FILE_TYPES):
await update.message.reply_text(f"{EMOJI.CANCEL} Invalid file type. Please send a Python file.", reply_markup=get_cancel_keyboard())
return EDIT_CODE

loading_msg = await send_loading_animation(context, update.effective_chat.id, f"{EMOJI.LOADING} Downloading your edited code...")

with tempfile.TemporaryDirectory() as temp_dir:
temp_file_path = os.path.join(temp_dir, document.file_name)
if not await download_file(context.bot, document.file_id, temp_file_path):
await loading_msg.edit_caption(f"{EMOJI.CANCEL} Failed to download the file. Please try again.", reply_markup=get_cancel_keyboard())
return EDIT_CODE

with open(temp_file_path, 'r', encoding='utf-8') as f:
edited_code = f.read()

bot_name = context.user_data['edit_bot_name']

# Update the bot code
bot_dir = running_bots[bot_name]['bot_dir']
bot_file_path = os.path.join(bot_dir, "bot.py")

with open(bot_file_path, 'w', encoding='utf-8') as f:
f.write(edited_code)

await loading_msg.edit_caption(
f"{EMOJI.SUCCESS} Code for `{bot_name}` has been updated!\n\n"
f"Would you like to restart the bot to apply changes?",
parse_mode=ParseMode.MARKDOWN,
reply_markup=InlineKeyboardMarkup([
[InlineKeyboardButton(f"{EMOJI.RESTART} Yes, restart now", callback_data=f'bot_action:restart:{bot_name}')],
[InlineKeyboardButton(f"{EMOJI.CANCEL} No, I'll do it later", callback_data=f'select_bot:{bot_name}')]
])
)

context.user_data.clear()
return ConversationHandler.END
async def save_edited_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
query = update.callback_query
await query.answer()

_, bot_name = query.data.split(':', 1)

await query.edit_message_text(
f"{EMOJI.CODE} Please send me the edited Python file for `{bot_name}`.",
parse_mode=ParseMode.MARKDOWN,
reply_markup=get_cancel_keyboard()
)

return EDIT_CODE
--- Other Callback Query Handlers ---
async def main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
query = update.callback_query
await query.answer()

welcome_message = f"""
{EMOJI.SPARKLES} Welcome to BotHoster Pro! {EMOJI.SPARKLES}
I can host and manage your Python Telegram bots.
{EMOJI.GEAR} Use the menu below to get started.
"""
await query.message.delete()
await query.message.chat.send_animation(
animation=LOADING_ANIMATION_URL,
caption=welcome_message,
parse_mode=ParseMode.MARKDOWN,
reply_markup=get_main_menu_keyboard()
)

@authorized_only
async def select_bot_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
query = update.callback_query
await query.answer()

bot_name = query.data.split(':')[1]

if bot_name not in running_bots:
await edit_or_reply_message(update, f"{EMOJI.CANCEL} Bot not found. It might have been removed.", get_back_to_main_menu_keyboard())
return

info = running_bots[bot_name]
is_running = info['process'].poll() is None
status_emoji = EMOJI.GREEN_CIRCLE if is_running else EMOJI.RED_CIRCLE
status_text = "Running" if is_running else "Stopped"

uptime = "N/A"
if is_running:
td = datetime.now() - info['start_time']
uptime = str(td).split('.')[0]

restart_count = info.get('restart_count', 0)
last_restart = info.get('last_restart')
last_restart_text = last_restart.strftime("%Y-%m-%d %H:%M:%S") if last_restart else "N/A"

text = f"""
{EMOJI.GEAR} Managing Bot: {bot_name}
Status: {status_emoji} {status_text}
Uptime: {uptime}
Restarts: {restart_count}
Last Restart: {last_restart_text}

What would you like to do?
"""
await edit_or_reply_message(update, text, get_bot_actions_keyboard(bot_name))

@authorized_only
async def bot_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
query = update.callback_query
await query.answer()


Line Wrapping

Collapse
Copy
1
2
3
4
5
6
7
8
9
10
11
12
13
14
15
16
17
18
19
20
21
22
23
24
25
26
27
28
29
30
31
32
33
34
35
36
37
38
39
40
41
_, action, bot_name = query.data.split(':', 2)

if action == 'delete_confirm':
    await edit_or_reply_message(update, f"{EMOJI.WARNING} Are you sure you want to permanently delete `{bot_name}`?", reply_markup=get_delete_confirmation_keyboard(bot_name))
    return

if action == 'edit':
    # This is handled by the conversation handler
    return await edit_bot_code(update, context)

loading_msg = await send_loading_animation(context, query.message.chat_id, f"{EMOJI.LOADING} Processing request for `{bot_name}`...")

if bot_name not in running_bots and action != 'backup':
    await loading_msg.edit_caption(f"{EMOJI.CANCEL} Bot not found.", reply_markup=get_back_to_main_menu_keyboard())
    return
    
if action == 'stop':
    stop_bot_process(bot_name)
    await loading_msg.edit_caption(f"{EMOJI.STOP} Bot `{bot_name}` has been stopped.", reply_markup=get_bot_actions_keyboard(bot_name))

elif action == 'start':
    if start_bot_process(bot_name):
        await loading_msg.edit_caption(f"{EMOJI.SUCCESS} Bot `{bot_name}` successfully started!", reply_markup=get_bot_actions_keyboard(bot_name))
    else:
        await loading_msg.edit_caption(f"{EMOJI.CANCEL} Failed to start `{bot_name}`.", reply_markup=get_bot_actions_keyboard(bot_name))

elif action == 'restart':
    if restart_bot_process(bot_name):
        await loading_msg.edit_caption(f"{EMOJI.SUCCESS} Bot `{bot_name}` successfully restarted!", reply_markup=get_bot_actions_keyboard(bot_name))
    else:
        await loading_msg.edit_caption(f"{EMOJI.CANCEL} Failed to restart `{bot_name}`.", reply_markup=get_bot_actions_keyboard(bot_name))

elif action == 'logs':
    logs = get_bot_logs(bot_name)
    log_output = f"... {logs[-3500:]}" if len(logs) > 3500 else logs
    await loading_msg.delete()
    await query.message.reply_text(f"{EMOJI.LOGS} *Logs for `{bot_name}`:*\n\n```\n{log_output}\n```", parse_mode=ParseMode.MARKDOWN, reply_markup=get_bot_actions_keyboard(bot_name))

elif action == 'resources':
    resources = get_bot_resource_usage(bot_name)
    resource_text = f"""
{EMOJI.HEALTH} Resource Usage for {bot_name}
{EMOJI.BAR_CHART} CPU Usage: {resources['cpu_percent']:.1f}%
{EMOJI.STORAGE} Memory Usage: {resources['memory_used']} ({resources['memory_percent']:.1f}%)
{EMOJI.ROCKET} Threads: {resources['threads']}
{EMOJI.INFO} Status: {resources['status']}
{EMOJI.ROCKET} Running Time: {resources['running_time']}
"""
await loading_msg.edit_caption(resource_text, parse_mode=ParseMode.MARKDOWN, reply_markup=get_bot_actions_keyboard(bot_name))

elif action == 'download':
bot_dir = running_bots[bot_name]['bot_dir']
zip_buffer = io.BytesIO()
with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_f:
for item_name in ["bot.py", "requirements.txt"]:
item_path = os.path.join(bot_dir, item_name)
if os.path.exists(item_path):
zip_f.write(item_path, item_name)
zip_buffer.seek(0)

await loading_msg.delete()
await query.message.reply_document(document=zip_buffer, filename=f"{bot_name}_source.zip", caption=f"{EMOJI.DOWNLOAD} Here's the source code for `{bot_name}`.")

elif action == 'backup':
try:
# Create a backup of the bot
bot_dir = running_bots[bot_name]['bot_dir']
backup_buffer = io.BytesIO()

with zipfile.ZipFile(backup_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_f:
# Add bot files
for root, _, files in os.walk(bot_dir):
for file in files:
file_path = os.path.join(root, file)
arc_name = os.path.relpath(file_path, bot_dir)
zip_f.write(file_path, arc_name)

# Add metadata
metadata = {
"bot_name": bot_name,
"token": running_bots[bot_name]['token'],
"backup_date": datetime.now().isoformat(),
"restart_count": running_bots[bot_name].get('restart_count', 0)
}

zip_f.writestr("metadata.json", json.dumps(metadata, indent=2))

backup_buffer.seek(0)
await loading_msg.delete()
await query.message.reply_document(
document=backup_buffer,
filename=f"{bot_name}_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip",
caption=f"{EMOJI.BACKUP} Backup of `{bot_name}` created successfully!",
parse_mode=ParseMode.MARKDOWN
)
except Exception as e:
logger.error(f"Error creating backup for {bot_name}: {e}")
await loading_msg.edit_caption(f"{EMOJI.CANCEL} Failed to create backup: {str(e)}", reply_markup=get_bot_actions_keyboard(bot_name))

elif action == 'delete_final':
bot_dir = running_bots[bot_name].get('bot_dir')
stop_bot_process(bot_name)
del running_bots[bot_name]

if bot_dir and os.path.exists(bot_dir):
shutil.rmtree(bot_dir, ignore_errors=True)

# Also clean up log files
log_dir = os.path.join(LOGS_DIR, bot_name)
if os.path.exists(log_dir):
shutil.rmtree(log_dir, ignore_errors=True)

await loading_msg.edit_caption(f"{EMOJI.SUCCESS} Bot `{bot_name}` has been deleted.", reply_markup=get_back_to_main_menu_keyboard())
@authorized_only
async def delete_all_bots_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
query = update.callback_query
await query.answer()

if not running_bots:
await edit_or_reply_message(update, f"{EMOJI.CLIPBOARD} There are no bots to delete.", get_main_menu_keyboard())
return

await edit_or_reply_message(update, f"{EMOJI.WARNING} *DANGER ZONE*\n\nAre you sure you want to delete all *{len(running_bots)}* bots?", get_delete_all_confirmation_keyboard())
@authorized_only
async def delete_all_bots_final(update: Update, context: ContextTypes.DEFAULT_TYPE):
query = update.callback_query
await query.answer()

loading_msg = await send_loading_animation(context, query.message.chat_id, f"{EMOJI.LOADING} Deleting all bots...")

for bot_name in list(running_bots.keys()):
bot_dir = running_bots[bot_name].get('bot_dir')
stop_bot_process(bot_name)
if bot_dir and os.path.exists(bot_dir):
shutil.rmtree(bot_dir, ignore_errors=True)

# Also clean up log files
log_dir = os.path.join(LOGS_DIR, bot_name)
if os.path.exists(log_dir):
shutil.rmtree(log_dir, ignore_errors=True)

running_bots.clear()

await loading_msg.edit_caption(f"{EMOJI.SUCCESS} All hosted bots have been removed.", reply_markup=get_main_menu_keyboard())
@authorized_only
async def settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
query = update.callback_query
await query.answer()

settings_text = f"""
{EMOJI.GEAR} BotHoster Pro Settings
These settings are configured in the users.json file.

{EMOJI.ROBOT} Authorization

Authorized User IDs: {', '.join(map(str, AUTHORIZED_USERS))}
{EMOJI.WRENCH} Limits & Rules

Max Bots Per User: {MAX_BOTS_PER_USER}
Max Bot Script Size: {MAX_BOT_FILE_SIZE/1024/1024:.1f} MB
Max Mirror File Size: {MAX_MIRROR_FILE_SIZE/1024/1024:.0f} MB
Auto Restart Bots: {'Enabled' if AUTO_RESTART_BOTS else 'Disabled'}
Log Retention: {LOG_RETENTION_DAYS} days
{EMOJI.TEMPLATE} Templates

Available Templates: {len(BOT_TEMPLATES)}
"""
await edit_or_reply_message(update, settings_text, get_main_menu_keyboard())
async def autoreact(update: Update, context: ContextTypes.DEFAULT_TYPE):
if update.message:
try:
await update.message.set_reaction(reaction="👍")
except Exception as e:
logger.info(f"Could not set reaction: {e}")

--- Main Application Setup ---
def main():
"""Initializes and runs the bot application."""
application = Application.builder().token(TOKEN).build()

# Create template files
create_bot_template_files()

# Start the bot monitor task
global bot_monitor_task
bot_monitor_task = asyncio.create_task(monitor_bots())

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

edit_code_conv_handler = ConversationHandler(
entry_points=[CallbackQueryHandler(edit_bot_code, pattern='^bot_action:edit:')],
states={
EDIT_CODE: [
CallbackQueryHandler(save_edited_code, pattern='^save_code:'),
MessageHandler(filters.Document.ALL, receive_edited_code)
]
},
fallbacks=[CallbackQueryHandler(cancel_operation, pattern='^cancel_operation$'), CommandHandler('cancel', cancel_operation)],
per_user=True, per_chat=True
)

application.add_handler(upload_conv_handler)
application.add_handler(mirror_conv_handler)
application.add_handler(edit_code_conv_handler)

application.add_handler(CommandHandler("start", start_command))
application.add_handler(CommandHandler("list", list_bots_command))
application.add_handler(CommandHandler("stats", stats_command))
application.add_handler(CommandHandler("help", help_command))
application.add_handler(CallbackQueryHandler(main_menu_callback, pattern='^main_menu$'))
application.add_handler(CallbackQueryHandler(list_bots_command, pattern='^list_bots$'))
application.add_handler(CallbackQueryHandler(stats_command, pattern='^stats$'))
application.add_handler(CallbackQueryHandler(help_command, pattern='^help$'))
application.add_handler(CallbackQueryHandler(settings_callback, pattern='^settings$'))
application.add_handler(CallbackQueryHandler(system_health_command, pattern='^system_health$'))
application.add_handler(CallbackQueryHandler(clean_logs_command, pattern='^clean_logs$'))

application.add_handler(CallbackQueryHandler(template_list_command, pattern='^template_list$'))
application.add_handler(CallbackQueryHandler(select_template_command, pattern='^select_template:'))
application.add_handler(CallbackQueryHandler(use_template_command, pattern='^use_template:'))

application.add_handler(CallbackQueryHandler(start_all_bots_command, pattern='^start_all_bots$'))
application.add_handler(CallbackQueryHandler(stop_all_bots_command, pattern='^stop_all_bots$'))
application.add_handler(CallbackQueryHandler(manage_mirror_callback, pattern='^manage_mirror$'))
application.add_handler(CallbackQueryHandler(browse_mirror_callback, pattern='^browse_mirror$'))
application.add_handler(CallbackQueryHandler(delete_all_mirror_confirm_callback, pattern='^delete_all_mirror_confirm$'))
application.add_handler(CallbackQueryHandler(delete_all_mirror_final_callback, pattern='^delete_all_mirror_final$'))
application.add_handler(CallbackQueryHandler(select_bot_callback, pattern=r'^select_bot:'))
application.add_handler(CallbackQueryHandler(bot_action_callback, pattern=r'^bot_action:'))

application.add_handler(CallbackQueryHandler(delete_all_bots_confirm, pattern='^delete_all_confirm$'))
application.add_handler(CallbackQueryHandler(delete_all_bots_final, pattern='^delete_all_final$'))
application.add_handler(MessageHandler(filters.COMMAND, start_command)) # Fallback for unknown commands
application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, autoreact))

logger.info("Bot is starting...")
application.run_polling()
if name == "main":
main()


