import asyncio
import io
import os
import sys
import zipfile

from dotenv import load_dotenv
from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import facets, groq_select, keyword_filter, sp_client

load_dotenv()

SITE_URL = os.environ["SP_SITE_URL"].rstrip("/")
LIBRARY_PATH = os.environ["SP_LIBRARY_PATH"]
STATE_PATH = os.environ.get("SESSION_STATE_PATH", "./storage_state.json")
CACHE_PATH = os.environ.get("TREE_CACHE_PATH", "./tree_cache.json")
FACET_CACHE_PATH = os.environ.get("FACET_CACHE_PATH", "./facet_cache.json")

app = FastAPI()

# ローカル単一プロセス・単一ユーザー前提のシンプルな状態保持
STATE = {"files": [], "selected": [], "login_in_progress": False, "login_error": None}


@app.get("/api/status")
async def status():
    if not os.path.exists(STATE_PATH):
        return {
            "logged_in": False,
            "login_in_progress": STATE["login_in_progress"],
            "login_error": STATE["login_error"],
        }
    try:
        ok = await sp_client.check_session(SITE_URL, STATE_PATH)
    except Exception:
        ok = False
    return {
        "logged_in": ok,
        "login_in_progress": STATE["login_in_progress"],
        "login_error": STATE["login_error"],
    }


async def _run_login_script():
    STATE["login_in_progress"] = True
    STATE["login_error"] = None
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "scripts/login.py",
            cwd=os.getcwd(),
        )
        returncode = await proc.wait()
        if returncode != 0:
            STATE["login_error"] = "ログインがタイムアウトしたか、失敗しました。もう一度お試しください。"
    except Exception as e:
        STATE["login_error"] = f"ログインスクリプトの起動に失敗しました: {e}"
    finally:
        STATE["login_in_progress"] = False


@app.post("/api/login/start")
async def login_start():
    if STATE["login_in_progress"]:
        return {"started": False, "message": "既にログイン処理が進行中です"}
    asyncio.create_task(_run_login_script())
    return {"started": True}



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


# ---------- 検索メニュー(ファセット)の生成・取得・手直し ----------

@app.post("/api/facets/generate")
async def generate_facets():
    """
    フォルダ構造から検索フォームの選択肢を作る。
    1) コードのみで候補フォルダ名を抽出(頻度分析)
    2) 候補だけをGroqに1回渡し、「分野」「担当科」「ノイズ」に仕分けさせる
    3) 結果をローカルにキャッシュ(以降の検索ではAIを呼ばない)
    """
    if not STATE["files"]:
        raise HTTPException(400, "先に /api/explore を実行してください")

    candidates = facets.extract_candidate_names(STATE["files"])
    if not candidates:
        raise HTTPException(400, "候補フォルダ名が見つかりませんでした")

    classified = await groq_select.classify_facets(candidates)
    facets.save_facet_cache(FACET_CACHE_PATH, classified)

    return {"facets": classified, "candidate_count": len(candidates)}


@app.get("/api/facets")
async def get_facets():
    cached = facets.load_facet_cache(FACET_CACHE_PATH)
    if cached is None:
        raise HTTPException(404, "まだ選択肢が生成されていません。「選択肢を生成」を実行してください")
    return {"facets": cached}


@app.put("/api/facets")
async def put_facets(body: dict):
    """
    生成された選択肢を手直しして保存する(人手による編集)。
    body は {"分野": [{"label":..., "keywords":[...]}], "担当科": [...], "ノイズ": [...]} の形式そのもの。
    """
    if "分野" not in body or "担当科" not in body:
        raise HTTPException(400, "分野/担当科を含むfacets形式で送ってください")
    facets.save_facet_cache(FACET_CACHE_PATH, body)
    return {"ok": True}


# ---------- 選定 ----------

@app.post("/api/select")
async def select(body: dict):
    """
    body 例:
    {
      "years_back": 5,                              // 直近何年分か(ファイル名の年で判定)。0/nullで条件なし
      "semester": "前期",                            // "前期" / "後期" / ""(条件なし)
      "file_type": "both",                           // "kakomon" / "kaisetsu" / "both" / ""(条件なし)
      "selections": {"分野": ["腎・泌尿器"], "担当科": ["内科"]}  // facetsから選んだラベル
    }
    """
    if not STATE["files"]:
        raise HTTPException(400, "先に /api/explore を実行してください")

    years_back = body.get("years_back") or 0
    semester = (body.get("semester") or "").strip()
    file_type = (body.get("file_type") or "").strip()
    selections = body.get("selections") or {}

    # 1. 年数/前期後期/種別のような固定条件をAND条件グループに変換(LLM不使用)
    groups, start_year, current_year = keyword_filter.build_fixed_element_groups(
        years_back, semester, file_type
    )

    # 2. 分野/担当科のようなフォルダ構成依存の条件は、facetキャッシュのkeywordsを使って変換
    if selections:
        facet_cache = facets.load_facet_cache(FACET_CACHE_PATH) or {}
        groups += facets.build_element_groups_from_selections(selections, facet_cache)

    if not groups:
        raise HTTPException(400, "検索条件を1つ以上指定してください")

    # 3. 要素間はAND、要素内のキーワードはORで、通常の文字列処理により
    #    (LLM不使用)該当ファイルを特定する
    matched = keyword_filter.filter_by_element_groups(STATE["files"], groups)

    fell_back = False
    folder_prefixes: list[str] = []

    if not matched:
        candidates = STATE["files"]
        fell_back = True
    else:
        # 4. マッチしたファイルの「親フォルダ」を特定し、そのフォルダ配下を丸ごと
        #    候補に加える(ファイル名自体に条件語が含まれていない連番ファイル等を拾うため)
        prefixes = keyword_filter.derive_folder_prefixes(matched)
        folder_prefixes = sorted(prefixes)
        candidates = keyword_filter.expand_by_prefixes(STATE["files"], prefixes)

    # 5. 絞り込んだ候補(通常は数十件程度)だけを、条件を文章化した指示でGroqに渡し
    #    最終選定させる(件数が少ないので通常1リクエストで済む)
    instruction_parts = []
    if start_year:
        instruction_parts.append(f"{start_year}年から{current_year}年")
    if semester:
        instruction_parts.append(semester)
    for category, labels in selections.items():
        if labels:
            instruction_parts.append(f"{category}が{'・'.join(labels)}")
    if file_type == "kakomon":
        instruction_parts.append("過去問")
    elif file_type == "kaisetsu":
        instruction_parts.append("解説資料")
    elif file_type == "both":
        instruction_parts.append("過去問と解説資料")
    instruction = "の".join(instruction_parts) + "に該当するファイル"

    selected_paths = await groq_select.select_files(instruction, candidates)

    by_path = {f["path"]: f for f in STATE["files"]}
    selected_items = [by_path[p] for p in selected_paths if p in by_path]
    STATE["selected"] = selected_items

    return {
        "selected": selected_items,
        "instruction": instruction,
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
        zip_paths = keyword_filter.build_shallow_zip_paths(STATE["selected"])
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for item in STATE["selected"]:
                data = await sp_client.download_file(SITE_URL, item["path"], STATE_PATH)
                zf.writestr(zip_paths[item["path"]], data)
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
