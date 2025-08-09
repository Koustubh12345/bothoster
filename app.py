import os
import sys
import threading
import time
from flask import Flask, jsonify

# Create Flask app
app = Flask(__name__)

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "OK"}), 200

@app.route('/', methods=['GET'])
def home():
    return "Telegram Bot Hosting Service is running."

def run_web_server():
    """Run the Flask web server."""
    app.run(host='0.0.0.0', port=10000, threaded=True)

def run_bot():
    """Import and run the bot."""
    import bot
    bot.main()

if __name__ == '__main__':
    # Start the web server in a separate thread
    web_server_thread = threading.Thread(target=run_web_server)
    web_server_thread.daemon = True
    web_server_thread.start()
    
    # Run the bot in the main thread
    run_bot()
