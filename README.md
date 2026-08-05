# SharePoint File Fetcher

指示文に合致するファイルをSharePointサイトから探索・選定・一括ダウンロードするツール。

## 構成

```
ブラウザ（合言葉ログイン）
  ↓
FastAPI (app/main.py)
  ├─ /api/ms/login/*  → MSAL デバイスコードフローでMicrosoftアカウント認証（委任アクセス）
  ├─ /api/explore      → Graph APIで再帰探索（LLM不使用、コードのみ）
  ├─ /api/select        → ファイル一覧+指示文をGroqに渡し、該当ファイルを選定
  └─ /api/download      → 選定ファイルをZIPでまとめて返す
```

**設計方針**: 階層探索は決定的なコード処理（Graph APIの再帰呼び出し）で行い、
LLM（Groq）は「フラットなファイル一覧から指示に合うものを選ぶ」判断だけに使う。
これによりAPI呼び出し回数とトークン消費を最小限に抑えている。

## セットアップ

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

`.env` を編集:

- `APP_PASSWORD`: このWebアプリ自体の合言葉
- `SESSION_SECRET`: セッションCookie署名用のランダム文字列
- `GROQ_API_KEY`: console.groq.com で取得（無料）
- `GRAPH_CLIENT_ID`: 既定では公式の「Microsoft Graph PowerShell」クライアントIDを設定済み。
  組織で独自アプリ登録ができるなら、そちらのクライアントIDに差し替え可能
- `GRAPH_AUTHORITY`: テナント固有にしたい場合は
  `https://login.microsoftonline.com/<テナントID or ドメイン>` に変更

## 起動

```bash
uvicorn app.main:app --reload
```

`http://localhost:8000` を開く。

## 使い方

1. 合言葉でログイン
2. 「Microsoftでログイン」→ 表示されたコードを `microsoft.com/devicelogin` で入力
3. SharePointサイトのURLを入力して探索
4. 「2026年度の見積書のExcelだけ」のように自然文で指示 → Groqが該当ファイルを選定
5. ZIPでまとめてダウンロード

## デプロイ時の注意

- 本番では `APP_PASSWORD` / `SESSION_SECRET` / `GROQ_API_KEY` は環境変数として
  ホスティング先（Render / Fly.io / Railway等）のシークレット管理機能に設定し、`.env` はコミットしない
- 現状セッションはインメモリ管理のため、プロセス再起動でログイン状態が失われる。
  複数人・長期運用する場合はRedis等の外部ストアへの切り替えを検討
- ファイル件数が非常に多いサイト（数千〜）では `/api/explore` に時間がかかる。
  必要なら `app/graph.py` の `MAX_ITEMS` を調整
- Groqの無料枠は目安 30 RPM / 6,000〜12,000 TPM / 1日1,000リクエスト程度
  （モデルにより異なる）。ファイル一覧が多い場合は `app/groq_select.py` の
  `CHUNK_SIZE` を小さくして分割リクエストにする
