#!/opt/piper-venv/bin/python
"""
Xangi Audio Gateway Server
STT/TTS の統一 API エンドポイント
外部からのリクエストを内部サービスに転送
"""
import asyncio
import os
from typing import Any

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

app = FastAPI(title="Xangi Audio Gateway")


def _clean_base_url(value: str, default: str) -> str:
    return (value or default).rstrip("/")


class BackendConfig(BaseModel):
    name: str
    base_url: str
    transcription_path: str = "/v1/audio/transcriptions"
    health_path: str = "/health"
    models_path: str = "/v1/models"

    @property
    def transcription_url(self) -> str:
        return f"{self.base_url}{self.transcription_path}"

    @property
    def health_url(self) -> str:
        return f"{self.base_url}{self.health_path}"

    @property
    def models_url(self) -> str:
        return f"{self.base_url}{self.models_path}"


STT_BACKEND = os.getenv("STT_BACKEND", "llama_cpp").strip().lower()
TIMEOUT_SECONDS = float(os.getenv("AUDIO_PROXY_TIMEOUT_SECONDS", "300"))
RETRY_COUNT = max(1, int(os.getenv("AUDIO_PROXY_RETRIES", "2")))

STT_BACKENDS: dict[str, BackendConfig] = {
    "llama_cpp": BackendConfig(
        name="llama_cpp",
        base_url=_clean_base_url(
            os.getenv("STT_BASE_URL", ""),
            "http://stt-llama-cpp:8080",
        ),
        transcription_path=os.getenv("STT_REQUEST_PATH", "/v1/audio/transcriptions"),
        health_path=os.getenv("STT_HEALTH_PATH", "/health"),
        models_path=os.getenv("STT_MODELS_PATH", "/v1/models"),
    ),
    "transformers": BackendConfig(
        name="transformers",
        base_url=_clean_base_url(
            os.getenv("TRANSFORMERS_STT_BASE_URL", ""),
            "http://stt-transformers:8001",
        ),
    ),
    "vllm": BackendConfig(
        name="vllm",
        base_url=_clean_base_url(
            os.getenv("VLLM_STT_BASE_URL", ""),
            "http://stt-vllm:8001",
        ),
    ),
}

if STT_BACKEND not in STT_BACKENDS:
    raise RuntimeError(
        f"Unsupported STT_BACKEND={STT_BACKEND}. "
        f"Expected one of: {', '.join(sorted(STT_BACKENDS))}"
    )

ACTIVE_STT_BACKEND = STT_BACKENDS[STT_BACKEND]
TTS_BASE_URL = _clean_base_url(os.getenv("TTS_BASE_URL", ""), "http://piper-plus:8000")
TTS_HEALTH_PATH = os.getenv("TTS_HEALTH_PATH", "/health")
TTS_MODELS_PATH = os.getenv("TTS_MODELS_PATH", "/v1/models")


class SpeechRequest(BaseModel):
    model: str = "tts-1"
    input: str
    voice: str = "alloy"
    response_format: str = "wav"
    speed: float = 1.0


async def _request_with_retries(
    method: str,
    url: str,
    *,
    timeout: float | None = None,
    **kwargs: Any,
) -> httpx.Response:
    last_error: Exception | None = None
    effective_timeout = timeout or TIMEOUT_SECONDS

    for attempt in range(1, RETRY_COUNT + 1):
        try:
            async with httpx.AsyncClient(timeout=effective_timeout) as client:
                response = await client.request(method, url, **kwargs)
                return response
        except (httpx.TimeoutException, httpx.RequestError) as error:
            last_error = error
            if attempt >= RETRY_COUNT:
                break
            await asyncio.sleep(min(attempt, 3))

    if isinstance(last_error, httpx.TimeoutException):
        raise HTTPException(status_code=504, detail=f"Upstream timeout: {url}")
    if isinstance(last_error, httpx.RequestError):
        raise HTTPException(status_code=502, detail=f"Upstream request failed: {url}")
    raise HTTPException(status_code=500, detail="Unexpected proxy error")


def _extract_error(response: httpx.Response, default_message: str) -> str:
    try:
        payload = response.json()
    except ValueError:
        text = response.text.strip()
        return text or default_message

    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, str) and detail:
            return detail
        error = payload.get("error")
        if isinstance(error, str) and error:
            return error
    return default_message


async def _fetch_service_status(name: str, url: str) -> dict[str, Any]:
    try:
        response = await _request_with_retries("GET", url, timeout=20.0)
        body: Any
        try:
            body = response.json()
        except ValueError:
            body = response.text
        return {
            "service": name,
            "ok": response.is_success,
            "status_code": response.status_code,
            "url": url,
            "body": body,
        }
    except HTTPException as exc:
        return {
            "service": name,
            "ok": False,
            "status_code": exc.status_code,
            "url": url,
            "body": exc.detail,
        }


@app.post("/audio/transcribe")
@app.post("/v1/audio/transcriptions")
async def transcribe(
    file: UploadFile = File(...),
    model: str | None = Form(default=None),
    language: str | None = Form(default=None),
    prompt: str | None = Form(default=None),
    response_format: str | None = Form(default=None),
    temperature: str | None = Form(default=None),
    timestamp_granularities: list[str] | None = Form(default=None),
):
    """
    音声ファイルを文字起こし (STT)

    backend ごとの差分は gateway 側で吸収し、OpenAI 互換の形で転送する。
    """
    content = await file.read()
    form_data: dict[str, Any] = {}

    for key, value in {
        "model": model,
        "language": language,
        "prompt": prompt,
        "response_format": response_format,
        "temperature": temperature,
    }.items():
        if value not in (None, ""):
            form_data[key] = value

    if timestamp_granularities:
        form_data["timestamp_granularities[]"] = timestamp_granularities

    response = await _request_with_retries(
        "POST",
        ACTIVE_STT_BACKEND.transcription_url,
        files={"file": (file.filename, content, file.content_type or "application/octet-stream")},
        data=form_data,
    )

    if not response.is_success:
        raise HTTPException(
            status_code=response.status_code,
            detail=_extract_error(response, "STT processing failed"),
        )

    media_type = response.headers.get("content-type", "application/json")
    return Response(content=response.content, media_type=media_type)


@app.get("/audio/models")
@app.get("/v1/models")
async def list_models():
    """
    STT/TTS のモデル情報をまとめて返す。
    """
    stt_response = await _request_with_retries("GET", ACTIVE_STT_BACKEND.models_url, timeout=30.0)
    tts_response = await _request_with_retries(
        "GET",
        f"{TTS_BASE_URL}{TTS_MODELS_PATH}",
        timeout=30.0,
    )

    stt_models = stt_response.json() if stt_response.is_success else {"error": _extract_error(stt_response, "STT models fetch failed")}
    tts_models = tts_response.json() if tts_response.is_success else {"error": _extract_error(tts_response, "TTS models fetch failed")}

    return {
        "object": "xangi-audio-models",
        "stt_backend": ACTIVE_STT_BACKEND.name,
        "stt": stt_models,
        "tts": tts_models,
    }


@app.post("/audio/speech")
@app.post("/v1/audio/speech")
async def speech(request: SpeechRequest):
    """
    テキストを音声に変換 (TTS)

    OpenAI 互換 API エンドポイントを内部の piper-plus に転送する。
    """
    response = await _request_with_retries(
        "POST",
        f"{TTS_BASE_URL}/v1/audio/speech",
        json=request.model_dump(),
        headers={"Content-Type": "application/json"},
        timeout=60.0,
    )

    if not response.is_success:
        raise HTTPException(
            status_code=response.status_code,
            detail=_extract_error(response, "TTS processing failed"),
        )

    media_type = response.headers.get("content-type", "audio/wav")
    return Response(content=response.content, media_type=media_type)


@app.get("/audio/backends")
async def backends():
    return {
        "default_stt_backend": ACTIVE_STT_BACKEND.name,
        "available_stt_backends": sorted(STT_BACKENDS.keys()),
        "tts_backend": "piper-plus",
    }


@app.get("/health")
async def health():
    """ヘルスチェック"""
    stt_status, tts_status = await asyncio.gather(
        _fetch_service_status(ACTIVE_STT_BACKEND.name, ACTIVE_STT_BACKEND.health_url),
        _fetch_service_status("piper-plus", f"{TTS_BASE_URL}{TTS_HEALTH_PATH}"),
    )
    overall_status = "healthy" if stt_status["ok"] and tts_status["ok"] else "degraded"
    return {
        "status": overall_status,
        "stt_backend": ACTIVE_STT_BACKEND.name,
        "services": {
            "stt": stt_status,
            "tts": tts_status,
        },
    }


@app.get("/")
async def root():
    """API 情報"""
    return {
        "name": "Xangi Audio Gateway",
        "version": "2.0.0",
        "stt_backend": ACTIVE_STT_BACKEND.name,
        "endpoints": {
            "STT": "POST /v1/audio/transcriptions",
            "TTS": "POST /v1/audio/speech",
            "Models": "GET /v1/models",
            "Backends": "GET /audio/backends",
            "Health": "GET /health",
        },
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
