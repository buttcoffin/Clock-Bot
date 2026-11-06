import os, sys, time, random, imaplib, email, re, quopri
from datetime import datetime
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, StaleElementReferenceException

# ==========================================
# CONFIGURATION & LOGGING
# ==========================================
load_dotenv("/home/user/clock-bot/.env")

CLOCK_USER = os.getenv("CLOCK_USER")
CLOCK_PASS = os.getenv("CLOCK_PASS")
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")

IMAP_SERVER = "imap.gmail.com"
SE_OFFLINE = os.getenv("SE_OFFLINE", "false").lower() == "true"

sys.stdout = open('/home/user/clock-bot/punch.log', 'a', buffering=1)
sys.stderr = sys.stdout

# ==========================================
# MFA LOGIC (UNSEEN SMS FORWARD)
# ==========================================

def get_mfa_code():
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL_USER, EMAIL_PASS)
        
        for attempt in range(10):
            # 1. Moved select() inside the loop to refresh mailbox state
            mail.select('INBOX')

            # 2. Fixed search syntax by separating arguments and dropping outer parentheses
            status, messages = mail.search(None, 'UNSEEN', 'SUBJECT', '"SMSFW"')

            if messages[0]:
                current_ids = messages[0].split()
                latest_id = current_ids[-1] 

                res, msg_data = mail.fetch(latest_id, "(RFC822)")

                for response_part in msg_data:
                    if isinstance(response_part, tuple):
                        msg = email.message_from_bytes(response_part[1])
                        raw_content = ""

                        for part in msg.walk():
                            if part.get_content_maintype() == 'multipart': continue
                            
                            # decode=True already handles Quoted-Printable/Base64 unpacking
                            payload = part.get_payload(decode=True)
                            if payload:
                                # 3. Removed redundant quopri decoding
                                decoded = payload.decode('utf-8', errors='ignore')
                                raw_content += decoded

                        code_match = re.search(r'\b(\d{6})\b', raw_content)
                        if code_match:
                            return code_match.group(1)
            time.sleep(10)

        return None
    except Exception as e:
        print(f"IMAP Error: {e}")
        return None

# ==========================================
# MAIN BOT LOGIC
# ==========================================
def run_punch():
    # --- THE SELF-CLEANING SWEEP ---
    # Force-kill any lingering chromedriver or chromium instances to free up RAM
    os.system("pkill -f chromedriver > /dev/null 2>&1")
    os.system("pkill -f chromium > /dev/null 2>&1")
    time.sleep(2) # Give the OS a brief second to flush the memory

    if SE_OFFLINE:
        print("WARNING: SE_OFFLINE is true.\nThis is a dry run.")
    else:
        delay = random.randint(1, 600)
        time.sleep(delay)
    
    options = Options()
    options.binary_location = "/usr/bin/chromium" 
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--window-size=1280x720")
    options.add_argument("--disable-dev-shm-usage")
    options.page_load_strategy = 'eager'
    
    service = Service("/usr/bin/chromedriver")
    driver = webdriver.Chrome(service=service, options=options)
    wait = WebDriverWait(driver, 45) 

    try:
        driver.get("https://timeclock.com")
        time.sleep(15)

        wait.until(EC.presence_of_element_located((By.ID, "username"))).send_keys(CLOCK_USER)
        driver.find_element(By.ID, "password").send_keys(CLOCK_PASS)
        driver.find_element(By.NAME, "action").click()

        # --- CONDITIONAL MFA BRANCH ---
        try:
            # Use a dedicated, shorter 10-second wait. 
            # If we used the global 45-second wait, the bot would sit frozen for 45 seconds every day MFA was skipped.
            mfa_field = WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "code")))
            #print("MFA DETECTED. PULLING CODE.")

            mfa = get_mfa_code()
            if not mfa:
                raise Exception("ERROR: New MFA code never arrived!")

            mfa_field.send_keys(mfa)
            driver.find_element(By.NAME, "action").click()
            time.sleep(10)
            
        except TimeoutException:
            # If the 10 seconds pass and no "code" box appears, Python executes this block instead of crashing.
            time.sleep(10)

        # --- RESUME NORMAL NAVIGATION ---
        #print("Hitting company link.. ")
        wait.until(EC.element_to_be_clickable((By.PARTIAL_LINK_TEXT, "My Company"))).click()
        time.sleep(15)

        #print("Hitting favorite..")
        #wait.until(EC.element_to_be_clickable((By.LINK_TEXT, "Time Clock Entry"))).click()
        wait.until(EC.element_to_be_clickable((By.ID, "nav-menu-button"))).click()
        
        #print("Hitting time clock entry..")
        time.sleep(10)
        fav_btn = wait.until(EC.presence_of_element_located((By.ID, "FAVORITE_1")))
        driver.execute_script("arguments[0].click();", fav_btn)
        time.sleep(5)

        #print("Opening content frame..")
        wait.until(EC.frame_to_be_available_and_switch_to_it((By.ID, "ContentFrame")))

        for attempt in range(3):
            try:
                punch_btn = wait.until(EC.element_to_be_clickable((By.ID, "btnPunch")))
                
                if SE_OFFLINE:
                    print("SE_OFFLINE ACTIVE\nSafely skipping the final punch.")
                else:
                    punch_btn.click()
                    punch_time = datetime.now().strftime("%-I:%M %p")
                    print(f"✅ CLOCK PUNCHED AT {punch_time}!")
                break
                
            except StaleElementReferenceException:
                print(f"DOM Refresh detected inside frame!\nRetrying... (Attempt {attempt + 1}/3)")
                time.sleep(1)
                
        driver.switch_to.default_content()

    except Exception as e:
        with open("/home/user/clock-bot/frame_source.html", "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        driver.save_screenshot("/home/user/clock-bot/crash_screen.png")
        print(f"CRITICAL ERROR: {e}")
    finally:
        driver.quit()

if __name__ == "__main__":
    run_punch()
