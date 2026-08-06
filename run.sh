#!/usr/bin/env bash
# SharePoint File Fetcher をダブルクリックで起動するためのランチャー。
#
# 初回だけ: 仮想環境の作成・依存パッケージのインストール・
#           Playwrightのブラウザ本体(+OS依存パッケージ)のインストールを行う。
#           OS依存パッケージのインストールには管理者権限(sudo)が必要なため、
#           ここで初めてパスワード入力を求められる。
# 2回目以降: 何もインストールし直さず、そのままサーバーを起動してブラウザを開く。
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

    # ブラウザ本体+OS依存パッケージのインストール。ここで管理者権限が必要になる。
    echo "ブラウザのインストールに管理者権限が必要です。パスワードを求められたら入力してください。"
    playwright install --with-deps chromium

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

# サーバーの起動を少し待ってからブラウザを開く
sleep 2
xdg-open "http://127.0.0.1:8000" >/dev/null 2>&1 || echo "ブラウザで http://127.0.0.1:8000 を開いてください"

echo "サーバーを終了するには、このウィンドウで Ctrl+C を押してください。"
wait $SERVER_PID
