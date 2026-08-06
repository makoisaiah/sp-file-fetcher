"""
ファイル一覧(パスのフラットリスト)を指示文に基づいてGroqに選ばせる。
探索(walk_folder)は完全にコード側で行い、ここでは「選定」のみをLLMに任せる。

対応している点:
- 日本語ファイル名はトークン数を多く消費するため、413 Payload Too Large が出たら
  バッチを自動で半分に割って再試行する
- Groq無料枠のレート制限(回数ベース RPM)を守るため、リクエスト間に最低間隔を空ける
- Groq無料枠のレート制限(トークン数ベース TPM)を守るため、直近60秒間の
  推定トークン使用量を追跡し、予算を超えそうなら送信前に待つ
- それでも 429 Too Many Requests が出た場合は Retry-After に従って待って再試行する
"""
import asyncio
import json
import os
import time

import httpx

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
INITIAL_CHUNK_SIZE = 40  # 初期バッチサイズ。413が出たら自動で半分に割る
MIN_CHUNK_SIZE = 5  # これ以下には割らず、諦めてスキップする
MIN_INTERVAL_SEC = 2.2  # 30 RPM ≈ 2秒/回 に余裕を持たせた最低リクエスト間隔
MAX_429_RETRIES = 5

# Groq無料枠のTPM(1分あたりトークン数)上限の目安。モデルやプランによって異なるため
# .env の GROQ_TPM_LIMIT で上書き可能。安全マージンとして実際の枠より少し低めに設定する。
TPM_LIMIT = int(os.environ.get("GROQ_TPM_LIMIT", "10000"))
TPM_SAFETY_MARGIN = 0.7  # 見積り誤差やレスポンス分も見込んで、予算の7割までしか使わない

SYSTEM_PROMPT = """あなたはファイル選定アシスタントです。
ユーザーの指示と、SharePoint上のファイル一覧(パス・サイズ・更新日)が与えられます。

重要: ファイル名の表記ゆれは同一内容とみなして柔軟に判断してください。
- 全角/半角、スペースの有無、括弧の種類(「（内科）」と「_内科_」など)の違いは無視して同じ意味として扱う
- 「解答」「回答」「答え」は、その過去問・試験に対応する解答であり、指示に「過去問」や「解説」が
  含まれる場合はその対象に含めてよい
- 「本試」「再試」は同じ科目・年度の試験の実施回次を表すラベル。「解答」はどちらの回次の試験に対する
  ものかに関わらず、その試験の答案・正解を意味する。ラベルが付いていないファイルは、明示的に「再試」と
  書かれていない限り「本試」の試験として扱ってよい
- 同じフォルダ内にある、命名パターンが酷似したファイル(同じ科目・同じ回の別ファイルなど)は、
  一部だけ表記が違うという理由で除外しないこと
- 迷った場合は、指示の意図に沿う限り除外より包含を優先する

指示に合致するファイルだけを選び、必ず次のJSON形式のみで回答してください。前置きや説明は一切書かないこと。

{"selected": ["path1", "path2", ...]}

該当するファイルがなければ {"selected": []} と返してください。
"""

FACET_SYSTEM_PROMPT = """あなたはファイルサーバーのフォルダ名を分類するアシスタントです。
渡されたフォルダ名の一覧を、次の3種類に仕分けしてください。

- 分野: 臓器・診療科目・学習テーマなど、内容を表す固有の名称(例: 腎泌尿器, 循環器, 血液, 消化器, 婦人科)
- 担当科: 内科・外科・病理など「担当科」を表す名称
- ノイズ: 日付、人名、「未整理」「削除してください」等の運用メモ、シラバス、その他検索の絞り込みに使えないもの

表記ゆれ(例: 「脳・神経」と「脳神経」、「頭頚部」と「頭頸部」)がある場合は同じ意味としてまとめ、
1つのlabelに複数のkeywords(元のフォルダ名の表記ゆれ)を持たせてください。

必ず次のJSON形式のみで回答してください。前置きや説明は一切書かないこと。

{
  "分野": [{"label": "腎・泌尿器", "keywords": ["腎泌尿器", "腎・泌尿器"]}],
  "担当科": [{"label": "内科", "keywords": ["内科"]}, {"label": "外科", "keywords": ["外科"]}],
  "ノイズ": ["4月12日", "中村"]
}
"""

_rate_lock = asyncio.Lock()
_last_call_time = 0.0

_token_lock = asyncio.Lock()
_token_log: list[tuple[float, int]] = []  # [(送信時刻, 推定トークン数), ...]


def _estimate_tokens(*texts: str) -> int:
    """
    大雑把なトークン数見積もり。日本語混じりのテキストは1文字あたり
    1トークン近くになることもあるため、安全側に倒して「文字数」をそのまま
    トークン数の見積もりとして使う(実際より多めに見積もる分には問題ない)。
    """
    total_chars = sum(len(t) for t in texts)
    return total_chars + 500  # レスポンス分の余裕も足しておく


async def _respect_rate_limit():
    global _last_call_time
    async with _rate_lock:
        now = time.monotonic()
        wait = _last_call_time + MIN_INTERVAL_SEC - now
        if wait > 0:
            await asyncio.sleep(wait)
        _last_call_time = time.monotonic()


async def _respect_token_budget(estimated_tokens: int):
    """直近60秒間の推定トークン使用量が予算を超えないよう、必要なら待機する。"""
    budget = int(TPM_LIMIT * TPM_SAFETY_MARGIN)
    async with _token_lock:
        while True:
            now = time.monotonic()
            _token_log[:] = [(t, n) for t, n in _token_log if now - t < 60]
            used = sum(n for _, n in _token_log)

            if used + estimated_tokens <= budget:
                _token_log.append((now, estimated_tokens))
                return

            if not _token_log:
                # 単発のリクエストだけでも予算を超える場合は待っても仕方ないので進む
                _token_log.append((now, estimated_tokens))
                return

            oldest_time = _token_log[0][0]
            wait = max(0.5, 60 - (now - oldest_time) + 0.1)
            print(f"[groq_select] TPM予算({budget}トークン/分)に近いため {wait:.1f}秒待機します")
            await asyncio.sleep(wait)


def _chunk(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _build_payload(model: str, system_prompt: str, user_content: str) -> dict:
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }


def _build_selection_user_content(instruction: str, batch: list) -> str:
    listing = "\n".join(
        f"- {f['path']} (size={f['size']}, modified={f['modified']})" for f in batch
    )
    return f"指示: {instruction}\n\nファイル一覧:\n{listing}"


async def _post_with_retry(client, api_key: str, model: str, system_prompt: str, user_content: str):
    estimated = _estimate_tokens(system_prompt, user_content)

    for attempt in range(MAX_429_RETRIES + 1):
        await _respect_token_budget(estimated)
        await _respect_rate_limit()
        resp = await client.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=_build_payload(model, system_prompt, user_content),
        )
        if resp.status_code != 429:
            return resp

        retry_after = float(resp.headers.get("retry-after", 5))
        print(f"[groq_select] 429 Too Many Requests: {retry_after:.1f}秒待って再試行します"
              f" (試行 {attempt + 1}/{MAX_429_RETRIES})")
        await asyncio.sleep(retry_after)

    resp.raise_for_status()  # ここに来たら最終的に失敗として例外を投げる
    return resp


async def _call_groq_select(client, api_key, model, instruction, batch):
    """1バッチをGroqに投げる。413なら再帰的に半分ずつに割って再試行する。"""
    if not batch:
        return set()

    user_content = _build_selection_user_content(instruction, batch)
    resp = await _post_with_retry(client, api_key, model, SYSTEM_PROMPT, user_content)

    if resp.status_code == 413:
        if len(batch) <= MIN_CHUNK_SIZE:
            print(f"[groq_select] warning: {len(batch)}件のバッチが413のためスキップします")
            return set()
        mid = len(batch) // 2
        left = await _call_groq_select(client, api_key, model, instruction, batch[:mid])
        right = await _call_groq_select(client, api_key, model, instruction, batch[mid:])
        return left | right

    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]

    try:
        parsed = json.loads(content)
        return set(parsed.get("selected", []))
    except json.JSONDecodeError:
        return set()


async def classify_facets(candidate_names: list[str]) -> dict:
    """
    フォルダ名候補(コード側のfacets.extract_candidate_namesで抽出済みのもの、
    数百件程度)を、Groqに1回だけ渡して「分野」「担当科」「ノイズ」に仕分けさせる。
    結果は呼び出し側でキャッシュし、検索のたびには呼び出さない想定。
    """
    api_key = os.environ["GROQ_API_KEY"]
    model = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

    user_content = "フォルダ名一覧:\n" + "\n".join(f"- {n}" for n in candidate_names)

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await _post_with_retry(client, api_key, model, FACET_SYSTEM_PROMPT, user_content)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            parsed = {"分野": [], "担当科": [], "ノイズ": candidate_names}

        # 形式チェック: label/keywordsを持つものだけ残す
        cleaned = {}
        for category in ("分野", "担当科"):
            options = []
            for opt in parsed.get(category, []):
                keywords = [k for k in opt.get("keywords", []) if isinstance(k, str) and k.strip()]
                label = opt.get("label", "")
                if label and keywords:
                    options.append({"label": label, "keywords": keywords})
            cleaned[category] = options
        cleaned["ノイズ"] = [n for n in parsed.get("ノイズ", []) if isinstance(n, str)]
        return cleaned


async def select_files(instruction: str, files: list) -> list:
    api_key = os.environ["GROQ_API_KEY"]
    model = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

    selected_paths = set()

    async with httpx.AsyncClient(timeout=60.0) as client:
        for batch in _chunk(files, INITIAL_CHUNK_SIZE):
            selected_paths |= await _call_groq_select(client, api_key, model, instruction, batch)

    return sorted(selected_paths)
