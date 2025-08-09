import os
import sys
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler

class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health':
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'OK')
        else:
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'Telegram Bot Hosting Service is running.')

def run_web_server():
    server_address = ('', 10000)
    httpd = HTTPServer(server_address, SimpleHTTPRequestHandler)
    print("Web server running on port 10000...")
    httpd.serve_forever()

def run_bot():
    # Import and run the bot
    from bot import main  # Changed to import the main function directly
    main()  # Call the main function

if __name__ == '__main__':
    # Start the web server in a separate thread
    web_server_thread = threading.Thread(target=run_web_server)
    web_server_thread.daemon = True
    web_server_thread.start()
    
    # Run the bot in the main thread
    run_bot()
