# Clock-Bot: Automated Timecard & MFA Handler

![Python](https://img.shields.io/badge/Python-3.x-blue?logo=python&logoColor=white)
![Debian/Raspbian](https://img.shields.io/badge/OS-Raspbian%20%7C%20Debian-A81D33?logo=debian&logoColor=white)
![Selenium](https://img.shields.io/badge/Selenium-Automation-43B02A?logo=selenium&logoColor=white)

A hardware-integrated automation service designed to run on a Raspberry Pi or Debian headless server. This bot automates enterprise timecard punches using Selenium, reads SMS-forwarded MFA codes via IMAP, reports to you over Telegram, and shows status on a physical Waveshare e-Paper display.

> 🤖 **A note from the management:** Large chunks of this code were written by an AI. Before you panic, rest assured that every line was hand-checked by a seasoned, battle-hardened human who has definitely read more stack traces than is healthy. The robot typed it and the human squinted at it. Together, they are unstoppable. Probably.

## ✨ Features

* **Headless Browser Automation:** Uses Selenium and Chromium to navigate the HR portal without a GUI.
* **Automated MFA Handling:** Polls a designated inbox via IMAP to pull SMS-forwarded authentication codes.
* **Telegram Remote Control:** Pause, resume, skip days, punch on demand, and get a ✅/❌ after every punch, all from your phone. No cron and no open ports.
* **Safe Scheduling:** random punch times inside your windows, daily hours kept at 8–8.25 and the week at 40–41, a 20–30 minute lunch, and an automatic pause if anything goes wrong.
* **Optional Status Display:** a Waveshare e-Paper module (SPI + `lgpio`) can show the latest punch log and network status. Not needed when you use Telegram.
* **One-File Configuration:** Everything is set in `.env`.
* **Simple Provisioning:** `setup.sh` installs Chromium and builds an isolated virtual environment. `./setup.sh --display` adds the e-Paper driver bits (only the 3 Waveshare files it needs).

## 🧰 Build list

### Hardware

| Part | Notes |
|---|---|
| **Raspberry Pi 4 Model B (1GB+)** | Current home of the bot, running headless alongside RaspAP. A Pi Zero 2 W also works (it ran this project for months), but 512MB is tight for Chromium. A Debian x86_64 box works too. |
| **microSD card, 16GB+** | A1/A2 rated if possible |
| **Official power supply** | Pi 4: 5V/3A USB-C. Pi Zero 2 W: 5V/2.5A micro-USB. Under-powered Pis cause random crashes. |
| **Waveshare 2.13" e-Paper HAT, V3** (250×122, black/white) | Optional. Must be **V3**, so check the sticker on the back of the panel. V4 and the color (B/C/D) versions use different drivers and won't work as-is. https://www.waveshare.com/2.13inch-e-paper-hat.htm |
| **Network** | Ethernet or Wi-Fi with outbound HTTPS (timeclock site, IMAP, Telegram). No inbound ports needed. |
| **Case** | Optional. Pick one with room for the HAT on the GPIO header. |

### Software and accounts

| Item | Notes |
|---|---|
| **Raspberry Pi OS Lite (64-bit)**, Bookworm or newer | Or Debian 12+ |
| **Chromium + chromedriver** | `sudo apt install chromium chromium-driver` (on older Pi OS releases the package may be `chromium-browser`) |
| **SPI enabled** | Display only: `sudo raspi-config` → Interface Options → SPI |
| **Phone with an SMS forwarding app** | Forwards the timeclock's MFA texts to an email inbox |
| **Email account with IMAP** | Gmail with an App Password works |
| **Telegram account** | For the remote control bot |

## 📁 What's in the box

| File | Purpose |
|---|---|
| `clockbot.py` | Logs in, handles MFA, and clicks Punch. One run = one punch. |
| `controller.py` | Long-running scheduler and Telegram remote. Runs `clockbot.py` for you. |
| `clockbot-controller.service` | systemd unit that keeps `controller.py` running |
| `eink-monitor.service` | systemd unit for the e-Paper display |
| `eink_monitor.py` | Drives the e-Paper display (optional) |
| `shrink.py` | Resizes `logo.png` for the display |
| `setup.sh` | Installs Chromium, builds `venv/`; `--display` adds the e-Paper bits |
| `requirements-display.txt` | Extra Python packages for the e-Paper display only |
| `.env.example` | Template for all settings |

---

## 🚀 Setup

All commands are run on the Pi. The examples assume the user is `user` and the folder is `/home/user/clock-bot`. Swap in your own values wherever you see those.

### 1. Get the code and install dependencies

```bash
git clone https://github.com/buttcoffin/Clock-Bot.git ~/clock-bot
cd ~/clock-bot
./setup.sh
```

`setup.sh` installs Chromium and chromedriver if they're missing (it asks for your sudo password for that), then builds the Python environment. If you use the e-Paper display, run `./setup.sh --display` instead. That also installs the display packages and downloads just the three Waveshare driver files this project uses.

### 2. Set up MFA forwarding

The timeclock texts you a 6-digit code, and the bot reads it from email.

1. On your phone, use an SMS forwarding app to forward the timeclock's texts to an email address, with a fixed subject such as `SMSFW`.
2. For Gmail, turn on 2-Step Verification for that account, then create an **App Password** and use it as `EMAIL_PASS`. Your normal password won't work over IMAP.

### 3. Create the Telegram bot

1. In Telegram, message **@BotFather**, send `/newbot`, and follow the prompts. Copy the **token** it gives you.
2. Open your new bot and send it any message (for example, "hi").
3. In a browser, open this URL with your token in place of `<TOKEN>` (keep the word `bot` in front):
   ```
   https://api.telegram.org/bot<TOKEN>/getUpdates
   ```
4. Find `"chat":{"id":123456789` in the response. That number is your **chat ID**.
   * If you see `"result":[]`, send the bot another message and refresh. Also make sure `controller.py` isn't already running, because it consumes the messages first.
   * Alternative: message **@userinfobot** and it replies with your ID.

> Keep the token private. Anyone who has it can talk to your bot, though the controller ignores every chat except `TELEGRAM_CHAT_ID`.

### 4. Fill in `.env`

```bash
cp .env.example .env
nano .env
```

| Variable | What it controls | Default |
|---|---|---|
| `CLOCK_USER`, `CLOCK_PASS` | Timeclock login | **required** |
| `EMAIL_USER`, `EMAIL_PASS` | Inbox the MFA SMS is forwarded to | **required** |
| `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID` | Telegram bot token and your chat ID (step 3) | **required** |
| `SE_OFFLINE` | `true` = dry run that does everything except click Punch, with no random delay | `false` |
| `CLOCK_URL` | Login page the bot opens first | `https://timeclock.com` |
| `COMPANY_LINK_TEXT` | Company link clicked after login (partial text match) | `My Company` |
| `FAVORITE_ID` | Element ID of the Time Clock Entry favorite | `FAVORITE_1` |
| `IMAP_SERVER` | Mail server for MFA codes | `imap.gmail.com` |
| `MFA_SUBJECT` | Subject line of the forwarded SMS emails | `SMSFW` |
| `PUNCH_IN` | Clock-in window. A random time inside it is picked daily. | `07:45-07:55` |
| `PUNCH_OUT` | Clock-out window. The exact time is calculated to hit the hours rules and stays inside this window whenever possible. | `16:10-16:20` |
| `LUNCH_OUT` | Lunch-out window during **standard time** (Nov–Mar). Leave blank for no lunch punches. | `11:05-11:20` |
| `LUNCH_OUT_DST` | Lunch-out window during **daylight saving time** (Mar–Nov) | `12:05-12:20` |
| `LUNCH_MIN`, `LUNCH_MAX` | Lunch length limits in minutes (unpaid), measured between the real clicks | `20`, `30` |
| `DAY_MIN_HOURS`, `DAY_MAX_HOURS` | Net hours per day (lunch excluded) | `8`, `8.25` |
| `WEEK_MAX_HOURS` | Weekly cap (Monday–Sunday). With 5 days of at least 8 h, the week lands at 40–41. | `41` |
| `PUNCH_LEAD_MIN` | How many minutes early clockbot starts (leaves room for a retry); it waits on the punch page until the exact time | `15` |
| `PUNCH_ATTEMPTS` | If something fails before the click (a page didn't load, for example), start over with a fresh browser up to this many times | `3` |
| `PUNCH_LATE_MAX_MIN` | Never click more than this many minutes after the planned time. Fail and alert instead. | `10` |
| `PUNCH_DAYS` | Days to punch | `mon,tue,wed,thu,fri` |
| `PUNCH_GRACE_MIN` | How late a punch may still start (for example, after a reboot) | `5` |
| `MAX_DELAY_SEC` | Random delay, from 1 to N seconds, used only when `clockbot.py` is run by hand or by cron. The controller uses the windows instead. | `600` |
| `PAGE_TIMEOUT_SEC` | How long to wait for page elements before giving up | `45` |
| `CHROMIUM_PATH`, `CHROMEDRIVER_PATH` | Browser and driver binaries | `/usr/bin/chromium`, `/usr/bin/chromedriver` |

> **Different timeclock site?** Element IDs such as `username`, `password`, `code`, and `btnPunch` are in `clockbot.py`. Record your own clock-in with Selenium IDE and update them to match.

### 5. Do a dry run

Set `SE_OFFLINE=true` in `.env`, then:

```bash
venv/bin/python controller.py
```

1. Telegram should say **"Clock-bot controller started… ⏸ PAUSED"**.
2. Send `/punch`. After a minute or two you should get a 🧪 reply with the log. That means it logged in and found the Punch button without clicking it.
3. If you get ❌ instead, check `punch.log`, `crash_screen.png`, and `frame_source.html` in the folder.
4. Press **Ctrl+C** to stop. Set `SE_OFFLINE=false` when you're happy.

### 6. Remove old cron jobs

If you used cron before, remove those jobs so nothing punches twice:

```bash
crontab -e
```

Delete or comment out (`#`) any `clockbot.py` lines.

### 7. Install the service

Open `clockbot-controller.service` and make sure `User=` and the three paths match your setup (`whoami` and `pwd` will tell you). Then:

```bash
sudo cp clockbot-controller.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now clockbot-controller
```

Check that it's alive:

```bash
systemctl status clockbot-controller
journalctl -u clockbot-controller -f
```

### 8. Go live

It always starts **paused** the first time. In Telegram, send `/status` to check today's plan, then `/resume`. 🎉

## ⏰ How the schedule works

* **Each work day:** clock in is random inside `PUNCH_IN` (7:45–7:55), and lunch out is random inside `LUNCH_OUT` (11:05–11:20, or 12:05–12:20 during DST).
* **Lunch length and clock out are calculated**, not just random:
  * Each day nets between `DAY_MIN_HOURS` and `DAY_MAX_HOURS` (8–8.25 h), with lunch unpaid.
  * The week never goes over `WEEK_MAX_HOURS` (41). Later days in the week leave room for at least 8 h each, so a normal 5-day week lands at 40–41 h.
  * When you `/skip` days, each remaining day still stays at 8–8.25 h.
  * Lunch is kept between `LUNCH_MIN` and `LUNCH_MAX` (20–30 min), with a minute of headroom.
  * Clock out stays inside `PUNCH_OUT` (4:10–4:20) whenever that's possible. If a late punch makes it impossible, **the hours rules win** and clock out floats slightly outside.
* **Re-solved after every punch** using the *real* click times, so a punch that lands a bit late is corrected for by the later ones.
* About `PUNCH_LEAD_MIN` minutes before each time, it starts `clockbot.py`. The bot logs in, handles MFA, and then **waits on the Punch button until the exact second** before clicking.
* Punches run one at a time, in order. Each day's net hours are saved, and `/status` shows today's plan and the week so far.
* **Retries:** if a step fails before the click, the bot starts over with a fresh browser (up to `PUNCH_ATTEMPTS` times), and the log says which step failed. Once the Punch button has been clicked, it never retries.
* **Failure safety:** the Punch button *toggles*, so one missed punch would flip every later punch that day. If a punch fails (❌), is **missed** (the Pi was off), or is **interrupted** by a reboot, the controller **pauses itself**. Check your timecard, punch manually if needed, then `/resume`.
* If the service starts mid-day, punches that already passed are skipped, not chased.

---

## 📱 Telegram commands

| Command | Effect |
|---|---|
| `/status` | Paused or running, today's punch times with ✅/⏳ status, next day, skipped days |
| `/pause`, `/resume` | Stop or restart all scheduled punches |
| `/skip` | Skip the next punch day |
| `/skip today`, `/skip tomorrow`, `/skip 2026-12-24` | Skip a specific day |
| `/unskip 2026-12-24` | Undo a skip |
| `/punch` | Punch right now, as soon as the bot reaches the page (a few minutes) |
| `/log` | Last 15 lines of `punch.log` |

* Every punch reports back ✅ (punched), 🧪 (dry run) or ❌ (failed), along with its log.
* If the Pi was off at a punch time and comes back more than `PUNCH_GRACE_MIN` minutes late, it **won't punch late**. It messages you, and you can `/punch` manually if you want.
* Pause, skips, and today's plan are saved in `state.json`, so they survive restarts. A punch that was mid-run during a restart is reported so you can check it.

## 🛠️ Day-to-day

| Task | Command |
|---|---|
| Apply `.env` or code changes | `sudo systemctl restart clockbot-controller` |
| Watch live output | `journalctl -u clockbot-controller -f` |
| Turn it off completely | `sudo systemctl disable --now clockbot-controller` |
| Changed the `.service` file | Copy it again, then `sudo systemctl daemon-reload && sudo systemctl restart clockbot-controller` |

## 🖥️ E-Paper display (optional)

Skip this section if Telegram is enough. Install with `./setup.sh --display` first. `eink_monitor.py` shows the time, network status, and the last few lines of `punch.log`. To put your logo on it, drop a `logo.png` in the folder and run `venv/bin/python shrink.py` once to resize it.

To launch it at boot, install the included service (edit `User=` and the paths first if yours differ):

```bash
sudo cp eink-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now eink-monitor
```

SPI must be enabled (`sudo raspi-config` → Interface Options → SPI).
