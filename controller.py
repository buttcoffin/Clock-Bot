"""
Telegram remote control + scheduler for clockbot.py.

Replaces cron: this runs as one long-lived service. Each work day it picks a
random clock-in and lunch-out time inside their windows, then works out the
lunch length and clock-out so the day nets DAY_MIN_HOURS..DAY_MAX_HOURS (lunch
unpaid) and the week never goes over WEEK_MAX_HOURS, keeping clock-out inside
its window whenever possible. The plan is re-solved from the real click time
after every punch. clockbot.py is started a few minutes early and waits on the
punch page until the exact time. Only messages from TELEGRAM_CHAT_ID are
obeyed; everything else is ignored.

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
LEAD_MIN = int(os.getenv("PUNCH_LEAD_MIN", "15"))    # start clockbot this early (room for a retry); it waits on the punch page
LUNCH_MIN = int(os.getenv("LUNCH_MIN", "20"))
LUNCH_MAX = int(os.getenv("LUNCH_MAX", "30"))
DAY_MIN_H = float(os.getenv("DAY_MIN_HOURS", "8"))
DAY_MAX_H = float(os.getenv("DAY_MAX_HOURS", "8.25"))
WEEK_MAX_H = float(os.getenv("WEEK_MAX_HOURS", "41"))

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
    """Punch windows for a day: clock-in, lunch-out (None if no lunch), clock-out.
    The lunch-out window switches with DST; lunch-in is derived from lunch length."""
    lunch = os.getenv("LUNCH_OUT_DST") if is_dst(d) else os.getenv("LUNCH_OUT")
    return {
        "in": parse_window(os.getenv("PUNCH_IN", "07:45-07:55")),
        "lunch": parse_window(lunch),
        "out": parse_window(os.getenv("PUNCH_OUT", "16:10-16:20")),
    }


def at(d, hm):
    return datetime.combine(d, datetime.min.time()).replace(hour=hm[0], minute=hm[1]).timestamp()


def rand_between(lo, hi):
    return lo if hi <= lo else random.uniform(lo, hi)


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


# ---------- hours ----------
def week_dates(d):
    start = d - timedelta(days=d.weekday())  # Monday
    return [start + timedelta(days=i) for i in range(7)]


def week_worked(s, d):
    """Hours already recorded this week before day d."""
    return sum(s.get("hours", {}).get(x.isoformat(), 0) for x in week_dates(d) if x < d)


def day_hours_range(s, d):
    """(min, max) net hours allowed today: DAY_MIN..DAY_MAX, capped so the week can't pass
    WEEK_MAX if the remaining work days this week each get at least DAY_MIN."""
    later = [x for x in week_dates(d) if x > d and is_work_day(x) and x.isoformat() not in s["skip"]]
    cap = WEEK_MAX_H - week_worked(s, d) - DAY_MIN_H * len(later)
    hi = clamp(cap, DAY_MIN_H, DAY_MAX_H)  # never plan a day under DAY_MIN
    return DAY_MIN_H, hi


def solve(s, d, t_in, t_lunch=None, lunch_len=None):
    """Given clock-in (and lunch-out / lunch length once known), pick lunch length and
    clock-out so net hours fit today's range and clock-out stays in its window if it can.
    Returns (lunch_len_seconds, clock_out_ts). Hours rules win over the window."""
    w = windows_for(d)
    o_lo, o_hi = at(d, w["out"][0]), at(d, w["out"][1])
    h_lo, h_hi = (h * 3600 for h in day_hours_range(s, d))
    if t_lunch is None and w["lunch"] is None:
        lunch_len = 0
    if lunch_len is None:
        # A little headroom so a click that lands a few seconds late can't break the lunch rule.
        l_lo, l_hi = LUNCH_MIN * 60 + 30, LUNCH_MAX * 60 - 60
        a = max(l_lo, o_lo - t_in - h_hi)   # long enough that clock-out isn't before the window
        b = min(l_hi, o_hi - t_in - h_lo)   # short enough that clock-out isn't after it
        lunch_len = rand_between(a, b) if a <= b else (l_lo if o_hi - t_in - h_lo < l_lo else l_hi)
    base = t_in + lunch_len
    a, b = max(o_lo, base + h_lo), min(o_hi, base + h_hi)
    out = rand_between(a, b) if a <= b else clamp(rand_between(o_lo, o_hi), base + h_lo, base + h_hi)
    return lunch_len, out


def make_plan(d, now_ts, s):
    w = windows_for(d)
    t_in = rand_between(at(d, w["in"][0]), at(d, w["in"][1]))
    slots = [{"name": "Clock in", "at": t_in}]
    if w["lunch"]:
        t_lo = rand_between(at(d, w["lunch"][0]), at(d, w["lunch"][1]))
        lunch_len, t_out = solve(s, d, t_in, t_lo)
        slots += [{"name": "Lunch out", "at": t_lo}, {"name": "Lunch in", "at": t_lo + lunch_len}]
    else:
        _, t_out = solve(s, d, t_in)
    slots.append({"name": "Clock out", "at": t_out})
    for x in slots:
        # Anything already past when the plan is made (mid-day start) is not chased.
        x["status"] = "past" if now_ts > x["at"] + GRACE_MIN * 60 else "pending"
    return {"date": d.isoformat(), "slots": slots}


def slot_time(plan, name):
    """Real click time if we have it, else the planned time."""
    x = next((x for x in plan["slots"] if x["name"] == name), None)
    return None if x is None else x.get("actual") or x["at"]


def replan(s):
    """Re-solve the rest of today from what actually happened so far."""
    plan = s["plan"]
    d = date.fromisoformat(plan["date"])
    by = {x["name"]: x for x in plan["slots"]}
    t_in = slot_time(plan, "Clock in")
    if "Lunch out" in by:
        t_lo = slot_time(plan, "Lunch out")
        if by["Lunch in"]["status"] in ("done", "past") or by["Lunch in"].get("actual"):
            lunch_len = slot_time(plan, "Lunch in") - t_lo
        else:
            lunch_len = None
        if by["Lunch out"]["status"] == "pending":
            # lunch-out not clicked yet: keep its time, re-pick lunch length
            lunch_len, t_out = solve(s, d, t_in, t_lo)
        else:
            lunch_len, t_out = solve(s, d, t_in, t_lo, lunch_len)
        if by["Lunch in"]["status"] == "pending":
            by["Lunch in"]["at"] = t_lo + lunch_len
    else:
        _, t_out = solve(s, d, t_in)
    if by["Clock out"]["status"] == "pending":
        by["Clock out"]["at"] = t_out


def day_net_hours(plan):
    t_in, t_out = slot_time(plan, "Clock in"), slot_time(plan, "Clock out")
    lunch = 0
    if slot_time(plan, "Lunch out") is not None:
        lunch = slot_time(plan, "Lunch in") - slot_time(plan, "Lunch out")
    return (t_out - t_in - lunch) / 3600


def fmt_ts(ts):
    return datetime.fromtimestamp(ts).strftime("%-I:%M:%S %p")


# ---------- state ----------
def load_state():
    s = {"paused": True, "skip": [], "plan": None, "hours": {}}  # first run ever starts paused
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            s.update(json.load(f))
    s.pop("done", None)  # old format
    s.setdefault("hours", {})
    return s


def save_state(s):
    with state_lock:
        today = date.today().isoformat()
        s["skip"] = sorted(d for d in set(s["skip"]) if d >= today)
        cutoff = (date.today() - timedelta(days=21)).isoformat()
        s["hours"] = {k: v for k, v in s.get("hours", {}).items() if k >= cutoff}
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
        s["plan"] = make_plan(d, now.timestamp(), s) if is_work_day(d) else {"date": d.isoformat(), "slots": []}
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
            s["paused"] = True
            msgs.append(f"⚠️ Missed {slot['name']} ({fmt_ts(slot['at'])}), probably because the Pi was off or busy.\n"
                        "⏸ Paused so later punches can't land out of order. "
                        "Check your timecard, /punch if needed, then /resume.")
        return None, msgs
    if s["paused"] or skipped:
        return None, msgs
    if ts >= slot["at"] - LEAD_MIN * 60:
        return slot, msgs
    return None, msgs


def after_punch(s, slot, ok, punched_at):
    """Record the result and re-solve the rest of the day from the real click times."""
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
    replan(s)
    by = {x["name"]: x for x in plan["slots"]}
    if slot["name"] == "Lunch in" and "Lunch out" in by:
        mins = (slot_time(plan, "Lunch in") - slot_time(plan, "Lunch out")) / 60
        if not LUNCH_MIN - 0.5 <= mins <= LUNCH_MAX + 0.5:
            msgs.append(f"⚠️ Lunch was {mins:.1f} min (target {LUNCH_MIN}-{LUNCH_MAX}).")
    if slot["name"] == "Clock out":
        hours = day_net_hours(plan)
        d = date.fromisoformat(plan["date"])
        s["hours"][plan["date"]] = round(hours, 4)
        msgs.append(f"Day: {hours:.2f} h · Week so far: {week_worked(s, d) + hours:.2f} h")
    else:
        upcoming = [x for x in plan["slots"] if x["status"] == "pending"]
        if upcoming:
            lo, hi = day_hours_range(s, date.fromisoformat(plan["date"]))
            msgs.append("Next: " + ", ".join(f"{x['name']} {fmt_ts(x['at'])}" for x in upcoming)
                        + f" (day target {lo:g}-{hi:g} h, planned {day_net_hours(plan):.2f} h)")
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
            w = windows_for(n)["in"]
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
        lo, hi = day_hours_range(s, date.fromisoformat(plan["date"]))
        lines.append(f"  Planned: {day_net_hours(plan):.2f} h (target {lo:g}-{hi:g} h)")
    else:
        lines.append("No punches today.")
    d = date.today()
    done_today = s["hours"].get(d.isoformat(), 0)
    lines.append(f"Week so far: {week_worked(s, d) + done_today:.2f} h (max {WEEK_MAX_H:g})")
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
        if interrupted:
            s["paused"] = True
        save_state(s)
    send("Clock-bot controller started.\n" + fmt_status(s))
    for x in interrupted:
        send(f"⚠️ {x['name']} was interrupted by a restart (reboot or power loss) and probably did NOT punch.\n"
             "⏸ Paused so later punches can't land out of order. "
             "Check your timecard, /punch if needed, then /resume.")
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
