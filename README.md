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

