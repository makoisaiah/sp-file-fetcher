@echo off
REM Windows用: エクスプローラーでダブルクリックすると、このバッチファイルがコマンドプロンプトで実行される。
setlocal
cd /d "%~dp0"

set VENV_DIR=.venv
set MARKER_FILE=%VENV_DIR%\.setup_complete

if not exist "%MARKER_FILE%" (
    echo === 初回セットアップを行います(数分かかることがあります) ===

    if not exist "%VENV_DIR%" (
        python -m venv "%VENV_DIR%"
    )
    call "%VENV_DIR%\Scripts\activate.bat"

    python -m pip install --quiet --upgrade pip
    pip install --quiet -r requirements.txt

    REM Windowsではブラウザは通常ユーザー領域にダウンロードされ、管理者権限は不要
    playwright install chromium

    if not exist ".env" (
        copy .env.example .env >nul
        echo.
        echo === .env を作成しました。GROQ_API_KEY 等を編集してから、もう一度起動し直してください ===
        echo 編集ファイル: %cd%\.env
        pause
        exit /b 0
    )

    type nul > "%MARKER_FILE%"
    echo === セットアップ完了 ===
) else (
    call "%VENV_DIR%\Scripts\activate.bat"
)

echo サーバーを起動しています...
start "" cmd /c "timeout /t 2 >nul && start http://127.0.0.1:8000"

echo このウィンドウを閉じるとサーバーも終了します。
uvicorn app.main:app --host 127.0.0.1 --port 8000
