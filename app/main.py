import io
import os
import secrets
import zipfile

import msal
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, URLSafeSerializer

from . import graph, groq_select

load_dotenv()

APP_PASSWORD = os.environ["APP_PASSWORD"]
SESSION_SECRET = os.environ["SESSION_SECRET"]
GRAPH_CLIENT_ID = os.environ["GRAPH_CLIENT_ID"]
GRAPH_AUTHORITY = os.environ.get("GRAPH_AUTHORITY", "https://login.microsoftonline.com/common")
GRAPH_SCOPES = os.environ.get("GRAPH_SCOPES", "Sites.Read.All Files.Read.All").split()

serializer = URLSafeSerializer(SESSION_SECRET)
app = FastAPI()

# セッションごとの状態(単一プロセス・インメモリ。個人利用想定。再起動で消える)
SESSIONS: dict[str, dict] = {}


def get_session(request: Request) -> tuple[str, dict]:
    cookie = request.cookies.get("session")
    if cookie:
        try:
            sid = serializer.loads(cookie)
        except BadSignature:
            sid = None
    else:
        sid = None

    if not sid or sid not in SESSIONS:
        sid = secrets.token_urlsafe(24)
        SESSIONS[sid] = {"authed": False, "msal_app": None, "flow": None, "token": None,
                          "drive_id": None, "files": [], "selected": []}
    return sid, SESSIONS[sid]


def require_auth(session: dict):
    if not session["authed"]:
        raise HTTPException(401, "合言葉での認証が必要です")
    if not session["token"]:
        raise HTTPException(401, "Microsoftアカウントでのログインが必要です")


# ---------- 合言葉認証 ----------

@app.post("/api/auth/password")
async def auth_password(request: Request, response: Response):
    body = await request.json()
    sid, session = get_session(request)
    if not secrets.compare_digest(body.get("password", ""), APP_PASSWORD):
        raise HTTPException(403, "パスワードが違います")
    session["authed"] = True
    response.set_cookie("session", serializer.dumps(sid), httponly=True, samesite="lax")
    return {"ok": True}


@app.get("/api/auth/status")
async def auth_status(request: Request, response: Response):
    sid, session = get_session(request)
    response.set_cookie("session", serializer.dumps(sid), httponly=True, samesite="lax")
    return {"password_ok": session["authed"], "ms_ok": bool(session["token"])}


# ---------- Microsoftアカウント ログイン(デバイスコードフロー) ----------

@app.post("/api/ms/login/start")
async def ms_login_start(request: Request):
    sid, session = get_session(request)
    if not session["authed"]:
        raise HTTPException(401, "先に合言葉認証をしてください")

    msal_app = msal.PublicClientApplication(GRAPH_CLIENT_ID, authority=GRAPH_AUTHORITY)
    flow = msal_app.initiate_device_flow(scopes=GRAPH_SCOPES)
    if "user_code" not in flow:
        raise HTTPException(500, f"デバイスコード開始失敗: {flow}")

    session["msal_app"] = msal_app
    session["flow"] = flow
    return {
        "verification_uri": flow["verification_uri"],
        "user_code": flow["user_code"],
        "message": flow["message"],
    }


@app.post("/api/ms/login/poll")
async def ms_login_poll(request: Request):
    import asyncio

    sid, session = get_session(request)
    msal_app: msal.PublicClientApplication = session.get("msal_app")
    flow = session.get("flow")
    if not msal_app or not flow:
        raise HTTPException(400, "先に /api/ms/login/start を呼んでください")

    # acquire_token_by_device_flow はflowの有効期限まで内部でポーリングし続けるブロッキング呼び出しなので
    # スレッドに逃がして実行する(個人利用の単発フローなので許容)
    result = await asyncio.to_thread(msal_app.acquire_token_by_device_flow, flow)

    if "access_token" not in result:
        raise HTTPException(400, f"ログイン失敗: {result.get('error_description', result)}")

    session["token"] = result["access_token"]
    return {"ok": True}


# ---------- 探索(コードのみ、LLM不使用) ----------

@app.post("/api/explore")
async def explore(request: Request):
    sid, session = get_session(request)
    require_auth(session)

    body = await request.json()
    site_url = body.get("site_url", "").strip()
    if not site_url:
        raise HTTPException(400, "site_url が必要です")

    info = await graph.resolve_site_and_drive(session["token"], site_url)
    files = await graph.walk_drive(session["token"], info["drive_id"])

    session["drive_id"] = info["drive_id"]
    session["files"] = files
    session["selected"] = []

    return {"site_name": info["site_name"], "file_count": len(files)}


# ---------- 選定(Groq) ----------

@app.post("/api/select")
async def select(request: Request):
    sid, session = get_session(request)
    require_auth(session)

    if not session["files"]:
        raise HTTPException(400, "先に /api/explore を実行してください")

    body = await request.json()
    instruction = body.get("instruction", "").strip()
    if not instruction:
        raise HTTPException(400, "instruction が必要です")

    selected_paths = await groq_select.select_files(instruction, session["files"])
    by_path = {f["path"]: f for f in session["files"]}
    selected_items = [by_path[p] for p in selected_paths if p in by_path]
    session["selected"] = selected_items

    return {"selected": selected_items}


# ---------- ダウンロード(選定分をzipでまとめて返す) ----------

@app.get("/api/download")
async def download(request: Request):
    sid, session = get_session(request)
    require_auth(session)

    if not session["selected"]:
        raise HTTPException(400, "選定済みファイルがありません")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in session["selected"]:
            data = await graph.download_item_bytes(session["token"], session["drive_id"], item["id"])
            # SharePoint側のフォルダ構造をzip内のパスとして維持
            zf.writestr(item["path"].lstrip("/"), data)
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=selected_files.zip"},
    )


# ---------- 静的フロントエンド ----------

app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/")
async def index():
    return FileResponse("app/static/index.html")
