"""
Playwrightで保存したセッションCookie(storage_state.json)を使い、
SharePoint REST API(_api/web/...)を直接叩く。OAuthアプリを経由しないため、
Entra ID側のアプリ同意ポリシーの対象外で動く。

探索(walk_folder)はコードのみで完結させ、LLMは選定にのみ使う方針は
Graph API版と同じ。
"""
import json
import os
from urllib.parse import quote

import httpx

MAX_ITEMS = 20000
MAX_DEPTH = 25

HEADERS = {"Accept": "application/json;odata=verbose"}


def _encode_relative_url(path: str) -> str:
    """
    SharePoint REST APIの GetFolderByServerRelativeUrl('...') / GetFileByServerRelativeUrl('...')
    に渡すパスを安全な形にする。
    - OData文字列リテラル内のシングルクォートは '' に二重化してエスケープ
    - & や日本語などはURLエンコード(パス区切りの / だけは残す)
    """
    escaped = path.replace("'", "''")
    return quote(escaped, safe="/")


def load_cookies(state_path: str) -> httpx.Cookies:
    with open(state_path, "r", encoding="utf-8") as f:
        state = json.load(f)

    cookies = httpx.Cookies()
    for c in state.get("cookies", []):
        cookies.set(c["name"], c["value"], domain=c["domain"], path=c.get("path", "/"))
    return cookies


def make_client(state_path: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(cookies=load_cookies(state_path), headers=HEADERS, timeout=30.0)


async def check_session(site_url: str, state_path: str) -> bool:
    """セッションがまだ有効かを軽い1リクエストで確認する。"""
    async with make_client(state_path) as client:
        resp = await client.get(f"{site_url}/_api/web/title")
        return resp.status_code == 200


async def walk_folder(site_url: str, library_path: str, state_path: str) -> list[dict]:
    """
    library_path(サーバー相対URL)配下を再帰的に探索し、フラットなファイル一覧を返す。
    """
    results: list[dict] = []

    async with make_client(state_path) as client:

        async def _walk(relative_url: str, depth: int):
            if depth > MAX_DEPTH or len(results) >= MAX_ITEMS:
                return

            url = (
                f"{site_url}/_api/web/GetFolderByServerRelativeUrl('{_encode_relative_url(relative_url)}')"
                f"?$expand=Folders,Files"
            )
            resp = await client.get(url)
            if resp.status_code == 401:
                raise PermissionError("セッションが失効しています。scripts/login.py を再実行してください。")
            resp.raise_for_status()
            data = resp.json()["d"]

            for f in data.get("Files", {}).get("results", []):
                results.append(
                    {
                        "path": f["ServerRelativeUrl"],
                        "name": f["Name"],
                        "size": int(f.get("Length", 0)),
                        "modified": f.get("TimeLastModified"),
                    }
                )
                if len(results) >= MAX_ITEMS:
                    return

            for sub in data.get("Folders", {}).get("results", []):
                # システム用フォルダ(Forms等)はスキップ
                if sub["Name"].lower() == "forms":
                    continue
                await _walk(sub["ServerRelativeUrl"], depth + 1)

        await _walk(library_path, 0)

    return results


async def download_file(site_url: str, server_relative_url: str, state_path: str) -> bytes:
    async with make_client(state_path) as client:
        resp = await client.get(
            f"{site_url}/_api/web/GetFileByServerRelativeUrl('{_encode_relative_url(server_relative_url)}')/$value"
        )
        if resp.status_code == 401:
            raise PermissionError("セッションが失効しています。scripts/login.py を再実行してください。")
        resp.raise_for_status()
        return resp.content
