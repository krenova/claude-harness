#!/bin/bash
set -e

echo "=== Claude Harness Linux Installer ==="

# Detect Debian/Ubuntu
if command -v apt-get &> /dev/null; then
    echo "Detected Debian/Ubuntu-based system"

    # Install Python 3 if not present
    if ! command -v python3 &> /dev/null; then
        echo "Installing Python 3..."
        sudo apt-get update
        sudo apt-get install -y python3 python3-pip python3-venv
    else
        echo "Python 3 is already installed"
    fi

    # Install pip if not present
    if ! command -v pip3 &> /dev/null; then
        echo "Installing pip..."
        sudo apt-get update
        sudo apt-get install -y python3-pip
    else
        echo "pip is already installed"
    fi

# Detect RHEL/CentOS/Fedora
elif command -v yum &> /dev/null; then
    echo "Detected RHEL/CentOS/Fedora-based system"

    if ! command -v python3 &> /dev/null; then
        echo "Installing Python 3..."
        sudo yum install -y python3 python3-pip
    else
        echo "Python 3 is already installed"
    fi

# Detect Arch Linux
elif command -v pacman &> /dev/null; then
    echo "Detected Arch Linux-based system"

    if ! command -v python3 &> /dev/null; then
        echo "Installing Python 3..."
        sudo pacman -Sy python python-pip
    else
        echo "Python 3 is already installed"
    fi

else
    echo "ERROR: Unsupported Linux distribution. Please install Python 3 manually."
    exit 1
fi

# Verify Python installation
PYTHON_VERSION=$(python3 --version 2>&1 | cut -d' ' -f2 | cut -d'.' -f1,2)
REQUIRED_VERSION="3.10"

if [ "$(printf '%s\n' "$REQUIRED_VERSION" "$PYTHON_VERSION" | sort -V | head -n1)" != "$REQUIRED_VERSION" ]; then
    echo "ERROR: Python 3.10 or higher is required. Found: $PYTHON_VERSION"
    exit 1
fi

echo "Python version: $PYTHON_VERSION"

# Install claude-harness
echo "Installing claude-harness..."
pip3 install --user claude-harness

# Add pip binaries to PATH if not already there
SHELL_RC="$HOME/.bashrc"
if [ -f "$SHELL_RC" ]; then
    if ! grep -q "\$HOME/.local/bin" "$SHELL_RC"; then
        echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$SHELL_RC"
        echo "Added ~/.local/bin to PATH in $SHELL_RC"
    fi
fi

echo ""
echo "=== Installation Complete ==="
echo ""
echo "Please restart your shell or run:"
echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
echo ""
echo "Then verify the installation with:"
echo "  harness --version"
