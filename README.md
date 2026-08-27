![](https://img.shields.io/badge/Google Cloud Platform-yellow)
![](https://img.shields.io/badge/状態-リリース-blue)
![](https://img.shields.io/badge/build-passing-green)



# GCP Rootech Radio Backend

「Rootech Radio(ルーテックラジオ)」のバックエンドパイプライン一式です。
RSS収集からAI原稿生成、音声合成、動画化、YouTube投稿、Firestore保存まで、
すべてGoogle Cloud Platform(Cloud Run)上で完結する自動化パイプラインです。

Rootech Radioは、AIパーソナリティ「須藤」("sudo"にかけた名前)が、毎日最新の
技術ニュースをお届けするラジオ番組です。

- YouTube: https://www.youtube.com/channel/UCyjTSKLiCrw8kmd3kBVCiBQ
- Webサイト(原稿・過去回): https://rootechradio.web.app
- 原稿アーカイブ: https://github.com/Integral-07/rootech-radio-scripts

## 全体構成

```
Cloud Scheduler(曜日ごとに9ジョブ、朝6時/10時起動)
  │
  ▼
radio-orchestrator ─── パイプライン全体の指揮・Discord通知
  │
  ├─▶ radio-rss ───────── RSSフィードから記事収集
  │
  ├─▶ radio-summarize ─── Gemini APIで原稿生成(検索機能付き)、GitHubに原稿保存
  │
  ├─▶ radio-tts ────────── Cloud TTSで音声合成(長文チャンク分割・結合)
  │
  ├─▶ radio-video ──────── ffmpegで動画化(背景画像・波形ビジュアライザー・BGM)
  │
  └─▶ radio-upload ─────── YouTube投稿 + Firestoreにエピソード情報保存

radio-episodes-api ── フロントエンド(techRadioPlt)向けの公開API(当日分のみ)
radio-feedback-api ── リスナーからの感想・要望受付API
```

## 各サービスの役割

| サービス | 役割 | 主な技術 |
|---|---|---|
| `radio-rss` | RSSフィードから直近1週間の記事を収集 | feedparser |
| `radio-summarize` | 記事からラジオ原稿を生成、カタカナ変換、GitHub保存 | Gemini API (google-genai), Google検索連携 |
| `radio-tts` | 原稿を音声合成し、無音トリミング・結合 | Cloud Text-to-Speech, pydub |
| `radio-video` | 音声・背景画像・BGMから動画を生成 | ffmpeg |
| `radio-upload` | YouTubeへ動画投稿、Firestoreに保存 | YouTube Data API v3, Firestore |
| `radio-orchestrator` | 全体の実行順序を制御、Discordへ進捗ログ通知 | - |
| `radio-episodes-api` | フロントエンド向けに当日分エピソードを配信(CORS許可・認証なし) | Firestore |
| `radio-feedback-api` | 感想・要望を受け付け、IPアドレス記録・レート制限 | Firestore |

## 配信スケジュール

| 曜日 | トピック |
|---|---|
| 月 | AI・機械学習 |
| 火 | Web開発・フロントエンド |
| 水 | セキュリティ |
| 木 | バックエンド・DB |
| 金 | クラウド・インフラ |
| 土 | プログラミング言語・OSSトレンド / ハードウェア・ガジェット(2本立て) |
| 日 | 気になる企業ウォッチ / 週間まとめ(2本立て) |

## 認証情報の管理

APIキー・トークン類はすべて Secret Manager 経由で注入しており、リポジトリ内には
含まれていません。デプロイ時は各サービスに応じて以下を `--set-secrets` で渡します:

- `GEMINI_API_KEY`
- `YOUTUBE_CLIENT_ID` / `YOUTUBE_CLIENT_SECRET` / `YOUTUBE_REFRESH_TOKEN`
- `GITHUB_TOKEN`
- `DISCORD_WEBHOOK_URL`

`env-vars.yaml`(URLなど機密性の低い設定値)は `.gitignore` で除外しています。
各サービスのデプロイ時に、個別に用意してください。

## デプロイ方法(例: radio-rss)

```bash
cd radio-rss
gcloud run deploy radio-rss \
  --source . \
  --region us-central1 \
  --set-env-vars FEED_URLS="...",MAX_ARTICLES_PER_FEED=5,DAYS_LOOKBACK=7 \
  --timeout=300 \
  --no-allow-unauthenticated
```

サービスごとに必要な環境変数・シークレットが異なるため、各 `main.py` 冒頭の
docstring を参照してください。

## Cloud Storage ライフサイクル

生成された `.mp3` / `.mp4` / `.txt` は、作成から7日後に自動削除されます
(原稿は `radio-summarize` から GitHub にも保存されるため、長期保存はそちらに一任)。
