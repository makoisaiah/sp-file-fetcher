import io
import os
import zipfile

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import groq_select, sp_client

load_dotenv()

SITE_URL = os.environ["SP_SITE_URL"].rstrip("/")
LIBRARY_PATH = os.environ["SP_LIBRARY_PATH"]
STATE_PATH = os.environ.get("SESSION_STATE_PATH", "./storage_state.json")

app = FastAPI()

# ローカル単一プロセス・単一ユーザー前提のシンプルな状態保持
STATE = {"files": [], "selected": []}


@app.get("/api/status")
async def status():
    if not os.path.exists(STATE_PATH):
        return {"logged_in": False, "message": "scripts/login.py を実行してログインしてください"}
    try:
        ok = await sp_client.check_session(SITE_URL, STATE_PATH)
    except Exception:
        ok = False
    return {"logged_in": ok}


@app.post("/api/explore")
async def explore():
    if not os.path.exists(STATE_PATH):
        raise HTTPException(401, "先に scripts/login.py でログインしてください")
    try:
        files = await sp_client.walk_folder(SITE_URL, LIBRARY_PATH, STATE_PATH)
    except PermissionError as e:
        raise HTTPException(401, str(e))
    STATE["files"] = files
    STATE["selected"] = []
    return {"file_count": len(files)}


@app.post("/api/select")
async def select(body: dict):
    if not STATE["files"]:
        raise HTTPException(400, "先に /api/explore を実行してください")
    instruction = body.get("instruction", "").strip()
    if not instruction:
        raise HTTPException(400, "instruction が必要です")

    selected_paths = await groq_select.select_files(instruction, STATE["files"])
    by_path = {f["path"]: f for f in STATE["files"]}
    selected_items = [by_path[p] for p in selected_paths if p in by_path]
    STATE["selected"] = selected_items
    return {"selected": selected_items}


@app.get("/api/download")
async def download():
    if not STATE["selected"]:
        raise HTTPException(400, "選定済みファイルがありません")

    buf = io.BytesIO()
    try:
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for item in STATE["selected"]:
                data = await sp_client.download_file(SITE_URL, item["path"], STATE_PATH)
                zf.writestr(item["path"].lstrip("/"), data)
    except PermissionError as e:
        raise HTTPException(401, str(e))
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=selected_files.zip"},
    )


app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/")
async def index():
    return FileResponse("app/static/index.html")
