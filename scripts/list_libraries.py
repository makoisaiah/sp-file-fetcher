"""
SP_LIBRARY_PATH に何を入れればいいか分からない/404になる場合の調査用スクリプト。

1. サイト内の全ドキュメントライブラリと、そのルートのServerRelativeUrlを一覧表示
2. 引数でフォルダのServerRelativeUrlを渡すと、その直下のフォルダ一覧を表示

使い方:
    # ライブラリ一覧を見る
    python scripts/list_libraries.py

    # 特定フォルダの中身を見る(木を辿って正しいパスを探す)
    python scripts/list_libraries.py "/sites/FMU-examDB-m333/共有ドキュメント"
"""
import asyncio
import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.sp_client import make_client, _encode_relative_url  # noqa: E402

load_dotenv()

SITE_URL = os.environ["SP_SITE_URL"].rstrip("/")
STATE_PATH = os.environ.get("SESSION_STATE_PATH", "./storage_state.json")


async def list_document_libraries():
    async with make_client(STATE_PATH) as client:
        resp = await client.get(
            f"{SITE_URL}/_api/web/lists?$filter=BaseTemplate eq 101"
            f"&$select=Title,RootFolder/ServerRelativeUrl&$expand=RootFolder"
        )
        resp.raise_for_status()
        data = resp.json()["d"]["results"]

        print("=== このサイトのドキュメントライブラリ一覧 ===")
        for lib in data:
            print(f"タイトル: {lib['Title']}")
            print(f"  ServerRelativeUrl: {lib['RootFolder']['ServerRelativeUrl']}")
            print("  ↑ これを SP_LIBRARY_PATH にそのままコピペしてください\n")


async def list_folder_children(relative_url: str):
    async with make_client(STATE_PATH) as client:
        url = (
            f"{SITE_URL}/_api/web/GetFolderByServerRelativeUrl('{_encode_relative_url(relative_url)}')"
            f"?$expand=Folders"
        )
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()["d"]

        print(f"=== {relative_url} 直下のフォルダ一覧 ===")
        for sub in data.get("Folders", {}).get("results", []):
            print(f"名前: {sub['Name']}")
            print(f"  ServerRelativeUrl: {sub['ServerRelativeUrl']}\n")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        asyncio.run(list_folder_children(sys.argv[1]))
    else:
        asyncio.run(list_document_libraries())
