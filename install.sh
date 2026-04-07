BASE_SERVICE="configflow"
DIR=""
SERVICE=""
INSTANCE_NUM=""
BOT_NAME=""

if [[ "${BASH_SOURCE[0]:-}" == /dev/fd/* ]] || [[ "${BASH_SOURCE[0]:-}" == /proc/*/fd/* ]]; then
  SCRIPT_DIR="$(pwd)"
else
@@ -14,6 +17,8 @@ fi

R='\033[31m'; G='\033[32m'; Y='\033[33m'; C='\033[36m'; M='\033[35m'; B='\033[1m'; W='\033[97m'; N='\033[0m'

# ─────────────────────────── header ───────────────────────────

header() {
  clear 2>/dev/null || true
  echo ""
 -29,49 +34,83 @@ header() {
  echo -e "${C}║${N}          ${W}${B}⚡ ConfigFlow — Telegram Config Sales Bot ⚡${N}                  ${C}║${N}"
  echo -e "${C}║${N}                                                                          ${C}║${N}"
  echo -e "${C}╠══════════════════════════════════════════════════════════════════════════╣${N}"

  echo -e "${C}║${N}   ${B}${G}GitHub:${N}    github.com/Emadhabibnia1385/ConfigFlow                      ${C}║${N}"
  echo -e "${C}║${N}   ${B}${G}Developer:${N} t.me/EmadHabibnia                                          ${C}║${N}"
  echo -e "${C}║${N}   ${B}${G}Channel:${N}   @Emadhabibnia                                               ${C}║${N}"

  echo -e "${C}╚══════════════════════════════════════════════════════════════════════════╝${N}"
  echo ""
}

err()  { echo -e "${R}✗ $*${N}" >&2; exit 1; }
ok()   { echo -e "${G}✓ $*${N}"; }
info() { echo -e "${Y}➜ $*${N}"; }

on_error() { echo -e "${R}✗ Error on line ${BASH_LINENO[0]}${N}"; }


trap on_error ERR

check_root() {
  [[ $EUID -eq 0 ]] || err "Please run with sudo or as root"


}

ensure_safe_cwd() { cd / 2>/dev/null || true; }







install_prereqs() {
  info "Installing prerequisites..."
  apt-get update -y
  apt-get install -y git python3 python3-venv python3-pip curl
}

# ─────────────────────── helpers for name/time ───────────────────────────

get_bot_name() {
  local d="${BASE_DIR}-${1}"
  if [[ -f "$d/.bot_name" ]]; then
    cat "$d/.bot_name"
  else
    echo "Bot #${1}"
  fi
}

save_bot_name() {
  echo "$BOT_NAME" > "$DIR/.bot_name"
}

record_update_time() {
  date '+%Y-%m-%d %H:%M:%S' > "$DIR/.last_update"
}

get_last_update() {
  local d="${BASE_DIR}-${1}"
  if [[ -f "$d/.last_update" ]]; then
    cat "$d/.last_update"
  else
    echo "Never"
  fi
}

get_service_status() {
  local svc="${BASE_SERVICE}-${1}"
  if systemctl is-active "$svc" >/dev/null 2>&1; then
    echo -e "${G}🟢 Online${N}"
  else
    echo -e "${R}🔴 Offline${N}"
  fi
}

get_autoupdate_status_label() {
  local svc="${BASE_SERVICE}-${1}-autoupdate"
  if systemctl is-active "${svc}.timer" >/dev/null 2>&1; then
    echo -e "${G}[ON]${N}"
  else
    echo -e "${R}[OFF]${N}"
  fi
}

# ─────────────────────────── repo / venv ───────────────────────────

clone_or_update_repo() {
  info "Downloading ConfigFlow..."

  mkdir -p "$DIR"

  if [[ -d "$DIR/.git" ]]; then
    info "Repository exists. Updating..."
    cd "$DIR"
@@ -83,39 +122,38 @@ clone_or_update_repo() {
    git clone "$REPO" "$DIR"
    cd "$DIR"
  fi
  [[ -f "$DIR/main.py" ]]          || err "main.py not found after download."

  [[ -f "$DIR/requirements.txt" ]] || err "requirements.txt not found after download."
  record_update_time
}

setup_venv() {
  info "Setting up Python environment..."
  [[ -d "$DIR/venv" ]] || python3 -m venv "$DIR/venv"



  "$DIR/venv/bin/pip" install --upgrade pip wheel
  "$DIR/venv/bin/pip" install -r "$DIR/requirements.txt"
}

# ─────────────────────────── configure ───────────────────────────

configure_env() {
  echo ""
  echo -e "${C}╔══════════════════════════════════════════════════════════════════════════╗${N}"
  echo -e "${C}║${N}              ${B}${W}⚙️  Bot Configuration: ${BOT_NAME}${N}"
  echo -e "${C}╚══════════════════════════════════════════════════════════════════════════╝${N}"
  echo ""

  echo -e "${Y}📌 Get your bot token from ${B}@BotFather${N}${Y} on Telegram.${N}"
  echo ""
  read -r -p "$(echo -e "${B}🔑 Telegram Bot Token: ${N}")" INPUT_TOKEN
  INPUT_TOKEN="${INPUT_TOKEN// /}"
  [[ -n "$INPUT_TOKEN" ]]                      || err "Token cannot be empty"
  [[ "$INPUT_TOKEN" =~ ^[0-9]+:.+$ ]]         || err "Invalid token format. Example: 123456789:ABCdef..."

  echo ""
  echo -e "${Y}📌 Send a message to ${B}@userinfobot${N}${Y} on Telegram to get your Chat ID.${N}"
  echo ""
  read -r -p "$(echo -e "${B}Admin Chat ID (numeric): ${N}")" INPUT_ADMIN
  INPUT_ADMIN="${INPUT_ADMIN// /}"
  [[ "$INPUT_ADMIN" =~ ^-?[0-9]+$ ]] || err "Admin ID must be numeric"

@@ -136,41 +174,41 @@ EOF
configure_iran_worker() {
  echo ""
  echo -e "${C}╔══════════════════════════════════════════════════════════════════════════╗${N}"
  echo -e "${C}║${N}        ${B}${W}🇮🇷  Iran Worker (3x-ui) Configuration — ${BOT_NAME}${N}"
  echo -e "${C}╚══════════════════════════════════════════════════════════════════════════╝${N}"
  echo ""

  read -r -p "$(echo -e "${B}🌐 Panel IP (default 127.0.0.1): ${N}")" INPUT_PANEL_IP
  INPUT_PANEL_IP="${INPUT_PANEL_IP:-127.0.0.1}"

  read -r -p "$(echo -e "${B}🔌 Panel port (default 2053): ${N}")" INPUT_PANEL_PORT
  INPUT_PANEL_PORT="${INPUT_PANEL_PORT:-2053}"
  [[ "$INPUT_PANEL_PORT" =~ ^[0-9]+$ ]] || err "Port must be numeric"

  read -r -p "$(echo -e "${B}📄 Path (optional, e.g. /xui — press Enter to skip): ${N}")" INPUT_PATCH
  INPUT_PATCH="${INPUT_PATCH:-}"

  read -r -p "$(echo -e "${B}👤 Panel username: ${N}")" INPUT_PANEL_USER
  [[ -n "$INPUT_PANEL_USER" ]] || err "Username cannot be empty"

  read -r -s -p "$(echo -e "${B}🔑 Panel password: ${N}")" INPUT_PANEL_PASS
  echo ""
  [[ -n "$INPUT_PANEL_PASS" ]] || err "Password cannot be empty"

  read -r -p "$(echo -e "${B}🆔 Inbound ID (default 1): ${N}")" INPUT_INBOUND_ID
  INPUT_INBOUND_ID="${INPUT_INBOUND_ID:-1}"
  [[ "$INPUT_INBOUND_ID" =~ ^[0-9]+$ ]] || err "Inbound ID must be numeric"

  read -r -p "$(echo -e "${B}🔐 Worker API Key (min 16 chars; press Enter to auto-generate): ${N}")" INPUT_WORKER_KEY
  if [[ -z "$INPUT_WORKER_KEY" ]]; then
    INPUT_WORKER_KEY=$(tr -dc 'A-Za-z0-9' </dev/urandom 2>/dev/null | head -c 32 || openssl rand -hex 16)
  fi
  [[ ${#INPUT_WORKER_KEY} -ge 16 ]] || err "API key must be at least 16 characters"

  read -r -p "$(echo -e "${B}🌍 Bot API URL (e.g. http://foreign-server:8080): ${N}")" INPUT_API_URL
  [[ -n "$INPUT_API_URL" ]] || err "Bot API URL cannot be empty"

  read -r -p "$(echo -e "${B}⏱ Poll interval (seconds, default 10): ${N}")" INPUT_POLL
  INPUT_POLL="${INPUT_POLL:-10}"
  [[ "$INPUT_POLL" =~ ^[0-9]+$ ]] || err "Poll interval must be numeric"

@@ -188,20 +226,22 @@ PROTOCOL=vless
ENVEOF
  chmod 600 "$DIR/config.env"
  echo ""
  ok "Worker configuration saved to $DIR/config.env"
  echo -e "${Y}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${N}"
  echo -e "${B}${W}   ⚠️  Save this API Key for the bot admin panel:${N}"
  echo -e "   ${B}${G}WORKER_API_KEY = ${INPUT_WORKER_KEY}${N}"
  echo -e "${Y}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${N}"
  echo ""
  read -r -p "Press Enter to continue..."
}

# ─────────────────────────── systemd ───────────────────────────

create_systemd_service() {
  info "Creating systemd service for ${SERVICE}..."
  cat > "/etc/systemd/system/$SERVICE.service" << EOF
[Unit]
Description=ConfigFlow Telegram Bot — ${BOT_NAME}
After=network.target

[Service]
@@ -215,17 +255,16 @@ RestartSec=5
[Install]
WantedBy=multi-user.target
EOF

  systemctl daemon-reload
  systemctl enable "$SERVICE" >/dev/null 2>&1 || true
}

create_worker_service() {
  [[ -f "$DIR/config.env" ]] || return 0
  info "Creating Iran Worker service for ${SERVICE}-worker..."
  cat > "/etc/systemd/system/${SERVICE}-worker.service" << EOF
[Unit]
Description=ConfigFlow Iran Worker — ${BOT_NAME}
After=network.target

[Service]
@@ -243,41 +282,167 @@ WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable "${SERVICE}-worker" >/dev/null 2>&1 || true
  ok "Worker service created: ${SERVICE}-worker"
}

start_service() {
  systemctl restart "$SERVICE"
  echo ""
  echo -e "${G}╔══════════════════════════════════════════════════════════════════════════╗${N}"
  echo -e "${G}║${N}        ${B}${G}✅  ${BOT_NAME} installed and started!${N}                          ${G}║${N}"
  echo -e "${G}╚══════════════════════════════════════════════════════════════════════════╝${N}"
  echo ""
  systemctl status "$SERVICE" --no-pager -l || true
}

# ─────────────────────────── auto-update ───────────────────────────

enable_auto_update() {
  ensure_safe_cwd
  [[ -d "$DIR/.git" ]] || err "Bot not installed. Please install first."

  local AUTOUPDATE_SCRIPT="$DIR/auto_update.sh"
  local AUTOUPDATE_SVC="${SERVICE}-autoupdate"

  info "Creating auto-update script..."
  cat > "$AUTOUPDATE_SCRIPT" << EOFSCRIPT
#!/bin/bash
cd "$DIR" || exit 1
git fetch --all --prune 2>/dev/null
LOCAL=\$(git rev-parse HEAD)
REMOTE=\$(git rev-parse origin/main)
if [[ "\$LOCAL" != "\$REMOTE" ]]; then
  echo "\$(date '+%Y-%m-%d %H:%M:%S') — Update found (\${LOCAL:0:7} → \${REMOTE:0:7}), updating..."
  git reset --hard origin/main
  "$DIR/venv/bin/pip" install -r "$DIR/requirements.txt" -q
  date '+%Y-%m-%d %H:%M:%S' > "$DIR/.last_update"
  systemctl restart "$SERVICE"
  echo "\$(date '+%Y-%m-%d %H:%M:%S') — Updated and restarted $SERVICE"
else
  echo "\$(date '+%Y-%m-%d %H:%M:%S') — Already up to date (\${LOCAL:0:7})"
fi
EOFSCRIPT
  chmod +x "$AUTOUPDATE_SCRIPT"

  cat > "/etc/systemd/system/${AUTOUPDATE_SVC}.service" << EOF
[Unit]
Description=ConfigFlow Auto Update — ${BOT_NAME}
After=network.target

[Service]
Type=oneshot
ExecStart=$AUTOUPDATE_SCRIPT
StandardOutput=append:$DIR/autoupdate.log
StandardError=append:$DIR/autoupdate.log
EOF

  cat > "/etc/systemd/system/${AUTOUPDATE_SVC}.timer" << EOF
[Unit]
Description=ConfigFlow Auto Update Timer — ${BOT_NAME}

[Timer]
OnCalendar=minutely
Persistent=true

[Install]
WantedBy=timers.target
EOF

  systemctl daemon-reload
  systemctl enable "${AUTOUPDATE_SVC}.timer" >/dev/null 2>&1
  systemctl start  "${AUTOUPDATE_SVC}.timer"
  echo ""
  ok "Auto-update enabled for ${BOT_NAME} (checked every minute)"
  echo -e "${Y}Log: $DIR/autoupdate.log${N}"
  echo ""
}

disable_auto_update() {
  ensure_safe_cwd
  local AUTOUPDATE_SVC="${SERVICE}-autoupdate"
  systemctl stop    "${AUTOUPDATE_SVC}.timer"   2>/dev/null || true
  systemctl disable "${AUTOUPDATE_SVC}.timer"   2>/dev/null || true
  systemctl stop    "${AUTOUPDATE_SVC}.service" 2>/dev/null || true
  rm -f "/etc/systemd/system/${AUTOUPDATE_SVC}.timer"
  rm -f "/etc/systemd/system/${AUTOUPDATE_SVC}.service"
  rm -f "$DIR/auto_update.sh"
  systemctl daemon-reload
  ok "Auto-update disabled for ${BOT_NAME}."
}

toggle_auto_update() {
  local AUTOUPDATE_SVC="${SERVICE}-autoupdate"
  if systemctl is-active "${AUTOUPDATE_SVC}.timer" >/dev/null 2>&1; then
    disable_auto_update
  else
    enable_auto_update
  fi
  read -r -p "Press Enter to continue..."
}

# ─────────────────────────── install / update / remove ───────────────────────────

install_bot() {
  ensure_safe_cwd
  install_prereqs
  clone_or_update_repo
  save_bot_name
  setup_venv
  configure_env
  create_systemd_service
  # auto-update by default
  enable_auto_update
  start_service
}

update_bot() {
  ensure_safe_cwd
  [[ -d "$DIR/.git" ]] || err "Not installed. Please install first."
  info "Updating ${BOT_NAME}..."
  clone_or_update_repo
  setup_venv
  systemctl restart "$SERVICE"
  ok "Update of ${BOT_NAME} completed!"
}

edit_config() {
  ensure_safe_cwd
  [[ -f "$DIR/.env" ]] || err "Config file not found. Please install first."
  nano "$DIR/.env"
  systemctl restart "$SERVICE"
  ok "Settings saved and bot restarted!"
}

remove_bot() {
  ensure_safe_cwd
  read -r -p "Are you sure you want to remove ${BOT_NAME}? (yes/no): " confirm
  [[ "$confirm" == "yes" ]] || { info "Cancelled"; return; }

  for svc in "$SERVICE" "${SERVICE}-worker"; do
    systemctl stop    "$svc" 2>/dev/null || true
    systemctl disable "$svc" 2>/dev/null || true
    rm -f "/etc/systemd/system/${svc}.service"
  done
  systemctl stop    "${SERVICE}-autoupdate.timer"   2>/dev/null || true
  systemctl disable "${SERVICE}-autoupdate.timer"   2>/dev/null || true
  systemctl stop    "${SERVICE}-autoupdate.service" 2>/dev/null || true
  rm -f "/etc/systemd/system/${SERVICE}-autoupdate.timer"
  rm -f "/etc/systemd/system/${SERVICE}-autoupdate.service"
  systemctl daemon-reload
  rm -rf "$DIR"
  ok "${BOT_NAME} has been completely removed"
}

install_worker() {
  echo ""
  echo -e "${C}┌──────────────────────────────────────┐${N}"
  echo -e "${C}│${N}    ${B}${W}📦 Worker Installation Source${N}               ${C}│${N}"
  echo -e "${C}├──────────────────────────────────────┤${N}"
  echo -e "${C}│${N}  ${B}${G}g)${N} 🌐 Install from GitHub                 ${C}│${N}"
  echo -e "${C}│${N}  ${B}${M}l)${N} 📁 Install from local files          ${C}│${N}"


  echo -e "${C}└──────────────────────────────────────┘${N}"
  echo ""
  read -r -p "$(echo -e "${B}Select [g/l]: ${N}")" src_choice
  case "${src_choice:-}" in
    g) _install_worker_github ;;
    l) _install_worker_local  ;;
@@ -293,182 +458,336 @@ _install_worker_github() {
  create_worker_service
  systemctl restart "${SERVICE}-worker"
  echo ""
  ok "Iran Worker for ${BOT_NAME} installed and started!"



  systemctl status "${SERVICE}-worker" --no-pager -l || true
}

_install_worker_local() {
  ensure_safe_cwd
  info "Installing Iran Worker from local files: $SCRIPT_DIR"


  local missing=0
  for f in worker.py requirements.txt; do
    [[ -f "$SCRIPT_DIR/$f" ]] || { echo -e "${R}✗ Missing file: $SCRIPT_DIR/$f${N}" >&2; missing=1; }



  done
  [[ $missing -eq 0 ]] || err "Place the required files next to install.sh"


  install_prereqs


  mkdir -p "$DIR"
  for f in worker.py requirements.txt; do
    cp -v "$SCRIPT_DIR/$f" "$DIR/$f"
    ok "Copied $f → $DIR/$f"
  done

  [[ -f "$SCRIPT_DIR/config.env.example" ]] && cp "$SCRIPT_DIR/config.env.example" "$DIR/config.env.example" || true


  setup_venv

  configure_iran_worker
  create_worker_service
  systemctl restart "${SERVICE}-worker"
  echo ""
  ok "Iran Worker for ${BOT_NAME} installed and started!"



  systemctl status "${SERVICE}-worker" --no-pager -l || true
}

# ─────────────────────────── BULK OPERATIONS ───────────────────────────

all_instances() {
  local list=()
  for d in /opt/configflow-*/; do
    [[ -d "$d" ]] || continue
    local num; num="$(basename "$d" | sed 's/configflow-//')"
    [[ "$num" =~ ^[0-9]+$ ]] && list+=("$num")
  done
  echo "${list[@]:-}"
}

bulk_update_all() {
  local instances; instances="$(all_instances)"
  [[ -n "$instances" ]] || { echo -e "${Y}No installed bots found.${N}"; read -r -p "Enter..."; return; }
  for num in $instances; do
    DIR="${BASE_DIR}-${num}"
    SERVICE="${BASE_SERVICE}-${num}"
    BOT_NAME="$(get_bot_name "$num")"
    echo ""
    echo -e "${C}━━━ Updating ${BOT_NAME} (instance ${num}) ━━━${N}"
    [[ -d "$DIR/.git" ]] || { echo -e "${R}✗ Not installed, skipping.${N}"; continue; }
    clone_or_update_repo
    setup_venv
    systemctl restart "$SERVICE" 2>/dev/null || true
    ok "${BOT_NAME} updated"
  done
  echo ""
  read -r -p "Press Enter to continue..."
}

bulk_enable_autoupdate() {
  local instances; instances="$(all_instances)"
  [[ -n "$instances" ]] || { echo -e "${Y}No installed bots found.${N}"; read -r -p "Enter..."; return; }
  for num in $instances; do
    DIR="${BASE_DIR}-${num}"
    SERVICE="${BASE_SERVICE}-${num}"
    BOT_NAME="$(get_bot_name "$num")"
    echo ""
    echo -e "${C}━━━ Enabling auto-update for ${BOT_NAME} ━━━${N}"
    [[ -d "$DIR/.git" ]] || { echo -e "${R}✗ Not installed, skipping.${N}"; continue; }
    enable_auto_update
  done
  echo ""
  read -r -p "Press Enter to continue..."
}

bulk_disable_autoupdate() {
  local instances; instances="$(all_instances)"
  [[ -n "$instances" ]] || { echo -e "${Y}No installed bots found.${N}"; read -r -p "Enter..."; return; }
  for num in $instances; do
    DIR="${BASE_DIR}-${num}"
    SERVICE="${BASE_SERVICE}-${num}"
    BOT_NAME="$(get_bot_name "$num")"
    echo ""
    echo -e "${C}━━━ Disabling auto-update for ${BOT_NAME} ━━━${N}"
    disable_auto_update
  done
  echo ""
  read -r -p "Press Enter to continue..."
}

bulk_restart_all() {
  local instances; instances="$(all_instances)"
  [[ -n "$instances" ]] || { echo -e "${Y}No installed bots found.${N}"; read -r -p "Enter..."; return; }
  for num in $instances; do
    local svc="${BASE_SERVICE}-${num}"
    local name; name="$(get_bot_name "$num")"
    systemctl restart "$svc" 2>/dev/null && ok "Restarted: ${name}" || echo -e "${R}✗ Error: ${name}${N}"
  done
  echo ""
  read -r -p "Press Enter to continue..."
}

bulk_start_all() {
  local instances; instances="$(all_instances)"
  [[ -n "$instances" ]] || { echo -e "${Y}No installed bots found.${N}"; read -r -p "Enter..."; return; }
  for num in $instances; do
    local svc="${BASE_SERVICE}-${num}"
    local name; name="$(get_bot_name "$num")"
    systemctl start "$svc" 2>/dev/null && ok "Started: ${name}" || echo -e "${R}✗ Error: ${name}${N}"
  done
  echo ""
  read -r -p "Press Enter to continue..."
}

bulk_stop_all() {
  local instances; instances="$(all_instances)"
  [[ -n "$instances" ]] || { echo -e "${Y}No installed bots found.${N}"; read -r -p "Enter..."; return; }
  for num in $instances; do
    local svc="${BASE_SERVICE}-${num}"
    local name; name="$(get_bot_name "$num")"
    systemctl stop "$svc" 2>/dev/null && ok "Stopped: ${name}" || echo -e "${R}✗ Error: ${name}${N}"
  done
  echo ""
  read -r -p "Press Enter to continue..."
}

bulk_remove_all() {
  local instances; instances="$(all_instances)"
  [[ -n "$instances" ]] || { echo -e "${Y}No installed bots found.${N}"; read -r -p "Enter..."; return; }
  echo -e "${R}⚠️  This will remove ALL bots!${N}"
  read -r -p "Type DELETE ALL to confirm: " confirm
  [[ "$confirm" == "DELETE ALL" ]] || { info "Cancelled"; read -r -p "Press Enter to continue..."; return; }
  for num in $instances; do
    DIR="${BASE_DIR}-${num}"
    SERVICE="${BASE_SERVICE}-${num}"
    BOT_NAME="$(get_bot_name "$num")"
    echo ""
    echo -e "${C}━━━ Removing ${BOT_NAME} ━━━${N}"
    for svc in "$SERVICE" "${SERVICE}-worker"; do
      systemctl stop    "$svc" 2>/dev/null || true
      systemctl disable "$svc" 2>/dev/null || true
      rm -f "/etc/systemd/system/${svc}.service"
    done
    systemctl stop    "${SERVICE}-autoupdate.timer"   2>/dev/null || true
    systemctl disable "${SERVICE}-autoupdate.timer"   2>/dev/null || true
    rm -f "/etc/systemd/system/${SERVICE}-autoupdate.timer"
    rm -f "/etc/systemd/system/${SERVICE}-autoupdate.service"
    rm -rf "$DIR"
    ok "${BOT_NAME} removed"
  done
  systemctl daemon-reload
  echo ""
  read -r -p "Press Enter to continue..."
}

# ─────────────────────────── menus ───────────────────────────

list_instances_table() {
  local found=0
  echo -e "${C}┌────┬────────────────────────────┬───────────────┬──────────────────────┐${N}"
  echo -e "${C}│${N} ${B}${W}#${N}  ${C}│${N} ${B}${W}Bot Name${N}                    ${C}│${N} ${B}${W}Status${N}         ${C}│${N} ${B}${W}Last Update${N}          ${C}│${N}"
  echo -e "${C}├────┼────────────────────────────┼───────────────┼──────────────────────┤${N}"
  for d in /opt/configflow-*/; do
    [[ -d "$d" ]] || continue
    local num; num="$(basename "$d" | sed 's/configflow-//')"
    [[ "$num" =~ ^[0-9]+$ ]] || continue
    local name; name="$(get_bot_name "$num")"
    local svc="${BASE_SERVICE}-${num}"
    local status_raw status_str
    if systemctl is-active "$svc" >/dev/null 2>&1; then
      status_str="${G}🟢 Online   ${N}"
    else
      status_str="${R}🔴 Offline${N}"
    fi
    local last; last="$(get_last_update "$num")"
    printf "${C}│${N} %-2s ${C}│${N} %-26s ${C}│${N} " "$num" "$name"
    echo -ne "$status_str"
    printf " ${C}│${N} %-20s ${C}│${N}\n" "$last"
    found=1
  done
  if [[ $found -eq 0 ]]; then
    echo -e "${C}│${N}               ${Y}No bots installed${N}                              ${C}│${N}"
  fi
  echo -e "${C}└────┴────────────────────────────┴───────────────┴──────────────────────┘${N}"
  echo ""
}

show_global_menu() {
  echo -e "${C}┌──────────────────────────────────────────┐${N}"
  echo -e "${C}│${N}       ${B}${W}🌐 Main Menu — ConfigFlow${N}         ${C}│${N}"
  echo -e "${C}├──────────────────────────────────────────┤${N}"
  echo -e "${C}│${N}  ${B}${G}m)${N} 🤖 Manage a bot (select number)    ${C}│${N}"
  echo -e "${C}├──────────────────────────────────────────┤${N}"
  echo -e "${C}│${N}  ${B}${Y}1)${N} 🔄 Update all bots                    ${C}│${N}"
  echo -e "${C}│${N}  ${B}${Y}2)${N} ⚡ Enable auto-update for all         ${C}│${N}"
  echo -e "${C}│${N}  ${B}${Y}3)${N} 🔕 Disable auto-update for all     ${C}│${N}"
  echo -e "${C}│${N}  ${B}${Y}4)${N} 🔁 Restart all bots                     ${C}│${N}"
  echo -e "${C}│${N}  ${B}${Y}5)${N} ▶️  Start all bots                     ${C}│${N}"
  echo -e "${C}│${N}  ${B}${Y}6)${N} ⏹️  Stop all bots                     ${C}│${N}"
  echo -e "${C}│${N}  ${B}${R}7)${N} 🗑️  Remove all bots                          ${C}│${N}"
  echo -e "${C}├──────────────────────────────────────────┤${N}"
  echo -e "${C}│${N}  ${B}${R}0)${N} 🚪 Exit                            ${C}│${N}"
  echo -e "${C}└──────────────────────────────────────────┘${N}"
  echo ""
}

show_bot_header() {
  local au_status; au_status="$(get_autoupdate_status_label "$INSTANCE_NUM")"
  local bot_status; bot_status="$(get_service_status "$INSTANCE_NUM")"
  local last_upd; last_upd="$(get_last_update "$INSTANCE_NUM")"
  echo -e "${C}╔══════════════════════════════════════════════════════════════════════════╗${N}"
  echo -e "${C}║${N}  🤖 ${B}${W}${BOT_NAME}${N}  (instance ${INSTANCE_NUM})                                        ${C}║${N}"
  echo -e "${C}║${N}  Status: $bot_status   │  Auto-update: $au_status   │  Last update: ${W}${last_upd}${N}  ${C}║${N}"
  echo -e "${C}╚══════════════════════════════════════════════════════════════════════════╝${N}"
  echo ""
}

show_bot_menu() {
  local au_label; au_label="$(get_autoupdate_status_label "$INSTANCE_NUM")"
  echo -e "${C}┌──────────────────────────────────────┐${N}"
  echo -e "${C}│${N}  ${B}${G}1)${N} 📦 Install / Reinstall                     ${C}│${N}"
  echo -e "${C}│${N}  ${B}${G}2)${N} 🔄 Update from GitHub                 ${C}│${N}"
  echo -e "${C}│${N}  ${B}${G}3)${N} ✏️  Edit settings (.env)            ${C}│${N}"
  echo -e "${C}│${N}  ${B}${G}4)${N} ▶️  Start                                       ${C}│${N}"
  echo -e "${C}│${N}  ${B}${G}5)${N} ⏹️  Stop                                       ${C}│${N}"
  echo -e "${C}│${N}  ${B}${G}6)${N} 🔁 Restart                                       ${C}│${N}"
  echo -e "${C}│${N}  ${B}${G}7)${N} 📜 Live log                                      ${C}│${N}"
  echo -e "${C}│${N}  ${B}${G}8)${N} 📊 Service status                             ${C}│${N}"
  echo -e "${C}│${N}  ${B}${G}9)${N} 🗑️  Remove this bot                          ${C}│${N}"
  echo -e "${C}│${N}  ${B}${C}a)${N} ⚡ Auto-update: $au_label           ${C}│${N}"
  echo -e "${C}│${N}  ${B}${C}u)${N} 📋 Auto-update log                    ${C}│${N}"
  echo -e "${C}│${N}  ${B}${M}i)${N} 🇮🇷 Install Iran Worker (3x-ui)      ${C}│${N}"
  echo -e "${C}│${N}  ${B}${M}w)${N} 📋 Worker log                                ${C}│${N}"
  echo -e "${C}│${N}  ${B}${M}W)${N} 🔁 Restart                  Worker              ${C}│${N}"
  echo -e "${C}│${N}  ${B}${R}b)${N} 🔙 Back to main menu               ${C}│${N}"
  echo -e "${C}└──────────────────────────────────────┘${N}"
  echo ""
}

# ─────────────────────────── instance selection ───────────────────────────

select_instance() {
  echo ""
  list_instances_table
  echo -e "${Y}📌 Enter the bot number (e.g. 1, 2, 3 ...).${N}"
  echo -e "${Y}   Each number is a separate bot with its own settings and database.${N}"
  echo ""
  read -r -p "$(echo -e "${B}🔢 Bot number: ${N}")" INSTANCE_NUM
  INSTANCE_NUM="${INSTANCE_NUM// /}"
  [[ "$INSTANCE_NUM" =~ ^[0-9]+$ ]] || err "Number must be a positive integer (e.g. 1, 2, 3)"
  [[ "$INSTANCE_NUM" -ge 1 ]]       || err "Number must be >= 1"

  DIR="${BASE_DIR}-${INSTANCE_NUM}"
  SERVICE="${BASE_SERVICE}-${INSTANCE_NUM}"

  # If new instance → ask for a name
  if [[ ! -f "$DIR/.bot_name" ]]; then
    echo ""
    echo -e "${Y}📌 This is a new bot. Enter a name for easy identification.${N}"
    read -r -p "$(echo -e "${B}📛 Bot name (e.g. "Main Sales Bot"): ${N}")" INPUT_BOT_NAME
    INPUT_BOT_NAME="${INPUT_BOT_NAME:-Bot #${INSTANCE_NUM}}"
    BOT_NAME="$INPUT_BOT_NAME"
    mkdir -p "$DIR"
    save_bot_name
  else
    BOT_NAME="$(get_bot_name "$INSTANCE_NUM")"
  fi

  echo ""
  ok "Bot selected: ${B}${BOT_NAME}${N}  (dir: $DIR  service: $SERVICE)"
  echo ""
}

# ─────────────────────────── main loops ───────────────────────────

bot_loop() {
  while true; do
    header
    show_bot_header
    show_bot_menu

    read -r -p "$(echo -e "${C}${BOT_NAME}${N} ${B}➜${N} option ${W}[0-9/a/u/i/w/W/b]${N}: ")" choice

    case "${choice:-}" in
      1) install_bot; read -r -p "Enter...";;
      2) update_bot;  read -r -p "Enter...";;
      3) edit_config ;;
      4) systemctl start   "$SERVICE" 2>/dev/null && ok "Started: ${BOT_NAME}";   read -r -p "Enter...";;
      5) systemctl stop    "$SERVICE" 2>/dev/null && ok "Stopped: ${BOT_NAME}";  read -r -p "Enter...";;
      6) systemctl restart "$SERVICE" 2>/dev/null && ok "Restarted: ${BOT_NAME}"; read -r -p "Enter...";;
      7) echo -e "${Y}Press Ctrl+C to exit log${N}"; sleep 1; journalctl -u "$SERVICE" -f;;
      8) systemctl status "$SERVICE" --no-pager -l; read -r -p "Enter...";;
      9) remove_bot; read -r -p "Enter..."; return;;
      a) toggle_auto_update ;;
      u) echo -e "${Y}Press Ctrl+C to exit log${N}"; sleep 1
         tail -f "$DIR/autoupdate.log" 2>/dev/null || echo -e "${R}Log file not found.${N}"
         read -r -p "Enter...";;
      i) install_worker; read -r -p "Enter...";;
      w) echo -e "${Y}Press Ctrl+C to exit log${N}"; sleep 1; journalctl -u "${SERVICE}-worker" -f;;
      W) systemctl restart "${SERVICE}-worker" 2>/dev/null && ok "Worker restarted"; read -r -p "Enter...";;
      b) return;;
      *) echo -e "${R}Invalid option${N}"; sleep 1;;
    esac
  done
}

main() {

  [[ -t 0 ]] || exec < /dev/tty
  check_root
  ensure_safe_cwd


  while true; do
    header
    list_instances_table
    show_global_menu


    read -r -p "$(echo -e "${C}ConfigFlow${N} ${B}➜${N} option ${W}[m/1-7/0]${N}: ")" choice

    case "${choice:-}" in
      m)
        select_instance
        bot_loop
        ;;
      1) header; bulk_update_all ;;
      2) header; bulk_enable_autoupdate ;;
      3) header; bulk_disable_autoupdate ;;
      4) header; bulk_restart_all ;;
      5) header; bulk_start_all ;;
      6) header; bulk_stop_all ;;
      7) header; bulk_remove_all ;;

      0) echo "Goodbye!"; exit 0;;
      *) echo -e "${R}Invalid option${N}"; sleep 1;;
    esac
