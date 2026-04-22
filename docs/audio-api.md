**日本語**

# Audio API

xangi の音声連携で利用する API 仕様です。

現時点では次の 2 系統があります。

- 外部 PC から xangi に音声を送って会話する `audio-chat API`
- xangi 内部で STT / TTS をまとめて扱う `audio gateway API`

## 1. Audio Chat API

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

- `401 Unauthorized`
  - API key 不一致
- `400 Bad Request`
  - `file` がない
  - `channel_id` も `XANGI_AUDIO_CHAT_CHANNEL_ID` もない
- `500 Internal Server Error`
  - STT / Discord 投稿 / LLM / TTS のいずれかで失敗

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

### Current Behavior Notes

- LLM 応答は音声読み上げ前提の短文制約を内部プロンプトで付与しています
- 応答音声は現在 `audio.base64` に base64 で格納して返します
- 単発リクエスト前提で、会話セッションの継続管理はまだありません

## 2. Audio Gateway API

xangi 内部で利用する STT / TTS の統合 API です。

### Host / Port

- `xangi-audio-gateway`
- デフォルト公開ポート: `8000`

### Endpoints

- `POST /v1/audio/transcriptions`
- `POST /v1/audio/speech`
- `GET /v1/models`
- `GET /audio/backends`
- `GET /health`

## 2.1 STT

### Endpoint

- `POST /v1/audio/transcriptions`

### Request

`multipart/form-data`

必須:

- `file`: 音声ファイル

任意:

- `model`
- `language`
- `prompt`
- `response_format`
- `temperature`

### cURL Example

```bash
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -F "file=@sample.wav" \
  -F "language=ja" \
  -F "response_format=json"
```

### Response

現在の `llama.cpp` backend では `json` 応答を前提に扱っています。

例:

```json
{
  "type": "transcript.text.done",
  "text": "こんにちは",
  "usage": {
    "type": "tokens",
    "input_tokens": 100,
    "output_tokens": 20,
    "total_tokens": 120
  }
}
```

## 2.2 TTS

### Endpoint

- `POST /v1/audio/speech`

### Request

`application/json`

フィールド:

- `model`: 例 `tsukuyomi-chan-6lang-fp16`
- `input`: 読み上げテキスト
- `voice`: 任意
- `response_format`: 現在は主に `wav`
- `speed`: 任意

### cURL Example

```bash
curl -X POST http://localhost:8000/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{
    "model": "tsukuyomi-chan-6lang-fp16",
    "input": "こんにちは。xangiです。",
    "response_format": "wav"
  }' \
  -o output.wav
```

### Response

- `200 OK`
- `Content-Type: audio/wav` など
- レスポンス本文は音声バイナリ

## 2.3 Models

### Endpoint

- `GET /v1/models`

### Response

```json
{
  "object": "xangi-audio-models",
  "stt_backend": "llama_cpp",
  "stt": {
    "object": "list",
    "data": []
  },
  "tts": {
    "object": "list",
    "data": []
  }
}
```

## 2.4 Backends

### Endpoint

- `GET /audio/backends`

### Response

```json
{
  "default_stt_backend": "llama_cpp",
  "available_stt_backends": ["llama_cpp", "transformers", "vllm"],
  "tts_backend": "piper-plus"
}
```

## 2.5 Health

### Endpoint

- `GET /health`

### Response

```json
{
  "status": "healthy",
  "stt_backend": "llama_cpp",
  "services": {
    "stt": {
      "service": "llama_cpp",
      "ok": true
    },
    "tts": {
      "service": "piper-plus",
      "ok": true
    }
  }
}
```

## 3. Environment Variables

主に利用する設定:

- `AUDIO_CHAT_API_ENABLED=true`
- `AUDIO_CHAT_API_PORT=18888`
- `XANGI_AUDIO_CHAT_API_KEY=...`
- `XANGI_AUDIO_CHAT_CHANNEL_ID=...`
- `XANGI_AUDIO_BASE_URL=http://xangi-audio-gateway:8000`
- `XANGI_AUDIO_TTS_MODEL=tsukuyomi-chan-6lang-fp16`
- `STT_BACKEND=llama_cpp`
- `STT_LLAMA_SERVER_ARGS=...`
- `TTS_BASE_URL=http://piper-plus:8000`

詳細な設定例は [`.env.example`](../.env.example) を参照してください。

## 4. Current Limitations

- `audio-chat API` はまだ単発会話のみです
- 応答音声は JSON の base64 返却で、ストリーミング返却ではありません
- STT の細かい timestamps や alignment には未対応です
- 音声向けの返答制約は現在コード内プロンプトで付与しています
- API 仕様は現時点では実装準拠で、将来変更の可能性があります
