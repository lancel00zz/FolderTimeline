#!/bin/bash
# ──────────────────────────────────────────────────────────────────────────────
#  Folder Timeline — Setup / Upgrade / Uninstall
#  Double-click to run, or: bash setup.command
# ──────────────────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"

# ── Colors ────────────────────────────────────────────────────────────────────
BLD=$'\033[1m'
DIM=$'\033[2m'
GRN=$'\033[0;32m'
YEL=$'\033[0;33m'
RED=$'\033[0;31m'
CYN=$'\033[0;36m'
RST=$'\033[0m'

# ── Paths ─────────────────────────────────────────────────────────────────────
if [[ "$(uname -m)" == "arm64" ]]; then
    BREW_PREFIX="/opt/homebrew"
else
    BREW_PREFIX="/usr/local"
fi
BREW_BIN="$BREW_PREFIX/bin/brew"
PY3_BIN="$BREW_PREFIX/bin/python3"

SCRIPT_PATH="$HOME/bin/timeline.py"
WORKFLOW_PATH="$HOME/Library/Services/FolderTimeline.workflow"
SRC_PY="$SCRIPT_DIR/timeline.py"
SRC_WF="$SCRIPT_DIR/FolderTimeline.workflow"

# ── Version helpers ───────────────────────────────────────────────────────────
get_version() {
    grep -m1 "^VERSION = '" "$1" 2>/dev/null | sed "s/VERSION = '//;s/'.*//"
}
# Returns 0 (true) if version $1 is strictly greater than $2 (numeric comparison)
ver_gt() { awk -v a="$1" -v b="$2" 'BEGIN { exit (a + 0 > b + 0) ? 0 : 1 }'; }

# ── Source file checks ────────────────────────────────────────────────────────
if [[ ! -f "$SRC_PY" ]] || [[ ! -d "$SRC_WF" ]]; then
    clear
    echo ""
    echo "  ${RED}${BLD}Missing files${RST}"
    echo ""
    echo "  Cannot find all required files next to this script."
    echo "  Please keep all files from the downloaded folder together."
    echo ""
    read -n1 -rsp "  Press any key to close…"; echo ""; exit 1
fi

# ── Assess state ──────────────────────────────────────────────────────────────
BUNDLED_VER=$(get_version "$SRC_PY")
INSTALLED_VER=""
[[ -f "$SCRIPT_PATH" ]] && INSTALLED_VER=$(get_version "$SCRIPT_PATH")

HAS_BREW=false; HAS_PY=false; HAS_WEBVIEW=false
IS_INSTALLED=false; HAS_WORKFLOW=false

[[ -x "$BREW_BIN" ]]         && HAS_BREW=true
[[ -x "$PY3_BIN"  ]]         && HAS_PY=true
if $HAS_PY; then
    "$PY3_BIN" -c "import webview" 2>/dev/null && HAS_WEBVIEW=true || true
fi
[[ -f "$SCRIPT_PATH"   ]]    && IS_INSTALLED=true
[[ -d "$WORKFLOW_PATH" ]]    && HAS_WORKFLOW=true

# ── Header ────────────────────────────────────────────────────────────────────
clear
echo ""
echo "  ${BLD}Folder Timeline${RST}"
echo "  ──────────────────────────────────────────────"
echo ""

# ── Status report ─────────────────────────────────────────────────────────────
echo "  ${BLD}Status on this Mac${RST}"
echo ""

if $IS_INSTALLED; then
    if [[ -n "$INSTALLED_VER" ]]; then
        INST_LABEL="${GRN}v$INSTALLED_VER${RST}"
    else
        INST_LABEL="${YEL}older version (no version info)${RST}"
    fi
else
    INST_LABEL="${DIM}not installed${RST}"
fi

echo "  Installed:   $INST_LABEL"
echo "  Available:   ${CYN}v$BUNDLED_VER${RST}"
echo ""

_tick()  { echo "  ${GRN}✓${RST}  $1"; }
_cross() { echo "  ${DIM}✗  $1${RST}"; }

$HAS_BREW     && _tick "Homebrew"             || _cross "Homebrew"
$HAS_PY       && _tick "Python 3"             || _cross "Python 3"
$HAS_WEBVIEW  && _tick "pywebview"            || _cross "pywebview"
if $IS_INSTALLED; then
    [[ -n "$INSTALLED_VER" ]] \
        && _tick "timeline.py  (v$INSTALLED_VER)" \
        || _tick "timeline.py  (legacy, no version info)"
else
    _cross "timeline.py"
fi
$HAS_WORKFLOW && _tick "Finder Quick Action"  || _cross "Finder Quick Action"

echo ""
echo "  ──────────────────────────────────────────────"
echo ""

# ── Menu ──────────────────────────────────────────────────────────────────────
UPGRADE_AVAILABLE=false
if $IS_INSTALLED; then
    if [[ -z "$INSTALLED_VER" ]]; then
        UPGRADE_AVAILABLE=true
    elif ver_gt "$BUNDLED_VER" "$INSTALLED_VER"; then
        UPGRADE_AVAILABLE=true
    fi
fi

ACTION=""

if ! $IS_INSTALLED; then
    # ── Not installed ─────────────────────────────────────────────────────────
    echo "  Folder Timeline is not installed on this Mac."
    echo "  The bundled version is ${CYN}v$BUNDLED_VER${RST}."
    echo ""
    echo "  ${BLD}What do you want to do?${RST}"
    echo ""
    echo "  ${BLD}1.${RST}  Install Folder Timeline v$BUNDLED_VER"
    echo "  ${BLD}2.${RST}  Exit"
    echo ""
    read -rp "  Your choice [1/2]: " CHOICE; echo ""
    case "$CHOICE" in
        1) ACTION="install" ;;
        *)
            echo "  Nothing was changed."
            echo ""
            read -n1 -rsp "  Press any key to close…"; echo ""; exit 0
            ;;
    esac

elif $UPGRADE_AVAILABLE; then
    # ── Upgrade available ─────────────────────────────────────────────────────
    if [[ -z "$INSTALLED_VER" ]]; then
        echo "  An older version without version info is installed."
        echo "  Upgrading to ${CYN}v$BUNDLED_VER${RST} is recommended."
    else
        echo "  ${GRN}${BLD}Upgrade available:${RST}  v$INSTALLED_VER  →  v$BUNDLED_VER"
    fi
    echo ""
    echo "  ${BLD}What do you want to do?${RST}"
    echo ""
    echo "  ${BLD}1.${RST}  Upgrade to v$BUNDLED_VER  ${DIM}(replaces script + Quick Action only)${RST}"
    echo "  ${BLD}2.${RST}  Full reinstall v$BUNDLED_VER  ${DIM}(re-checks all components)${RST}"
    echo "  ${BLD}3.${RST}  Uninstall Folder Timeline"
    echo "  ${BLD}4.${RST}  Exit"
    echo ""
    read -rp "  Your choice [1-4]: " CHOICE; echo ""
    case "$CHOICE" in
        1) ACTION="upgrade"   ;;
        2) ACTION="install"   ;;
        3) ACTION="uninstall" ;;
        *)
            echo "  Nothing was changed."
            echo ""
            read -n1 -rsp "  Press any key to close…"; echo ""; exit 0
            ;;
    esac

elif [[ "$BUNDLED_VER" == "$INSTALLED_VER" ]]; then
    # ── Same version ──────────────────────────────────────────────────────────
    echo "  ${GRN}You are already on the latest version (v$INSTALLED_VER).${RST}"
    echo ""
    echo "  ${BLD}What do you want to do?${RST}"
    echo ""
    echo "  ${BLD}1.${RST}  Reinstall v$BUNDLED_VER"
    echo "  ${BLD}2.${RST}  Uninstall Folder Timeline"
    echo "  ${BLD}3.${RST}  Exit"
    echo ""
    read -rp "  Your choice [1-3]: " CHOICE; echo ""
    case "$CHOICE" in
        1) ACTION="install"   ;;
        2) ACTION="uninstall" ;;
        *)
            echo "  Nothing was changed."
            echo ""
            read -n1 -rsp "  Press any key to close…"; echo ""; exit 0
            ;;
    esac

else
    # ── Bundled is older than installed (downgrade) ───────────────────────────
    echo "  ${YEL}Note:${RST} the installed version (v$INSTALLED_VER) is newer"
    echo "  than this bundled package (v$BUNDLED_VER)."
    echo ""
    echo "  ${BLD}What do you want to do?${RST}"
    echo ""
    echo "  ${BLD}1.${RST}  Reinstall v$BUNDLED_VER  ${DIM}(replaces current v$INSTALLED_VER)${RST}"
    echo "  ${BLD}2.${RST}  Uninstall Folder Timeline"
    echo "  ${BLD}3.${RST}  Exit"
    echo ""
    read -rp "  Your choice [1-3]: " CHOICE; echo ""
    case "$CHOICE" in
        1) ACTION="install"   ;;
        2) ACTION="uninstall" ;;
        *)
            echo "  Nothing was changed."
            echo ""
            read -n1 -rsp "  Press any key to close…"; echo ""; exit 0
            ;;
    esac
fi

echo "  ──────────────────────────────────────────────"
echo ""

# ══════════════════════════════════════════════════════════════════════════════
# UNINSTALL
# ══════════════════════════════════════════════════════════════════════════════
if [[ "$ACTION" == "uninstall" ]]; then

    echo "  The following will be removed:"
    echo ""
    $IS_INSTALLED && echo "  ${YEL}•${RST}  $SCRIPT_PATH"
    $HAS_WORKFLOW && echo "  ${YEL}•${RST}  $WORKFLOW_PATH"
    echo ""
    echo "  ${DIM}Homebrew, Python, and pywebview are not touched —"
    echo "  they may be used by other tools on this Mac.${RST}"
    echo ""

    read -rp "  Proceed with uninstall? [y/N] " CONFIRM
    CONFIRM="${CONFIRM:-N}"; echo ""

    if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
        echo "  Cancelled — nothing was changed."
        echo ""
        read -n1 -rsp "  Press any key to close…"; echo ""; exit 0
    fi

    $IS_INSTALLED && rm -f  "$SCRIPT_PATH"   && echo "  ${GRN}✓${RST}  Removed timeline.py"
    $HAS_WORKFLOW && rm -rf "$WORKFLOW_PATH"  && echo "  ${GRN}✓${RST}  Removed Finder Quick Action"
    /System/Library/CoreServices/pbs -flush 2>/dev/null || true
    echo "  ${GRN}✓${RST}  Finder services cache refreshed"
    echo ""
    echo "  ──────────────────────────────────────────────"
    echo "  ${GRN}${BLD}Folder Timeline has been uninstalled.${RST}"
    echo ""
    echo "  ${DIM}The 'Folder Timeline' entry will disappear from"
    echo "  Finder's Quick Actions menu on next use.${RST}"
    echo ""
    read -n1 -rsp "  Press any key to close…"; echo ""; exit 0
fi

# ══════════════════════════════════════════════════════════════════════════════
# UPGRADE — copy files only, skip dependency checks
# ══════════════════════════════════════════════════════════════════════════════
if [[ "$ACTION" == "upgrade" ]]; then

    echo "  Upgrading to v$BUNDLED_VER…"
    echo ""

    mkdir -p "$HOME/bin"
    cp "$SRC_PY" "$SCRIPT_PATH"
    chmod +x "$SCRIPT_PATH"
    echo "  ${GRN}✓${RST}  timeline.py updated  (v$BUNDLED_VER)"

    xattr -cr "$SRC_WF" 2>/dev/null || true
    mkdir -p "$HOME/Library/Services"
    rm -rf "$WORKFLOW_PATH"
    cp -R "$SRC_WF" "$WORKFLOW_PATH"

    WFLOW_DOC="$WORKFLOW_PATH/Contents/document.wflow"
    sed -i '' \
        "s|/opt/homebrew/bin/python3|$PY3_BIN|g;
         s|/usr/local/bin/python3|$PY3_BIN|g" \
        "$WFLOW_DOC" 2>/dev/null || \
    sed -i \
        "s|/opt/homebrew/bin/python3|$PY3_BIN|g;
         s|/usr/local/bin/python3|$PY3_BIN|g" \
        "$WFLOW_DOC" 2>/dev/null || true

    xattr -cr "$WORKFLOW_PATH" 2>/dev/null || true

    osascript 2>/dev/null <<APPLESCRIPT || true
tell application "Automator"
    set wf to open POSIX file "$WORKFLOW_PATH"
    save wf
    close wf
end tell
APPLESCRIPT

    /System/Library/CoreServices/pbs -flush 2>/dev/null || true
    echo "  ${GRN}✓${RST}  Finder Quick Action updated"
    echo ""
    echo "  ──────────────────────────────────────────────"
    echo "  ${GRN}${BLD}Folder Timeline upgraded to v$BUNDLED_VER.${RST}"
    echo ""
    read -n1 -rsp "  Press any key to close…"; echo ""; exit 0
fi

# ══════════════════════════════════════════════════════════════════════════════
# INSTALL (fresh install or full reinstall)
# ══════════════════════════════════════════════════════════════════════════════

echo "  Will be installed:"
! $HAS_BREW    && echo "  ${YEL}•${RST}  Homebrew                    ~5–10 min"
! $HAS_PY      && echo "  ${YEL}•${RST}  Python 3                    ~1–2 min"
! $HAS_WEBVIEW && echo "  ${YEL}•${RST}  pywebview                   < 1 min"
echo "  ${YEL}•${RST}  timeline.py v$BUNDLED_VER          instant"
echo "  ${YEL}•${RST}  Finder Quick Action           instant"
echo ""

TOTAL_LOW=0; TOTAL_HIGH=0
! $HAS_BREW    && TOTAL_LOW=$((TOTAL_LOW+5)) && TOTAL_HIGH=$((TOTAL_HIGH+10))
! $HAS_PY      && TOTAL_LOW=$((TOTAL_LOW+1)) && TOTAL_HIGH=$((TOTAL_HIGH+2))
! $HAS_WEBVIEW && TOTAL_LOW=$((TOTAL_LOW+1)) && TOTAL_HIGH=$((TOTAL_HIGH+1))

if (( TOTAL_LOW == 0 )); then
    echo "  ${DIM}Estimated time: a few seconds${RST}"
elif (( TOTAL_LOW == TOTAL_HIGH )); then
    echo "  ${DIM}Estimated time: about $TOTAL_LOW minute(s)${RST}"
else
    echo "  ${DIM}Estimated time: about $TOTAL_LOW–$TOTAL_HIGH minutes${RST}"
fi
echo ""

if ! $HAS_BREW; then
    echo "  ${CYN}ℹ${RST}  Homebrew is a free, well-known tool used by millions of Mac"
    echo "     users to install software. It will ask for your admin password"
    echo "     once, and you won't need to do this again."
    echo ""
fi

read -rp "  Ready to proceed? [y/N] " CONFIRM; CONFIRM="${CONFIRM:-N}"; echo ""
if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
    echo "  Cancelled — nothing was changed."
    echo ""
    read -n1 -rsp "  Press any key to close…"; echo ""; exit 0
fi

echo "  ──────────────────────────────────────────────"
echo ""

# Homebrew
if ! $HAS_BREW; then
    echo "  Installing Homebrew — this may take several minutes…"
    echo ""
    T0=$SECONDS
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    eval "$("$BREW_BIN" shellenv 2>/dev/null)" 2>/dev/null || true
    echo ""
    echo "  ${GRN}✓${RST}  Homebrew installed  ($(( SECONDS - T0 )) sec)"
    echo ""
fi

# Python 3
if ! $HAS_PY; then
    echo "  Installing Python 3…"
    T0=$SECONDS
    "$BREW_BIN" install python3
    echo "  ${GRN}✓${RST}  Python 3 installed  ($(( SECONDS - T0 )) sec)"
    echo ""
fi

# pywebview
if ! $HAS_WEBVIEW; then
    echo "  Installing pywebview…"
    T0=$SECONDS
    "$PY3_BIN" -m pip install pywebview --break-system-packages --quiet
    echo "  ${GRN}✓${RST}  pywebview installed  ($(( SECONDS - T0 )) sec)"
    echo ""
fi

# timeline.py
echo "  Copying timeline.py…"
mkdir -p "$HOME/bin"
cp "$SRC_PY" "$SCRIPT_PATH"
chmod +x "$SCRIPT_PATH"
echo "  ${GRN}✓${RST}  timeline.py v$BUNDLED_VER → ~/bin/"
echo ""

# Quick Action
echo "  Installing Finder Quick Action…"
xattr -cr "$SRC_WF" 2>/dev/null || true
mkdir -p "$HOME/Library/Services"
rm -rf "$WORKFLOW_PATH"
cp -R "$SRC_WF" "$WORKFLOW_PATH"

WFLOW_DOC="$WORKFLOW_PATH/Contents/document.wflow"
sed -i '' \
    "s|/opt/homebrew/bin/python3|$PY3_BIN|g;
     s|/usr/local/bin/python3|$PY3_BIN|g" \
    "$WFLOW_DOC" 2>/dev/null || \
sed -i \
    "s|/opt/homebrew/bin/python3|$PY3_BIN|g;
     s|/usr/local/bin/python3|$PY3_BIN|g" \
    "$WFLOW_DOC" 2>/dev/null || true

xattr -cr "$WORKFLOW_PATH" 2>/dev/null || true

osascript 2>/dev/null <<APPLESCRIPT || true
tell application "Automator"
    set wf to open POSIX file "$WORKFLOW_PATH"
    save wf
    close wf
end tell
APPLESCRIPT

/System/Library/CoreServices/pbs -flush 2>/dev/null || true
echo "  ${GRN}✓${RST}  Finder Quick Action installed"
echo ""

# Done
echo "  ──────────────────────────────────────────────"
echo ""
echo "  ${GRN}${BLD}Folder Timeline v$BUNDLED_VER is ready!${RST}"
echo ""
echo "  To use it: right-click any folder in Finder"
echo "  then choose  Quick Actions  →  Folder Timeline"
echo ""
echo "  ──────────────────────────────────────────────"
echo ""
echo "  ${BLD}If 'Folder Timeline' doesn't appear yet:${RST}"
echo ""
echo "  1. Open in Automator:  ~/Library/Services/FolderTimeline.workflow"
echo "  2. Press ${BLD}⌘S${RST} to save — then close Automator."
echo "  3. Right-click any folder in Finder."
echo ""
read -n1 -rsp "  Press any key to close…"; echo ""
