![](https://img.shields.io/badge/GoogleCloudPlatform-yellow)
![](https://img.shields.io/badge/状態-リリース-blue)
![](https://img.shields.io/badge/build-passing-green)

# Rootech Radio Maker & Uploader

「Rootech Radio(ルーテックラジオ)」のバックエンドパイプライン一式です。
RSS収集からAI原稿生成、音声合成、動画化、YouTube投稿、Firestore保存まで、
すべてGoogle Cloud Platform(Cloud Run)上で完結する自動化パイプラインです。

Rootech Radioは、AIパーソナリティ「須藤」("sudo"にかけた名前)が、毎日最新の
技術ニュースをお届けするラジオ番組です。

- YouTube: https://www.youtube.com/channel/UCyjTSKLiCrw8kmd3kBVCiBQ
- Webサイト(原稿・過去回): https://rootechradio.web.app
- 原稿アーカイブ: https://github.com/Integral-07/rootech-radio-scripts

## 関連ドキュメント

- [ARCHITECTURE.md](./ARCHITECTURE.md) — インフラ構成・技術的な詳細、既知の落とし穴
- [DATAFLOW.md](./DATAFLOW.md) — 各サービス間で実際にやり取りされるデータの形式
- [DEPLOYMENT.md](./DEPLOYMENT.md) — 全サービスのデプロイコマンド一覧

## 全体構成

```
Cloud Scheduler(曜日ごとに9ジョブ、朝6時/10時起動)
  │
  ▼
radio-orchestrator ─── パイプライン全体の指揮・Discord通知・失敗時の再実行対応
  │
  ├─▶ radio-rss ───────── RSSフィードから記事収集
  │
  ├─▶ radio-summarize ─── Gemini APIで原稿生成(検索機能付き)、GitHubに原稿保存
  │
  ├─▶ radio-tts ────────── Cloud TTSで音声合成(長文チャンク分割・結合)
  │
  ├─▶ radio-video ──────── ffmpegで動画化(背景画像・波形ビジュアライザー・BGM)
  │
  └─▶ radio-upload ─────── YouTube投稿 + 再生リスト追加 + Firestoreに保存

radio-episodes-api ── フロントエンド(techRadioPlt)向けの公開API(当日分のみ)
radio-feedback-api ── リスナーからの感想・要望受付API
radio-retry-api ───── 失敗したパイプラインを、失敗ステップから再実行するAPI
                       (Discord通知のリンクから呼び出される)
```

## 各サービスの役割

| サービス | 役割 | 主な技術 |
|---|---|---|
| `radio-rss` | RSSフィードから直近1週間の記事を収集 | feedparser |
| `radio-summarize` | 記事からラジオ原稿を生成、カタカナ変換版を別途作成、GitHub保存 | Gemini API (google-genai), Google検索連携 |
| `radio-tts` | 原稿を音声合成し、無音トリミング・結合 | Cloud Text-to-Speech, pydub |
| `radio-video` | 音声・背景画像・BGMから動画を生成 | ffmpeg |
| `radio-upload` | YouTubeへ動画投稿、再生リスト追加、Firestoreに保存 | YouTube Data API v3, Firestore |
| `radio-orchestrator` | 全体の実行順序を制御、Discordへ進捗ログ通知、失敗時の再実行リンク発行 | - |
| `radio-episodes-api` | フロントエンド向けに当日分エピソードを配信(CORS許可・認証なし) | Firestore |
| `radio-feedback-api` | 感想・要望を受け付け、IPアドレス記録・レート制限 | Firestore |
| `radio-retry-api` | 失敗ステップからパイプラインを再実行(secretによる簡易認証) | - |

## 配信スケジュール

| 曜日 | トピック |
|---|---|
| 月 | AI・機械学習 |
| 火 | Web開発・フロントエンド |
| 水 | セキュリティ |
| 木 | バックエンド・DB |
| 金 | クラウド・インフラ |
| 土(朝) | プログラミング言語・OSSトレンド |
| 土(昼) | ハードウェア・ガジェット |
| 日(朝) | 気になる企業ウォッチ |
| 日(昼) | 週間まとめ |

土日のみ1日2本配信(朝6時・昼10時)。それ以外の曜日は1本のみ。

## 障害対応:失敗したパイプラインの再実行

いずれかのステップが失敗すると、Discordに以下の形式で通知が届く。

```
[HH:MM:SS] [ERROR] step=upload status=failed detail=...
continue pipeline: <https://radio-retry-api-xxx.run.app?secret=...&data=...>
```

このリンクをクリックすると、`radio-retry-api` が `radio-orchestrator` を
`start_step` 付きで呼び出し、**失敗したステップから残りを再実行**する
(それより前のステップはやり直さない)。RSS未取得の場合など、途中データが
永続化されていないケースでは `rss` からの再実行になる。

再実行に必要な原稿・参考文献(sources)はCloud Storageに保存されているため、
`upload` 単体の再実行でも、参考文献付きで投稿できる。

## 認証情報の管理

APIキー・トークン類はすべて Secret Manager 経由で注入しており、リポジトリ内には
含まれていません。詳細な一覧は [DEPLOYMENT.md](./DEPLOYMENT.md) を参照。

`env-vars.yaml`(URLなど機密性の低い設定値)は `.gitignore` で除外しています。
各サービスのデプロイ時に、個別に用意してください。

YouTube連携の OAuth 同意画面は「本番環境」ステータスに設定済み(個人利用のため
Google審査は不要、プライバシーポリシー公開のみで切り替え可能だった)。
テストモードのままだとリフレッシュトークンが7日で失効するため、この設定は必須。

## デプロイ方法

各サービスの正確なデプロイコマンドは [DEPLOYMENT.md](./DEPLOYMENT.md) を参照。

## Cloud Storage ライフサイクル

生成された `.mp3` / `.mp4` / `.txt` は、作成から7日後に自動削除されます
(原稿は `radio-summarize` から GitHub にも保存されるため、長期保存はそちらに一任)。

## 費用について

Cloud Runの実行時間・メモリ使用量は Cloud Monitoring の指標で定期的に確認可能。
本プロジェクトの規模(週7〜9本の生成)では、通常運用時は Cloud Run 無料枠
(月180,000 vCPU秒・360,000 GiB秒)の範囲内に収まる。開発中の頻繁なテストで
一時的に無料枠を超えても、請求先アカウントに付与された期間限定クレジットで
相殺される。無料枠は暦月ベースで毎月リセットされる。
