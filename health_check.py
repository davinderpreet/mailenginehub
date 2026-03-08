"""
MailEngine — Health Check & Diagnostic Script
Run this any time something seems wrong: python health_check.py

Checks everything and tells you exactly what to fix.
"""

import sys
import os
import subprocess

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

PASS  = "[PASS]"
FAIL  = "[FAIL]"
WARN  = "[WARN]"
INFO  = "[INFO]"

issues = []
warnings = []

def check(label, ok, detail="", is_warning=False):
    if ok:
        print(f"  {PASS}  {label}")
    elif is_warning:
        print(f"  {WARN}  {label}")
        if detail: print(f"         {detail}")
        warnings.append(label)
    else:
        print(f"  {FAIL}  {label}")
        if detail: print(f"         --> {detail}")
        issues.append(label)

print()
print("=" * 60)
print("  MailEngine Health Check")
print("=" * 60)

# ── 1. Python version ───────────────────────────────────────────
print("\n[1] Python Environment")
major, minor = sys.version_info[:2]
check(f"Python version: {major}.{minor}", major == 3 and minor >= 10,
      "Need Python 3.10+. Download from python.org" if not (major == 3 and minor >= 10) else "")

# ── 2. Dependencies ─────────────────────────────────────────────
print("\n[2] Python Packages")
packages = {
    "flask":        "Flask",
    "peewee":       "peewee",
    "boto3":        "boto3",
    "requests":     "requests",
    "dotenv":       "python-dotenv",
}
for import_name, pip_name in packages.items():
    try:
        __import__(import_name)
        check(pip_name, True)
    except ImportError:
        check(pip_name, False, f"Run: pip install {pip_name}")

# ── 3. Required files ───────────────────────────────────────────
print("\n[3] Project Files")
required_files = [
    "run.py", "app.py", "database.py", "email_sender.py",
    "shopify_sync.py", "requirements.txt", ".env",
    "templates/base.html", "templates/dashboard.html",
    "templates/warmup.html",
]
for f in required_files:
    path = os.path.join(BASE_DIR, f)
    check(f, os.path.isfile(path), f"Missing file: {path}")

# ── 4. .env credentials ─────────────────────────────────────────
print("\n[4] Environment Configuration")
env_path = os.path.join(BASE_DIR, ".env")
if os.path.isfile(env_path):
    from dotenv import dotenv_values
    env = dotenv_values(env_path)

    def env_check(key, placeholder_hints):
        val = env.get(key, "")
        missing = not val or any(h in val for h in placeholder_hints)
        check(key, not missing,
              f"Still has placeholder value. Edit .env and fill in real value.",
              is_warning=missing)

    env_check("AWS_ACCESS_KEY_ID",     ["YOUR_", "your_"])
    env_check("AWS_SECRET_ACCESS_KEY", ["YOUR_", "your_"])
    env_check("AWS_REGION",            [])
    env_check("DEFAULT_FROM_EMAIL",    ["yourdomain", "YOUR_"])
    env_check("SHOPIFY_STORE_URL",     ["your-store", "YOUR_"])
    env_check("SHOPIFY_ACCESS_TOKEN",  ["YOUR_TOKEN", "shpat_YOUR"])
else:
    check(".env file exists", False, "Create a .env file — copy from SETUP_GUIDE.md")

# ── 5. Python syntax check ──────────────────────────────────────
print("\n[5] Python Syntax")
py_files = ["run.py", "app.py", "database.py", "email_sender.py", "shopify_sync.py"]
for f in py_files:
    path = os.path.join(BASE_DIR, f)
    if not os.path.isfile(path):
        continue
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", path],
        capture_output=True, text=True
    )
    check(f, result.returncode == 0,
          result.stderr.strip() if result.returncode != 0 else "")

# ── 6. Module imports ───────────────────────────────────────────
print("\n[6] Module Imports")
result = subprocess.run(
    [sys.executable, "-c",
     "import sys; sys.path.insert(0, r'" + BASE_DIR.replace("\\", "/") + "');"
     "from dotenv import load_dotenv; load_dotenv();"
     "from database import (db, Contact, EmailTemplate, Campaign, CampaignEmail,"
     " WarmupConfig, WarmupLog, get_warmup_config, init_db);"
     "from app import app;"
     "print('OK')"],
    capture_output=True, text=True, cwd=BASE_DIR
)
check("All modules import cleanly", "OK" in result.stdout,
      result.stderr.strip()[:300] if "OK" not in result.stdout else "")

# ── 7. Database ─────────────────────────────────────────────────
print("\n[7] Database")
db_path = os.path.join(BASE_DIR, "email_platform.db")
check("Database file exists", os.path.isfile(db_path),
      "Will be created on first run — not an error unless you can't start the app")
if os.path.isfile(db_path):
    try:
        sys.path.insert(0, BASE_DIR)
        from dotenv import load_dotenv; load_dotenv()
        from database import db, Contact, Campaign, WarmupConfig, init_db
        db.connect(reuse_if_open=True)
        contacts  = Contact.select().count()
        campaigns = Campaign.select().count()
        check("Database tables readable", True)
        print(f"  {INFO}  Contacts: {contacts}  |  Campaigns: {campaigns}")
        config, _ = WarmupConfig.get_or_create(id=1)
        print(f"  {INFO}  Warmup: {'ACTIVE' if config.is_active else 'inactive'}"
              f"  |  Phase: {config.current_phase}")
        db.close()
    except Exception as e:
        check("Database tables readable", False, str(e))

# ── 8. Port availability ────────────────────────────────────────
print("\n[8] Port 5000")
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(1)
in_use = s.connect_ex(("127.0.0.1", 5000)) == 0
s.close()
if in_use:
    check("Port 5000 available", False,
          "Something is already on port 5000. If it's MailEngine, that's fine.\n"
          "         If it's something else: find and stop it, or change the port in run.py")
else:
    check("Port 5000 available", True)

# ── 9. Template syntax ──────────────────────────────────────────
print("\n[9] Template Syntax")
templates_dir = os.path.join(BASE_DIR, "templates")
try:
    from jinja2 import Environment, FileSystemLoader, TemplateSyntaxError
    env_j2 = Environment(loader=FileSystemLoader(templates_dir))
    html_files = [f for f in os.listdir(templates_dir) if f.endswith(".html")]
    all_ok = True
    for tmpl in html_files:
        try:
            with open(os.path.join(templates_dir, tmpl), encoding="utf-8") as fh:
                env_j2.parse(fh.read())
        except TemplateSyntaxError as e:
            check(tmpl, False, f"Line {e.lineno}: {e.message}")
            all_ok = False
    if all_ok:
        check(f"All {len(html_files)} templates parse cleanly", True)
except ImportError:
    check("Jinja2 template check", False, "Jinja2 not installed")

# ── Summary ─────────────────────────────────────────────────────
print()
print("=" * 60)
if not issues and not warnings:
    print("  All checks passed — MailEngine is healthy!")
    print("  Start the platform: python run.py")
elif not issues:
    print(f"  {len(warnings)} warning(s) — platform should work but check the items above.")
    print("  Start the platform: python run.py")
else:
    print(f"  {len(issues)} issue(s) found — fix the [FAIL] items above before starting.")
    if warnings:
        print(f"  {len(warnings)} warning(s) — review [WARN] items when you can.")
print("=" * 60)
print()
sys.exit(1 if issues else 0)
