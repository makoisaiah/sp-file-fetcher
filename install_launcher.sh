#!/usr/bin/env bash
# デスクトップ/アプリ一覧に「SharePoint File Fetcher」のアイコンを登録する。
# 一度だけ実行すればよい。以降はアイコンのダブルクリックで起動できる。
set -e
cd "$(dirname "$0")"
INSTALL_DIR="$(pwd)"

DESKTOP_FILE_CONTENT="[Desktop Entry]
Type=Application
Name=SharePoint File Fetcher
Comment=SharePointからファイルを検索・ダウンロードするローカルツール
Exec=${INSTALL_DIR}/run.sh
Path=${INSTALL_DIR}
Terminal=true
Icon=folder-download
Categories=Utility;
"

mkdir -p "$HOME/.local/share/applications"
APP_DESKTOP_FILE="$HOME/.local/share/applications/sp-file-fetcher.desktop"
echo "$DESKTOP_FILE_CONTENT" > "$APP_DESKTOP_FILE"
chmod +x "$APP_DESKTOP_FILE"

if [ -d "$HOME/Desktop" ]; then
    DESKTOP_ICON_FILE="$HOME/Desktop/sp-file-fetcher.desktop"
    echo "$DESKTOP_FILE_CONTENT" > "$DESKTOP_ICON_FILE"
    chmod +x "$DESKTOP_ICON_FILE"
    # GNOME/Nautilusでは「信頼済み」にしないとダブルクリックで実行できないことがある
    gio set "$DESKTOP_ICON_FILE" "metadata::trusted" true 2>/dev/null || true
    echo "デスクトップにアイコンを作成しました: $DESKTOP_ICON_FILE"
fi

chmod +x "${INSTALL_DIR}/run.sh"

echo "アプリ一覧にも登録しました。アプリ名で「SharePoint File Fetcher」を検索してみてください。"
