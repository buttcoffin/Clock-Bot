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
* **Safe Scheduling:** Starts paused, never punches late after a reboot or outage, and remembers skipped days.
* **Hardware Status Display:** Waveshare e-Paper module (SPI + `lgpio`) shows the latest punch log and network status.
* **One-File Configuration:** Everything is set in `.env`.
* **Bulletproof Environment Provisioning:** `setup.sh` installs OS packages, compiles `liblgpio` from source if it's missing, and builds an isolated virtual environment.

## 🧰 Requirements

* Raspberry Pi (or a standard Debian x86_64 host)
* Chromium and chromedriver (`sudo apt install chromium chromium-driver`)
* Waveshare 2.13" e-Paper HAT, optional: https://www.waveshare.com/2.13inch-e-paper-hat.htm
* A phone that forwards the timeclock's MFA SMS to an email inbox
* A Telegram account

## 📁 What's in the box

| File | Purpose |
|---|---|
| `clockbot.py` | Logs in, handles MFA, and clicks Punch. One run = one punch. |
| `controller.py` | Long-running scheduler and Telegram remote. Runs `clockbot.py` for you. |
| `clockbot-controller.service` | systemd unit that keeps `controller.py` running |
| `eink_monitor.py` | Drives the e-Paper display (optional) |
| `shrink.py` | Resizes `logo.png` for the display |
| `setup.sh` | Installs dependencies and builds `venv/` |
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

`setup.sh` may ask for your sudo password to install packages.

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
| `PUNCH_TIMES` | 24-hour punch times, comma-separated | `08:00,17:00` |
| `PUNCH_DAYS` | Days to punch | `mon,tue,wed,thu,fri` |
| `PUNCH_GRACE_MIN` | How late a punch may still fire (for example, after a reboot) | `5` |
| `MAX_DELAY_SEC` | Random delay before each punch, from 1 to N seconds (`0` = none) | `600` |
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

It always starts **paused** the first time. In Telegram, send `/status` to check the schedule, then `/resume`. 🎉

---

## 📱 Telegram commands

| Command | Effect |
|---|---|
| `/status` | Paused or running, skipped days, next punch |
| `/pause`, `/resume` | Stop or restart all scheduled punches |
| `/skip` | Skip the next punch day |
| `/skip today`, `/skip tomorrow`, `/skip 2026-12-24` | Skip a specific day |
| `/unskip 2026-12-24` | Undo a skip |
| `/punch` | Punch right now (still uses the random delay) |
| `/log` | Last 15 lines of `punch.log` |

* Every punch reports back ✅ (punched), 🧪 (dry run) or ❌ (failed), along with its log.
* If the Pi was off at a punch time and comes back more than `PUNCH_GRACE_MIN` minutes late, it **won't punch late**. It messages you, and you can `/punch` manually if you want.
* Pause, skips, and completed punches are saved in `state.json`, so they survive restarts.

## 🛠️ Day-to-day

| Task | Command |
|---|---|
| Apply `.env` or code changes | `sudo systemctl restart clockbot-controller` |
| Watch live output | `journalctl -u clockbot-controller -f` |
| Turn it off completely | `sudo systemctl disable --now clockbot-controller` |
| Changed the `.service` file | Copy it again, then `sudo systemctl daemon-reload && sudo systemctl restart clockbot-controller` |

## 🖥️ E-Paper display (optional)

`eink_monitor.py` shows the time, network status, and the last few lines of `punch.log`. To put your logo on it, drop a `logo.png` in the folder and run `venv/bin/python shrink.py` once to resize it.

To launch it at boot, create `/etc/systemd/system/eink-monitor.service`:

```ini
[Unit]
Description=Clock-bot e-Paper display
After=multi-user.target

[Service]
User=user
WorkingDirectory=/home/user/clock-bot
ExecStart=/home/user/clock-bot/venv/bin/python /home/user/clock-bot/eink_monitor.py
Restart=always

[Install]
WantedBy=multi-user.target
```

Then enable it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now eink-monitor
```

SPI must be enabled (`sudo raspi-config` → Interface Options → SPI).
