# SharePoint File Fetcher (Local / Cookie方式)

大学テナントのMicrosoft Graph API利用が管理者同意ポリシーでブロックされているため、
OAuthアプリを経由せず、ブラウザの通常ログインで発行されるセッションCookieを使って
SharePoint REST API(`_api/web/...`)を直接叩く構成に変更したもの。
**ローカルPC上での実行専用**(合言葉によるWeb公開は行わない)。

## 構成

```
scripts/login.py
  → Playwrightでブラウザを開き、手動でMicrosoftアカウントにログイン(MFA含む)
  → セッションCookieを storage_state.json に保存

app/main.py (FastAPI, 127.0.0.1限定)
  ├─ /api/status   → セッションがまだ有効か確認
  ├─ /api/explore  → SharePoint REST APIで再帰探索(LLM不使用、コードのみ)
  ├─ /api/select   → ファイル一覧+指示文をGroqに渡し、該当ファイルを選定
  └─ /api/download → 選定ファイルをZIPでまとめて返す
```

**なぜ管理者同意が不要になるか**: OAuthアプリとしてMicrosoftに登録・認識される
プロセスを一切経由せず、あなたが普段ブラウザで見ているのと同じセッションを
プログラムから使っているだけのため、Entra ID側のアプリ同意ポリシーの対象外。

## セットアップ

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

cp .env.example .env
```

`.env` を編集:

- `GROQ_API_KEY`: console.groq.com で取得
- `SP_SITE_URL`: 対象のSharePointサイトURL
- `SP_LIBRARY_PATH`: 探索したいドキュメントライブラリのサーバー相対URL
  (SharePointでライブラリを開いたときのURLの `/sites/...` 以降の部分。
  日本語ライブラリ名(共有ドキュメント等)はそのまま日本語で入力してOK)

## 使い方

```bash
# 1. ログイン(初回・セッション失効時に実行)
python scripts/login.py

# 2. アプリ起動
uvicorn app.main:app --reload
```

`http://localhost:8000` を開いて:

1. 「確認する」でログイン状態をチェック
2. 「探索する」でファイル構造を取得
3. 自然文で指示を入力 →「選定する」でGroqが該当ファイルを選ぶ
4. 「ダウンロード」でZIP取得

## セッションが切れたら

`/api/explore` や `/api/download` が401エラーを返したら、セッションが失効しています。
`python scripts/login.py` を再実行してブラウザで再ログインし、
保存された `storage_state.json` を更新してください。

## 注意点

- `storage_state.json` にはログインCookieがそのまま入っているので、
  `.gitignore` に含めて絶対にコミットしないこと(すでに設定済み)
- あくまで「自分が普段閲覧できる範囲」を自動化しているだけだが、
  短時間に大量アクセスするとセキュリティ監視に引っかかる可能性があるため、
  個人利用の範囲(常識的な頻度・件数)にとどめること
- ライブラリ内のフォルダ数・ファイル数が多い場合、探索に時間がかかる
  (`app/sp_client.py` の `MAX_ITEMS` で上限調整可能)
