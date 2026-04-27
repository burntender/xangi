**日本語**

# Audio API

xangi の音声連携で利用する API 仕様です。

## システム構成

現在の音声システムは 3 層のアーキテクチャになっています。

```
┌──────────────────────┐      ┌──────────────────────┐      ┌─────────────┐
│   xangi (web-chat)   │─────▶│  xangi-audio-gateway  │─────▶│   STT       │
│  ポート 18888         │      │  ポート 8000          │      │  ポート 18081  │
└──────────────────────┘      └──────────────────────┘      └─────────────┘
                                 │                            │
                                 └────────────────────────────┘
                                          │
                                      ┌─────────────┐
                                      │   TTS       │
                                      │  ポート 18082  │
                                      └─────────────┘
```

- **xangi**（`xangi-custom`）: 音声チャット API / Web UI
- **xangi-audio-gateway**: STT / TTS の統合プロキシ（xangi 専用）
- **STT**（`stt`）: 文字起こし独立コンテナ（transformers backend）
- **TTS**（`tts`）: 音声合成独立コンテナ（piper-plus）

STT / TTS コンテナは `docker/audio-services/docker-compose.yml` で独立して起動・運用できます。
他のアプリからも直接 API として利用可能です。

---

## 1. 独立 STT / TTS API（audio-services）

xangi とは独立して起動できる STT / TTS サービスです。
他のアプリからも直接利用できます。

### 1.1 起動方法

```bash
# xangi ルートから
docker compose -f docker/audio-services/docker-compose.yml up --build -d
```

### 1.2 STT（文字起こし）

**Endpoint**: `POST http://localhost:18081/v1/audio/transcriptions`
**Backend**: Qwen3-ASR（transformers）

#### Request

`multipart/form-data`

| フィールド | 必須 | 説明 |
|----------|------|------|
| `file` | はい | 音声ファイル |
| `language` | いいえ | 言語コード（`ja`, `en`, `zh` など）。未指定時は自動検出 |
| `model` | いいえ | 受け付けますが未使用（互換用） |
| `prompt` | いいえ | 受け付けますが未使用（互換用） |
| `response_format` | いいえ | 受け付けますが未使用（互換用） |
| `temperature` | いいえ | 受け付けますが未使用（互換用） |

> **注**: transformers backend は `model`、`prompt`、`response_format`、`temperature` を受け付けますが、現在は内部で無視されます。

#### Response

```json
{
  "object": "transcription",
  "backend": "transformers",
  "text": "こんにちは",
  "language": "Japanese"
}
```

#### cURL Example

```bash
curl -X POST http://localhost:18081/v1/audio/transcriptions \
  -F "file=@sample.wav" \
  -F "language=ja"
```

#### 対応言語

| コード | 言語 | コード | 言語 |
|-------|------|-------|------|
| `ja` | Japanese | `en` | English |
| `zh` | Chinese | `ko` | Korean |
| `fr` | French | `de` | German |
| `es` | Spanish | `pt` | Portuguese |
| `ru` | Russian | `ar` | Arabic |
| `hi` | Hindi | `th` | Thai |
| `vi` | Vietnamese | `id` | Indonesian |
| `ms` | Malay | `nl` | Dutch |
| `it` | Italian | `tr` | Turkish |
| `ca` | Cantonese | | |

#### Health Check

```bash
curl http://localhost:18081/health
```

```json
{
  "ok": true,
  "backend": "transformers",
  "model_path": "/models/qwen3-asr/qwen3-asr",
  "model_loaded": true,
  "device_map": "cuda:0",
  "max_new_tokens": 256
}
```

#### Models

```bash
curl http://localhost:18081/v1/models
```

```json
{
  "object": "list",
  "data": [
    {
      "id": "Qwen3-ASR-1.7B",
      "object": "model",
      "owned_by": "local",
      "backend": "transformers",
      "path": "/models/qwen3-asr/qwen3-asr"
    }
  ]
}
```

---

### 1.3 TTS（音声合成）

**Endpoint**: `POST http://localhost:18082/v1/audio/speech`
**Backend**: piper-plus（tsukuyomi-chan-6lang-fp16）

#### Request

`application/json`

| フィールド | 型 | 必須 | デフォルト | 説明 |
|----------|---|------|----------|------|
| `model` | string | いいえ | `tsukuyomi-chan-6lang-fp16` | モデル名。`tts-1` / `tts-1-hd` もエイリアスとして対応 |
| `input` | string | はい | — | 合成するテキスト |
| `speaker_id` | int | いいえ | `0` | 話者 ID |
| `response_format` | string | いいえ | `wav` | 出力形式（`wav` / `mp3`） |
| `speed` | float | いいえ | `0.9` | 話速（`0.25`〜`2.0`） |
| `voice` | string | いいえ | — | 未使用（互換用） |

#### Response

- `200 OK`
- `Content-Type: audio/wav`（または `audio/mpeg`）
- レスポンス本文は音声バイナリ

#### cURL Example

```bash
curl -X POST http://localhost:18082/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{
    "input": "こんにちは。お元気ですか？",
    "response_format": "wav",
    "speed": 0.9
  }' \
  -o output.wav
```

#### ストリーミングエンドポイント

**Endpoint**: `POST http://localhost:18082/v1/audio/speech/stream`

リクエスト形式は上記と同じです。
レスポンスは `audio/raw` のストリーミング（chunked transfer）です。

#### Health Check

```bash
curl http://localhost:18082/health
```

```json
{
  "status": "healthy",
  "service": "piper-tts"
}
```

#### Models

```bash
curl http://localhost:18082/v1/models
```

```json
{
  "object": "list",
  "data": [
    {
      "id": "tsukuyomi-chan-6lang-fp16",
      "object": "model",
      "created": 1234567890,
      "owned_by": "piper"
    }
  ]
}
```

---

## 2. Audio Gateway API（xangi 専用）

xangi 内部で STT / TTS を統合して扱う API です。
外部アプリからは直接 STT / TTS API を利用してください。

### Host / Port

- `xangi-audio-gateway`
- デフォルト公開ポート: `8000`

### Endpoints

| メソッド | エンドポイント | 説明 |
|---------|--------------|------|
| `POST` | `/v1/audio/transcriptions` | STT（文字起こし） |
| `POST` | `/v1/audio/speech` | TTS（音声合成） |
| `GET` | `/v1/models` | STT / TTS モデル一覧 |
| `GET` | `/audio/backends` | バックエンド情報 |
| `GET` | `/health` | ヘルスチェック |

### 2.1 STT

**Endpoint**: `POST http://localhost:8000/v1/audio/transcriptions`

内部の STT コンテナ（ポート 18081）にプロキシします。

#### Request

`multipart/form-data`

| フィールド | 必須 | 説明 |
|----------|------|------|
| `file` | はい | 音声ファイル |
| `language` | いいえ | 言語コード。`STT_FORCE_LANGUAGE` が設定されている場合は上書きされる |
| `model` | いいえ | バックエンド名 |
| `prompt` | いいえ | プロンプト |
| `response_format` | いいえ | 応答フォーマット |
| `temperature` | いいえ | テンパチャ。`STT_DEFAULT_TEMPERATURE` が設定されている場合は上書きされる |
| `timestamp_granularities` | いいえ | タイムスタンプ粒度（配列） |

#### cURL Example

```bash
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -F "file=@sample.wav" \
  -F "language=ja"
```

### 2.2 TTS

**Endpoint**: `POST http://localhost:8000/v1/audio/speech`

内部の TTS コンテナ（ポート 18082）にプロキシします。

#### Request

`application/json`

| フィールド | 型 | 必須 | デフォルト | 説明 |
|----------|---|------|----------|------|
| `model` | string | いいえ | `tts-1` | モデル名 |
| `input` | string | はい | — | 合成するテキスト |
| `voice` | string | いいえ | `alloy` | 話者 |
| `response_format` | string | いいえ | `wav` | 出力形式 |
| `speed` | float | いいえ | `1.0` | 話速 |

#### cURL Example

```bash
curl -X POST http://localhost:8000/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{
    "input": "こんにちは。xangiです。",
    "response_format": "wav"
  }' \
  -o output.wav
```

#### Response

- `200 OK`
- `Content-Type: audio/wav`
- レスポンス本文は音声バイナリ

### 2.3 Models

**Endpoint**: `GET http://localhost:8000/v1/models`

STT と TTS の両方のモデル情報をまとめて返します。

#### Response

```json
{
  "object": "xangi-audio-models",
  "stt_backend": "transformers",
  "stt": {
    "object": "list",
    "data": [...]
  },
  "tts": {
    "object": "list",
    "data": [...]
  }
}
```

### 2.4 Backends

**Endpoint**: `GET http://localhost:8000/audio/backends`

#### Response

```json
{
  "default_stt_backend": "transformers",
  "available_stt_backends": ["llama_cpp", "transformers", "vllm"],
  "tts_backend": "piper-plus"
}
```

### 2.5 Health

**Endpoint**: `GET http://localhost:8000/health`

STT と TTS の両方のヘルスステータスを確認します。

#### Response

```json
{
  "status": "healthy",
  "stt_backend": "transformers",
  "services": {
    "stt": {
      "service": "transformers",
      "ok": true,
      "status_code": 200,
      "url": "http://host.docker.internal:18081/health",
      "body": {...}
    },
    "tts": {
      "service": "piper-plus",
      "ok": true,
      "status_code": 200,
      "url": "http://host.docker.internal:18082/health",
      "body": {...}
    }
  }
}
```

- `healthy`: STT と TTS の両方が応答している
- `degraded`: いずれかのサービスが応答していない

---

## 3. Audio Chat API

外部 PC から音声ファイルを送ると、xangi 側で以下を実行します。

1. STT で文字起こし
2. 文字起こし結果を Discord に投稿
3. LLM で音声向け返答文を生成
4. Discord に返信投稿
5. TTS で音声化
6. JSON レスポンスで結果を返却

### Endpoint

- `POST /api/audio-chat`

### Host / Port

- `xangi-custom`
- デフォルト公開ポート: `18888`

### Authentication

`XANGI_AUDIO_CHAT_API_KEY` が設定されている場合、ヘッダ `x-api-key` が必須です。

例:

```http
x-api-key: your-secret-key
```

### Request

`multipart/form-data`

必須フィールド:

- `file`: 音声ファイル

任意フィールド:

- `channel_id`: Discord チャンネル ID
- `language`: 音声言語コード。例: `ja`, `en`

`channel_id` を省略した場合は、`XANGI_AUDIO_CHAT_CHANNEL_ID` が使われます。

### cURL Example

```bash
curl -X POST http://<xangi-host>:18888/api/audio-chat \
  -H "x-api-key: <your-api-key>" \
  -F "file=@sample.wav" \
  -F "channel_id=123456789012345678" \
  -F "language=ja"
```

### Success Response

- `200 OK`
- `Content-Type: application/json`

```json
{
  "ok": true,
  "transcript": "こんにちは",
  "replyText": "こんにちは。ご用件をどうぞ。",
  "discord": {
    "channelId": "123456789012345678",
    "transcriptMessageId": "234567890123456789",
    "replyMessageId": "345678901234567890"
  },
  "audio": {
    "contentType": "audio/wav",
    "base64": "<base64-encoded-audio>"
  }
}
```

### Error Response

- `401 Unauthorized` — API key 不一致
- `400 Bad Request` — `file` がいない、`channel_id` も `XANGI_AUDIO_CHAT_CHANNEL_ID` もない
- `500 Internal Server Error` — STT / Discord 投稿 / LLM / TTS のいずれかで失敗

例:

```json
{
  "error": "audio chat failed"
}
```

または

```json
{
  "error": "STT failed: 400 ..."
}
```

### 動作上の注意点

- LLM 応答は音声読み上げ前提の短文制約を内部プロンプトで付与しています
- 応答音声は `audio.base64` に base64 で格納して返します
- 単発リクエスト前提で、会話セッションの継続管理はまだありません
- 長い音声ファイルは内部でチャンク分割して文字起こしします（デフォルト 20 秒区切り）
- 文字起こし結果の反復ループを検知した場合、エラーを返します

---

## 4. Environment Variables

### Audio Chat API（xangi 側）

| 環境変数 | デフォルト | 説明 |
|---------|----------|------|
| `AUDIO_CHAT_API_ENABLED` | `false` | 外部音声 API を有効化 |
| `AUDIO_CHAT_API_PORT` | `18888` | 外部音声 API ポート |
| `XANGI_AUDIO_CHAT_API_KEY` | — | API キー |
| `XANGI_AUDIO_CHAT_CHANNEL_ID` | — | デフォルト Discord チャンネル ID |

### Audio Gateway（xangi 側）

| 環境変数 | デフォルト | 説明 |
|---------|----------|------|
| `XANGI_AUDIO_BASE_URL` | `http://xangi-audio-gateway:8000` | audio gateway URL |
| `STT_BACKEND` | `transformers` | STT バックエンド（`llama_cpp` / `transformers` / `vllm`） |
| `TRANSFORMERS_STT_BASE_URL` | `http://host.docker.internal:18081` | STT コンテナ URL |
| `TTS_BASE_URL` | `http://host.docker.internal:18082` | TTS コンテナ URL |
| `STT_FORCE_LANGUAGE` | `ja` | 文字起こし強制的言語 |
| `STT_DEFAULT_TEMPERATURE` | `0` | デフォルトテンパチャ |
| `AUDIO_PROXY_TIMEOUT_SECONDS` | `300` | プロキシタイムアウト |
| `AUDIO_PROXY_RETRIES` | `2` | リトライ回数 |

### Audio Services（独立 STT / TTS）

| 環境変数 | デフォルト | 説明 |
|---------|----------|------|
| `STT_PORT` | `18081` | STT ホストポート |
| `TTS_PORT` | `18082` | TTS ホストポート |
| `TRANSFORMERS_STT_MODEL_PATH` | `/models/qwen3-asr/qwen3-asr` | STT モデルパス |
| `TRANSFORMERS_STT_MODEL_ID` | `Qwen3-ASR-1.7B` | STT モデル ID |
| `TRANSFORMERS_STT_DEVICE_MAP` | `cuda:0` | STT デバイス |
| `TRANSFORMERS_STT_MAX_NEW_TOKENS` | `256` | STT 最大トークン |
| `TRANSFORMERS_STT_MAX_BATCH_SIZE` | `8` | STT バッチサイズ |
| `PIPER_MODEL_PATH` | `./app/models/tsukuyomi-chan-6lang-fp16.onnx` | TTS モデルパス |
| `XANGI_AUDIO_TTS_SPEED` | `0.9` | TTS デフォルト話速 |

### xangi 本体（音声関連）

| 環境変数 | デフォルト | 説明 |
|---------|----------|------|
| `XANGI_AUDIO_TTS_MODEL` | `tsukuyomi-chan-6lang-fp16` | TTS モデル名 |
| `XANGI_AUDIO_TTS_SPEED` | `0.9` | TTS 話速 |
| `STT_CHUNK_SECONDS` | `20` | 音声チャンク分割秒数 |
| `STT_SPLIT_THRESHOLD_SECONDS` | `25` | チャンク分割の閾値 |

詳細な設定例は [`.env.example`](../.env.example) と [`docker/audio-services/.env.example`](../docker/audio-services/.env.example) を参照してください。

---

## 5. 利用パターン

### パターン A: 他のアプリから直接 STT / TTS を利用

```bash
# STT: 直接叩く
curl -X POST http://192.168.1.100:18081/v1/audio/transcriptions \
  -F "file=@voice.wav" -F "language=ja"

# TTS: 直接叩く
curl -X POST http://192.168.1.100:18082/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{"input": "テスト音声"}' -o test.wav
```

### パターン B: xangi から音声チャットを利用

```bash
# xangi の audio gateway 経由
curl -X POST http://localhost:18888/api/audio-chat \
  -H "x-api-key: <key>" \
  -F "file=@voice.wav"
```

---

## 6. Current Limitations

- `audio-chat API` はまだ単発会話のみです
- 応答音声は JSON の base64 返却で、ストリーミング返却ではありません
- STT の細かい timestamps や alignment には未対応です
- 音声向けの返答制約は現在コード内プロンプトで付与しています
- API 仕様は現時点では実装準拠で、将来変更の可能性があります
