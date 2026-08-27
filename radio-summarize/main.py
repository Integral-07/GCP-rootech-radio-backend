import os
import time
import base64
from datetime import datetime, timezone
import functions_framework
import requests
from google import genai
from google.genai import errors as genai_errors
from google.cloud import storage

BUCKET_NAME = os.environ.get("BUCKET_NAME", "your-project-id-radio-audio")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "")

MAX_OUTPUT_TOKENS = 32000


def generate_with_retry(client, **kwargs):
    max_retries = 5
    retryable_codes = {429, 503}
    for attempt in range(max_retries):
        try:
            return client.models.generate_content(**kwargs)
        except genai_errors.APIError as e:
            status_code = getattr(e, "code", None)
            if status_code not in retryable_codes or attempt == max_retries - 1:
                raise
            wait_seconds = 2 ** (attempt + 1)
            time.sleep(wait_seconds)
    raise RuntimeError("Gemini API呼び出しがリトライ上限に達しました")


SYSTEM_PROMPT = """あなたは技術系ニュースラジオ「Rootech Radio(ルーテックラジオ)」の放送作家です。
パーソナリティは「須藤」という一人語りです(エンジニアなら分かる"sudo"にかけた名前です。
このネタは初回や節目で軽く触れる程度に留め、毎回説明しなくてよい)。
以下の特徴を持つラジオ番組の原稿を書いてください:

- 番組名・パーソナリティ名: オープニングで「Rootech Radio」「須藤」を必ず名乗る。
  番組名やパーソナリティ名を勝手に別の名前に変えたり、創作したりしない。
- 話者は須藤一人のみ。複数人の掛け合い形式にはしない。
- 想定リスナー: 情報系の学生〜若手エンジニア。基礎的なプログラミング・技術用語は
  知っている前提で構わないが、専門用語や固有名詞が出てきたら一言説明を添える。
  ただし説明は簡潔にとどめ、そこで立ち止まらず本題の掘り下げに時間を使う。
- トーン: カジュアルな語りかけ口調(友達に話すような親しみやすさ)をベースにしつつ、
  技術的な背景や意味については本格的に踏み込んで解説する。単なる雑談と侮れない、
  聞き応えのある内容にする。
- 深さ: 「何が起きたか」の紹介で終わらせず、以下のような掘り下げを積極的に入れる
  - なぜ今このタイミングで起きたのか、業界のどんな流れの中にあるのか
  - 技術的な仕組み・アーキテクチャ上のポイント(可能な範囲で具体的に)
  - 既存の類似技術・競合との違いや優位性
  - 開発者・エンジニアにとって実務上どう影響しうるか
  - 数値や具体的な事実(ベンチマーク、価格、リリース時期など)
- 長さ: 話し言葉で10分以上(目安3000〜4000文字程度)
- 構成:
  1. オープニング(番組名・パーソナリティ名を名乗る挨拶、今週の全体的な雰囲気を一言)
  2. 各ニュースを1つずつ紹介(タイトル→何が起きたか→技術的な深掘り→なぜ重要か→一言コメント)
  3. エンディング(まとめ、次週への軽い一言)
- 話し言葉として自然な文章にする(「〜ですね」「〜なんですよ」など、読み上げに適した口調)
- この原稿はそのまま音声合成(TTS)で読み上げられる。Markdownの見出し記法(##など)や
  箇条書き記号は使わない。各ニュースへの導入は、見出しではなく「さて、次は〜」
  「続いては〜」のような自然な話し言葉の繋ぎで行う

重要: 渡される記事情報(タイトルと概要)は情報量が少ないことがあります。
概要だけでは具体的な事実・数字・技術的背景が不足していると感じた場合は、
必ずGoogle検索を使って各ニュースの詳細(発表の背景、具体的な数値、技術仕様、
関連する過去の動き、専門家やユーザーの反応など)を調べ、その情報を原稿に反映してください。
表面的な言い換えや一般論だけの内容にせず、聞いて「そうだったのか」と思える
具体性・深さを持たせることを最優先してください。
"""

KATAKANA_CONVERSION_PROMPT = """以下はラジオ原稿です。この原稿を音声合成(TTS)で読み上げるため、
英語の固有名詞・専門用語・製品名・技術用語を、日本語話者が自然に発音するカタカナ表記に変換してください。

ルール:
- 英語表記(アルファベット)は、読み方が一般的に定着しているカタカナに置き換える
  例: GitHub→ギットハブ, API→エーピーアイ, Kubernetes→クーバネティス, AI→エーアイ
- 意味・文脈・文章構成・トーンは一切変更しない。カタカナ変換以外の書き換えは行わない
- 変換後の原稿本文のみを出力し、前置きや説明は不要

原稿:
{script}
"""


def convert_to_katakana(client: "genai.Client", script_text: str) -> str:
    response = generate_with_retry(
        client,
        model="gemini-2.5-flash",
        contents=KATAKANA_CONVERSION_PROMPT.format(script=script_text),
        config=genai.types.GenerateContentConfig(
            max_output_tokens=MAX_OUTPUT_TOKENS,
        ),
    )
    return response.text


def save_script_to_github(script_text: str, topic: str, date_obj) -> dict:
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return {"status": "skipped", "reason": "GitHub未設定"}

    month_folder = date_obj.strftime("%Y-%m")
    day = date_obj.strftime("%d")
    safe_topic = topic.replace("/", "-") if topic else "episode"
    path = f"{month_folder}/{day}_{safe_topic}.txt"

    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }

    content_b64 = base64.b64encode(script_text.encode("utf-8")).decode("utf-8")
    payload = {
        "message": f"Add script: {path}",
        "content": content_b64,
    }

    try:
        resp = requests.put(url, headers=headers, json=payload, timeout=30)
        if resp.status_code in (200, 201):
            return {"status": "success", "path": path}
        return {
            "status": "error",
            "status_code": resp.status_code,
            "detail": resp.text[:500],
        }
    except requests.RequestException as e:
        return {"status": "error", "detail": str(e)}


def build_user_prompt(articles: list, topic: str = "") -> str:
    articles_text = "\n\n".join(
        f"【記事{i+1}】\nタイトル: {a['title']}\n概要: {a['summary']}\nURL: {a.get('url', '')}"
        for i, a in enumerate(articles)
    )
    topic_line = f"今回のテーマは「{topic}」です。\n\n" if topic else ""
    return f"""{topic_line}以下は今週の技術ニュース記事です。これらを元に、ラジオ原稿を作成してください。

{articles_text}

上記の記事をもとに、指定されたトーン・構成でラジオ原稿を書いてください。
原稿本文のみを出力し、前置きや説明は不要です。"""


@functions_framework.http
def generate_script(request):
    request_json = request.get_json(silent=True)

    if not request_json or "articles" not in request_json:
        return {"error": "リクエストボディに 'articles' フィールドが必要です"}, 400

    articles = request_json["articles"]
    filename = request_json.get("filename", "script.txt")
    topic = request_json.get("topic", "")

    if not GEMINI_API_KEY:
        return {"error": "GEMINI_API_KEY が設定されていません"}, 500

    client = genai.Client(api_key=GEMINI_API_KEY)

    response = generate_with_retry(
        client,
        model="gemini-2.5-flash",
        contents=build_user_prompt(articles, topic),
        config=genai.types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            tools=[genai.types.Tool(google_search=genai.types.GoogleSearch())],
        ),
    )

    script_text = response.text

    truncated_warning = None
    try:
        finish_reason = response.candidates[0].finish_reason
        if str(finish_reason) not in ("STOP", "FinishReason.STOP"):
            truncated_warning = f"原稿生成が途中で打ち切られた可能性があります(finish_reason={finish_reason})"
    except (AttributeError, IndexError):
        pass

    script_text = convert_to_katakana(client, script_text)

    storage_client = storage.Client()
    bucket = storage_client.bucket(BUCKET_NAME)
    blob = bucket.blob(filename)
    blob.upload_from_string(script_text, content_type="text/plain; charset=utf-8")

    result = {
        "status": "success",
        "bucket": BUCKET_NAME,
        "filename": filename,
        "gcs_path": f"gs://{BUCKET_NAME}/{filename}",
        "script_text": script_text,
        "script_length": len(script_text),
    }
    if truncated_warning:
        result["warning"] = truncated_warning

    github_result = save_script_to_github(script_text, topic, datetime.now(timezone.utc))
    result["github_result"] = github_result

    return result
