#!/bin/bash
# Usage: ./setup.sh            core install (Chromium bot + Telegram controller)
#        ./setup.sh --display  also install the Waveshare e-Paper display bits
set -e

WITH_DISPLAY=0
[ "$1" = "--display" ] && WITH_DISPLAY=1

echo "Checking system-level dependencies..."

PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
MISSING_PACKAGES=""

# venv module (Debian ships it separately)
if ! python3 -c "import venv, ensurepip" &> /dev/null; then
    MISSING_PACKAGES+="python${PY_VER}-venv "
fi

# Headless browser + driver used by clockbot.py
if [ ! -x /usr/bin/chromium ] && [ ! -x /usr/bin/chromium-browser ]; then
    MISSING_PACKAGES+="chromium "
fi
if ! command -v chromedriver &> /dev/null; then
    MISSING_PACKAGES+="chromium-driver "
fi

if [ "$WITH_DISPLAY" = 1 ]; then
    # Compile tools for liblgpio, plus wget/unzip for downloads
    if ! command -v swig &> /dev/null || ! command -v unzip &> /dev/null; then
        MISSING_PACKAGES+="swig build-essential python3-dev unzip "
    fi
    if ! command -v wget &> /dev/null; then
        MISSING_PACKAGES+="wget "
    fi
fi

if [ -n "$MISSING_PACKAGES" ]; then
    echo "Missing required apt packages: $MISSING_PACKAGES"
    echo "Attempting to install (this will prompt for your sudo password)..."
    sudo apt-get update
    sudo apt-get install -y $MISSING_PACKAGES
    echo "Apt dependencies successfully installed."
fi

if [ "$WITH_DISPLAY" = 1 ] && [ ! -f "/usr/local/include/lgpio.h" ] && [ ! -f "/usr/include/lgpio.h" ]; then
    echo "Missing underlying liblgpio C library. Compiling from source..."
    wget -q https://github.com/joan2937/lg/archive/master.zip -O lg-master.zip
    unzip -q lg-master.zip
    (cd lg-master && make && sudo make install)
    sudo ldconfig
    rm -rf lg-master lg-master.zip
    echo "liblgpio C library successfully installed."
fi

echo "System dependencies are all present."

echo "Building Python virtual environment..."
python3 -m venv venv
source venv/bin/activate

echo "Installing Python packages from requirements.txt..."
pip install -q -r requirements.txt

if [ "$WITH_DISPLAY" = 1 ]; then
    echo "Installing display packages..."
    pip install -q -r requirements-display.txt

    # Waveshare e-Paper driver: fetch only the 3 files this project uses
    # instead of pip-cloning Waveshare's entire (very large) repository.
    WAVESHARE_REF="${WAVESHARE_REF:-master}"
    WAVESHARE_URL="https://raw.githubusercontent.com/waveshareteam/e-Paper/${WAVESHARE_REF}/RaspberryPi_JetsonNano/python/lib/waveshare_epd"
    EPD_DIR="$(python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')/waveshare_epd"
    if [ -f "$EPD_DIR/epd2in13_V3.py" ]; then
        echo "Waveshare e-Paper driver already installed."
    else
        echo "Downloading Waveshare e-Paper driver (3 files)..."
        mkdir -p "$EPD_DIR"
        for f in __init__.py epdconfig.py epd2in13_V3.py; do
            wget -q -O "$EPD_DIR/$f" "$WAVESHARE_URL/$f" || { rm -rf "$EPD_DIR"; echo "Failed to download $f"; exit 1; }
        done
        echo "Waveshare driver installed to $EPD_DIR"
    fi
fi

echo "Build complete."
