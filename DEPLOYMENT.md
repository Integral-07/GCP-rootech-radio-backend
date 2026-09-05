# DEPLOYMENT.md

Rootech Radio バックエンド各サービスの、デプロイ・起動コマンド一覧。
すべて `us-central1` リージョン、プロジェクト `project-d7204da6-8cc8-4fdd-849` を前提とする。

## 事前準備(共通)

### 必要なAPIの有効化

```bash
gcloud services enable run.googleapis.com
gcloud services enable cloudbuild.googleapis.com
gcloud services enable secretmanager.googleapis.com
gcloud services enable firestore.googleapis.com
gcloud services enable cloudscheduler.googleapis.com
gcloud services enable texttospeech.googleapis.com
gcloud services enable youtube.googleapis.com
gcloud services enable firebase.googleapis.com
```

### Secret Manager に登録済みのシークレット一覧

| シークレット名 | 用途 |
|---|---|
| `gemini-api-key` | Gemini API |
| `youtube-client-id` | YouTube OAuthクライアントID |
| `youtube-client-secret` | YouTube OAuthクライアントシークレット |
| `youtube-refresh-token` | YouTube OAuthリフレッシュトークン |
| `github-token` | GitHub Fine-grained PAT |
| `discord-webhook-url` | Discord通知先Webhook URL |
| `retry-secret` | 再実行APIの簡易認証キー |

新規作成する場合:
```bash
echo -n "シークレットの値" | gcloud secrets create <シークレット名> --data-file=-
gcloud secrets add-iam-policy-binding <シークレット名> \
  --member="serviceAccount:243824509590-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

### サービス間認証(共通)

各サービスは `roles/run.invoker` を付与しないと、他サービスから呼び出せない。

```bash
gcloud run services add-iam-policy-binding <サービス名> \
  --region us-central1 \
  --member="serviceAccount:243824509590-compute@developer.gserviceaccount.com" \
  --role="roles/run.invoker"
```

`radio-episodes-api`・`radio-feedback-api`・`radio-retry-api` は認証なし
(`--allow-unauthenticated`)で公開しているため、この設定は不要。

---

## radio-rss

RSSフィードから記事を収集するサービス。

```bash
cd ~/radio-rss
gcloud run deploy radio-rss \
  --source . \
  --region us-central1 \
  --set-env-vars FEED_URLS="https://zenn.dev/feed",MAX_ARTICLES_PER_FEED=5,DAYS_LOOKBACK=7 \
  --timeout=300 \
  --no-allow-unauthenticated
```

- `FEED_URLS`: デフォルトのRSS一覧(カンマ区切り)。実際は呼び出し時のリクエストボディで
  `feed_urls` を渡すため、この値は使われないことが多い
- `MAX_ARTICLES_PER_FEED`: 1フィードあたりの最大取得件数
- `DAYS_LOOKBACK`: 何日以内の記事を対象にするか

---

## radio-summarize

Gemini APIで原稿を生成し、Cloud Storage・GitHubに保存するサービス。

```bash
cd ~/radio-summarize
gcloud run deploy radio-summarize \
  --source . \
  --region us-central1 \
  --env-vars-file env-vars.yaml \
  --set-secrets GEMINI_API_KEY=gemini-api-key:latest,GITHUB_TOKEN=github-token:latest \
  --timeout=300 \
  --no-allow-unauthenticated
```

`env-vars.yaml` の内容(機密性の低い設定値、`.gitignore`対象):
```yaml
BUCKET_NAME: "rootech-radio-audio"
GITHUB_REPO: "Integral-07/rootech-radio-scripts"
```

- `.python-version` は `3.13` を指定(buildpacksの対応バージョンに注意)

---

## radio-tts

Cloud TTSで音声合成するサービス。Dockerfile(ffmpeg同梱)でビルドする。

```bash
cd ~/radio-tts
gcloud run deploy radio-tts \
  --source . \
  --region us-central1 \
  --set-env-vars BUCKET_NAME=rootech-radio-audio,VOICE_NAME=ja-JP-Neural2-B \
  --timeout=1800 \
  --memory=2Gi \
  --cpu=4 \
  --clear-base-image \
  --no-allow-unauthenticated
```

- `VOICE_NAME`: 音声の種類。`gcloud run services update radio-tts --region us-central1
  --update-env-vars VOICE_NAME=<voice名>` で再デプロイ不要に変更可能
- `--clear-base-image`: buildpacksで一度デプロイした後にDockerfileへ切り替える際に必要
- `Dockerfile` は `python:3.12-slim` ベース(pydubがPython 3.13で削除された`audioop`に
  依存するため、3.12固定が必須)
- CPU=4を指定する場合、メモリは **2Gi〜16Gi の範囲で指定する制約がある**
  (例: CPU=4でmemory=1Giは指定不可)

---

## radio-video

音声・背景画像・BGMから動画を生成するサービス。Dockerfile(ffmpeg同梱)でビルドする。

```bash
cd ~/radio-video
gcloud run deploy radio-video \
  --source . \
  --region us-central1 \
  --set-env-vars BUCKET_NAME=rootech-radio-audio \
  --timeout=1800 \
  --memory=2Gi \
  --cpu=4 \
  --clear-base-image \
  --no-allow-unauthenticated
```

- 背景画像: `gs://rootech-radio-audio/assets/background.jpg` に配置必須
- BGM素材: `gs://rootech-radio-audio/bgm/` 配下に1つ以上配置必須
- CPU/メモリの制約は radio-tts と同様

---

## radio-upload

YouTube投稿・再生リスト追加・Firestore保存を行うサービス。

```bash
cd ~/radio-upload
gcloud run deploy radio-upload \
  --source . \
  --region us-central1 \
  --set-env-vars BUCKET_NAME=rootech-radio-audio,SITE_URL=https://rootechradio.web.app \
  --set-secrets YOUTUBE_CLIENT_ID=youtube-client-id:latest,YOUTUBE_CLIENT_SECRET=youtube-client-secret:latest,YOUTUBE_REFRESH_TOKEN=youtube-refresh-token:latest \
  --timeout=1800 \
  --no-allow-unauthenticated
```

- YouTube OAuth同意画面は「本番環境」に切り替え済み(テストモードだとリフレッシュ
  トークンが7日で失効するため)
- スコープは `youtube.upload` + `youtube`(再生リスト操作に必要)。スコープ変更時は
  `get_youtube_refresh_token.py` を再実行し、`youtube-refresh-token` シークレットの
  バージョンを更新する必要がある:
  ```bash
  echo -n "新しいリフレッシュトークン" | gcloud secrets versions add youtube-refresh-token --data-file=-
  ```

---

## radio-orchestrator

パイプライン全体を指揮するサービス。

```bash
cd ~/radio-orchestrator
gcloud run deploy radio-orchestrator \
  --source . \
  --region us-central1 \
  --set-env-vars RSS_SERVICE_URL=https://radio-rss-243824509590.us-central1.run.app,SUMMARIZE_SERVICE_URL=https://radio-summarize-243824509590.us-central1.run.app,TTS_SERVICE_URL=https://radio-tts-243824509590.us-central1.run.app,VIDEO_SERVICE_URL=https://radio-video-243824509590.us-central1.run.app,UPLOAD_SERVICE_URL=https://radio-upload-243824509590.us-central1.run.app,BUCKET_NAME=rootech-radio-audio,RETRY_API_URL=https://radio-retry-api-pijirfw6rq-uc.a.run.app \
  --set-secrets DISCORD_WEBHOOK_URL=discord-webhook-url:latest,RETRY_SECRET=retry-secret:latest \
  --timeout=1800 \
  --no-allow-unauthenticated
```

`start_step` パラメータで途中のステップから再開可能(`rss`/`summarize`/`tts`/`video`/`upload`)。
詳細は ARCHITECTURE.md / DATAFLOW.md を参照。

---

## radio-episodes-api

フロントエンド向けの公開API(当日分のエピソードのみ提供)。

```bash
cd ~/radio-episodes-api
gcloud run deploy radio-episodes-api \
  --source . \
  --region us-central1 \
  --set-env-vars ALLOWED_ORIGIN=https://rootechradio.web.app \
  --timeout=60 \
  --allow-unauthenticated
```

- `/episodes`, `/episodes/{date}` は403で無効化中(過去分の野放し公開を防ぐため、意図的)

---

## radio-feedback-api

リスナーからの感想・要望を受け付けるAPI。

```bash
cd ~/radio-feedback-api
gcloud run deploy radio-feedback-api \
  --source . \
  --region us-central1 \
  --set-env-vars ALLOWED_ORIGIN=https://rootechradio.web.app \
  --timeout=60 \
  --allow-unauthenticated
```

- 同一IPからの60秒あたり3件超の投稿は自動的に429で拒否(Firestoreの複合インデックスが
  必要。初回デプロイ後、実際にAPIを叩いた際のエラーメッセージ内リンクから作成する)

---

## radio-retry-api

失敗したパイプラインを、失敗したステップから再実行するためのAPI。
Discordの通知リンクから呼び出される。

```bash
cd ~/radio-retry-api
gcloud run deploy radio-retry-api \
  --source . \
  --region us-central1 \
  --set-env-vars ORCHESTRATOR_URL=https://radio-orchestrator-243824509590.us-central1.run.app \
  --set-secrets RETRY_SECRET=retry-secret:latest \
  --timeout=1800 \
  --allow-unauthenticated
```

- 認証なしで公開しているが、`RETRY_SECRET` による簡易的なアクセス制御あり
- Discordの標準Webhook(Botアプリケーション非所有)はボタン(components)を送信できないため、
  URLはメッセージ内のプレーンテキストリンクとして送信している

---

## Cloud Storage ライフサイクル設定

```bash
cat > lifecycle.json << 'EOF'
{
  "rule": [
    {
      "action": {"type": "Delete"},
      "condition": {
        "age": 7,
        "matchesSuffix": [".mp4", ".mp3", ".txt"]
      }
    }
  ]
}
EOF
gcloud storage buckets update gs://rootech-radio-audio --lifecycle-file=lifecycle.json
```

(原稿はGitHubにも永続保存されるため、txtの7日後削除で問題ない)

---

## Cloud Scheduler ジョブ一覧(9個)

全ジョブ共通のOIDC認証設定:
```bash
--oidc-service-account-email=243824509590-compute@developer.gserviceaccount.com \
--oidc-token-audience="$ORCHESTRATOR_URL"
```

| ジョブID | スケジュール(JST) | トピック |
|---|---|---|
| radio-monday-ai | 毎週月 6:00 | AI・機械学習 |
| radio-tuesday-webdev | 毎週火 6:00 | Web開発・フロントエンド |
| radio-wednesday-security | 毎週水 6:00 | セキュリティ |
| radio-thursday-backend | 毎週木 6:00 | バックエンド・DB |
| radio-friday-cloud | 毎週金 6:00 | クラウド・インフラ |
| radio-saturday-programming | 毎週土 6:00 | プログラミング言語・OSSトレンド |
| radio-saturday-gadget | 毎週土 10:00 | ハードウェア・ガジェット |
| radio-sunday-companies | 毎週日 6:00 | 気になる企業ウォッチ |
| radio-sunday-weekly-summary | 毎週日 10:00 | 週間まとめ |

ジョブ作成例(月曜・AI):
```bash
ORCHESTRATOR_URL=$(gcloud run services describe radio-orchestrator --region us-central1 --format='value(status.url)')

gcloud scheduler jobs create http radio-monday-ai \
  --location=us-central1 \
  --schedule="0 6 * * 1" \
  --time-zone="Asia/Tokyo" \
  --uri="$ORCHESTRATOR_URL" \
  --http-method=POST \
  --headers="Content-Type=application/json" \
  --message-body='{"topic":"AI・機械学習","day_of_week":"monday","feed_urls":[...]}' \
  --oidc-service-account-email=243824509590-compute@developer.gserviceaccount.com \
  --oidc-token-audience="$ORCHESTRATOR_URL"
```

各ジョブの `feed_urls` の具体的な値は、`radio-orchestrator` へのリクエストボディの
一部としてジョブ定義に含まれている。変更する場合は
`gcloud scheduler jobs update http <ジョブID> --message-body='...'` で更新する。

---

## デプロイ時によくあるエラーと対処

| エラー | 原因 | 対処 |
|---|---|---|
| `MissingTargetException` | エントリポイント名とコード内の関数名不一致 | `Procfile` の `--target` を確認 |
| Python 3.14系で依存ライブラリが壊れる | buildpacksが最新Pythonを自動選択 | `.python-version` を明示(`3.13`など) |
| `pydub`が`audioop`エラー | Python 3.13でaudioopが削除された | Dockerfileで`python:3.12-slim`を使う |
| `--clear-base-image` エラー | buildpacks→Dockerfile切り替え時 | デプロイコマンドに`--clear-base-image`を追加 |
| memory/cpu指定エラー | CPU数に応じた最低メモリ要件がある | 上記の「vCPUとメモリ要件」表を参照 |
| Firestore `FailedPrecondition` | 複合インデックス未作成 | エラーメッセージ内リンクからインデックス作成 |
