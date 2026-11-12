# Clock-Bot: Automated Timecard & MFA Handler

![Python](https://img.shields.io/badge/Python-3.x-blue?logo=python&logoColor=white)
![Debian/Raspbian](https://img.shields.io/badge/OS-Raspbian%20%7C%20Debian-A81D33?logo=debian&logoColor=white)
![Selenium](https://img.shields.io/badge/Selenium-Automation-43B02A?logo=selenium&logoColor=white)

A hardware-integrated automation service designed to run on a Raspberry Pi or Debian headless server. This bot automates enterprise timecard punches using Selenium, intercepts and parses SMS-forwarded MFA tokens via Gmail IMAP, and outputs real-time status updates to a physical Waveshare e-Paper display.

## ✨ Features

* **Headless Browser Automation:** Uses Selenium and Chromium to navigate the HR portal seamlessly without a GUI.
* **Automated MFA Handling:** Bypasses manual 2FA by polling a designated Gmail inbox via IMAP to extract SMS-forwarded authentication codes.
* **Hardware Status Display:** Integrates with a Waveshare e-Paper module (via SPI and `lgpio`) for persistent, low-power visual status updates of the current punch state.
* **Bulletproof Environment Provisioning:** Features a custom shell script that dynamically checks system architecture, compiles necessary C libraries (`liblgpio`) from source, handles Debian's stripped Python packages (`python3-venv`), and builds an isolated virtual environment.

## 🧰 Hardware Requirements

* Raspberry Pi (or standard Debian x86_64 host)
* Waveshare e-Paper Display Module (https://www.waveshare.com/2.13inch-e-paper-hat.htm)
* Network connection for IMAP/HTTPS traffic

## 🚀 Installation

This project uses a robust deployment script that automatically handles OS-level dependencies, C compiler tools, and Python environment isolation. Files are pretty self explanatory. Software relies on MFA being sent to email. shrink.py will assist with creating logo.png for logo on Waveshare display. .env contains credentials for clock logins and a variable for testing. Login script can be rebuilt using your own data by recording clock in using Selenium. Highly recommend setting up cron jobs to initiate script. eink_monitor.py was setup as a service to launch at boot.

1. **Clone the repository:**
   ```bash
   git clone [https://github.com/buttcoffin/Clock-Bot.git](https://github.com/buttcoffin/Clock-Bot.git)
   cd Clock-Bot


## 📱 Remote control (Telegram) — replaces cron

`controller.py` is a single long-running service that punches at `PUNCH_TIMES` on `PUNCH_DAYS` and takes commands from your Telegram chat only. It makes outbound HTTPS calls to `api.telegram.org` only — no open ports.

1. In Telegram, message **@BotFather** → `/newbot` → copy the token into `TELEGRAM_TOKEN` in `.env`.
2. Send your new bot any message, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `message.chat.id` into `TELEGRAM_CHAT_ID`.
3. Set `PUNCH_TIMES` / `PUNCH_DAYS` in `.env`, and **remove the old cron entries** (`crontab -e`).
4. Install the service (edit the paths/user in it first):
   ```bash
   sudo cp clockbot-controller.service /etc/systemd/system/
   sudo systemctl enable --now clockbot-controller
   ```
5. The first start is **paused**. Send `/status` to check it, then `/resume`.

| Command | Effect |
|---|---|
| `/status` | Paused/running, skipped days, next punch |
| `/pause`, `/resume` | Stop / restart all scheduled punches |
| `/skip` `[today\|tomorrow\|YYYY-MM-DD]` | Skip a day (no argument = next punch day) |
| `/unskip YYYY-MM-DD` | Undo a skip |
| `/punch` | Punch now (still uses the random delay) |
| `/log` | Last 15 lines of `punch.log` |

Every punch reports ✅ / ❌ with its log output. If the controller was down at a punch time (reboot, outage) and comes back more than `PUNCH_GRACE_MIN` minutes late, it **won't punch late**. It messages you instead.
