"""
Telegram remote control + scheduler for clockbot.py.

Replaces cron: this runs as one long-lived service. Each work day it picks a
random time inside each punch window from .env, starts clockbot.py a few
minutes early, and clockbot waits on the punch page until that exact time.
Lunch-in is re-timed from the real lunch-out click so the break always lands
between LUNCH_MIN and LUNCH_MAX minutes. Only messages from TELEGRAM_CHAT_ID
are obeyed; everything else is ignored.

Commands:
  /status                 paused?, today's punch plan, skipped days
  /pause  /resume         stop / restart all scheduled punches
  /skip [date]            skip a day (today, tomorrow, or YYYY-MM-DD; default = next punch day)
  /unskip <date>          undo a skip
  /punch                  run a punch right now
  /log                    last lines of punch.log
"""
import json, os, random, subprocess, sys, threading, time, urllib.parse, urllib.request
from datetime import datetime, date, timedelta
from dotenv import load_dotenv

BASE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE, ".env"))

TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = int(os.environ["TELEGRAM_CHAT_ID"])
PUNCH_DAYS = [d.strip().lower()[:3] for d in os.getenv("PUNCH_DAYS", "mon,tue,wed,thu,fri").split(",")]
GRACE_MIN = int(os.getenv("PUNCH_GRACE_MIN", "5"))   # never start a punch more than this late
LEAD_MIN = int(os.getenv("PUNCH_LEAD_MIN", "10"))    # start clockbot this early; it waits on the punch page
LUNCH_MIN = int(os.getenv("LUNCH_MIN", "20"))
LUNCH_MAX = int(os.getenv("LUNCH_MAX", "30"))

STATE_FILE = os.path.join(BASE, "state.json")
LOG_FILE = os.path.join(BASE, "punch.log")
LAST_PUNCH_FILE = os.path.join(BASE, "last_punch")
CLOCKBOT = os.path.join(BASE, "clockbot.py")
API = f"https://api.telegram.org/bot{TOKEN}/"
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

punch_lock = threading.Lock()
state_lock = threading.RLock()


# ---------- windows ----------
def parse_window(text):
    """'07:50-08:00' -> ((7, 50), (8, 0)); '' -> None"""
    text = (text or "").strip()
    if not text:
        return None
    a, b = text.split("-")
    ha, ma = map(int, a.strip().split(":"))
    hb, mb = map(int, b.strip().split(":"))
    return (ha, ma), (hb, mb)


def is_dst(d):
    return time.localtime(time.mktime((d.year, d.month, d.day, 12, 0, 0, 0, 0, -1))).tm_isdst > 0


def windows_for(d):
    """Ordered (name, window) list for a day. Lunch windows switch with DST."""
    dst = is_dst(d)
    lunch_out = os.getenv("LUNCH_OUT_DST") if dst else os.getenv("LUNCH_OUT")
    lunch_in = os.getenv("LUNCH_IN_DST") if dst else os.getenv("LUNCH_IN")
    out = [("Clock in", parse_window(os.getenv("PUNCH_IN", "07:50-08:00")))]
    if lunch_out and lunch_in:
        out += [("Lunch out", parse_window(lunch_out)), ("Lunch in", parse_window(lunch_in))]
    out.append(("Clock out", parse_window(os.getenv("PUNCH_OUT", "16:10-16:20"))))
    return out


def at(d, hm):
    return datetime.combine(d, datetime.min.time()).replace(hour=hm[0], minute=hm[1]).timestamp()


def rand_between(lo, hi):
    return lo if hi <= lo else random.uniform(lo, hi)


def lunch_in_time(lunch_out_ts, window, d):
    """Random time in the lunch-in window that keeps the break within LUNCH_MIN..LUNCH_MAX.
    If the window can't satisfy that (e.g. lunch-out was late), the break rule wins."""
    lo, hi = lunch_out_ts + LUNCH_MIN * 60, lunch_out_ts + LUNCH_MAX * 60
    w_lo, w_hi = at(d, window[0]), at(d, window[1])
    a, b = max(lo, w_lo), min(hi, w_hi)
    return rand_between(a, b) if a <= b else rand_between(lo, hi)


def make_plan(d, now_ts):
    slots = []
    for name, w in windows_for(d):
        if name == "Lunch in":
            ts = lunch_in_time(slots[-1]["at"], w, d)
        else:
            ts = rand_between(at(d, w[0]), at(d, w[1]))
        # Anything already past when the plan is made (mid-day start) is not chased.
        status = "past" if now_ts > ts + GRACE_MIN * 60 else "pending"
        slots.append({"name": name, "at": ts, "status": status})
    return {"date": d.isoformat(), "slots": slots}


def fmt_ts(ts):
    return datetime.fromtimestamp(ts).strftime("%-I:%M:%S %p")


# ---------- state ----------
def load_state():
    s = {"paused": True, "skip": [], "plan": None}  # first run ever starts paused
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            s.update(json.load(f))
    s.pop("done", None)  # old format
    return s


def save_state(s):
    with state_lock:
        today = date.today().isoformat()
        s["skip"] = sorted(d for d in set(s["skip"]) if d >= today)
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


def today_plan(s, now=None):
    now = now or datetime.now()
    d = now.date()
    if not s.get("plan") or s["plan"]["date"] != d.isoformat():
        s["plan"] = make_plan(d, now.timestamp()) if is_work_day(d) else {"date": d.isoformat(), "slots": []}
    return s["plan"]


def next_slot(plan):
    for slot in plan["slots"]:
        if slot["status"] in ("pending", "running"):
            return slot
    return None


def tick(s, now=None):
    """Decide what to do now. Returns (slot_to_start or None, [messages])."""
    now = now or datetime.now()
    msgs = []
    plan = today_plan(s, now)
    skipped = plan["date"] in s["skip"]
    slot = next_slot(plan)
    if not slot or slot["status"] == "running":
        return None, msgs
    ts = now.timestamp()
    if ts > slot["at"] + GRACE_MIN * 60:
        if s["paused"] or skipped:
            slot["status"] = "past"
        else:
            slot["status"] = "missed"
            msgs.append(f"⚠️ Missed {slot['name']} ({fmt_ts(slot['at'])}). Not punching late; use /punch if needed.")
        return None, msgs
    if s["paused"] or skipped:
        return None, msgs
    if ts >= slot["at"] - LEAD_MIN * 60:
        return slot, msgs
    return None, msgs


def after_punch(s, slot, ok, punched_at):
    """Record the result; re-time lunch-in from the real lunch-out click."""
    plan = s["plan"]
    slot["status"] = "done" if ok else "failed"
    if punched_at:
        slot["actual"] = punched_at
    msgs = []
    if not ok:
        s["paused"] = True
        msgs.append("⏸ Paused after a failed punch so nothing punches out of order. "
                    "Check /log, punch manually if needed, then /resume.")
        return msgs
    names = [x["name"] for x in plan["slots"]]
    if slot["name"] == "Lunch out" and "Lunch in" in names:
        nxt = plan["slots"][names.index("Lunch in")]
        if nxt["status"] == "pending":
            d = date.fromisoformat(plan["date"])
            window = dict(windows_for(d))["Lunch in"]
            nxt["at"] = lunch_in_time(punched_at or slot["at"], window, d)
            msgs.append(f"Lunch in set for {fmt_ts(nxt['at'])}.")
    if slot["name"] == "Lunch in":
        lo = next((x for x in plan["slots"] if x["name"] == "Lunch out"), None)
        if lo and lo.get("actual") and punched_at:
            mins = (punched_at - lo["actual"]) / 60
            if not LUNCH_MIN <= mins <= LUNCH_MAX:
                msgs.append(f"⚠️ Lunch was {mins:.1f} min (target {LUNCH_MIN}-{LUNCH_MAX}).")
    return msgs


# ---------- punching ----------
def run_clockbot(punch_at):
    """Run clockbot.py once. Returns (ok, punched_at or None, icon, log text)."""
    start = os.path.getsize(LOG_FILE) if os.path.exists(LOG_FILE) else 0
    try:
        os.remove(LAST_PUNCH_FILE)
    except FileNotFoundError:
        pass
    env = dict(os.environ, PUNCH_AT=str(punch_at))
    subprocess.run([sys.executable, CLOCKBOT], env=env, timeout=40 * 60)
    out = ""
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, errors="ignore") as f:
            f.seek(start)
            out = f.read().strip()
    punched_at = None
    if os.path.exists(LAST_PUNCH_FILE):
        with open(LAST_PUNCH_FILE) as f:
            punched_at = float(f.read().strip())
    if "CLOCK PUNCHED" in out:
        return True, punched_at, "✅", out
    if "SE_OFFLINE" in out:
        return True, None, "🧪", out
    return False, None, "❌", out


def punch_worker(s, slot):
    """Runs in a thread. slot=None means a manual /punch."""
    if not punch_lock.acquire(blocking=False):
        if slot:
            with state_lock:
                slot["status"] = "pending"  # try again on the next tick
        else:
            send("A punch is already running.")
        return
    label = slot["name"] if slot else "Manual punch"
    try:
        target = slot["at"] if slot else time.time()
        try:
            ok, punched_at, icon, out = run_clockbot(target)
        except Exception as e:
            ok, punched_at, icon, out = False, None, "❌", str(e)
        when = f" at {fmt_ts(punched_at)}" if punched_at else ""
        send(f"{icon} {label}{when}\n{out[-1500:] or '(no log output)'}")
        if slot:
            with state_lock:
                msgs = after_punch(s, slot, ok, punched_at)
                save_state(s)
            for m in msgs:
                send(m)
    finally:
        punch_lock.release()


def start_punch(s, slot):
    if slot:
        slot["status"] = "running"
    threading.Thread(target=punch_worker, args=(s, slot), daemon=True).start()


# ---------- commands ----------
ICONS = {"pending": "⏳", "running": "🔄", "done": "✅", "failed": "❌", "missed": "⚠️", "past": "·"}


def next_day_text(s):
    d = date.today()
    for i in range(1, 15):
        n = d + timedelta(days=i)
        if is_work_day(n) and n.isoformat() not in s["skip"]:
            w = windows_for(n)[0][1]
            return f"Next day: {n.strftime('%a %Y-%m-%d')}, clock in {w[0][0]:02d}:{w[0][1]:02d}-{w[1][0]:02d}:{w[1][1]:02d}"
    return "Next day: none in the next 2 weeks"


def fmt_status(s):
    plan = today_plan(s)
    lines = ["⏸ PAUSED" if s["paused"] else "▶️ Running"]
    if plan["date"] in s["skip"]:
        lines.append("Today is skipped.")
    if plan["slots"]:
        lines.append(f"Today ({'DST' if is_dst(date.today()) else 'standard time'}):")
        for x in plan["slots"]:
            t = fmt_ts(x.get("actual") or x["at"])
            lines.append(f"  {ICONS.get(x['status'], '?')} {x['name']}: {t}")
    else:
        lines.append("No punches today.")
    lines.append(next_day_text(s))
    lines.append(f"Skipping: {', '.join(s['skip']) or 'none'}")
    return "\n".join(lines)


def parse_day(arg, s):
    arg = (arg or "").strip().lower()
    if arg in ("", "next"):
        plan = today_plan(s)
        if next_slot(plan) and plan["date"] not in s["skip"]:
            return date.today()
        d = date.today()
        for i in range(1, 15):
            n = d + timedelta(days=i)
            if is_work_day(n) and n.isoformat() not in s["skip"]:
                return n
        return None
    if arg == "today":
        return date.today()
    if arg == "tomorrow":
        return date.today() + timedelta(days=1)
    return date.fromisoformat(arg)


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
        start_punch(s, None)
        return "Starting a manual punch now (takes a few minutes)…"
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
    with state_lock:
        plan = today_plan(s)
        # A punch that was mid-run when the service stopped was killed with it.
        interrupted = [x for x in plan["slots"] if x["status"] == "running"]
        for x in interrupted:
            x["status"] = "failed"
        save_state(s)
    send("Clock-bot controller started.\n" + fmt_status(s))
    for x in interrupted:
        send(f"⚠️ {x['name']} was interrupted by a restart. Check /log; punch manually if it didn't go through.")
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
                with state_lock:
                    reply = handle(msg["text"], s)
                    save_state(s)
                send(reply)
        except Exception as e:
            print(f"poll error: {e}", flush=True)
            time.sleep(10)

        with state_lock:
            slot, msgs = tick(s)
            if slot and not punch_lock.locked():
                start_punch(s, slot)
            save_state(s)
        for m in msgs:
            send(m)


if __name__ == "__main__":
    main()
