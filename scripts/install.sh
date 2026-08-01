#!/usr/bin/env bash
#
# One-shot setup for FactorioReforge: download the Factorio headless server,
# create a map, build a Python environment, and write a working config.yml.
#
#   ./scripts/install.sh                    # interactive, asks before overwriting
#   ./scripts/install.sh --yes              # accept defaults, never prompt
#   ./scripts/install.sh --version 2.0.77   # pin a Factorio version
#   ./scripts/install.sh --no-server        # only set up Python (server exists)
#
# Safe to re-run: anything already in place is left alone unless you pass
# --force, and your existing config.yml is never overwritten without asking.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

SERVER_DIR="$REPO_ROOT/server"
FACTORIO_DIR="$SERVER_DIR/factorio"
BINARY="$FACTORIO_DIR/bin/x64/factorio"
SAVE_NAME="reforge.zip"
VENV="$REPO_ROOT/.venv"

FACTORIO_VERSION="stable"
ASSUME_YES=0
FORCE=0
SKIP_SERVER=0
GAME_PORT=34197
RCON_PORT=27015
RCON_PASSWORD=""

bold=$'\033[1m'; dim=$'\033[2m'; red=$'\033[31m'; green=$'\033[32m'
yellow=$'\033[33m'; reset=$'\033[0m'

step()  { printf '\n%s==>%s %s%s\n' "$green$bold" "$reset$bold" "$*" "$reset"; }
info()  { printf '    %s\n' "$*"; }
note()  { printf '    %s%s%s\n' "$dim" "$*" "$reset"; }
warn()  { printf '%s[!]%s %s\n' "$yellow" "$reset" "$*" >&2; }
die()   { printf '%s[x]%s %s\n' "$red" "$reset" "$*" >&2; exit 1; }

confirm() {
  [[ $ASSUME_YES -eq 1 ]] && return 0
  local reply
  read -r -p "    $1 [y/N] " reply
  [[ $reply == [yY] || $reply == [yY][eE][sS] ]]
}

usage() {
  # Print the header comment block: everything from line 2 up to the first
  # line that is not a comment. Keeps the help text and the file in sync.
  awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "${BASH_SOURCE[0]}"
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -y|--yes)      ASSUME_YES=1 ;;
    --force)       FORCE=1 ;;
    --no-server)   SKIP_SERVER=1 ;;
    --version)     FACTORIO_VERSION="${2:?--version needs a value}"; shift ;;
    --port)        GAME_PORT="${2:?--port needs a value}"; shift ;;
    --rcon-port)   RCON_PORT="${2:?--rcon-port needs a value}"; shift ;;
    --rcon-password) RCON_PASSWORD="${2:?--rcon-password needs a value}"; shift ;;
    -h|--help)     usage ;;
    *)             die "unknown option: $1  (try --help)" ;;
  esac
  shift
done

# ---------------------------------------------------------------------------
# 0. Prerequisites
# ---------------------------------------------------------------------------

step "Checking prerequisites"

need() { command -v "$1" >/dev/null 2>&1 || die "$1 is required but not installed"; }
need curl
need tar

PYTHON=""
for candidate in python3.14 python3.13 python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
      PYTHON="$candidate"
      break
    fi
  fi
done
[[ -n $PYTHON ]] || die "Python 3.11 or newer is required; none found on PATH"
info "Python: $($PYTHON --version) at $(command -v "$PYTHON")"

"$PYTHON" -c 'import venv' 2>/dev/null \
  || die "the venv module is missing (on Debian/Ubuntu: apt install python3-venv)"

if ! command -v xz >/dev/null 2>&1 && ! tar --help 2>&1 | grep -q -- '--xz'; then
  warn "xz not found; extracting the headless tarball may fail"
fi

# ---------------------------------------------------------------------------
# 1. Factorio headless server
# ---------------------------------------------------------------------------

if [[ $SKIP_SERVER -eq 1 ]]; then
  step "Skipping the Factorio download (--no-server)"
elif [[ -x $BINARY && $FORCE -eq 0 ]]; then
  step "Factorio headless is already installed"
  info "$("$BINARY" --version | head -1)"
  note "pass --force to download it again"
else
  step "Downloading Factorio headless ($FACTORIO_VERSION)"
  mkdir -p "$SERVER_DIR"
  TARBALL="$SERVER_DIR/factorio-headless.tar.xz"
  URL="https://factorio.com/get-download/${FACTORIO_VERSION}/headless/linux64"
  info "$URL"
  curl -fL --progress-bar -o "$TARBALL" "$URL" \
    || die "download failed -- check the version string and your connection"

  # Guard against a redirect to an HTML error page being unpacked as a tarball.
  file_head=$(head -c 6 "$TARBALL" | tr -d '\0')
  [[ $file_head == $'\xfd7zXZ'* ]] || warn "the download does not look like an .xz archive"

  step "Extracting"
  tar -xJf "$TARBALL" -C "$SERVER_DIR"
  rm -f "$TARBALL"
  [[ -x $BINARY ]] || die "extraction finished but $BINARY is missing"
  info "$("$BINARY" --version | head -1)"
fi

# ---------------------------------------------------------------------------
# 2. Server configuration and a map
# ---------------------------------------------------------------------------

if [[ -x $BINARY ]]; then
  step "Setting up server configuration"

  if [[ -z $RCON_PASSWORD ]]; then
    # A password nobody chose is better than a password everyone knows.
    RCON_PASSWORD=$("$PYTHON" -c 'import secrets; print(secrets.token_urlsafe(18))')
    note "generated a random RCON password"
  fi

  if [[ -f "$FACTORIO_DIR/server-settings.json" && $FORCE -eq 0 ]]; then
    info "server-settings.json already exists, leaving it alone"
  else
    "$PYTHON" - "$FACTORIO_DIR" <<'PY'
import json, pathlib, sys
d = pathlib.Path(sys.argv[1])
settings = json.loads((d / "data/server-settings.example.json").read_text())
settings.update({
    "name": "FactorioReforge Server",
    "description": "managed by FactorioReforge",
    "visibility": {"public": False, "lan": True},
    "username": "", "token": "", "game_password": "",
    "require_user_verification": True,
    "allow_commands": "admins-only",
    "autosave_interval": 10,
    "autosave_slots": 5,
    "auto_pause": True,
    "non_blocking_saving": True,
})
(d / "server-settings.json").write_text(json.dumps(settings, indent=2))
PY
    info "wrote server-settings.json"
  fi

  for list in adminlist whitelist banlist; do
    target="$FACTORIO_DIR/server-$list.json"
    [[ -f $target ]] || echo '[]' > "$target"
  done
  info "admin / whitelist / ban lists ready"
  note "add yourself: edit server/factorio/server-adminlist.json"

  if [[ -f "$FACTORIO_DIR/saves/$SAVE_NAME" && $FORCE -eq 0 ]]; then
    info "save $SAVE_NAME already exists, keeping it"
  else
    step "Creating a map (this takes a moment)"
    ( cd "$FACTORIO_DIR" && ./bin/x64/factorio --create "./saves/$SAVE_NAME" >/dev/null ) \
      || die "map creation failed"
    info "created saves/$SAVE_NAME"
  fi
fi

# ---------------------------------------------------------------------------
# 3. Python environment
# ---------------------------------------------------------------------------

step "Building the Python environment"

if [[ -d $VENV && $FORCE -eq 0 ]]; then
  info ".venv already exists, reusing it"
else
  rm -rf "$VENV"
  "$PYTHON" -m venv "$VENV"
  info "created .venv"
fi

"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -e ".[console,telegram,dev]" \
  || die "installing dependencies failed"
info "installed FactorioReforge and its optional extras"

# ---------------------------------------------------------------------------
# 4. config.yml
# ---------------------------------------------------------------------------

step "Writing FactorioReforge configuration"

mkdir -p plugins config logs snapshots

if [[ -f config.yml && $FORCE -eq 0 ]]; then
  info "config.yml already exists, leaving it alone"
  note "delete it (or pass --force) to regenerate"
else
  if [[ -f config.yml ]] && ! confirm "Overwrite the existing config.yml?"; then
    info "keeping the existing config.yml"
  else
    "$VENV/bin/python" - "$GAME_PORT" "$RCON_PORT" "$RCON_PASSWORD" <<'PY'
import sys
from pathlib import Path
from factorio_reforge.config import Config

game_port, rcon_port, rcon_password = sys.argv[1], sys.argv[2], sys.argv[3]
config = Config()
config.root = Path.cwd()
config.start_command = (
    "./bin/x64/factorio --start-server ./saves/reforge.zip "
    "--server-settings ./server-settings.json "
    "--server-adminlist ./server-adminlist.json "
    "--server-banlist ./server-banlist.json "
    f"--port {game_port} --rcon-port {rcon_port} --rcon-password {rcon_password}"
)
config.rcon.port = int(rcon_port)
config.rcon.password = rcon_password
config.dump(Path("config.yml"))
PY
    info "wrote config.yml"
  fi
fi

# ---------------------------------------------------------------------------
# 5. Validate the whole thing
# ---------------------------------------------------------------------------

step "Validating the configuration"

if "$VENV/bin/python" - <<'PY'
import sys
from pathlib import Path
from factorio_reforge.config import Config, ConfigError
try:
    Config.load(Path("config.yml").resolve())
except ConfigError as exc:
    print(f"    {exc}", file=sys.stderr)
    sys.exit(1)
PY
then
  info "config.yml is valid"
else
  die "config.yml did not validate -- fix the message above and re-run"
fi

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------

cat <<EOF

$(printf '%s' "$green$bold")Setup complete.$(printf '%s' "$reset")

  Start it:        ./scripts/run.sh          (or: .venv/bin/python -m factorio_reforge)
  Then try:        !!FR help
  Connect from the game:  Multiplayer -> Connect to address -> 127.0.0.1:$GAME_PORT

  Make yourself an admin in game:
    edit server/factorio/server-adminlist.json  ->  ["your_factorio_name"]

  Optional next steps:
    - Telegram control:  fill config/telegram_bridge/config.json, then !!FR reload
    - Open to the world: server/factorio/server-settings.json, and forward $GAME_PORT/udp

  Full walkthrough: docs/TUTORIAL.md  (中文: docs/TUTORIAL_zh.md)

$(printf '%s' "$dim")RCON is bound to localhost with a generated password; it is in config.yml.
config.yml, config/, logs/, snapshots/ and server/ are gitignored.$(printf '%s' "$reset")
EOF
