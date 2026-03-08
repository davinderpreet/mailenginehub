"""
Startup script — loads .env then launches the platform.
Run with: python run.py
"""
from dotenv import load_dotenv
load_dotenv()

from database import init_db
from app import app

if __name__ == "__main__":
    init_db()
    print("\n" + "="*55)
    print("  MailEngine -- Your Email Marketing Platform")
    print("  Open in browser: http://localhost:5000")
    print("  Press Ctrl+C to stop")
    print("="*55 + "\n")
    app.run(debug=True, port=5000, use_reloader=False)
