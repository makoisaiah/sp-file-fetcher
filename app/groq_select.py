"""
ファイル一覧(パスのフラットリスト)を指示文に基づいてGroqに選ばせる。
探索(walk_drive)は完全にコード側で行い、ここでは「選定」のみをLLMに任せる。
Groq無料枠のTPM制限に収まるよう、CHUNK_SIZE件ずつに分けて問い合わせる。
"""
import json
import os
import httpx

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
CHUNK_SIZE = 300  # 1リクエストあたりのファイル件数の目安。TPMに応じて調整可能

SYSTEM_PROMPT = """あなたはファイル選定アシスタントです。
ユーザーの指示と、SharePoint上のファイル一覧(パス・サイズ・更新日)が与えられます。
指示に合致するファイルだけを選び、必ず次のJSON形式のみで回答してください。前置きや説明は一切書かないこと。

{"selected": ["path1", "path2", ...]}

該当するファイルがなければ {"selected": []} と返してください。
"""


def _chunk(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


async def select_files(instruction: str, files: list[dict]) -> list[str]:
    api_key = os.environ["GROQ_API_KEY"]
    model = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

    selected_paths: set[str] = set()

    async with httpx.AsyncClient(timeout=60.0) as client:
        for batch in _chunk(files, CHUNK_SIZE):
            listing = "\n".join(
                f"- {f['path']} (size={f['size']}, modified={f['modified']})" for f in batch
            )
            user_content = f"指示: {instruction}\n\nファイル一覧:\n{listing}"

            resp = await client.post(
                GROQ_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_content},
                    ],
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                },
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]

            try:
                parsed = json.loads(content)
                selected_paths.update(parsed.get("selected", []))
            except json.JSONDecodeError:
                # モデルが形式を崩した場合はこのチャンクをスキップ(全体は止めない)
                continue

    return sorted(selected_paths)
