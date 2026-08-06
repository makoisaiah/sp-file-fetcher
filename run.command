#!/usr/bin/env bash
# macOS用: Finderでダブルクリックすると、このスクリプトがTerminal.appで実行される。
# (初回はGatekeeperの「開発元を確認できません」警告が出ることがあります。
#  右クリック→「開く」で一度だけ許可すれば、以降は警告なしで実行できます)
set -e
cd "$(dirname "$0")"

VENV_DIR=".venv"
MARKER_FILE="${VENV_DIR}/.setup_complete"

if [ ! -f "$MARKER_FILE" ]; then
    echo "=== 初回セットアップを行います(数分かかることがあります) ==="

    if [ ! -d "$VENV_DIR" ]; then
        python3 -m venv "$VENV_DIR"
    fi
    source "${VENV_DIR}/bin/activate"

    pip install --quiet --upgrade pip
    pip install --quiet -r requirements.txt

    # macOSではブラウザは通常ユーザー領域にダウンロードされ、管理者権限は不要
    playwright install chromium

    if [ ! -f ".env" ]; then
        cp .env.example .env
        echo ""
        echo "=== .env を作成しました。GROQ_API_KEY 等を編集してから、もう一度起動し直してください ==="
        echo "編集ファイル: $(pwd)/.env"
        read -p "Enterキーで終了します..." _
        exit 0
    fi

    touch "$MARKER_FILE"
    echo "=== セットアップ完了 ==="
else
    source "${VENV_DIR}/bin/activate"
fi

echo "サーバーを起動しています..."
uvicorn app.main:app --host 127.0.0.1 --port 8000 &
SERVER_PID=$!

sleep 2
open "http://127.0.0.1:8000"

echo "サーバーを終了するには、このウィンドウで Ctrl+C を押してください。"
wait $SERVER_PID
