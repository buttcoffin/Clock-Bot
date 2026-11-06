import os, time, subprocess, logging, random
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
from waveshare_epd import epd2in13_V3

# Define directories and file names
script_dir = os.path.dirname(os.path.abspath(__file__))
LOG_IMAGE_FILE = os.path.join(script_dir, "logo.png")
LOG_TEXT_FILE = os.path.join(script_dir, "punch.log")

def get_active_interface():
    """Dynamically checks the Linux routing table for the active internet gateway."""
    try:
        route_output = subprocess.check_output("ip route show default", shell=True).decode()
        if "dev " in route_output:
            active_iface = route_output.split("dev ")[1].split(" ")[0]
            return active_iface
        return "NONE"
    except Exception:
        return "NONE"

def check_internet():
    """Hardware-agnostic check to see if packets can actually reach the web."""
    # Cloudflare, Google, Quad9, and OpenDNS
    test_ips = ['1.1.1.1', '8.8.8.8', '9.9.9.9', '208.67.222.222']
    target = random.choice(test_ips)
    
    try:
        response = subprocess.run(
            ['ping', '-c', '1', '-W', '2', target],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        return response.returncode == 0
    except Exception:
        return False

def get_last_lines(filename, line_count=5):
    if not os.path.exists(filename):
        return ["Log file not found..."]
    with open(filename, 'r') as f:
        lines = f.readlines()
        return [line.strip()[:35] for line in lines[-line_count:]]

def update_display():
    try:
        epd = epd2in13_V3.EPD()
        epd.init()

        # 1. Start with a simple 1-bit white canvas (255 = white, 0 = black)
        main_canvas = Image.new('1', (epd.height, epd.width), 255)
        draw_main = ImageDraw.Draw(main_canvas)
        font = ImageFont.load_default()

        logo_final = None
        x_pos = 0
        y_pos = 0

        # 2. Process the logo once before the loop
        if os.path.exists(LOG_IMAGE_FILE):
            with Image.open(LOG_IMAGE_FILE) as logo_orig:
                rgba = logo_orig.convert("RGBA")
                white_bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
                white_bg.paste(rgba, (0, 0), rgba)
                logo_bw = white_bg.convert('1')

                baseheight = 85
                h_percent = (baseheight / float(logo_bw.size[1]))
                w_size = int((float(logo_bw.size[0]) * float(h_percent)))

                logo_final = logo_bw.resize((w_size, baseheight), Image.Resampling.NEAREST)
                x_pos = epd.height - logo_final.size[0] - 5
                y_pos = 25
        else:
            logging.info("Logo image not found.")

        # Display the initial blank/white base frame (rotated for your hardware mount)
        epd.displayPartBaseImage(epd.getbuffer(main_canvas.rotate(180)))

        refresh_counter = 0
        last_mod_time = 0
        last_screen_update = time.time() - 60

        while True:
            current_time = time.time()

            try:
                current_mod_time = os.path.getmtime(LOG_TEXT_FILE)
            except FileNotFoundError:
                current_mod_time = last_mod_time

            file_changed = current_mod_time > last_mod_time
            time_elapsed = (current_time - last_screen_update) >= 60

            if file_changed or time_elapsed:
                draw_main.rectangle((0, 0, epd.height, epd.width), fill=255)

                if logo_final:
                    main_canvas.paste(logo_final, (x_pos, y_pos))

                # Draw Header
                now = datetime.now().strftime("%A %-I:%M %p")
                draw_main.text((5, 5), f"{now}", font=font, fill=0)

                # Dynamically check internet and active interface
                has_internet = check_internet()
                active_iface = get_active_interface()

                # Draw Dual-WAN Wifi Badge
                if has_internet and active_iface != "NONE":
                    display_name = active_iface.upper()
                    if len(display_name) > 5:
                        display_name = display_name[:5]
                    
                    badge_text = f"[{display_name}: OK]"
                    
                    # 1. Ask Pillow exactly how many pixels wide this specific string is
                    try:
                        text_width = int(draw_main.textlength(badge_text, font=font))
                    except AttributeError:
                        text_width = font.getsize(badge_text)[0]
                    
                    # 2. Subtract text width from total screen width, minus a 5px margin
                    right_aligned_x = epd.height - text_width - 5
                    draw_main.text((right_aligned_x, 5), badge_text, font=font, fill=0)
                
                else:
                    # 3. The Fallback for when the network completely drops
                    draw_main.rectangle((180, 3, 245, 17), fill=0)
                    draw_main.text((185, 5), "NO NET!", font=font, fill=255)

                draw_main.line((5, 20, 245, 20), fill=0)

                # Draw Log Lines
                log_lines = get_last_lines(LOG_TEXT_FILE, 5)
                y_offset = 25
                for line in log_lines:
                    draw_main.text((5, y_offset), line, font=font, fill=0)
                    y_offset += 15

                # Final update logic
                epd_buffer = epd.getbuffer(main_canvas.rotate(180))
                refresh_counter += 1

                if refresh_counter % 30 == 0:
                    epd.init()
                    epd.display(epd_buffer)
                    epd.displayPartBaseImage(epd_buffer)
                else:
                    epd.displayPartial(epd_buffer)

                last_mod_time = current_mod_time
                last_screen_update = current_time

            time.sleep(3)

    except IOError as e:
        logging.info(e)
    except KeyboardInterrupt:
        logging.info("ctrl + c:")
        epd2in13_V3.epdconfig.module_exit(cleanup=True)
        exit()

if __name__ == '__main__':
    update_display()
