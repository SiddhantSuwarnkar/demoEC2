#!/usr/bin/env bash
# ==============================================================================
# RouteIQ / StockSense - Production Backend Application Launcher
# File: run
# Description: Starts the FastAPI ASGI backend with environment verification,
#              virtual environment activation, and database migration checks.
# ==============================================================================

set -eo pipefail

# ANSI Color Codes
BOLD="\033[1m"
GREEN="\033[0;32m"
CYAN="\033[0;36m"
YELLOW="\033[1;33m"
RED="\033[0;31m"
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

# Resolve directory of this script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Resolve backend application root
APP_DIR=""
if [ -f "${SCRIPT_DIR}/app/main.py" ]; then
    APP_DIR="${SCRIPT_DIR}"
elif [ -f "${SCRIPT_DIR}/backend/app/main.py" ]; then
    APP_DIR="${SCRIPT_DIR}/backend"
elif [ -f "${SCRIPT_DIR}/backend/backend/app/main.py" ]; then
    APP_DIR="${SCRIPT_DIR}/backend/backend"
elif [ -f "$(pwd)/app/main.py" ]; then
    APP_DIR="$(pwd)"
else
    log_error "Could not locate app/main.py. Please run this script from the backend directory."
    exit 1
fi

cd "${APP_DIR}"
log_info "Working directory set to: ${APP_DIR}"

# Locate and activate Python virtual environment
VENV_PATH=""
for candidate in "${APP_DIR}/.venv" "${APP_DIR}/venv" "${SCRIPT_DIR}/.venv" "${SCRIPT_DIR}/venv" "/home/ubuntu/.venv" "/home/ec2-user/.venv"; do
    if [ -f "${candidate}/bin/activate" ]; then
        VENV_PATH="${candidate}"
        break
    fi
done

if [ -n "${VENV_PATH}" ]; then
    log_info "Activating Python virtual environment: ${VENV_PATH}"
    # shellcheck disable=SC1090
    source "${VENV_PATH}/bin/activate"
else
    log_warn "No local virtual environment found. Attempting to use system python3."
fi

# Verify Python runtime
if ! command -v python3 >/dev/null 2>&1; then
    log_error "Python 3 is not installed or not in PATH."
    exit 1
fi

# Load environment configuration (.env)
if [ -f "${APP_DIR}/.env" ]; then
    log_info "Loading environment variables from ${APP_DIR}/.env"
    set -a
    # shellcheck disable=SC1091
    source "${APP_DIR}/.env"
    set +a
elif [ -f "${SCRIPT_DIR}/.env" ]; then
    log_info "Loading environment variables from ${SCRIPT_DIR}/.env"
    set -a
    # shellcheck disable=SC1091
    source "${SCRIPT_DIR}/.env"
    set +a
else
    log_warn "No .env file found. Using default environment variables."
fi

# Default configuration settings
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
CPU_CORES="$(nproc 2>/dev/null || echo 1)"
DEFAULT_WORKERS=$(( 2 * CPU_CORES + 1 ))
# Cap default workers at 4 for standard EC2 t2/t3.micro/small instances to prevent OOM
if [ "${DEFAULT_WORKERS}" -gt 4 ]; then
    DEFAULT_WORKERS=4
fi
WORKERS="${WORKERS:-${DEFAULT_WORKERS}}"
LOG_LEVEL="${LOG_LEVEL:-info}"
LOG_LEVEL_LOWER="$(echo "${LOG_LEVEL}" | tr '[:upper:]' '[:lower:]')"

# Parse optional command line flags
EXTRA_ARGS=()
RELOAD=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --host)
            HOST="$2"
            shift 2
            ;;
        --port)
            PORT="$2"
            shift 2
            ;;
        --workers)
            WORKERS="$2"
            shift 2
            ;;
        --reload)
            RELOAD=true
            shift
            ;;
        --migrate)
            log_info "Running database migrations..."
            alembic upgrade head
            shift
            ;;
        --seed)
            log_info "Seeding database with demo records..."
            python3 -c "from app.db.session import SessionLocal; from app.seed.demo_data import seed_demo_data; db = SessionLocal(); print(seed_demo_data(db)); db.close()"
            shift
            ;;
        --help|-h)
            echo -e "${BOLD}Usage:${NC} ./run [options]"
            echo ""
            echo "Options:"
            echo "  --host HOST          Bind host address (default: 0.0.0.0)"
            echo "  --port PORT          Bind port number (default: 8000)"
            echo "  --workers N          Number of worker processes (default: ${DEFAULT_WORKERS})"
            echo "  --reload             Enable auto-reload for local development"
            echo "  --migrate            Run Alembic database migrations before starting"
            echo "  --seed               Seed demo data into the database"
            echo "  --help, -h           Show this help message"
            exit 0
            ;;
        *)
            EXTRA_ARGS+=("$1")
            shift
            ;;
    esac
done

# Run pending database schema migrations
if command -v alembic >/dev/null 2>&1; then
    log_info "Checking database schema migrations (alembic upgrade head)..."
    alembic upgrade head || {
        log_warn "Alembic migration encountered a warning or error. Check database status."
    }
else
    log_warn "Alembic CLI not found in active PATH. Skipping automated migration."
fi

# Banner
echo -e "${BOLD}======================================================${NC}"
echo -e "${GREEN}  RouteIQ / StockSense Backend API Service${NC}"
echo -e "  Host:         ${BOLD}${HOST}${NC}"
echo -e "  Port:         ${BOLD}${PORT}${NC}"
echo -e "  Workers:      ${BOLD}${WORKERS}${NC}"
echo -e "  Environment:  ${BOLD}${ENVIRONMENT:-production}${NC}"
echo -e "  Directory:    ${BOLD}${APP_DIR}${NC}"
echo -e "${BOLD}======================================================${NC}"

# Start server using Gunicorn (production) or Uvicorn
if [ "${RELOAD}" = true ]; then
    log_info "Starting Uvicorn in development mode with --reload..."
    exec uvicorn app.main:app \
        --host "${HOST}" \
        --port "${PORT}" \
        --reload \
        --log-level "${LOG_LEVEL_LOWER}" \
        "${EXTRA_ARGS[@]}"
elif command -v gunicorn >/dev/null 2>&1; then
    log_info "Starting Gunicorn with ${WORKERS} Uvicorn worker(s)..."
    exec gunicorn app.main:app \
        --workers "${WORKERS}" \
        --worker-class uvicorn.workers.UvicornWorker \
        --bind "${HOST}:${PORT}" \
        --timeout 120 \
        --keep-alive 5 \
        --access-logfile - \
        --error-logfile - \
        --log-level "${LOG_LEVEL_LOWER}" \
        "${EXTRA_ARGS[@]}"
else
    log_info "Starting Uvicorn standalone with ${WORKERS} worker(s)..."
    exec uvicorn app.main:app \
        --host "${HOST}" \
        --port "${PORT}" \
        --workers "${WORKERS}" \
        --log-level "${LOG_LEVEL_LOWER}" \
        --proxy-headers \
        --forwarded-allow-ips "*" \
        "${EXTRA_ARGS[@]}"
fi
