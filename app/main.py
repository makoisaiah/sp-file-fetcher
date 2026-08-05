import io
import os
import zipfile

from dotenv import load_dotenv
from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import groq_select, keyword_filter, sp_client

load_dotenv()

SITE_URL = os.environ["SP_SITE_URL"].rstrip("/")
LIBRARY_PATH = os.environ["SP_LIBRARY_PATH"]
STATE_PATH = os.environ.get("SESSION_STATE_PATH", "./storage_state.json")
CACHE_PATH = os.environ.get("TREE_CACHE_PATH", "./tree_cache.json")

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
async def explore(body: dict = Body(default={})):
    """
    body 例:
      {}                                   → キャッシュがあればキャッシュを使用、なければ全体探索
      {"mode": "full"}                     → 強制的に全体を再探索してキャッシュを上書き
      {"mode": "subfolder", "path": "..."} → 指定フォルダ配下だけ再探索し、キャッシュにマージ
    """
    if not os.path.exists(STATE_PATH):
        raise HTTPException(401, "先に scripts/login.py でログインしてください")

    body = body or {}
    mode = body.get("mode", "auto")

    try:
        if mode == "subfolder":
            subfolder = body.get("path", "").strip()
            if not subfolder:
                raise HTTPException(400, "path が必要です")
            cached = sp_client.load_tree_cache(CACHE_PATH) or []
            new_subtree = await sp_client.walk_folder(SITE_URL, subfolder, STATE_PATH)
            files = sp_client.merge_subtree(cached, subfolder, new_subtree)
            sp_client.save_tree_cache(CACHE_PATH, files)
            source = f"部分再探索({subfolder})"

        elif mode == "full":
            files = await sp_client.walk_folder(SITE_URL, LIBRARY_PATH, STATE_PATH)
            sp_client.save_tree_cache(CACHE_PATH, files)
            source = "全体再探索"

        else:  # auto: キャッシュがあれば使う
            cached = sp_client.load_tree_cache(CACHE_PATH)
            if cached is not None:
                files = cached
                source = "キャッシュ"
            else:
                files = await sp_client.walk_folder(SITE_URL, LIBRARY_PATH, STATE_PATH)
                sp_client.save_tree_cache(CACHE_PATH, files)
                source = "全体探索(初回)"

    except PermissionError as e:
        raise HTTPException(401, str(e))

    STATE["files"] = files
    STATE["selected"] = []
    return {"file_count": len(files), "source": source}


@app.post("/api/select")
async def select(body: dict):
    if not STATE["files"]:
        raise HTTPException(400, "先に /api/explore を実行してください")
    instruction = body.get("instruction", "").strip()
    if not instruction:
        raise HTTPException(400, "instruction が必要です")

    # 1. Groqに指示を「分野」「種類」「期間」等の要素に分解させ、要素ごとの
    #    表記ゆれキーワード候補を挙げさせる(軽量な1リクエスト)
    elements = await groq_select.decompose_instruction(instruction)

    # 2. 要素間はAND、要素内のキーワードはORで、通常の文字列処理により
    #    (LLM不使用)該当ファイルを特定する
    matched = keyword_filter.filter_by_element_groups(STATE["files"], elements)

    fell_back = False
    folder_prefixes: list[str] = []

    if not matched:
        # 要素分解・AND一致で1件も引っかからなかった場合は絞り込みを諦めて全件を対象にする
        # (要素が細かすぎた/表記ゆれが外れた可能性があるため、精度より網羅性を優先)
        candidates = STATE["files"]
        fell_back = True
    else:
        # 3. マッチしたファイルの「親フォルダ」を特定し、そのフォルダ配下を丸ごと
        #    候補に加える(ファイル名自体にキーワードが含まれていない連番ファイル等を拾うため)
        prefixes = keyword_filter.derive_folder_prefixes(matched)
        folder_prefixes = sorted(prefixes)
        candidates = keyword_filter.expand_by_prefixes(STATE["files"], prefixes)

    # 4. 絞り込んだ候補だけを元の指示文でGroqに渡して最終選定させる
    #    (件数が大幅に減っているので、通常は1リクエストで済む)
    selected_paths = await groq_select.select_files(instruction, candidates)

    by_path = {f["path"]: f for f in STATE["files"]}
    selected_items = [by_path[p] for p in selected_paths if p in by_path]
    STATE["selected"] = selected_items

    return {
        "selected": selected_items,
        "elements": elements,
        "matched_count": len(matched),
        "folder_prefixes": folder_prefixes,
        "candidate_count": len(candidates),
        "fell_back_to_full": fell_back,
    }


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
