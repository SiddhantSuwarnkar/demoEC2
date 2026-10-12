#!/usr/bin/env bash
# ==============================================================================
# RouteIQ / StockSense - Production AWS EC2 Initial Setup & Provisioning Script
# File: ec2_setup
# Description: Automated, production-grade provisioning script for AWS EC2 instances
#              (Ubuntu 22.04/24.04 or Amazon Linux 2023). Installs system dependencies,
#              Python 3.11+, virtual environment, PostgreSQL, Nginx reverse proxy,
#              configures environment variables, systemd daemon, and verifies health.
# ==============================================================================

set -euo pipefail

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

log_step() {
    echo -e "\n${BOLD}${MAGENTA}==> Step $1: $2${NC}"
}

# ------------------------------------------------------------------------------
# 1. Pre-flight Checks & Privilege Verification
# ------------------------------------------------------------------------------
echo -e "${BOLD}${CYAN}"
cat << "EOF"
  ____             _       ___ ___  
 |  _ \ ___  _   _| |_ ___|_ _/ _ \ 
 | |_) / _ \| | | | __/ _ \| | | | |
 |  _ < (_) | |_| | ||  __/| | |_| |
 |_| \_\___/ \__,_|\__\___|___\__\_\
   StockSense EC2 Provisioning
EOF
echo -e "${NC}"

if [ "${EUID}" -ne 0 ]; then
    log_warn "This script requires superuser privileges for system package installation."
    log_info "Re-running script with sudo..."
    exec sudo bash "$0" "$@"
fi

# Detect non-root real user and home directory
REAL_USER="${SUDO_USER:-$(logname 2>/dev/null || echo "ubuntu")}"
REAL_GROUP="$(id -gn "${REAL_USER}" 2>/dev/null || echo "${REAL_USER}")"
USER_HOME="$(eval echo ~"${REAL_USER}")"

log_info "Target deployment user: ${BOLD}${REAL_USER}${NC} (Group: ${REAL_GROUP}, Home: ${USER_HOME})"

# Resolve backend application directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${SCRIPT_DIR}"

if [ -f "${SCRIPT_DIR}/app/main.py" ]; then
    APP_DIR="${SCRIPT_DIR}"
elif [ -f "${SCRIPT_DIR}/backend/backend/app/main.py" ]; then
    APP_DIR="${SCRIPT_DIR}/backend/backend"
elif [ -f "${SCRIPT_DIR}/backend/app/main.py" ]; then
    APP_DIR="${SCRIPT_DIR}/backend"
fi

log_info "Backend application directory resolved to: ${BOLD}${APP_DIR}${NC}"

# ------------------------------------------------------------------------------
# 2. Operating System Detection & Package Manager Update
# ------------------------------------------------------------------------------
log_step "1/8" "Detecting Operating System & Updating Package Repositories"

OS_TYPE="unknown"
if [ -f /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    OS_TYPE="${ID}"
fi

case "${OS_TYPE}" in
    ubuntu|debian)
        log_info "Detected Debian/Ubuntu Linux distribution (${VERSION_CODENAME:-lts})."
        export DEBIAN_FRONTEND=noninteractive
        apt-get update -y
        apt-get upgrade -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold"
        ;;
    amzn|almalinux|rocky|rhel|centos|fedora)
        log_info "Detected Amazon Linux / Enterprise Linux distribution (${OS_TYPE})."
        if command -v dnf >/dev/null 2>&1; then
            dnf update -y
        else
            yum update -y
        fi
        ;;
    *)
        log_warn "Unrecognized OS distribution: ${OS_TYPE}. Attempting generic package installation."
        ;;
esac

# ------------------------------------------------------------------------------
# 3. Install Core System Tools, Compilers & Python Runtimes
# ------------------------------------------------------------------------------
log_step "2/8" "Installing Build Tools, Python 3.11+, and System Libraries"

if [[ "${OS_TYPE}" == "ubuntu" || "${OS_TYPE}" == "debian" ]]; then
    # Install software-properties-common if needed
    apt-get install -y --no-install-recommends \
        curl wget git jq unzip htop net-tools software-properties-common ca-certificates ufw nginx

    # Install Python 3.11 or latest available
    if ! command -v python3.11 >/dev/null 2>&1; then
        log_info "Installing Python 3.11 / Python 3 packages..."
        apt-get install -y python3 python3-pip python3-venv python3-dev build-essential libpq-dev libssl-dev libffi-dev || {
            log_info "Adding deadsnakes PPA for Python 3.11..."
            add-apt-repository -y ppa:deadsnakes/ppa || true
            apt-get update -y
            apt-get install -y python3.11 python3.11-venv python3.11-dev build-essential libpq-dev
        }
    else
        apt-get install -y python3-pip python3-venv python3-dev build-essential libpq-dev
    fi
else
    # Amazon Linux 2023 / Enterprise Linux
    if command -v dnf >/dev/null 2>&1; then
        dnf install -y python3.11 python3.11-pip python3.11-devel gcc gcc-c++ make git curl wget jq nginx postgresql15-devel || \
        dnf install -y python3 python3-pip python3-devel gcc gcc-c++ make git curl wget jq nginx libpq-devel
    else
        yum install -y python3 python3-pip python3-devel gcc gcc-c++ make git curl wget jq nginx postgresql-devel
    fi
fi

# Ensure python3 executable is available
PYTHON_BIN="$(command -v python3.11 || command -v python3)"
log_success "Active Python binary: ${PYTHON_BIN} ($("${PYTHON_BIN}" --version))"

# ------------------------------------------------------------------------------
# 4. PostgreSQL Database Installation & Configuration (Local or Remote)
# ------------------------------------------------------------------------------
log_step "3/8" "Configuring Database Layer (PostgreSQL / SQLite)"

DB_NAME="stocksense_db"
DB_USER="stocksense_user"
DB_PASS="$(openssl rand -hex 16)"
DB_PORT="5432"

INSTALL_LOCAL_PG="false"
if ! command -v psql >/dev/null 2>&1; then
    # Check if user requested non-interactive or default to installing local Postgres
    log_info "PostgreSQL is recommended for production. Installing local PostgreSQL server..."
    if [[ "${OS_TYPE}" == "ubuntu" || "${OS_TYPE}" == "debian" ]]; then
        apt-get install -y postgresql postgresql-contrib
    else
        if command -v dnf >/dev/null 2>&1; then
            dnf install -y postgresql15-server postgresql15 || dnf install -y postgresql-server postgresql
            postgresql-setup --initdb || true
        fi
    fi
    INSTALL_LOCAL_PG="true"
else
    INSTALL_LOCAL_PG="true"
fi

if [ "${INSTALL_LOCAL_PG}" = "true" ]; then
    log_info "Ensuring PostgreSQL service is enabled and active..."
    systemctl enable postgresql || true
    systemctl start postgresql || true

    log_info "Configuring local PostgreSQL database '${DB_NAME}' and user '${DB_USER}'..."
    sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname = '${DB_USER}'" | grep -q 1 || {
        sudo -u postgres psql -c "CREATE USER ${DB_USER} WITH ENCRYPTED PASSWORD '${DB_PASS}';"
    }
    sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname = '${DB_NAME}'" | grep -q 1 || {
        sudo -u postgres psql -c "CREATE DATABASE ${DB_NAME} OWNER ${DB_USER};"
    }
    sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE ${DB_NAME} TO ${DB_USER};"
    sudo -u postgres psql -d "${DB_NAME}" -c "GRANT ALL ON SCHEMA public TO ${DB_USER};" || true
    
    DETECTED_DATABASE_URL="postgresql://${DB_USER}:${DB_PASS}@localhost:${DB_PORT}/${DB_NAME}"
    log_success "Local PostgreSQL configured: ${DB_NAME} (User: ${DB_USER})"
else
    DETECTED_DATABASE_URL="sqlite:///./stocksense.db"
fi

# ------------------------------------------------------------------------------
# 5. Virtual Environment Creation & Dependency Installation
# ------------------------------------------------------------------------------
log_step "4/8" "Setting up Python Virtual Environment & Installing Dependencies"

VENV_DIR="${APP_DIR}/.venv"
if [ ! -d "${VENV_DIR}" ]; then
    log_info "Creating virtual environment at ${VENV_DIR}..."
    sudo -u "${REAL_USER}" "${PYTHON_BIN}" -m venv "${VENV_DIR}"
fi

# Upgrade pip, wheel, setuptools inside virtualenv
sudo -u "${REAL_USER}" "${VENV_DIR}/bin/pip" install --upgrade pip setuptools wheel

# Install requirements
if [ -f "${APP_DIR}/requirements.txt" ]; then
    log_info "Installing Python dependencies from ${APP_DIR}/requirements.txt..."
    sudo -u "${REAL_USER}" "${VENV_DIR}/bin/pip" install -r "${APP_DIR}/requirements.txt"
else
    log_warn "No requirements.txt found in ${APP_DIR}!"
fi

# Ensure production ASGI server (gunicorn) is installed
if ! "${VENV_DIR}/bin/pip" show gunicorn >/dev/null 2>&1; then
    log_info "Installing gunicorn for high-performance production concurrency..."
    sudo -u "${REAL_USER}" "${VENV_DIR}/bin/pip" install "gunicorn>=21.2.0"
fi

log_success "Python virtual environment dependencies installed successfully."

# ------------------------------------------------------------------------------
# 6. Environment File (.env) Generation & AWS Metadata Integration
# ------------------------------------------------------------------------------
log_step "5/8" "Generating Production Environment Configuration (.env)"

ENV_FILE="${APP_DIR}/.env"

# Fetch EC2 Public IP and Public Hostname from IMDSv2
EC2_PUBLIC_IP=""
EC2_PUBLIC_DNS=""
IMDS_TOKEN="$(curl -s -S -X PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 60" 2>/dev/null || echo "")"
if [ -n "${IMDS_TOKEN}" ]; then
    EC2_PUBLIC_IP="$(curl -s -S -H "X-aws-ec2-metadata-token: ${IMDS_TOKEN}" "http://169.254.169.254/latest/meta-data/public-ipv4" 2>/dev/null || echo "")"
    EC2_PUBLIC_DNS="$(curl -s -S -H "X-aws-ec2-metadata-token: ${IMDS_TOKEN}" "http://169.254.169.254/latest/meta-data/public-hostname" 2>/dev/null || echo "")"
fi

if [ -z "${EC2_PUBLIC_IP}" ]; then
    EC2_PUBLIC_IP="$(curl -s https://checkip.amazonaws.com 2>/dev/null || curl -s https://ifconfig.me 2>/dev/null || echo "0.0.0.0")"
fi

log_info "EC2 Public IP: ${BOLD}${EC2_PUBLIC_IP}${NC}"
if [ -n "${EC2_PUBLIC_DNS}" ]; then
    log_info "EC2 Public DNS: ${BOLD}${EC2_PUBLIC_DNS}${NC}"
fi

# Build CORS origins array
CORS_ORIGINS_JSON='["http://localhost:3000","http://127.0.0.1:3000","https://stocksense-self.vercel.app"'
if [ -n "${EC2_PUBLIC_IP}" ]; then
    CORS_ORIGINS_JSON="${CORS_ORIGINS_JSON},\"http://${EC2_PUBLIC_IP}\",\"http://${EC2_PUBLIC_IP}:8000\""
fi
if [ -n "${EC2_PUBLIC_DNS}" ]; then
    CORS_ORIGINS_JSON="${CORS_ORIGINS_JSON},\"http://${EC2_PUBLIC_DNS}\",\"http://${EC2_PUBLIC_DNS}:8000\""
fi
CORS_ORIGINS_JSON="${CORS_ORIGINS_JSON}]"

# Generate cryptographically secure 64-character secret key
PROD_SECRET_KEY="$(openssl rand -hex 32)"

if [ ! -f "${ENV_FILE}" ]; then
    log_info "Writing fresh production .env configuration..."
    cat > "${ENV_FILE}" << EOF
# ==============================================================================
# StockSense / RouteIQ Production Environment Configuration
# Generated automatically by ec2_setup on $(date -u +"%Y-%m-%d %H:%M:%S UTC")
# ==============================================================================

ENVIRONMENT=production
PROJECT_NAME="StockSense"
VERSION="0.1.0"
API_V1_PREFIX="/api/v1"

# Database Connection URL
DATABASE_URL="${DETECTED_DATABASE_URL}"

# Cryptographically Secure Secret Key (64 hex characters)
SECRET_KEY="${PROD_SECRET_KEY}"

# CORS Configuration
CORS_ORIGINS='${CORS_ORIGINS_JSON}'

# Logging Level
LOG_LEVEL="INFO"

# Worker & Server Parameters
PORT=8000
HOST="0.0.0.0"
EOF
    chown "${REAL_USER}:${REAL_GROUP}" "${ENV_FILE}"
    chmod 600 "${ENV_FILE}"
    log_success ".env configuration created successfully."
else
    log_info "Existing .env detected at ${ENV_FILE}. Preserving existing configuration."
fi

# Ensure executable permissions on run script
if [ -f "${APP_DIR}/run" ]; then
    chmod +x "${APP_DIR}/run"
    chown "${REAL_USER}:${REAL_GROUP}" "${APP_DIR}/run"
fi

# ------------------------------------------------------------------------------
# 7. Database Migrations & Initial Data Seeding
# ------------------------------------------------------------------------------
log_step "6/8" "Applying Alembic Database Migrations & Initial Seeds"

cd "${APP_DIR}"
if [ -f "${APP_DIR}/alembic.ini" ]; then
    log_info "Applying database schema via Alembic..."
    sudo -u "${REAL_USER}" "${VENV_DIR}/bin/alembic" upgrade head || {
        log_warn "Alembic upgrade encountered an issue. Please verify database connectivity."
    }
fi

# Seed initial demonstration data
log_info "Seeding demo records (SKUs & demand records)..."
sudo -u "${REAL_USER}" "${VENV_DIR}/bin/python3" -c "
try:
    from app.db.session import SessionLocal
    from app.seed.demo_data import seed_demo_data
    db = SessionLocal()
    result = seed_demo_data(db)
    print('Seeding completed:', result)
    db.close()
except Exception as e:
    print('Seed note:', e)
" || log_warn "Demo seed script skipped or table already populated."

# ------------------------------------------------------------------------------
# 8. Systemd Daemon Installation & Service Registration
# ------------------------------------------------------------------------------
log_step "7/8" "Registering Systemd Unit Service (routeiq.service)"

SERVICE_FILE="/etc/systemd/system/routeiq.service"
log_info "Writing systemd unit file to ${SERVICE_FILE}..."

cat > "${SERVICE_FILE}" << EOF
[Unit]
Description=RouteIQ / StockSense FastAPI Backend Application Service
After=network.target network-online.target postgresql.service
Wants=network-online.target

[Service]
Type=simple
User=${REAL_USER}
Group=${REAL_GROUP}
WorkingDirectory=${APP_DIR}
ExecStart=/bin/bash ${APP_DIR}/run
ExecReload=/bin/kill -s HUP \$MAINPID
Restart=always
RestartSec=5s
KillMode=mixed
TimeoutStopSec=30

# Environment Configuration
Environment="PYTHONUNBUFFERED=1"
Environment="PATH=${VENV_DIR}/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin"
EnvironmentFile=-${ENV_FILE}

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=routeiq

# Resource & File Descriptor Limits
LimitNOFILE=65535
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

# Create alias symlink stocksense.service -> routeiq.service
ln -sf "${SERVICE_FILE}" /etc/systemd/system/stocksense.service

systemctl daemon-reload
systemctl enable routeiq.service
systemctl restart routeiq.service
log_success "Systemd service 'routeiq' registered, enabled, and started."

# ------------------------------------------------------------------------------
# 9. Nginx Reverse Proxy & Firewall Configuration
# ------------------------------------------------------------------------------
log_step "8/8" "Configuring Nginx Reverse Proxy & Firewall"

NGINX_CONF_AVAILABLE="/etc/nginx/sites-available/routeiq"
NGINX_CONF_ENABLED="/etc/nginx/sites-enabled/routeiq"

if [ -d "/etc/nginx/sites-available" ]; then
    cat > "${NGINX_CONF_AVAILABLE}" << EOF
server {
    listen 80;
    listen [::]:80;
    server_name _;

    client_max_body_size 64M;

    # Gzip Compression
    gzip on;
    gzip_types text/plain text/css application/json application/javascript text/xml application/xml;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 180s;
        proxy_connect_timeout 60s;
    }
}
EOF
    # Remove default site if present
    rm -f /etc/nginx/sites-enabled/default
    ln -sf "${NGINX_CONF_AVAILABLE}" "${NGINX_CONF_ENABLED}"
elif [ -d "/etc/nginx/conf.d" ]; then
    cat > "/etc/nginx/conf.d/routeiq.conf" << EOF
server {
    listen 80;
    server_name _;

    client_max_body_size 64M;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF
fi

# Test and reload Nginx
if nginx -t 2>/dev/null; then
    systemctl enable nginx || true
    systemctl restart nginx || true
    log_success "Nginx reverse proxy configured and active on port 80."
else
    log_warn "Nginx configuration test failed. Check /etc/nginx configs."
fi

# Firewall configuration (UFW if present)
if command -v ufw >/dev/null 2>&1; then
    log_info "Configuring UFW firewall rules..."
    ufw allow 22/tcp >/dev/null 2>&1 || true
    ufw allow 80/tcp >/dev/null 2>&1 || true
    ufw allow 443/tcp >/dev/null 2>&1 || true
    ufw allow 8000/tcp >/dev/null 2>&1 || true
    ufw --force enable >/dev/null 2>&1 || true
    log_success "Firewall rules updated: ports 22, 80, 443, 8000 open."
fi

# ------------------------------------------------------------------------------
# 10. Verification & Live Health Checks
# ------------------------------------------------------------------------------
log_info "Verifying backend service status (polling http://127.0.0.1:8000/health/live)..."
HEALTHY=false
for i in {1..15}; do
    if curl -s -f "http://127.0.0.1:8000/health/live" >/dev/null 2>&1; then
        HEALTHY=true
        break
    fi
    sleep 2
done

echo ""
echo -e "${BOLD}${GREEN}==============================================================================${NC}"
if [ "${HEALTHY}" = true ]; then
    echo -e "${BOLD}${GREEN}  DEPLOYMENT SUCCESSFUL! BACKEND IS LIVE & RESPONDING HEALTHY${NC}"
else
    echo -e "${BOLD}${YELLOW}  DEPLOYMENT FINISHED (Backend service starting up, please verify logs)${NC}"
fi
echo -e "${BOLD}${GREEN}==============================================================================${NC}"
echo ""
echo -e "  ${BOLD}API Live Endpoint:${NC}      http://${EC2_PUBLIC_IP}/health/live"
echo -e "  ${BOLD}Swagger Documentation:${NC}  http://${EC2_PUBLIC_IP}/docs"
echo -e "  ${BOLD}Direct API Port:${NC}        http://${EC2_PUBLIC_IP}:8000/api/v1/products"
echo ""
echo -e "  ${BOLD}Service Status:${NC}         sudo systemctl status routeiq"
echo -e "  ${BOLD}Live Log Stream:${NC}        journalctl -u routeiq -f"
echo -e "  ${BOLD}Redeploy / Update:${NC}      ./ec2_update"
echo -e "${BOLD}${GREEN}==============================================================================${NC}"
