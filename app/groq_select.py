"""
ファイル一覧(パスのフラットリスト)を指示文に基づいてGroqに選ばせる。
探索(walk_folder)は完全にコード側で行い、ここでは「選定」のみをLLMに任せる。

対応している点:
- 日本語ファイル名はトークン数を多く消費するため、413 Payload Too Large が出たら
  バッチを自動で半分に割って再試行する
- Groq無料枠のレート制限(目安 30 RPM)を守るため、リクエスト間に最低間隔を空ける
- それでも 429 Too Many Requests が出た場合は Retry-After に従って待って再試行する
"""
import asyncio
import json
import os
import time

import httpx

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
INITIAL_CHUNK_SIZE = 100  # 初期バッチサイズ。413が出たら自動で半分に割る
MIN_CHUNK_SIZE = 5  # これ以下には割らず、諦めてスキップする
MIN_INTERVAL_SEC = 2.2  # 30 RPM ≈ 2秒/回 に余裕を持たせた最低リクエスト間隔
MAX_429_RETRIES = 5

SYSTEM_PROMPT = """あなたはファイル選定アシスタントです。
ユーザーの指示と、SharePoint上のファイル一覧(パス・サイズ・更新日)が与えられます。
指示に合致するファイルだけを選び、必ず次のJSON形式のみで回答してください。前置きや説明は一切書かないこと。

{"selected": ["path1", "path2", ...]}

該当するファイルがなければ {"selected": []} と返してください。
"""

KEYWORD_SYSTEM_PROMPT = """あなたはファイル検索のためのキーワード抽出アシスタントです。
ユーザーの指示から、ファイル名やフォルダ名に含まれていそうな検索キーワードを考えてください。

考慮すること:
- 日本語の表記ゆれ(例: 令和8年 / R8 / 2026年度 / 2026)
- 略語とフルネームの両方(わかる場合)
- 単語を分割しすぎない(「過去問」のような複合語はそのまま1キーワードとして残す)
- 多すぎても絞り込みの意味が薄れるので、5〜12個程度を目安にする

必ず次のJSON形式のみで回答してください。前置きや説明は一切書かないこと。

{"keywords": ["keyword1", "keyword2", ...]}
"""

DECOMPOSE_SYSTEM_PROMPT = """あなたはファイル検索クエリの分解アシスタントです。
ユーザーの指示を、独立した「要素」に分解してください。典型的な要素は次のようなものです(これに限らない):

- 期間(例: 年度、年号、期間の範囲)
- 分野・科目名(例: 診療科、部署名、テーマ)
- ファイルの種類(例: 過去問、議事録、見積書、報告書)

指示に実際に含まれている要素だけを抽出してください(なければその要素は出力しない)。
各要素について、ファイル名・フォルダ名に実際に使われていそうな表記ゆれを含む
キーワード候補を複数(3〜8個程度)列挙してください。例えば「腎臓内科」なら
["腎臓内科", "腎内", "腎臓", "Nephrology", "腎"] のように、略語・別表記・英語表記なども含めます。
「2026年度」のような期間なら ["2026年度", "2026", "令和8年", "R8", "R.8"] のように和暦・西暦・略記を含めます。

これらの要素は最終的に「全ての要素を同時に満たすファイル」を探すためのAND条件として使われます。
要素を細かく分けすぎると該当ファイルが0件になりやすいので、指示から明確に読み取れる要素だけに絞ってください。

必ず次のJSON形式のみで回答してください。前置きや説明は一切書かないこと。

{"elements": [{"category": "分野", "keywords": ["腎臓内科", "腎内", "腎"]}, {"category": "種類", "keywords": ["過去問", "過去問題"]}]}

要素が1つも読み取れない場合は {"elements": []} と返してください。
"""

_rate_lock = asyncio.Lock()
_last_call_time = 0.0


async def _respect_rate_limit():
    global _last_call_time
    async with _rate_lock:
        now = time.monotonic()
        wait = _last_call_time + MIN_INTERVAL_SEC - now
        if wait > 0:
            await asyncio.sleep(wait)
        _last_call_time = time.monotonic()


def _chunk(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _build_payload(model: str, instruction: str, batch: list) -> dict:
    listing = "\n".join(
        f"- {f['path']} (size={f['size']}, modified={f['modified']})" for f in batch
    )
    user_content = f"指示: {instruction}\n\nファイル一覧:\n{listing}"
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }


async def _post_with_retry(client, api_key, model, instruction, batch):
    for attempt in range(MAX_429_RETRIES + 1):
        await _respect_rate_limit()
        resp = await client.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=_build_payload(model, instruction, batch),
        )
        if resp.status_code != 429:
            return resp

        retry_after = float(resp.headers.get("retry-after", 5))
        print(f"[groq_select] 429 Too Many Requests: {retry_after:.1f}秒待って再試行します"
              f" (試行 {attempt + 1}/{MAX_429_RETRIES})")
        await asyncio.sleep(retry_after)

    resp.raise_for_status()  # ここに来たら最終的に失敗として例外を投げる
    return resp


async def _call_groq(client, api_key, model, instruction, batch):
    """1バッチをGroqに投げる。413なら再帰的に半分ずつに割って再試行する。"""
    if not batch:
        return set()

    resp = await _post_with_retry(client, api_key, model, instruction, batch)

    if resp.status_code == 413:
        if len(batch) <= MIN_CHUNK_SIZE:
            print(f"[groq_select] warning: {len(batch)}件のバッチが413のためスキップします")
            return set()
        mid = len(batch) // 2
        left = await _call_groq(client, api_key, model, instruction, batch[:mid])
        right = await _call_groq(client, api_key, model, instruction, batch[mid:])
        return left | right

    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]

    try:
        parsed = json.loads(content)
        return set(parsed.get("selected", []))
    except json.JSONDecodeError:
        return set()


async def decompose_instruction(instruction: str) -> list[dict]:
    """
    指示文を「分野」「種類」「期間」のような要素に分解し、要素ごとの
    キーワード候補(表記ゆれ含む)を生成させる。これらはAND条件として
    フォルダの絞り込みに使う。
    """
    api_key = os.environ["GROQ_API_KEY"]
    model = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

    async with httpx.AsyncClient(timeout=30.0) as client:
        for attempt in range(MAX_429_RETRIES + 1):
            await _respect_rate_limit()
            resp = await client.post(
                GROQ_URL,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": DECOMPOSE_SYSTEM_PROMPT},
                        {"role": "user", "content": instruction},
                    ],
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                },
            )
            if resp.status_code != 429:
                break
            retry_after = float(resp.headers.get("retry-after", 5))
            await asyncio.sleep(retry_after)

        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]

        try:
            parsed = json.loads(content)
            elements = parsed.get("elements", [])
            # 形式チェック: category(str) と keywords(list[str]) を持つものだけ残す
            cleaned = []
            for el in elements:
                keywords = [k for k in el.get("keywords", []) if isinstance(k, str) and k.strip()]
                if keywords:
                    cleaned.append({"category": el.get("category", ""), "keywords": keywords})
            return cleaned
        except json.JSONDecodeError:
            return []


async def generate_keywords(instruction: str) -> list[str]:
    """
    指示文から検索キーワードをGroqに考えさせる。ペイロードが小さいので
    413の心配はほぼ無いが、429対策の共通ロジック(_post_with_retry)は使い回す。
    """
    api_key = os.environ["GROQ_API_KEY"]
    model = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

    async with httpx.AsyncClient(timeout=30.0) as client:
        for attempt in range(MAX_429_RETRIES + 1):
            await _respect_rate_limit()
            resp = await client.post(
                GROQ_URL,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": KEYWORD_SYSTEM_PROMPT},
                        {"role": "user", "content": instruction},
                    ],
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                },
            )
            if resp.status_code != 429:
                break
            retry_after = float(resp.headers.get("retry-after", 5))
            await asyncio.sleep(retry_after)

        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]

        try:
            parsed = json.loads(content)
            return [k for k in parsed.get("keywords", []) if isinstance(k, str) and k.strip()]
        except json.JSONDecodeError:
            return []


async def select_files(instruction: str, files: list) -> list:
    api_key = os.environ["GROQ_API_KEY"]
    model = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

    selected_paths = set()

    async with httpx.AsyncClient(timeout=60.0) as client:
        for batch in _chunk(files, INITIAL_CHUNK_SIZE):
            selected_paths |= await _call_groq(client, api_key, model, instruction, batch)

    return sorted(selected_paths)
