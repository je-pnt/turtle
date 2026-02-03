#!/bin/bash
################################################################################
# SDK Install Script - Prepare SDK environment
################################################################################

# Determine where we are - this script should be in /sdk
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SDK="${SCRIPT_DIR}"
DEPLOY_ROOT="$(dirname "$SDK")"
VENV="${DEPLOY_ROOT}/venv"
LOG_DIR="${DEPLOY_ROOT}/logs"

# Create log directory
mkdir -p "$LOG_DIR"

# Function to log with timestamp
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "${LOG_DIR}/sdk_install.log"
}

log "SDK Install Script"
log "=================="

# CRITICAL: Unset PYTHONPATH to avoid import conflicts during venv creation
unset PYTHONPATH
export PYTHONPATH=""

# CRITICAL: Change to parent directory to avoid sdk/logging shadowing built-in logging
cd "$DEPLOY_ROOT"

# Check Python version
PYTHON_VERSION=$(python3 --version 2>&1 | grep -oP '\d+\.\d+' || echo "0.0")
MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)

if [ "$MAJOR" -lt 3 ] || { [ "$MAJOR" -eq 3 ] && [ "$MINOR" -lt 8 ]; }; then
    log "ERROR: Python 3.8+ required (found $PYTHON_VERSION)"
    exit 1
fi
log "✓ Python $PYTHON_VERSION"

# Create virtual environment if it doesn't exist
if [ ! -d "$VENV" ]; then
    log "Creating virtual environment at ${VENV}..."
    python3 -m venv "$VENV" || {
        log "ERROR: Failed to create virtual environment"
        exit 1
    }
else
    log "✓ Using existing virtual environment at ${VENV}"
fi

# Activate venv
source "$VENV/bin/activate" || {
    log "ERROR: Failed to activate virtual environment"
    exit 1
}

log "Installing/updating SDK dependencies..."
pip install -q --upgrade pip wheel setuptools

# Install SDK dependencies
if [ -f "${SDK}/requirements.txt" ]; then
    log "Installing from ${SDK}/requirements.txt..."
    pip install -r "${SDK}/requirements.txt"
    log "✓ SDK dependencies installed"
else
    log "ERROR: ${SDK}/requirements.txt not found"
    exit 1
fi

log ""
log "SDK installation complete!"
log "Virtual environment: ${VENV}"
log "Python: $(which python3)"
log ""
log "Installed packages:"
pip list | grep -E "(pynng|pyserial|nats-py|numpy|pandas|pyproj|pyyaml)"
