"""
SharePointに手動でログインし、セッションCookieを storage_state.json に保存するスクリプト。

使い方:
    python scripts/login.py

ブラウザが開くので、いつも通りMicrosoftアカウントでログイン(MFA含む)し、
SharePointサイトのトップページが表示されたら、このターミナルに戻って Enter を押す。

セッションは通常 数時間〜数日 で失効するので、
アプリ側で401エラーが出たら再度このスクリプトを実行してやり直す。
"""
import os

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

load_dotenv()

SITE_URL = os.environ["SP_SITE_URL"]
STATE_PATH = os.environ.get("SESSION_STATE_PATH", "./storage_state.json")


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(SITE_URL)

        print("\nブラウザでMicrosoftアカウントにログインしてください。")
        print("SharePointサイトのトップページが表示されたら、このターミナルに戻って Enter を押してください。")
        input()

        context.storage_state(path=STATE_PATH)
        browser.close()

    print(f"セッションを {STATE_PATH} に保存しました。")


if __name__ == "__main__":
    main()
