#!/bin/bash
# ──────────────────────────────────────────────────────────────────────────────
#  Folder Timeline — Setup
#  Handles both install and uninstall from a single entry point.
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

# ── Homebrew prefix (Apple Silicon vs Intel) ──────────────────────────────────
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

# ── Menu ──────────────────────────────────────────────────────────────────────
clear
echo ""
echo "  ${BLD}Folder Timeline${RST}"
echo "  ──────────────────────────────────────────────"
echo ""
echo "  What do you want to do?"
echo ""
echo "  ${BLD}1.${RST}  Install Folder Timeline"
echo "  ${BLD}2.${RST}  Uninstall Folder Timeline"
echo ""

read -rp "  Your choice [1/2]: " CHOICE
echo ""

case "$CHOICE" in
    1) ;;   # fall through to install
    2)
        # ── UNINSTALL ─────────────────────────────────────────────────────────
        HAS_SCRIPT=false
        HAS_WORKFLOW=false
        [[ -f "$SCRIPT_PATH"   ]] && HAS_SCRIPT=true
        [[ -d "$WORKFLOW_PATH" ]] && HAS_WORKFLOW=true

        if ! $HAS_SCRIPT && ! $HAS_WORKFLOW; then
            echo "  ${GRN}Nothing to remove — Folder Timeline is not installed.${RST}"
            echo ""
            read -n1 -rsp "  Press any key to close…"
            echo ""
            exit 0
        fi

        echo "  The following will be removed:"
        echo ""
        $HAS_SCRIPT   && echo "  ${YEL}•${RST}  $SCRIPT_PATH"
        $HAS_WORKFLOW && echo "  ${YEL}•${RST}  $WORKFLOW_PATH"
        echo ""
        echo "  ${DIM}Homebrew, Python, and pywebview are not touched —"
        echo "  they may be used by other tools on this Mac.${RST}"
        echo ""

        read -rp "  Proceed with uninstall? [y/N] " CONFIRM
        CONFIRM="${CONFIRM:-N}"
        echo ""

        if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
            echo "  Cancelled — nothing was changed."
            echo ""
            read -n1 -rsp "  Press any key to close…"
            echo ""
            exit 0
        fi

        echo "  ──────────────────────────────────────────────"
        echo ""

        $HAS_SCRIPT   && rm -f  "$SCRIPT_PATH"   && echo "  ${GRN}✓${RST}  Removed $SCRIPT_PATH"
        $HAS_WORKFLOW && rm -rf "$WORKFLOW_PATH"  && echo "  ${GRN}✓${RST}  Removed $WORKFLOW_PATH"

        /System/Library/CoreServices/pbs -flush 2>/dev/null || true
        echo "  ${GRN}✓${RST}  Finder services cache refreshed"
        echo ""
        echo "  ──────────────────────────────────────────────"
        echo "  ${GRN}${BLD}Folder Timeline has been uninstalled.${RST}"
        echo ""
        echo "  ${DIM}The 'Folder Timeline' entry will disappear from"
        echo "  Finder's Quick Actions menu on next use.${RST}"
        echo ""
        read -n1 -rsp "  Press any key to close…"
        echo ""
        exit 0
        ;;
    *)
        echo "  Invalid choice — please enter 1 or 2."
        echo ""
        read -n1 -rsp "  Press any key to close…"
        echo ""
        exit 1
        ;;
esac

# ── INSTALL ───────────────────────────────────────────────────────────────────
echo "  ──────────────────────────────────────────────"
echo ""

# Source file checks
if [[ ! -f "$SRC_PY" ]]; then
    echo "  ${RED}Cannot find timeline.py next to this script.${RST}"
    echo "  Please keep all files from the zip in the same folder."
    echo ""
    read -n1 -rsp "  Press any key to close…"
    exit 1
fi
if [[ ! -d "$SRC_WF" ]]; then
    echo "  ${RED}Cannot find FolderTimeline.workflow next to this script.${RST}"
    echo "  Please keep all files from the zip in the same folder."
    echo ""
    read -n1 -rsp "  Press any key to close…"
    exit 1
fi

# Assess current state
echo "  Checking what's already installed on this Mac…"
echo ""

HAS_BREW=false; HAS_PY=false; HAS_WEBVIEW=false
HAS_TIMELINE=false; HAS_WORKFLOW=false

[[ -x "$BREW_BIN" ]]  && HAS_BREW=true
[[ -x "$PY3_BIN"  ]]  && HAS_PY=true
if $HAS_PY; then "$PY3_BIN" -c "import webview" 2>/dev/null && HAS_WEBVIEW=true || true; fi
[[ -f "$SCRIPT_PATH"   ]] && HAS_TIMELINE=true
[[ -d "$WORKFLOW_PATH" ]] && HAS_WORKFLOW=true

# Already-installed summary
ANY_READY=false
{ $HAS_BREW || $HAS_PY || $HAS_WEBVIEW || $HAS_TIMELINE || $HAS_WORKFLOW; } && ANY_READY=true

if $ANY_READY; then
    echo "  Already installed:"
    $HAS_BREW     && echo "  ${GRN}✓${RST}  Homebrew"
    $HAS_PY       && echo "  ${GRN}✓${RST}  Python 3"
    $HAS_WEBVIEW  && echo "  ${GRN}✓${RST}  pywebview"
    $HAS_TIMELINE && echo "  ${GRN}✓${RST}  timeline.py script"
    $HAS_WORKFLOW && echo "  ${GRN}✓${RST}  Finder Quick Action"
    echo ""
fi

if $HAS_BREW && $HAS_PY && $HAS_WEBVIEW && $HAS_TIMELINE && $HAS_WORKFLOW; then
    echo "  ${GRN}${BLD}Everything is already installed — nothing to do.${RST}"
    echo ""
    echo "  To use it: right-click any folder in Finder, then choose"
    echo "  Quick Actions → Folder Timeline."
    echo ""
    read -n1 -rsp "  Press any key to close…"
    exit 0
fi

# What will be installed
echo "  Will be installed:"
! $HAS_BREW     && echo "  ${YEL}•${RST}  Homebrew (package manager)        ~5–10 min"
! $HAS_PY       && echo "  ${YEL}•${RST}  Python 3                          ~1–2 min"
! $HAS_WEBVIEW  && echo "  ${YEL}•${RST}  pywebview (Python library)        < 1 min"
! $HAS_TIMELINE && echo "  ${YEL}•${RST}  timeline.py script                instant"
! $HAS_WORKFLOW && echo "  ${YEL}•${RST}  Finder Quick Action               instant"
echo ""

TOTAL_LOW=0; TOTAL_HIGH=0
! $HAS_BREW   && TOTAL_LOW=$(( TOTAL_LOW + 5 ))  && TOTAL_HIGH=$(( TOTAL_HIGH + 10 ))
! $HAS_PY     && TOTAL_LOW=$(( TOTAL_LOW + 1 ))  && TOTAL_HIGH=$(( TOTAL_HIGH + 2  ))
! $HAS_WEBVIEW && TOTAL_LOW=$(( TOTAL_LOW + 1 )) && TOTAL_HIGH=$(( TOTAL_HIGH + 1  ))

if (( TOTAL_LOW == 0 )); then
    echo "  ${DIM}Estimated total time: a few seconds${RST}"
elif (( TOTAL_LOW == TOTAL_HIGH )); then
    echo "  ${DIM}Estimated total time: about $TOTAL_LOW minute(s)${RST}"
else
    echo "  ${DIM}Estimated total time: about $TOTAL_LOW–$TOTAL_HIGH minutes${RST}"
fi
echo ""

if ! $HAS_BREW; then
    echo "  ${CYN}ℹ${RST}  Homebrew is a free, well-known tool used by millions of Mac"
    echo "     users to install software. It will ask for your admin password"
    echo "     once, and you won't need to do this again."
    echo ""
fi

read -rp "  Ready to proceed? [y/N] " CONFIRM
CONFIRM="${CONFIRM:-N}"
echo ""

if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
    echo "  Cancelled — nothing was changed."
    echo ""
    exit 0
fi

echo "  ──────────────────────────────────────────────"
echo ""

# Install Homebrew
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

# Install Python 3
if ! $HAS_PY; then
    echo "  Installing Python 3…"
    T0=$SECONDS
    "$BREW_BIN" install python3
    echo "  ${GRN}✓${RST}  Python 3 installed  ($(( SECONDS - T0 )) sec)"
    echo ""
fi

# Install pywebview
if ! $HAS_WEBVIEW; then
    echo "  Installing pywebview…"
    T0=$SECONDS
    "$PY3_BIN" -m pip install pywebview --break-system-packages --quiet
    echo "  ${GRN}✓${RST}  pywebview installed  ($(( SECONDS - T0 )) sec)"
    echo ""
fi

# Copy timeline.py
if ! $HAS_TIMELINE; then
    echo "  Copying timeline.py…"
    mkdir -p "$HOME/bin"
    cp "$SRC_PY" "$HOME/bin/timeline.py"
    chmod +x "$HOME/bin/timeline.py"
    echo "  ${GRN}✓${RST}  timeline.py → ~/bin/"
    echo ""
fi

# Install Quick Action
if ! $HAS_WORKFLOW; then
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
    echo "  ${GRN}✓${RST}  Quick Action installed"
    echo ""
fi

# Done
echo "  ──────────────────────────────────────────────"
echo ""
echo "  ${GRN}${BLD}All done!${RST}"
echo ""
echo "  To use Folder Timeline:"
echo "  • Right-click any folder in Finder"
echo "  • Choose  Quick Actions  →  Folder Timeline"
echo ""
echo "  ──────────────────────────────────────────────"
echo ""
echo "  ${BLD}If 'Folder Timeline' doesn't appear yet:${RST}"
echo ""
echo "  1. Open this file in Automator:"
echo "     ~/Library/Services/FolderTimeline.workflow"
echo "  2. Press  ${BLD}⌘S${RST}  to save — then close Automator."
echo "  3. Right-click any folder in Finder."
echo ""
read -n1 -rsp "  Press any key to close…"
echo ""
