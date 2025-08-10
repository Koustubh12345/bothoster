import os
import threading
import mimetypes
from http.server import HTTPServer, SimpleHTTPRequestHandler
import bot  # Import the enhanced bot logic

# --- Configuration ---
PORT = int(os.environ.get("PORT", 10000))
DATA_DIR = "data"
MIRROR_DIR = os.path.join(DATA_DIR, "mirror")

class CustomHTTPRequestHandler(SimpleHTTPRequestHandler):
    """
    Custom request handler to serve mirrored files and a health check endpoint.
    """
    def do_GET(self):
        # Health check endpoint for Render
        if self.path == '/health':
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'OK')
            return
            
        # Serve files from the mirror directory
        if self.path.startswith('/mirror/'):
            # Sanitize path to prevent directory traversal attacks
            base_path = os.path.abspath(MIRROR_DIR)
            requested_path = os.path.abspath(os.path.join(base_path, self.path.split('/mirror/', 1)[1]))
            
            if not requested_path.startswith(base_path):
                self.send_error(403, "Forbidden: Access denied.")
                return
                
            if os.path.isfile(requested_path):
                try:
                    with open(requested_path, 'rb') as f:
                        self.send_response(200)
                        content_type, _ = mimetypes.guess_type(requested_path)
                        self.send_header('Content-type', content_type or 'application/octet-stream')
                        self.send_header('Content-Length', str(os.path.getsize(requested_path)))
                        # Add headers to allow file downloads
                        self.send_header('Content-Disposition', f'inline; filename="{os.path.basename(requested_path)}"')
                        self.end_headers()
                        self.wfile.write(f.read())
                except IOError:
                    self.send_error(404, "File Not Found")
            else:
                self.send_error(404, "File Not Found")
            return
            
        # Default response for the root path
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(b"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>BotHoster Pro</title>
            <style>
                body {
                    font-family: Arial, sans-serif;
                    margin: 40px;
                    line-height: 1.6;
                    color: #333;
                }
                h1 {
                    color: #0088cc;
                }
                .container {
                    max-width: 800px;
                    margin: 0 auto;
                    padding: 20px;
                    border: 1px solid #ddd;
                    border-radius: 5px;
                }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>BotHoster Pro</h1>
                <p>BotHoster Pro web service is running.</p>
                <p>This service allows you to host and manage Telegram bots directly from your Telegram chat.</p>
                <p>To use this service, contact the bot on Telegram.</p>
            </div>
        </body>
        </html>
        """)

def run_web_server():
    """Starts the HTTP server."""
    server_address = ('', PORT)
    httpd = HTTPServer(server_address, CustomHTTPRequestHandler)
    print(f"Web server running on http://0.0.0.0:{PORT}")
    httpd.serve_forever()

def run_bot():
    """Starts the Telegram bot."""
    print("Initializing and starting enhanced Telegram bot...")
    bot.main()

if __name__ == '__main__':
    # Ensure all necessary data directories exist before starting
    os.makedirs(MIRROR_DIR, exist_ok=True)
    os.makedirs(os.path.join(DATA_DIR, "bots"), exist_ok=True)
    os.makedirs(os.path.join(DATA_DIR, "logs"), exist_ok=True)
    os.makedirs(os.path.join(DATA_DIR, "templates"), exist_ok=True)
    
    # Start the web server in a background thread
    web_server_thread = threading.Thread(target=run_web_server)
    web_server_thread.daemon = True
    web_server_thread.start()
    
    # Run the bot in the main thread
    run_bot()
