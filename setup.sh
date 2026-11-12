#!/bin/bash
# Stop execution immediately if any command fails
set -e

echo "Checking system-level dependencies..."

# Dynamically detect the current Python 3 version
PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
VENV_PACKAGE="python${PY_VER}-venv"
PIP_PACKAGE="python3-pip"

MISSING_PACKAGES=""

# Check for venv module
if ! python3 -c "import venv" &> /dev/null; then
    MISSING_PACKAGES="$VENV_PACKAGE "
fi

# Check for system pip
if ! command -v pip3 &> /dev/null; then
    MISSING_PACKAGES+="$PIP_PACKAGE "
fi

# Check for SWIG and compile tools (added wget and unzip for downloading the C library)
if ! command -v swig &> /dev/null || ! command -v unzip &> /dev/null; then
    MISSING_PACKAGES+="swig build-essential python3-dev wget unzip "
fi

# wget is needed for the Waveshare driver download
if ! command -v wget &> /dev/null && [[ "$MISSING_PACKAGES" != *wget* ]]; then
    MISSING_PACKAGES+="wget "
fi

# Install missing apt packages if necessary
if [ -n "$MISSING_PACKAGES" ]; then
    echo "Missing required apt packages: $MISSING_PACKAGES"
    echo "Attempting to install (this will prompt for your sudo password)..."
    
    sudo apt-get update
    sudo apt-get install -y $MISSING_PACKAGES
    
    echo "Apt dependencies successfully installed."
fi

# Check if the underlying liblgpio C library is installed on the OS by looking for its header file
if [ ! -f "/usr/local/include/lgpio.h" ] && [ ! -f "/usr/include/lgpio.h" ]; then
    echo "Missing underlying liblgpio C library. Compiling from source..."
    
    # Download the official C library source
    wget -q https://github.com/joan2937/lg/archive/master.zip -O lg-master.zip
    unzip -q lg-master.zip
    
    # Compile and install
    cd lg-master
    make
    sudo make install
    
    # Update the OS shared library cache so the compiler can find it
    sudo ldconfig
    
    # Return to the previous directory and clean up
    cd ..
    rm -rf lg-master lg-master.zip
    
    echo "liblgpio C library successfully installed."
fi

echo "System dependencies are all present."

echo "Building Python virtual environment..."
python3 -m venv venv

echo "Activating virtual environment..."
source venv/bin/activate

echo "Installing Python packages from requirements.txt..."
pip install -r requirements.txt

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

echo "Build complete. Run 'source venv/bin/activate' to use it."
