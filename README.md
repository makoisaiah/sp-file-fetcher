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

## 起動方法

### ダブルクリックで起動したい場合(推奨)

OSに応じて、以下のファイルをダブルクリックしてください。

| OS | ファイル | 管理者権限 |
|---|---|---|
| Linux | `run.sh` (事前に`./install_launcher.sh`を1回実行するとアイコン登録もされる) | 通常不要。既にGoogle Chrome等が入っている環境では、Playwrightに必要なライブラリは既に揃っていることが多い。不足している場合のみ、その場でsudoパスワードを求められる |
| macOS | `run.command` | 通常不要(初回はGatekeeperの警告が出ることがあるので、右クリック→「開く」で1回だけ許可) |
| Windows | `run.bat` | 通常不要 |

いずれも初回だけ自動セットアップ(仮想環境作成・依存パッケージインストール・ブラウザ本体のダウンロード)
が走り、`.env`が無ければテンプレートから自動生成されて一旦終了します。`GROQ_API_KEY`等を編集してから
もう一度同じファイルをダブルクリックすると、以降はセットアップをスキップしてすぐ起動し、既定のブラウザで
`http://127.0.0.1:8000` が自動的に開きます。

いずれのOSでも、実行中はターミナル/コマンドプロンプトのウィンドウが開いたままになります(裏で何が
起きているか見えるようにするためあえて表示したままにしています)。閉じるとサーバーも終了します。

### GitHub経由で配布・導入する場合

このプロジェクトをGitHubリポジトリとして管理する場合、典型的な導入手順は次の形になります。

```bash
git clone <あなたのリポジトリURL>
cd sp-file-fetcher-local
cp .env.example .env
# .env を編集(GROQ_API_KEY, SP_SITE_URL, SP_LIBRARY_PATH など)
```

その後、上記のOS別ファイルをダブルクリックするか、コマンドラインで直接起動してください。
`.env`・`storage_state.json`・`tree_cache.json`・`facet_cache.json`は`.gitignore`済みなので、
リポジトリには含まれません(各利用者が自分の環境で個別に作成する前提です)。

**注意**: `run.sh` / `run.command` の実行権限(実行可能ビット)はgitで管理されるため、
`git clone`した直後にダブルクリックで起動できない場合は、一度だけ以下を実行してください。

```bash
chmod +x run.sh run.command install_launcher.sh
```

### コマンドラインで起動する場合(全OS共通)

```bash
python -m venv .venv
source .venv/bin/activate   # Windowsは .venv\Scripts\activate
pip install -r requirements.txt
playwright install --with-deps chromium   # Windows/macOSは --with-deps 不要

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
uvicorn app.main:app --reload
```

(ダブルクリック起動の場合はこの手順は不要です)

`http://localhost:8000` を開くと:

1. 未ログインなら自動でログイン案内が表示されるので、「ログインを開始」を押す
   → ブラウザが開くのでMicrosoftアカウントでログイン(ログイン完了は自動検知されるので、
   ターミナル操作は不要)
2. ログインを検知すると「準備」画面が出るので、「はい、実行する」を押すと
   ファイル構造の探索と検索メニューの生成が自動で進む(手動で個別に行いたい場合は
   「手動で設定する」を選ぶと、詳細操作画面がそのまま開く)
3. 自然文ではなく選択式の条件(年数・前期後期・分野・担当科・種別)で検索
4. 「ダウンロード」で選定ファイルをZIP取得(ZIP内の階層は浅く保たれます)

「詳細設定を表示」リンクからは、いつでも手動探索・検索メニューの手直し(仕分け結果の
JSON直接編集)ができます。

## セッションが切れたら

`/api/explore` や `/api/download` が401エラーを返したら、セッションが失効しています。
アプリ画面に「ログインを開始」が再表示されるので、そこから再ログインしてください
(`python scripts/login.py` を手動実行しても構いません)。

## トラブルシューティング: `sudo apt`実行時に無関係なエラーが出る

Linuxで初回セットアップ時に依存パッケージのインストールが必要になった場合(`run.sh`が
「ライブラリが不足している可能性があります」と表示した場合)、`sudo apt-get install`が
呼ばれます。このとき、このプロジェクトとは無関係な、**システムに以前から溜まっていた
未設定パッケージ(カーネル関連など)のエラー**が一緒に表示されることがあります
(`apt`は指定したパッケージ以外に、保留中のパッケージ設定もまとめて処理しようとするため)。

これは事前に把握しておくべきOS側の問題で、Playwrightのインストールが原因ではありません。
先に以下を実行してOS側を正常な状態に戻してから、`run.sh`をもう一度実行してください。

```bash
sudo apt --fix-broken install
sudo dpkg --configure -a
```

## 注意点

- `storage_state.json` にはログインCookieがそのまま入っているので、
  `.gitignore` に含めて絶対にコミットしないこと(すでに設定済み)
- あくまで「自分が普段閲覧できる範囲」を自動化しているだけだが、
  短時間に大量アクセスするとセキュリティ監視に引っかかる可能性があるため、
  個人利用の範囲(常識的な頻度・件数)にとどめること
- ライブラリ内のフォルダ数・ファイル数が多い場合、探索に時間がかかる
  (`app/sp_client.py` の `MAX_ITEMS` で上限調整可能)
