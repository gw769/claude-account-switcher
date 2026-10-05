#!/usr/bin/env bash
# Install claude-account into ~/.local/bin and add "Claude 帳號切換" to the app menu.
#
#   bash install.sh              install or update
#   bash install.sh --uninstall  remove the program (account data is kept)

set -Eeuo pipefail

SRC_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
BIN_DIR="$HOME/.local/bin"
APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
DESKTOP_FILE="$APP_DIR/claude-account-switcher.desktop"

uninstall() {
    rm -f -- "$BIN_DIR/claude-account" "$DESKTOP_FILE"
    update-desktop-database "$APP_DIR" 2>/dev/null || true
    echo "已移除 claude-account。"
    echo "帳號資料沒有變動。如果之前切換過帳號，請在移除前先執行 claude-account restore。"
}

case ${1:-} in
    "") ;;
    --uninstall) uninstall; exit 0 ;;
    *) echo "用法：bash install.sh [--uninstall]" >&2; exit 2 ;;
esac

for dep in zenity flock setsid gio; do
    if ! command -v "$dep" >/dev/null; then
        echo "缺少 $dep。Ubuntu/Debian 可執行：sudo apt install zenity util-linux libglib2.0-bin" >&2
        exit 1
    fi
done
if [[ ! -x ${CLAUDE_ACCOUNT_EXE:-/usr/lib/claude-desktop/claude-desktop} ]]; then
    echo "警告：找不到 /usr/lib/claude-desktop/claude-desktop。" >&2
    echo "      若 Claude Desktop 裝在別處，請設定 CLAUDE_ACCOUNT_EXE。" >&2
fi

install -D -m 755 -- "$SRC_DIR/claude-account" "$BIN_DIR/claude-account"
echo "✓ 已安裝 $BIN_DIR/claude-account"

mkdir -p -- "$APP_DIR"
cat >"$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=Claude Account Switcher
Name[zh_TW]=Claude 帳號切換
Comment=Switch Claude Desktop between accounts
Comment[zh_TW]=在多個 Claude Desktop 帳號之間切換
Exec=$BIN_DIR/claude-account gui
Icon=claude-desktop
Terminal=false
Categories=Utility;
Keywords=claude;account;switch;帳號;切換;
StartupNotify=false
EOF
update-desktop-database "$APP_DIR" 2>/dev/null || true
echo "✓ 已加入應用程式選單：「Claude 帳號切換」"

if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    echo
    echo "提示：$BIN_DIR 不在 PATH 中，終端機裡要用完整路徑執行 claude-account。"
fi
