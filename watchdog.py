"""
MailEngine Watchdog
===================
Keeps the platform running 24/7. Run this instead of run.py:

    python watchdog.py

What it does:
- Starts MailEngine automatically
- Pings it every 20 seconds
- If the server returns an error or dies, it restarts it automatically
- Logs everything to watchdog.log
- Runs a full health check before each restart to diagnose the issue
- Prints clear status to the terminal
"""

import subprocess
import time
import os
import sys
import socket
import urllib.request
import urllib.error
from datetime import datetime

BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
LOG_FILE  = os.path.join(BASE_DIR, "watchdog.log")
PORT      = 5000
PING_URL  = f"http://localhost:{PORT}/"
PING_INTERVAL    = 20   # seconds between health checks
STARTUP_WAIT     = 6    # seconds to wait after starting the server
MAX_RESTARTS     = 10   # before giving up and requiring manual fix


# ── Logging ───────────────────────────────────────────────────────────────────

def log(msg, level="INFO"):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] [{level}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ── Server checks ─────────────────────────────────────────────────────────────

def is_port_in_use():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1)
    result = s.connect_ex(("127.0.0.1", PORT))
    s.close()
    return result == 0


def ping_server():
    """
    Returns (status_code, error_message).
    status_code is None if the request failed entirely.
    """
    try:
        req = urllib.request.urlopen(PING_URL, timeout=8)
        return req.status, None
    except urllib.error.HTTPError as e:
        return e.code, str(e)
    except urllib.error.URLError as e:
        return None, str(e.reason)
    except Exception as e:
        return None, str(e)


def kill_port():
    """Kill whatever process is holding port 5000."""
    try:
        result = subprocess.run(
            f'for /f "tokens=5" %a in (\'netstat -ano ^| findstr ":{PORT}"\') do taskkill /F /PID %a',
            shell=True, capture_output=True, text=True
        )
    except Exception:
        pass
    time.sleep(1)


# ── Health diagnosis ──────────────────────────────────────────────────────────

def run_health_check():
    """Run health_check.py and return summary of any failures."""
    health_script = os.path.join(BASE_DIR, "health_check.py")
    if not os.path.isfile(health_script):
        return "health_check.py not found"
    try:
        result = subprocess.run(
            [sys.executable, health_script],
            capture_output=True, text=True, timeout=30, cwd=BASE_DIR
        )
        output = result.stdout + result.stderr
        # Extract just the [FAIL] lines for concise logging
        fail_lines = [l.strip() for l in output.splitlines() if "[FAIL]" in l]
        if fail_lines:
            return "Issues found: " + " | ".join(fail_lines)
        return "Health check passed"
    except Exception as e:
        return f"Health check error: {e}"


# ── Server lifecycle ──────────────────────────────────────────────────────────

def start_server():
    """Start MailEngine as a subprocess. Returns the Popen object."""
    log("Starting MailEngine...", "START")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.Popen(
        [sys.executable, os.path.join(BASE_DIR, "run.py")],
        cwd=BASE_DIR,
        env=env,
        stdout=open(os.path.join(BASE_DIR, "server.log"), "a", encoding="utf-8"),
        stderr=subprocess.STDOUT,
    )
    log(f"Process started (PID {proc.pid})", "START")
    return proc


def wait_for_startup():
    """Wait for server to become responsive after starting."""
    log(f"Waiting for server to come up on port {PORT}...", "START")
    for attempt in range(STARTUP_WAIT * 2):
        time.sleep(0.5)
        status, _ = ping_server()
        if status is not None:
            log(f"Server is up (HTTP {status})", "START")
            return True
    log("Server did not respond within startup window", "WARN")
    return False


# ── Main loop ─────────────────────────────────────────────────────────────────

def main():
    log("=" * 55, "INFO")
    log("  MailEngine Watchdog", "INFO")
    log(f"  Monitoring: {PING_URL}", "INFO")
    log(f"  Check interval: every {PING_INTERVAL}s", "INFO")
    log(f"  Logs: watchdog.log + server.log", "INFO")
    log("=" * 55, "INFO")
    log("Press Ctrl+C to stop the watchdog (this also stops MailEngine)", "INFO")
    log("", "INFO")

    # Kill anything already on the port before starting fresh
    if is_port_in_use():
        log(f"Port {PORT} already in use — killing existing process", "WARN")
        kill_port()
        time.sleep(2)

    proc = start_server()
    if not wait_for_startup():
        diag = run_health_check()
        log(f"Startup failed. Diagnosis: {diag}", "ERROR")

    restart_count = 0
    consecutive_ok = 0

    while True:
        try:
            time.sleep(PING_INTERVAL)

            # ── Check process is alive ──────────────────────────────────
            if proc.poll() is not None:
                log(f"ALERT: Server process died (exit {proc.returncode})", "ALERT")
                diag = run_health_check()
                log(f"Diagnosis: {diag}", "ALERT")
                restart_count += 1
                consecutive_ok = 0
                if restart_count > MAX_RESTARTS:
                    log(f"Reached {MAX_RESTARTS} restarts. Check server.log for errors.", "ERROR")
                    log("Watchdog will keep trying but please review manually.", "ERROR")
                    restart_count = 0  # Reset so it keeps trying
                log(f"Restarting... (restart #{restart_count})", "START")
                proc = start_server()
                wait_for_startup()
                continue

            # ── Ping the HTTP endpoint ──────────────────────────────────
            status, error = ping_server()

            if status is None:
                log(f"ALERT: Server not responding — {error}", "ALERT")
                consecutive_ok = 0
                restart_count += 1
                proc.terminate()
                time.sleep(2)
                kill_port()
                diag = run_health_check()
                log(f"Diagnosis: {diag}", "ALERT")
                log(f"Restarting... (restart #{restart_count})", "START")
                proc = start_server()
                wait_for_startup()

            elif status >= 500:
                log(f"ALERT: Server returned HTTP {status} — application error", "ALERT")
                consecutive_ok = 0
                restart_count += 1
                proc.terminate()
                time.sleep(2)
                kill_port()
                diag = run_health_check()
                log(f"Diagnosis: {diag}", "ALERT")
                log(f"Restarting... (restart #{restart_count})", "START")
                proc = start_server()
                wait_for_startup()

            else:
                consecutive_ok += 1
                if consecutive_ok == 1 and restart_count > 0:
                    log(f"Server recovered after {restart_count} restart(s). HTTP {status} OK.", "OK")
                    restart_count = 0
                elif consecutive_ok % 10 == 0:
                    # Print a heartbeat every ~3 mins so you know it's still watching
                    log(f"Heartbeat — server healthy (HTTP {status}, {consecutive_ok} checks OK)", "OK")

        except KeyboardInterrupt:
            log("Watchdog stopping (Ctrl+C received)", "INFO")
            try:
                proc.terminate()
                log(f"MailEngine stopped (PID {proc.pid})", "INFO")
            except Exception:
                pass
            print()
            print("Watchdog stopped. MailEngine is no longer running.")
            print("To restart: python watchdog.py")
            print()
            sys.exit(0)

        except Exception as e:
            log(f"Watchdog error: {e}", "ERROR")
            time.sleep(5)


if __name__ == "__main__":
    main()
