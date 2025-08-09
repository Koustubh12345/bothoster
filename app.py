import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import bot # Import your bot's logic from bot.py
import requests
import base64
import json
import mimetypes

# --- Configuration ---
PORT = int(os.environ.get("PORT", 10000))
DATA_DIR = "data"
# PANTRY API key should be set as an environment variable
PANTRY_API_KEY = os.environ.get("PANTRY_API_KEY", "1355fa66-95d4-40e0-9508-4d92a74531fe")
PANTRY_URL = f"https://getpantry.cloud/apiv1/pantry/{PANTRY_API_KEY}"

class CustomHTTPRequestHandler(BaseHTTPRequestHandler):
    """
    Custom request handler to serve mirrored files via Pantry and a health check endpoint.
    """
    def do_GET(self):
        # Health check endpoint for Render
        if self.path == '/health':
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'OK')
            return

        # Serve files proxied from Pantry
        if self.path.startswith('/mirror/'):
            file_key = self.path.split('/mirror/', 1)[1]
            if not file_key:
                self.send_error(400, "Bad Request")
                return

            try:
                # Make a request to the Pantry API
                response = requests.get(f"{PANTRY_URL}/basket/{file_key}")
                
                if response.status_code == 200:
                    file_data = response.json()
                    
                    # Pantry stores files as Base64 encoded strings
                    encoded_content = file_data.get('content', '')
                    mime_type = file_data.get('mime_type', 'application/octet-stream')
                    file_name = file_data.get('name', 'file')
                    
                    if not encoded_content:
                        self.send_error(404, "File content is empty.")
                        return

                    decoded_content = base64.b64decode(encoded_content)
                    
                    self.send_response(200)
                    self.send_header('Content-type', mime_type)
                    self.send_header('Content-Length', str(len(decoded_content)))
                    self.send_header('Content-Disposition', f'attachment; filename="{file_name}"')
                    self.end_headers()
                    self.wfile.write(decoded_content)
                elif response.status_code == 404:
                    self.send_error(404, "File Not Found in Pantry")
                else:
                    self.send_error(500, f"Pantry API error: {response.status_code}")
            except Exception as e:
                print(f"Error serving file from Pantry: {e}")
                self.send_error(500, "Internal Server Error")
            return

        # Default response for the root path
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'BotHoster Pro web service is running.')

def run_web_server():
    """Starts the HTTP server."""
    server_address = ('', PORT)
    httpd = HTTPServer(server_address, CustomHTTPRequestHandler)
    print(f"Web server running on http://0.0.0.0:{PORT}")
    httpd.serve_forever()

def run_bot():
    """Starts the Telegram bot."""
    print("Initializing and starting Telegram bot...")
    bot.main()

if __name__ == '__main__':
    # We no longer need to create a local mirror directory
    os.makedirs(os.path.join(DATA_DIR, "bots"), exist_ok=True)
    
    # Start the web server in a background thread
    web_server_thread = threading.Thread(target=run_web_server)
    web_server_thread.daemon = True
    web_server_thread.start()
    
    # Run the bot in the main thread
    run_bot()
