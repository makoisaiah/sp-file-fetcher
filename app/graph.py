"""Microsoft Graph API とのやり取り。認証済みaccess_tokenを受け取って動く薄いラッパー。"""
import httpx

GRAPH_ROOT = "https://graph.microsoft.com/v1.0"

# 安全弁：暴走防止のための上限（必要なら調整）
MAX_ITEMS = 20000
MAX_DEPTH = 25


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def resolve_site_and_drive(token: str, site_url: str) -> dict:
    """
    site_url 例: https://contoso.sharepoint.com/sites/TeamA
    Graphの sites/{hostname}:{server-relative-path} 形式に変換して解決し、
    既定ドキュメントライブラリ(drive)のIDまで取得する。
    """
    from urllib.parse import urlparse

    parsed = urlparse(site_url)
    hostname = parsed.netloc
    path = parsed.path.rstrip("/")  # 例: /sites/TeamA

    async with httpx.AsyncClient() as client:
        site_resp = await client.get(
            f"{GRAPH_ROOT}/sites/{hostname}:{path}",
            headers=_headers(token),
        )
        site_resp.raise_for_status()
        site = site_resp.json()

        drive_resp = await client.get(
            f"{GRAPH_ROOT}/sites/{site['id']}/drive",
            headers=_headers(token),
        )
        drive_resp.raise_for_status()
        drive = drive_resp.json()

    return {"site_id": site["id"], "site_name": site.get("displayName"), "drive_id": drive["id"]}


async def walk_drive(token: str, drive_id: str) -> list[dict]:
    """
    ドライブ全体を再帰的に探索し、ファイルのフラットな一覧を返す。
    フォルダにはLLMを介在させず、コードだけで確定的に処理する。
    """
    results: list[dict] = []

    async with httpx.AsyncClient(timeout=30.0) as client:

        async def _walk(item_id: str, path: str, depth: int):
            if depth > MAX_DEPTH or len(results) >= MAX_ITEMS:
                return

            url = f"{GRAPH_ROOT}/drives/{drive_id}/items/{item_id}/children?$top=200"
            while url:
                resp = await client.get(url, headers=_headers(token))
                resp.raise_for_status()
                data = resp.json()

                for item in data.get("value", []):
                    full_path = f"{path}/{item['name']}"
                    if "folder" in item:
                        await _walk(item["id"], full_path, depth + 1)
                    else:
                        results.append(
                            {
                                "id": item["id"],
                                "path": full_path,
                                "name": item["name"],
                                "size": item.get("size", 0),
                                "modified": item.get("lastModifiedDateTime"),
                                "mime": item.get("file", {}).get("mimeType"),
                            }
                        )
                    if len(results) >= MAX_ITEMS:
                        return

                url = data.get("@odata.nextLink")

        await _walk("root", "", 0)

    return results


async def download_item_bytes(token: str, drive_id: str, item_id: str) -> bytes:
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.get(
            f"{GRAPH_ROOT}/drives/{drive_id}/items/{item_id}/content",
            headers=_headers(token),
            follow_redirects=True,
        )
        resp.raise_for_status()
        return resp.content
