#!/bin/bash
set -Eeuo pipefail

REPO="https://github.com/Emadhabibnia1385/ConfigFlow.git"
BASE_DIR="/opt/configflow"
BASE_SERVICE="configflow"
DIR=""
SERVICE=""

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

    read -r -p "$(echo -e "${C}ConfigFlow #${INSTANCE_NUM}${N} ${B}➜${N} Select option ${W}[0-9]${N}: ")" choice

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
      0) echo "Goodbye!"; exit 0;;
      *) echo -e "${R}Invalid option${N}"; sleep 1;;
    esac
  done
}

main
