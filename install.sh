#!/bin/bash

REPO="https://github.com/Emadhabibnia1385/ConfigFlow.git"
DIR="/opt/configflow"
SERVICE="configflow"
PY_FILE="bot.py"
BRANCH="main"

R='\033[31m'; G='\033[32m'; Y='\033[33m'; C='\033[36m'; M='\033[35m'; B='\033[1m'; N='\033[0m'

header() {
  clear 2>/dev/null || true
  echo -e "${C}╔════════════════════════════════════════════════════════════════════════╗${N}"
  echo -e "${C}║${N}                                                                        ${C}║${N}"
  echo -e "${C}║${N}   ${B}${M} ██████╗ ██████╗ ███╗   ██╗███████╗██╗ ██████╗ ███████╗██╗      ${N}${C}║${N}"
  echo -e "${C}║${N}   ${B}${M}██╔════╝██╔═══██╗████╗  ██║██╔════╝██║██╔════╝ ██╔════╝██║      ${N}${C}║${N}"
  echo -e "${C}║${N}   ${B}${M}██║     ██║   ██║██╔██╗ ██║█████╗  ██║██║  ███╗█████╗  ██║      ${N}${C}║${N}"
  echo -e "${C}║${N}   ${B}${M}██║     ██║   ██║██║╚██╗██║██╔══╝  ██║██║   ██║██╔══╝  ██║      ${N}${C}║${N}"
  echo -e "${C}║${N}   ${B}${M}╚██████╗╚██████╔╝██║ ╚████║██║     ██║╚██████╔╝██║     ███████╗ ${N}${C}║${N}"
  echo -e "${C}║${N}   ${B}${M} ╚═════╝ ╚═════╝ ╚═╝  ╚═══╝╚═╝     ╚═╝ ╚═════╝ ╚═╝     ╚══════╝ ${N}${C}║${N}"
  echo -e "${C}║${N}                                                                        ${C}║${N}"
  echo -e "${C}║${N}              ${B}🚀 ConfigFlow - Telegram VPN Selling Bot${N}               ${C}║${N}"
  echo -e "${C}║${N}                                                                        ${C}║${N}"
  echo -e "${C}║${N}          ${B}Inventory-based delivery | Wallet | Card-to-Card${N}          ${C}║${N}"
  echo -e "${C}║${N}                                                                        ${C}║${N}"
  echo -e "${C}╚════════════════════════════════════════════════════════════════════════╝${N}"
  echo ""
}

err() {
  echo -e "${R}✗ $*${N}" >&2
  echo ""
  read -p "Press Enter to continue..." _
  return 1
}

ok()  { echo -e "${G}✓ $*${N}"; }
info(){ echo -e "${Y}➜ $*${N}"; }
pause() { echo ""; read -p "Press Enter to continue..." _; }

check_root() {
  if [[ $EUID -ne 0 ]]; then
    echo -e "${R}✗ Please run with sudo or as root${N}"
    exit 1
  fi
}

run_silent() { "$@" >/dev/null 2>&1; }

detect_py_file() {
  if [[ -f "$DIR/bot.py" ]]; then
    PY_FILE="bot.py"
  elif [[ -f "$DIR/BOT.py" ]]; then
    PY_FILE="BOT.py"
  else
    err "No bot entry file found (bot.py or BOT.py) in $DIR"
    return 1
  fi
  return 0
}

validate_repo_access() {
  git ls-remote --heads "$REPO" "$BRANCH" >/dev/null 2>&1
}

ask_config() {
  echo ""
  info "ConfigFlow Configuration (required)"

  echo -n "Enter Telegram Bot TOKEN: "
  read -r BOT_TOKEN
  [[ -z "$BOT_TOKEN" ]] && { err "TOKEN cannot be empty"; return 1; }

  echo -n "Enter Primary Admin ID (numeric): "
  read -r ADMIN_IDS
  [[ ! "$ADMIN_IDS" =~ ^[0-9]+$ ]] && { err "Admin ID must be numeric"; return 1; }

  return 0
}

write_env() {
  cat > "$DIR/.env" << EOF
BOT_TOKEN=$BOT_TOKEN
ADMIN_IDS=$ADMIN_IDS
DB_NAME=trackless_bot.db
EOF
  chmod 600 "$DIR/.env" >/dev/null 2>&1 || true
}

create_service() {
  detect_py_file || return 1

  info "Creating systemd service..."
  cat > "/etc/systemd/system/$SERVICE.service" << EOF
[Unit]
Description=ConfigFlow Telegram Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=$DIR
EnvironmentFile=$DIR/.env
ExecStart=$DIR/venv/bin/python $DIR/$PY_FILE
Restart=always
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
EOF

  run_silent systemctl daemon-reload || { err "systemctl daemon-reload failed"; return 1; }
  run_silent systemctl enable "$SERVICE" || { err "systemctl enable failed"; return 1; }
  run_silent systemctl restart "$SERVICE" || { err "systemctl restart failed"; return 1; }
  return 0
}

install_bot() {
  info "Installing prerequisites..."
  run_silent apt-get update -qq || { err "apt update failed"; return 1; }
  run_silent apt-get install -y -qq git python3 python3-venv python3-pip sqlite3 curl ca-certificates || { err "apt install failed"; return 1; }

  info "Checking GitHub repository access..."
  validate_repo_access || { err "Cannot access repository or branch: $REPO [$BRANCH]. Make sure the repository is public and the branch exists."; return 1; }

  info "Downloading ConfigFlow..."
  if [[ -d "$DIR/.git" ]]; then
    (cd "$DIR" && git fetch origin "$BRANCH" && git reset --hard "origin/$BRANCH") >/dev/null 2>&1 || { err "git update failed"; return 1; }
  else
    rm -rf "$DIR"
    git clone --branch "$BRANCH" --single-branch "$REPO" "$DIR" || { err "git clone failed. Repository may be private or unreachable."; return 1; }
  fi

  detect_py_file || return 1

  info "Setting up Python environment..."
  if [[ ! -d "$DIR/venv" ]]; then
    python3 -m venv "$DIR/venv" >/dev/null 2>&1 || { err "venv create failed"; return 1; }
  fi

  info "Upgrading pip..."
  "$DIR/venv/bin/pip" install --upgrade pip wheel || { err "pip upgrade failed"; return 1; }

  info "Installing requirements..."
  if [[ -f "$DIR/requirements.txt" ]]; then
    "$DIR/venv/bin/pip" install -r "$DIR/requirements.txt" || { err "requirements install failed"; return 1; }
  else
    "$DIR/venv/bin/pip" install pyTelegramBotAPI qrcode pillow python-dotenv || { err "pip install failed"; return 1; }
  fi

  header
  ok "Packages downloaded and installed successfully!"
  echo ""

  ask_config || return 1
  write_env

  create_service || return 1

  echo ""
  ok "ConfigFlow installed successfully!"
  echo ""
  systemctl status "$SERVICE" --no-pager -l
  return 0
}

update_bot() {
  info "Updating ConfigFlow from GitHub..."
  [[ -d "$DIR/.git" ]] || { err "Not installed. Install first."; return 1; }

  validate_repo_access || { err "Cannot access repository or branch: $REPO [$BRANCH]"; return 1; }

  (cd "$DIR" && git fetch origin "$BRANCH" && git reset --hard "origin/$BRANCH") || { err "git update failed"; return 1; }

  detect_py_file || return 1

  info "Updating requirements..."
  if [[ -f "$DIR/requirements.txt" ]]; then
    "$DIR/venv/bin/pip" install -r "$DIR/requirements.txt" || { err "requirements update failed"; return 1; }
  fi

  run_silent systemctl restart "$SERVICE" || { err "restart failed"; return 1; }

  header
  ok "Updated successfully!"
  return 0
}

edit_config() {
  [[ -f "$DIR/.env" ]] || { err "Config not found. Install first."; return 1; }
  nano "$DIR/.env"
  run_silent systemctl restart "$SERVICE" || { err "restart failed"; return 1; }
  header
  ok "Configuration updated and bot restarted!"
  return 0
}

remove_bot() {
  echo -n "Are you sure you want to remove ConfigFlow? (yes/no): "
  read -r confirm
  if [[ "$confirm" != "yes" ]]; then
    info "Cancelled"
    return 0
  fi

  run_silent systemctl stop "$SERVICE"
  run_silent systemctl disable "$SERVICE"
  run_silent rm -f "/etc/systemd/system/$SERVICE.service"
  run_silent systemctl daemon-reload
  rm -rf "$DIR"

  header
  ok "ConfigFlow removed completely"
  return 0
}

show_menu() {
  echo -e "${B}1)${N} Install / Reinstall"
  echo -e "${B}2)${N} Update from GitHub"
  echo -e "${B}3)${N} Edit Config (.env)"
  echo -e "${B}4)${N} Start Bot"
  echo -e "${B}5)${N} Stop Bot"
  echo -e "${B}6)${N} Restart Bot"
  echo -e "${B}7)${N} View Live Logs"
  echo -e "${B}8)${N} Bot Status"
  echo -e "${B}9)${N} Uninstall"
  echo -e "${B}0)${N} Exit"
  echo ""
}

read_choice() {
  IFS= read -r choice
  choice="${choice//[$'\t\r\n ']/}"
  echo "$choice"
}

main() {
  check_root

  while true; do
    header
    show_menu

    echo -n "Select option [0-9]: "
    choice="$(read_choice)"

    case "$choice" in
      1) install_bot; pause ;;
      2) update_bot; pause ;;
      3) edit_config; pause ;;
      4) run_silent systemctl start "$SERVICE" && header && ok "Bot started"; pause ;;
      5) run_silent systemctl stop "$SERVICE" && header && ok "Bot stopped"; pause ;;
      6) run_silent systemctl restart "$SERVICE" && header && ok "Bot restarted"; pause ;;
      7)
        echo -e "${Y}Press Ctrl+C to exit logs${N}"
        sleep 2
        journalctl -u "$SERVICE" -f
        ;;
      8)
        systemctl status "$SERVICE" --no-pager -l
        pause
        ;;
      9) remove_bot; pause ;;
      0) echo "Goodbye!"; exit 0 ;;
      *) header; echo -e "${R}Invalid option${N}"; sleep 1 ;;
    esac
  done
}

main
