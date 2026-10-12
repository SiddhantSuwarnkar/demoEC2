#!/usr/bin/env bash
# ==============================================================================
# RouteIQ / StockSense - Production AWS EC2 Application Update & Redeploy Script
# File: ec2_update
# Description: Safely updates the deployed backend on an EC2 instance. Pulls latest
#              git changes, updates Python dependencies, runs Alembic migrations,
#              restarts systemd daemon, and verifies health status.
# ==============================================================================

set -eo pipefail

# ANSI Color Codes
BOLD="\033[1m"
GREEN="\033[0;32m"
CYAN="\033[0;36m"
YELLOW="\033[1;33m"
RED="\033[0;31m"
MAGENTA="\033[0;35m"
NC="\033[0m"

log_info() {
    echo -e "${CYAN}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
}

echo -e "${BOLD}${CYAN}"
cat << "EOF"
  ____             _       ___ ___  
 |  _ \ ___  _   _| |_ ___|_ _/ _ \ 
 | |_) / _ \| | | | __/ _ \| | | | |
 |  _ < (_) | |_| | ||  __/| | |_| |
 |_| \_\___/ \__,_|\__\___|___\__\_\
    StockSense EC2 Auto-Updater
EOF
echo -e "${NC}"

# Resolve script directory and application root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${SCRIPT_DIR}"

if [ -f "${SCRIPT_DIR}/app/main.py" ]; then
    APP_DIR="${SCRIPT_DIR}"
elif [ -f "${SCRIPT_DIR}/backend/backend/app/main.py" ]; then
    APP_DIR="${SCRIPT_DIR}/backend/backend"
elif [ -f "${SCRIPT_DIR}/backend/app/main.py" ]; then
    APP_DIR="${SCRIPT_DIR}/backend"
fi

cd "${APP_DIR}"
log_info "Application directory: ${BOLD}${APP_DIR}${NC}"

# Detect Python virtual environment
VENV_DIR=""
for candidate in "${APP_DIR}/.venv" "${APP_DIR}/venv" "${SCRIPT_DIR}/.venv" "${SCRIPT_DIR}/venv"; do
    if [ -f "${candidate}/bin/activate" ]; then
        VENV_DIR="${candidate}"
        break
    fi
done

if [ -z "${VENV_DIR}" ]; then
    log_error "Could not find virtual environment (.venv). Please run ./ec2_setup first."
    exit 1
fi

log_info "Using virtual environment: ${VENV_DIR}"

# ------------------------------------------------------------------------------
# 1. Git Repository Synchronization
# ------------------------------------------------------------------------------
BRANCH="${1:-}"

# Check if git repository is present
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "main")"
    TARGET_BRANCH="${BRANCH:-${CURRENT_BRANCH}}"
    
    log_info "Fetching latest code from remote for branch: ${BOLD}${TARGET_BRANCH}${NC}..."
    
    # Check for uncommitted local changes
    if ! git diff-index --quiet HEAD -- 2>/dev/null; then
        log_warn "Local unstaged modifications detected. Stashing changes before pull..."
        git stash save "Auto-stash before ec2_update on $(date '+%Y-%m-%d %H:%M:%S')"
    fi
    
    git fetch origin
    git checkout "${TARGET_BRANCH}"
    git pull origin "${TARGET_BRANCH}"
    
    LATEST_COMMIT="$(git log -1 --format="%h - %s (%cr)" 2>/dev/null || echo "Unknown")"
    log_success "Updated to commit: ${BOLD}${LATEST_COMMIT}${NC}"
else
    log_warn "Current directory is not a Git repository. Skipping git pull."
fi

# ------------------------------------------------------------------------------
# 2. Dependency Updates
# ------------------------------------------------------------------------------
if [ -f "${APP_DIR}/requirements.txt" ]; then
    log_info "Verifying and updating Python dependencies..."
    "${VENV_DIR}/bin/pip" install -r "${APP_DIR}/requirements.txt" --quiet
    log_success "Python dependencies verified and up-to-date."
fi

# Ensure gunicorn is installed for production
if ! "${VENV_DIR}/bin/pip" show gunicorn >/dev/null 2>&1; then
    "${VENV_DIR}/bin/pip" install "gunicorn>=21.2.0" --quiet
fi

# ------------------------------------------------------------------------------
# 3. Database Migrations
# ------------------------------------------------------------------------------
if [ -f "${APP_DIR}/alembic.ini" ]; then
    log_info "Executing Alembic database migrations (alembic upgrade head)..."
    "${VENV_DIR}/bin/alembic" upgrade head
    log_success "Database schema migrations applied successfully."
fi

# ------------------------------------------------------------------------------
# 4. Service File Update & Daemon Reload
# ------------------------------------------------------------------------------
if [ -f "${APP_DIR}/routeiq.service" ] && [ -w /etc/systemd/system ]; then
    log_info "Checking systemd service unit definition..."
    cp -u "${APP_DIR}/routeiq.service" /etc/systemd/system/routeiq.service 2>/dev/null || true
    systemctl daemon-reload 2>/dev/null || true
fi

# Ensure executable permissions on run script
chmod +x "${APP_DIR}/run" 2>/dev/null || true

# ------------------------------------------------------------------------------
# 5. Service Restart
# ------------------------------------------------------------------------------
log_info "Restarting routeiq systemd service..."
if command -v systemctl >/dev/null 2>&1; then
    if [ "${EUID}" -eq 0 ]; then
        systemctl restart routeiq
    else
        sudo systemctl restart routeiq
    fi
    log_success "Systemd service 'routeiq' restart triggered."
else
    log_warn "systemctl not found. Please restart your application process manually."
fi

# Reload Nginx if available
if command -v nginx >/dev/null 2>&1; then
    if [ "${EUID}" -eq 0 ]; then
        nginx -t >/dev/null 2>&1 && systemctl reload nginx 2>/dev/null || true
    else
        sudo nginx -t >/dev/null 2>&1 && sudo systemctl reload nginx 2>/dev/null || true
    fi
fi

# ------------------------------------------------------------------------------
# 6. Post-Deployment Health Check & Verification
# ------------------------------------------------------------------------------
log_info "Waiting for service to report healthy status..."
HEALTH_URL="http://127.0.0.1:8000/health/live"
IS_HEALTHY=false

for attempt in {1..15}; do
    if curl -s -f "${HEALTH_URL}" >/dev/null 2>&1; then
        IS_HEALTHY=true
        break
    fi
    echo -n "."
    sleep 2
done
echo ""

if [ "${IS_HEALTHY}" = true ]; then
    echo -e "${BOLD}${GREEN}==============================================================================${NC}"
    echo -e "${BOLD}${GREEN}  REDEPLOYMENT COMPLETE: BACKEND IS HEALTHY & SERVING TRAFFIC!${NC}"
    echo -e "${BOLD}${GREEN}==============================================================================${NC}"
    
    # Query service status summary
    if command -v systemctl >/dev/null 2>&1; then
        systemctl status routeiq --no-pager -l | head -n 12 || true
    fi
else
    echo -e "${BOLD}${RED}==============================================================================${NC}"
    echo -e "${BOLD}${RED}  WARNING: HEALTH CHECK TIMED OUT ON ${HEALTH_URL}${NC}"
    echo -e "${BOLD}${RED}==============================================================================${NC}"
    log_warn "Fetching recent journalctl logs for diagnosis:"
    if command -v journalctl >/dev/null 2>&1; then
        journalctl -u routeiq -n 25 --no-pager || true
    fi
    exit 1
fi
