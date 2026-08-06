"""
SharePointに手動でログインし、セッションCookieを storage_state.json に保存するスクリプト。

使い方:
    python scripts/login.py

ブラウザが開くので、いつも通りMicrosoftアカウントでログイン(MFA含む)してください。
SharePointサイトのトップページへの遷移を自動検知したら、自動でセッションを保存して
ブラウザを閉じます(ターミナルでのキー入力は不要)。

ブラウザの選択は次の優先順で自動フォールバックする(通常はPlaywright専用の
Chromiumダウンロードが不要になり、セットアップが軽くなる):
  1. システムに既に入っているGoogle Chrome
  2. システムに既に入っているMicrosoft Edge
  3. Playwright同梱のChromium(事前に `playwright install chromium` が必要)

Web UI(main.py)の「ログインを開始」ボタンからも、このスクリプトがバックグラウンドで
起動されます。

セッションは通常 数時間〜数日 で失効するので、
アプリ側で401エラーが出たら再度このスクリプトを実行してやり直す。
"""
import os
import sys
from urllib.parse import urlparse

from dotenv import load_dotenv
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

load_dotenv()

SITE_URL = os.environ["SP_SITE_URL"]
STATE_PATH = os.environ.get("SESSION_STATE_PATH", "./storage_state.json")
LOGIN_TIMEOUT_MS = 5 * 60 * 1000  # 5分。MFA等を含めても十分な余裕を見た値


def _launch_browser(p):
    for channel in ("chrome", "msedge", None):
        try:
            if channel:
                return p.chromium.launch(channel=channel, headless=False), channel
            return p.chromium.launch(headless=False), "chromium(同梱)"
        except Exception:
            continue
    print("使えるブラウザが見つかりませんでした。次のいずれかを行ってください:")
    print("  - Google Chrome または Microsoft Edge をインストールする")
    print("  - もしくは 'playwright install chromium' を実行してPlaywright同梱ブラウザを用意する")
    sys.exit(1)


def main():
    site_hostname = urlparse(SITE_URL).netloc

    with sync_playwright() as p:
        browser, used = _launch_browser(p)
        print(f"ブラウザ({used})を起動しました。")

        context = browser.new_context()
        page = context.new_page()
        page.goto(SITE_URL)

        print("ブラウザでMicrosoftアカウントにログインしてください。")
        print(f"({site_hostname} への遷移を自動検知します。最大5分待ちます)")

        try:
            # login.microsoftonline.com 等を経由したあと、対象サイトのホスト名に
            # 戻ってきたタイミングでログイン完了とみなす
            page.wait_for_url(f"**{site_hostname}**", timeout=LOGIN_TIMEOUT_MS)
            # 遷移直後はまだページ読み込み中のことがあるので少し待つ
            page.wait_for_load_state("networkidle", timeout=30000)
        except PlaywrightTimeoutError:
            print("タイムアウトしました。ログインが完了しませんでした。")
            browser.close()
            sys.exit(1)

        context.storage_state(path=STATE_PATH)
        browser.close()

    print(f"セッションを {STATE_PATH} に保存しました。")


if __name__ == "__main__":
    main()
