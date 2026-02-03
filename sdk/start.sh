#!/bin/bash
################################################################################
# SDK hardwareService Start Script
################################################################################

# Determine where we are - this script should be in /sdk
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SDK="${SCRIPT_DIR}"
DEPLOY_ROOT="$(dirname "$SDK")"
VENV="${DEPLOY_ROOT}/venv"
HARDWARE_CONFIG="${DEPLOY_ROOT}/hardware-config.json"
LOG_DIR="${DEPLOY_ROOT}/logs"

# Create log directory
mkdir -p "$LOG_DIR"

# Function to log with timestamp
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "${LOG_DIR}/hardwareService_startup.log"
}

log "hardwareService Start Script"
log "============================="

# Check virtual environment exists
if [ ! -d "$VENV" ]; then
    log "ERROR: Virtual environment not found at $VENV"
    log "Please run install.sh first"
    exit 1
fi

# CRITICAL: Check hardware-config.json
if [ ! -f "$HARDWARE_CONFIG" ]; then
    log "CRITICAL: hardware-config.json NOT FOUND at $HARDWARE_CONFIG"
    exit 1
fi

# Validate JSON
if ! python3 -m json.tool "$HARDWARE_CONFIG" > /dev/null 2>&1; then
    log "ERROR: hardware-config.json has invalid JSON syntax"
    exit 1
fi
log "✓ hardware-config.json valid"

# Activate venv
source "$VENV/bin/activate"

# Set PYTHONPATH
export PYTHONPATH="${DEPLOY_ROOT}:${PYTHONPATH}"

# Set log directory for SDK logging
export USS_LOG_DIR="${LOG_DIR}"

# Launch hardwareService
log "Launching hardwareService..."

# Check if tmux is available
if command -v tmux > /dev/null 2>&1; then
    # Kill existing session if it exists
    tmux kill-session -t hardwareService 2>/dev/null || true
    
    # Start hardwareService in tmux
    tmux new-session -d -s hardwareService -c "${DEPLOY_ROOT}" \
        "source ${VENV}/bin/activate && export PYTHONPATH=${DEPLOY_ROOT} && export USS_LOG_DIR=${LOG_DIR} && python3 -m sdk.hardwareService > ${LOG_DIR}/hardwareService.log 2>&1"
    log "✓ hardwareService started in tmux session 'hardwareService'"
    
else
    # Fallback: Simple background process
    cd "${DEPLOY_ROOT}"
    nohup python3 -m sdk.hardwareService > "${LOG_DIR}/hardwareService.log" 2>&1 &
    echo $! > "${DEPLOY_ROOT}/hardwareService.pid"
    log "✓ hardwareService started (PID: $!)"
fi

log "hardwareService started successfully"
log "Logs: ${LOG_DIR}/hardwareService.log"
log ""
log "To attach to session: tmux attach -t hardwareService"
log "To detach from session: Ctrl+B, then D"
