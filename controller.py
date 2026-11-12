"""
Telegram remote control + scheduler for clockbot.py.

Replaces cron: this runs as one long-lived service, punches at the times in
.env, and lets you pause/skip/check it from Telegram. Only messages from
TELEGRAM_CHAT_ID are obeyed; everything else is ignored.

Commands:
  /status                 paused?, skipped days, next punch
  /pause  /resume         stop / restart all scheduled punches
  /skip [date]            skip a day (today, tomorrow, or YYYY-MM-DD; default = next punch day)
  /unskip <date>          undo a skip
  /punch                  run a punch right now
  /log                    last lines of punch.log
"""
import json, os, subprocess, sys, threading, time, urllib.parse, urllib.request
from datetime import datetime, date, timedelta
from dotenv import load_dotenv

BASE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE, ".env"))

TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = int(os.environ["TELEGRAM_CHAT_ID"])
PUNCH_TIMES = sorted(t.strip() for t in os.getenv("PUNCH_TIMES", "08:00,17:00").split(",") if t.strip())
PUNCH_DAYS = [d.strip().lower()[:3] for d in os.getenv("PUNCH_DAYS", "mon,tue,wed,thu,fri").split(",")]
GRACE_MIN = int(os.getenv("PUNCH_GRACE_MIN", "5"))  # never "catch up" on a punch older than this

STATE_FILE = os.path.join(BASE, "state.json")
LOG_FILE = os.path.join(BASE, "punch.log")
CLOCKBOT = os.path.join(BASE, "clockbot.py")
API = f"https://api.telegram.org/bot{TOKEN}/"
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

punch_lock = threading.Lock()


# ---------- state ----------
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    # First run ever starts paused, so nothing fires until you say so.
    return {"paused": True, "skip": [], "done": []}


def save_state(s):
    today = date.today().isoformat()
    s["skip"] = sorted(d for d in set(s["skip"]) if d >= today)
    s["done"] = [k for k in s["done"] if k[:10] >= today]
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f, indent=1)
    os.replace(tmp, STATE_FILE)


# ---------- telegram ----------
def api(method, **params):
    data = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(API + method, data=data, timeout=70) as r:
        return json.load(r)


def send(text):
    try:
        api("sendMessage", chat_id=CHAT_ID, text=text)
    except Exception as e:
        print(f"send failed: {e}", flush=True)


# ---------- schedule ----------
def is_work_day(d):
    return DAYS[d.weekday()] in PUNCH_DAYS


def next_punch(s, now=None):
    now = now or datetime.now()
    for i in range(14):
        d = now.date() + timedelta(days=i)
        if not is_work_day(d) or d.isoformat() in s["skip"]:
            continue
        for t in PUNCH_TIMES:
            dt = datetime.combine(d, datetime.strptime(t, "%H:%M").time())
            if dt > now and f"{d.isoformat()} {t}" not in s["done"]:
                return dt
    return None


def due_slots(s, now):
    """Slots that should fire now. Slots missed by more than GRACE_MIN are marked
    done and reported instead of fired (e.g. after a reboot or outage)."""
    fire, missed = [], []
    d = now.date()
    if not is_work_day(d) or s["paused"] or d.isoformat() in s["skip"]:
        return fire, missed
    for t in PUNCH_TIMES:
        key = f"{d.isoformat()} {t}"
        if key in s["done"]:
            continue
        slot = datetime.combine(d, datetime.strptime(t, "%H:%M").time())
        if now < slot:
            continue
        (fire if now - slot <= timedelta(minutes=GRACE_MIN) else missed).append(key)
    return fire, missed


def parse_day(arg, s):
    arg = (arg or "").strip().lower()
    if arg in ("", "next"):
        n = next_punch(s)
        return n.date() if n else None
    if arg == "today":
        return date.today()
    if arg == "tomorrow":
        return date.today() + timedelta(days=1)
    return date.fromisoformat(arg)


# ---------- punching ----------
def run_punch(reason):
    if not punch_lock.acquire(blocking=False):
        send("A punch is already running.")
        return
    try:
        start = os.path.getsize(LOG_FILE) if os.path.exists(LOG_FILE) else 0
        subprocess.run([sys.executable, CLOCKBOT], timeout=30 * 60)
        out = ""
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, errors="ignore") as f:
                f.seek(start)
                out = f.read().strip()
        if "CLOCK PUNCHED" in out:
            icon = "✅"
        elif "SE_OFFLINE" in out:
            icon = "🧪"
        else:
            icon = "❌"
        send(f"{icon} {reason}\n{out[-1500:] or '(no log output)'}")
    except Exception as e:
        send(f"❌ {reason}: {e}")
    finally:
        punch_lock.release()


def start_punch(reason):
    threading.Thread(target=run_punch, args=(reason,), daemon=True).start()


# ---------- commands ----------
def fmt_status(s):
    n = next_punch(s)
    lines = [
        "⏸ PAUSED" if s["paused"] else "▶️ Running",
        f"Times: {', '.join(PUNCH_TIMES)} on {','.join(PUNCH_DAYS)}",
        f"Skipping: {', '.join(s['skip']) or 'none'}",
        f"Next: {n.strftime('%a %Y-%m-%d %H:%M') if n and not s['paused'] else '—'}",
    ]
    if punch_lock.locked():
        lines.append("A punch is running now.")
    return "\n".join(lines)


def handle(text, s):
    cmd, _, arg = text.strip().partition(" ")
    cmd = cmd.split("@")[0].lower()
    if cmd == "/pause":
        s["paused"] = True
        return "⏸ Paused. No punches until /resume."
    if cmd == "/resume":
        s["paused"] = False
        return "▶️ Resumed.\n" + fmt_status(s)
    if cmd == "/skip":
        try:
            d = parse_day(arg, s)
        except ValueError:
            return "Use /skip, /skip today, /skip tomorrow or /skip YYYY-MM-DD"
        if not d:
            return "Nothing scheduled to skip."
        s["skip"].append(d.isoformat())
        return f"Skipping {d.strftime('%a %Y-%m-%d')}."
    if cmd == "/unskip":
        try:
            d = parse_day(arg or "today", s).isoformat()
        except ValueError:
            return "Use /unskip YYYY-MM-DD"
        s["skip"] = [x for x in s["skip"] if x != d]
        return f"Un-skipped {d}."
    if cmd == "/punch":
        start_punch("Manual punch")
        return "Starting punch (includes the random delay)…"
    if cmd == "/log":
        if not os.path.exists(LOG_FILE):
            return "No log yet."
        with open(LOG_FILE, errors="ignore") as f:
            return "".join(f.readlines()[-15:])[-3500:] or "Log is empty."
    if cmd == "/status":
        return fmt_status(s)
    return __doc__.split("Commands:")[1].strip()


# ---------- main loop ----------
def main():
    s = load_state()
    save_state(s)
    send("Clock-bot controller started.\n" + fmt_status(s))
    offset = None
    while True:
        try:
            params = {"timeout": 25, "allowed_updates": '["message"]'}
            if offset is not None:
                params["offset"] = offset
            for u in api("getUpdates", **params).get("result", []):
                offset = u["update_id"] + 1
                msg = u.get("message") or {}
                if msg.get("chat", {}).get("id") != CHAT_ID or "text" not in msg:
                    continue
                reply = handle(msg["text"], s)
                save_state(s)
                send(reply)
        except Exception as e:
            print(f"poll error: {e}", flush=True)
            time.sleep(10)

        fire, missed = due_slots(s, datetime.now())
        for key in missed:
            send(f"⚠️ Missed {key} (controller was down). Not punching late — use /punch if needed.")
        for key in fire:
            start_punch(f"Scheduled punch {key}")
        if fire or missed:
            s["done"].extend(fire + missed)
            save_state(s)


if __name__ == "__main__":
    main()
