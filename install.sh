#!/bin/bash
set -Eeuo pipefail

REPO="https://github.com/Emadhabibnia1385/ConfigFlow.git"
BASE_DIR="/opt/configflow"
BASE_SERVICE="configflow"
DIR=""
SERVICE=""
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

R='\033[31m'; G='\033[32m'; Y='\033[33m'; C='\033[36m'; M='\033[35m'; B='\033[1m'; W='\033[97m'; N='\033[0m'

header() {
  clear 2>/dev/null || true
  echo ""
  echo -e "${C}╔══════════════════════════════════════════════════════════════════════════╗${N}"
  echo -e "${C}║${N}                                                                          ${C}║${N}"
  echo -e "${C}║${N}   ${B}${M} ██████╗ ██████╗ ███╗   ██╗███████╗██╗ ██████╗ ███████╗██╗      ██╗${N}  ${C}║${N}"
  echo -e "${C}║${N}   ${B}${M}██╔════╝██╔═══██╗████╗  ██║██╔════╝██║██╔════╝ ██╔════╝██║      ██║${N}  ${C}║${N}"
  echo -e "${C}║${N}   ${B}${M}██║     ██║   ██║██╔██╗ ██║█████╗  ██║██║  ███╗█████╗  ██║  █╗  ██║${N}  ${C}║${N}"
  echo -e "${C}║${N}   ${B}${M}██║     ██║   ██║██║╚██╗██║██╔══╝  ██║██║   ██║██╔══╝  ██║ ███╗ ██║${N}  ${C}║${N}"
  echo -e "${C}║${N}   ${B}${M}╚██████╗╚██████╔╝██║ ╚████║██║     ██║╚██████╔╝██║     ╚███╔███╔╝ ${N}  ${C}║${N}"
  echo -e "${C}║${N}   ${B}${M} ╚═════╝ ╚═════╝ ╚═╝  ╚═══╝╚═╝     ╚═╝ ╚═════╝ ╚═╝      ╚══╝╚══╝  ${N}  ${C}║${N}"
  echo -e "${C}║${N}                                                                          ${C}║${N}"
  echo -e "${C}║${N}          ${W}${B}⚡ ConfigFlow — Telegram Config Sales Bot ⚡${N}                  ${C}║${N}"
  echo -e "${C}║${N}                                                                          ${C}║${N}"
  echo -e "${C}╠══════════════════════════════════════════════════════════════════════════╣${N}"
  echo -e "${C}║${N}                                                                          ${C}║${N}"
  echo -e "${C}║${N}   ${B}${G}GitHub:${N}    github.com/Emadhabibnia1385/ConfigFlow                      ${C}║${N}"
  echo -e "${C}║${N}   ${B}${G}Developer:${N} t.me/EmadHabibnia                                          ${C}║${N}"
  echo -e "${C}║${N}   ${B}${G}Channel:${N}   @Emadhabibnia                                               ${C}║${N}"
  echo -e "${C}║${N}                                                                          ${C}║${N}"
  echo -e "${C}╚══════════════════════════════════════════════════════════════════════════╝${N}"
  echo ""
}

err() { echo -e "${R}✗ $*${N}" >&2; exit 1; }
ok()  { echo -e "${G}✓ $*${N}"; }
info(){ echo -e "${Y}➜ $*${N}"; }

on_error() {
  echo -e "${R}✗ Error on line ${BASH_LINENO[0]}${N}"
}
trap on_error ERR

check_root() {
  if [[ $EUID -ne 0 ]]; then
    err "Please run with sudo or as root"
  fi
}

ensure_safe_cwd() {
  cd / 2>/dev/null || true
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || err "Missing command: $1"
}

install_prereqs() {
  info "Installing prerequisites..."
  apt-get update -y
  apt-get install -y git python3 python3-venv python3-pip curl
}

clone_or_update_repo() {
  info "Downloading ConfigFlow..."

  mkdir -p "$DIR"

  if [[ -d "$DIR/.git" ]]; then
    info "Repository exists. Updating..."
    cd "$DIR"
    git fetch --all --prune
    git reset --hard origin/main
  else
    rm -rf "$DIR"
    mkdir -p "$DIR"
    git clone "$REPO" "$DIR"
    cd "$DIR"
  fi

  [[ -f "$DIR/bot.py" ]] || err "bot.py not found after download. Repo content missing?"
  [[ -f "$DIR/requirements.txt" ]] || err "requirements.txt not found after download."
}

setup_venv() {
  info "Setting up Python environment..."
  if [[ ! -d "$DIR/venv" ]]; then
    python3 -m venv "$DIR/venv"
  fi

  "$DIR/venv/bin/pip" install --upgrade pip wheel
  "$DIR/venv/bin/pip" install -r "$DIR/requirements.txt"
}

configure_env() {
  echo ""
  echo -e "${C}╔══════════════════════════════════════════════════════════════════════════╗${N}"
  echo -e "${C}║${N}                    ${B}${W}⚙️  Bot Configuration${N}                                  ${C}║${N}"
  echo -e "${C}╚══════════════════════════════════════════════════════════════════════════╝${N}"
  echo ""

  echo -e "${Y}📌 You can get a bot token from ${B}@BotFather${N}${Y} on Telegram.${N}"
  echo ""
  read -r -p "$(echo -e "${B}🔑 Enter your Telegram Bot TOKEN: ${N}")" INPUT_TOKEN
  INPUT_TOKEN="${INPUT_TOKEN// /}"
  [[ -n "$INPUT_TOKEN" ]] || err "TOKEN cannot be empty"
  [[ "$INPUT_TOKEN" =~ ^[0-9]+:.+$ ]] || err "Invalid token format. Expected format: 123456789:ABCdefGHI..."

  echo ""
  echo -e "${Y}📌 Send /start to ${B}@userinfobot${N}${Y} to get your numeric Chat ID.${N}"
  echo ""
  read -r -p "$(echo -e "${B}👤 Enter Admin Chat ID (numeric): ${N}")" INPUT_ADMIN
  INPUT_ADMIN="${INPUT_ADMIN// /}"
  [[ "$INPUT_ADMIN" =~ ^-?[0-9]+$ ]] || err "Admin ID must be numeric"

  echo ""
  read -r -p "$(echo -e "${B}📂 Database name [ConfigFlow.db]: ${N}")" INPUT_DB
  INPUT_DB="${INPUT_DB:-ConfigFlow.db}"

  cat > "$DIR/.env" << EOF
BOT_TOKEN=$INPUT_TOKEN
ADMIN_IDS=$INPUT_ADMIN
DB_NAME=$INPUT_DB
EOF
  chmod 600 "$DIR/.env"
  echo ""
  ok "Configuration saved to $DIR/.env"
}

configure_iran_worker() {
  echo ""
  echo -e "${C}╔══════════════════════════════════════════════════════════════════════════╗${N}"
  echo -e "${C}║${N}         ${B}${W}🇮🇷  Iran Server (3x-ui Worker) Configuration${N}               ${C}║${N}"
  echo -e "${C}╚══════════════════════════════════════════════════════════════════════════╝${N}"
  echo ""

  read -r -p "$(echo -e "${B}🌐 Panel IP (default 127.0.0.1): ${N}")" INPUT_PANEL_IP
  INPUT_PANEL_IP="${INPUT_PANEL_IP:-127.0.0.1}"

  read -r -p "$(echo -e "${B}🔌 Panel Port (default 2053): ${N}")" INPUT_PANEL_PORT
  INPUT_PANEL_PORT="${INPUT_PANEL_PORT:-2053}"
  [[ "$INPUT_PANEL_PORT" =~ ^[0-9]+$ ]] || err "Port must be numeric"

  read -r -p "$(echo -e "${B}📄 Patch (optional, e.g. /xui — press Enter to skip): ${N}")" INPUT_PATCH
  INPUT_PATCH="${INPUT_PATCH:-}"

  read -r -p "$(echo -e "${B}👤 Panel Username: ${N}")" INPUT_PANEL_USER
  [[ -n "$INPUT_PANEL_USER" ]] || err "Panel username cannot be empty"

  read -r -s -p "$(echo -e "${B}🔑 Panel Password: ${N}")" INPUT_PANEL_PASS
  echo ""
  [[ -n "$INPUT_PANEL_PASS" ]] || err "Panel password cannot be empty"

  read -r -p "$(echo -e "${B}🆔 Inbound ID to use (default 1): ${N}")" INPUT_INBOUND_ID
  INPUT_INBOUND_ID="${INPUT_INBOUND_ID:-1}"
  [[ "$INPUT_INBOUND_ID" =~ ^[0-9]+$ ]] || err "Inbound ID must be numeric"

  read -r -p "$(echo -e "${B}🔐 Worker API Key (min 16 chars; leave blank to auto-generate): ${N}")" INPUT_WORKER_KEY
  if [[ -z "$INPUT_WORKER_KEY" ]]; then
    INPUT_WORKER_KEY=$(tr -dc 'A-Za-z0-9' </dev/urandom 2>/dev/null | head -c 32 || openssl rand -hex 16)
  fi
  [[ ${#INPUT_WORKER_KEY} -ge 16 ]] || err "API key must be at least 16 characters"

  read -r -p "$(echo -e "${B}🌍 Bot API URL (e.g. http://your-foreign-server:8080): ${N}")" INPUT_API_URL
  [[ -n "$INPUT_API_URL" ]] || err "Bot API URL cannot be empty"

  read -r -p "$(echo -e "${B}⏱ Poll interval in seconds (default 10): ${N}")" INPUT_POLL
  INPUT_POLL="${INPUT_POLL:-10}"
  [[ "$INPUT_POLL" =~ ^[0-9]+$ ]] || err "Poll interval must be numeric"

  cat > "$DIR/config.env" << ENVEOF
BOT_API_URL=$INPUT_API_URL
WORKER_API_KEY=$INPUT_WORKER_KEY
PANEL_IP=$INPUT_PANEL_IP
PANEL_PORT=$INPUT_PANEL_PORT
PANEL_PATCH=$INPUT_PATCH
PANEL_USERNAME=$INPUT_PANEL_USER
PANEL_PASSWORD=$INPUT_PANEL_PASS
INBOUND_ID=$INPUT_INBOUND_ID
POLL_INTERVAL=$INPUT_POLL
PROTOCOL=vless
ENVEOF
  chmod 600 "$DIR/config.env"
  echo ""
  ok "Iran Worker config saved to $DIR/config.env"
  echo -e "${Y}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${N}"
  echo -e "${B}${W}   ⚠️  IMPORTANT — Save this API key for the bot admin panel:${N}"
  echo -e "   ${B}${G}WORKER_API_KEY = ${INPUT_WORKER_KEY}${N}"
  echo -e "${Y}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${N}"
  echo ""
  read -r -p "Press Enter to continue..."
}

create_systemd_service() {
  info "Creating systemd service..."
  cat > "/etc/systemd/system/$SERVICE.service" << EOF
[Unit]
Description=ConfigFlow Telegram Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=$DIR
EnvironmentFile=$DIR/.env
ExecStart=$DIR/venv/bin/python $DIR/bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

  systemctl daemon-reload
  systemctl enable "$SERVICE" >/dev/null 2>&1 || true
}

create_worker_service() {
  [[ -f "$DIR/config.env" ]] || return 0
  info "Creating Iran Worker systemd service..."
  cat > "/etc/systemd/system/${SERVICE}-worker.service" << EOF
[Unit]
Description=ConfigFlow Iran Worker (3x-ui)
After=network.target

[Service]
Type=simple
WorkingDirectory=$DIR
EnvironmentFile=$DIR/config.env
ExecStart=$DIR/venv/bin/python $DIR/worker.py
Restart=always
RestartSec=10
StandardOutput=append:${DIR}/worker.log
StandardError=append:${DIR}/worker.log

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable "${SERVICE}-worker" >/dev/null 2>&1 || true
  ok "Worker service ${SERVICE}-worker created"
}

start_service() {
  systemctl restart "$SERVICE"
  echo ""
  echo -e "${G}╔══════════════════════════════════════════════════════════════════════════╗${N}"
  echo -e "${G}║${N}          ${B}${G}✅  ConfigFlow installed & running successfully!${N}                ${G}║${N}"
  echo -e "${G}╚══════════════════════════════════════════════════════════════════════════╝${N}"
  echo ""
  systemctl status "$SERVICE" --no-pager -l || true
}

install_bot() {
  ensure_safe_cwd
  install_prereqs
  clone_or_update_repo
  setup_venv
  configure_env
  create_systemd_service
  start_service
}

install_worker() {
  echo ""
  echo -e "${C}┌──────────────────────────────────────┐${N}"
  echo -e "${C}│${N}    ${B}${W}📦 Worker Installation Source${N}       ${C}│${N}"
  echo -e "${C}├──────────────────────────────────────┤${N}"
  echo -e "${C}│${N}  ${B}${G}g)${N} 🌐 Install from GitHub              ${C}│${N}"
  echo -e "${C}│${N}  ${B}${M}l)${N} 📁 Install from Local files         ${C}│${N}"
  echo -e "${C}│${N}     ${Y}(worker.py + requirements.txt${N}       ${C}│${N}"
  echo -e "${C}│${N}     ${Y} same folder as install.sh)${N}          ${C}│${N}"
  echo -e "${C}└──────────────────────────────────────┘${N}"
  echo ""
  read -r -p "$(echo -e "${B}Select source [g/l]: ${N}")" src_choice
  case "${src_choice:-}" in
    g) _install_worker_github ;;
    l) _install_worker_local  ;;
    *) echo -e "${R}Invalid option${N}"; return 1 ;;
  esac
}

_install_worker_github() {
  ensure_safe_cwd
  [[ -d "$DIR/.git" ]] || { install_prereqs; clone_or_update_repo; setup_venv; }
  [[ -d "$DIR/venv" ]] || setup_venv
  configure_iran_worker
  create_worker_service
  systemctl restart "${SERVICE}-worker"
  echo ""
  echo -e "${G}╔══════════════════════════════════════════════════════════════════════════╗${N}"
  echo -e "${G}║${N}      ${B}${G}✅  ConfigFlow Iran Worker installed & running!${N}                   ${G}║${N}"
  echo -e "${G}╚══════════════════════════════════════════════════════════════════════════╝${N}"
  echo ""
  systemctl status "${SERVICE}-worker" --no-pager -l || true
}

_install_worker_local() {
  ensure_safe_cwd
  info "Installing Iran Worker from local files in: $SCRIPT_DIR"

  # Validate required files
  local missing=0
  for f in worker.py requirements.txt; do
    if [[ ! -f "$SCRIPT_DIR/$f" ]]; then
      echo -e "${R}✗ Missing file: $SCRIPT_DIR/$f${N}" >&2
      missing=1
    fi
  done
  [[ $missing -eq 0 ]] || err "Copy the required files next to install.sh and try again"

  # Install system prerequisites (python3, venv)
  install_prereqs

  # Create target directory and copy files
  mkdir -p "$DIR"
  for f in worker.py requirements.txt; do
    cp -v "$SCRIPT_DIR/$f" "$DIR/$f"
    ok "Copied $f → $DIR/$f"
  done
  # Copy config.env.example if present
  [[ -f "$SCRIPT_DIR/config.env.example" ]] && cp "$SCRIPT_DIR/config.env.example" "$DIR/config.env.example" || true

  # Setup Python venv
  setup_venv

  configure_iran_worker
  create_worker_service
  systemctl restart "${SERVICE}-worker"
  echo ""
  echo -e "${G}╔══════════════════════════════════════════════════════════════════════════╗${N}"
  echo -e "${G}║${N}      ${B}${G}✅  ConfigFlow Iran Worker installed & running!${N}                   ${G}║${N}"
  echo -e "${G}╚══════════════════════════════════════════════════════════════════════════╝${N}"
  echo ""
  systemctl status "${SERVICE}-worker" --no-pager -l || true
}

update_bot() {
  ensure_safe_cwd
  [[ -d "$DIR/.git" ]] || err "Not installed. Please run Install first."
  info "Updating ConfigFlow..."
  clone_or_update_repo
  setup_venv
  systemctl restart "$SERVICE"
  ok "Updated successfully!"
}

edit_config() {
  ensure_safe_cwd
  [[ -f "$DIR/.env" ]] || err "Config file not found. Please install first."
  nano "$DIR/.env"
  systemctl restart "$SERVICE"
  ok "Configuration updated and bot restarted!"
}

remove_bot() {
  ensure_safe_cwd
  read -r -p "Are you sure you want to remove ConfigFlow? (yes/no): " confirm
  if [[ "$confirm" != "yes" ]]; then
    info "Cancelled"
    return
  fi

  systemctl stop "$SERVICE" 2>/dev/null || true
  systemctl disable "$SERVICE" 2>/dev/null || true
  rm -f "/etc/systemd/system/$SERVICE.service"
  systemctl stop "${SERVICE}-worker" 2>/dev/null || true
  systemctl disable "${SERVICE}-worker" 2>/dev/null || true
  rm -f "/etc/systemd/system/${SERVICE}-worker.service"
  systemctl daemon-reload
  rm -rf "$DIR"
  ok "ConfigFlow removed completely"
}

show_menu() {
  echo -e "${C}┌──────────────────────────────────────┐${N}"
  echo -e "${C}│${N}         ${B}${W}📋 Main Menu${N}                  ${C}│${N}"
  echo -e "${C}├──────────────────────────────────────┤${N}"
  echo -e "${C}│${N}  ${B}${G}1)${N} 📦 Install / Reinstall            ${C}│${N}"
  echo -e "${C}│${N}  ${B}${G}2)${N} 🔄 Update from GitHub             ${C}│${N}"
  echo -e "${C}│${N}  ${B}${G}3)${N} ✏️  Edit Config (.env)             ${C}│${N}"
  echo -e "${C}│${N}  ${B}${G}4)${N} ▶️  Start Bot                      ${C}│${N}"
  echo -e "${C}│${N}  ${B}${G}5)${N} ⏹️  Stop Bot                       ${C}│${N}"
  echo -e "${C}│${N}  ${B}${G}6)${N} 🔁 Restart Bot                    ${C}│${N}"
  echo -e "${C}│${N}  ${B}${G}7)${N} 📜 View Live Logs                 ${C}│${N}"
  echo -e "${C}│${N}  ${B}${G}8)${N} 📊 Bot Status                     ${C}│${N}"
  echo -e "${C}│${N}  ${B}${G}9)${N} 🗑️  Uninstall                      ${C}│${N}"
  echo -e "${C}│${N}  ${B}${M}i)${N} 🇮🇷 Install Iran Worker (3x-ui)    ${C}│${N}"
  echo -e "${C}│${N}  ${B}${M}w)${N} 📋 Worker Logs                    ${C}│${N}"
  echo -e "${C}│${N}  ${B}${M}W)${N} 🔁 Restart Worker                 ${C}│${N}"
  echo -e "${C}│${N}  ${B}${R}0)${N} 🚪 Exit                           ${C}│${N}"
  echo -e "${C}└──────────────────────────────────────┘${N}"
  echo ""
}

list_instances() {
  local found=0
  echo -e "${C}┌──────────────────────────────────────┐${N}"
  echo -e "${C}│${N}     ${B}${W}📋 Installed Instances${N}             ${C}│${N}"
  echo -e "${C}├──────────────────────────────────────┤${N}"
  for d in /opt/configflow-*/; do
    [[ -d "$d" ]] || continue
    local name="$(basename "$d")"
    local svc="${name}"
    local status="⚪"
    if systemctl is-active "$svc" >/dev/null 2>&1; then
      status="${G}🟢 running${N}"
    else
      status="${R}🔴 stopped${N}"
    fi
    echo -e "${C}│${N}  ${B}${name}${N}  $status  ${C}│${N}"
    found=1
  done
  if [[ $found -eq 0 ]]; then
    echo -e "${C}│${N}  ${Y}No instances installed yet${N}          ${C}│${N}"
  fi
  echo -e "${C}└──────────────────────────────────────┘${N}"
  echo ""
}

select_instance() {
  echo ""
  list_instances
  echo -e "${Y}📌 Enter instance number (e.g. 1, 2, 3, ...) to manage that bot.${N}"
  echo -e "${Y}   Each number creates a separate bot with its own config & database.${N}"
  echo ""
  read -r -p "$(echo -e "${B}🔢 Instance number: ${N}")" INSTANCE_NUM
  INSTANCE_NUM="${INSTANCE_NUM// /}"
  [[ "$INSTANCE_NUM" =~ ^[0-9]+$ ]] || err "Instance number must be a positive number (e.g. 1, 2, 3)"
  [[ "$INSTANCE_NUM" -ge 1 ]] || err "Instance number must be >= 1"

  DIR="${BASE_DIR}-${INSTANCE_NUM}"
  SERVICE="${BASE_SERVICE}-${INSTANCE_NUM}"
  echo ""
  ok "Selected instance: ${B}#${INSTANCE_NUM}${N}  (dir: $DIR  service: $SERVICE)"
  echo ""
}

main() {
  check_root
  ensure_safe_cwd
  select_instance

  while true; do
    header
    echo -e "  ${B}${M}Instance #${INSTANCE_NUM}${N}  —  dir: ${W}$DIR${N}  service: ${W}$SERVICE${N}"
    echo ""
    show_menu

    read -r -p "$(echo -e "${C}ConfigFlow #${INSTANCE_NUM}${N} ${B}➜${N} Select option ${W}[0-9/i/w/W]${N}: ")" choice

    case "${choice:-}" in
      1) install_bot ;;
      2) update_bot ;;
      3) edit_config ;;
      4) systemctl start "$SERVICE"; ok "Bot #${INSTANCE_NUM} started"; read -r -p "Press Enter to continue...";;
      5) systemctl stop "$SERVICE"; ok "Bot #${INSTANCE_NUM} stopped"; read -r -p "Press Enter to continue...";;
      6) systemctl restart "$SERVICE"; ok "Bot #${INSTANCE_NUM} restarted"; read -r -p "Press Enter to continue...";;
      7) echo -e "${Y}Press Ctrl+C to exit logs${N}"; sleep 1; journalctl -u "$SERVICE" -f;;
      8) systemctl status "$SERVICE" --no-pager -l; read -r -p "Press Enter to continue...";;
      9) remove_bot; read -r -p "Press Enter to continue...";;
      i) install_worker; read -r -p "Press Enter to continue...";;
      w) echo -e "${Y}Press Ctrl+C to exit logs${N}"; sleep 1; journalctl -u "${SERVICE}-worker" -f;;
      W) systemctl restart "${SERVICE}-worker"; ok "Worker #${INSTANCE_NUM} restarted"; read -r -p "Press Enter to continue...";;
      0) echo "Goodbye!"; exit 0;;
      *) echo -e "${R}Invalid option${N}"; sleep 1;;
    esac
  done
}

main
