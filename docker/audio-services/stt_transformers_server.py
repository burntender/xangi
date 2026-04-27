#!/opt/gpu-env/.venv/bin/python
"""
Qwen3-ASR transformers backend server for xangi.

The implementation follows the model's local README guidance and exposes a
minimal OpenAI-compatible `/v1/audio/transcriptions` endpoint.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import torch
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from qwen_asr import Qwen3ASRModel

app = FastAPI(title="xangi STT transformers backend")

MODEL_PATH = Path(os.getenv("TRANSFORMERS_STT_MODEL_PATH", "/models/qwen3-asr/qwen3-asr"))
MODEL_ID = os.getenv("TRANSFORMERS_STT_MODEL_ID", MODEL_PATH.name)
DEVICE_MAP = os.getenv(
    "TRANSFORMERS_STT_DEVICE_MAP",
    "cuda:0" if torch.cuda.is_available() else "cpu",
)
MAX_NEW_TOKENS = int(os.getenv("TRANSFORMERS_STT_MAX_NEW_TOKENS", "256"))
MAX_INFERENCE_BATCH_SIZE = int(os.getenv("TRANSFORMERS_STT_MAX_BATCH_SIZE", "8"))
ATTN_IMPLEMENTATION = os.getenv("TRANSFORMERS_STT_ATTN_IMPLEMENTATION", "").strip()

_MODEL: Qwen3ASRModel | None = None

LANGUAGE_MAP = {
    "ar": "Arabic",
    "ca": "Cantonese",
    "de": "German",
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "hi": "Hindi",
    "id": "Indonesian",
    "it": "Italian",
    "ja": "Japanese",
    "ko": "Korean",
    "ms": "Malay",
    "nl": "Dutch",
    "pt": "Portuguese",
    "ru": "Russian",
    "th": "Thai",
    "tr": "Turkish",
    "vi": "Vietnamese",
    "zh": "Chinese",
}


def _normalize_language(language: str | None) -> str | None:
    if language in (None, ""):
        return None

    normalized = language.strip()
    if not normalized:
        return None

    lowered = normalized.lower()
    if lowered in LANGUAGE_MAP:
        return LANGUAGE_MAP[lowered]

    return normalized


def _extract_result_item(result: Any) -> tuple[str, str | None]:
    if isinstance(result, list):
        if not result:
            return "", None
        result = result[0]

    if isinstance(result, dict):
        return str(result.get("text", "")).strip(), result.get("language")

    text = str(getattr(result, "text", "")).strip()
    language = getattr(result, "language", None)
    return text, language


def get_model() -> Qwen3ASRModel:
    global _MODEL
    if _MODEL is not None:
        return _MODEL

    if not MODEL_PATH.exists():
        raise RuntimeError(f"transformers STT model path does not exist: {MODEL_PATH}")

    kwargs: dict[str, Any] = {
        "dtype": torch.bfloat16 if torch.cuda.is_available() else torch.float32,
        "device_map": DEVICE_MAP,
        "max_inference_batch_size": MAX_INFERENCE_BATCH_SIZE,
        "max_new_tokens": MAX_NEW_TOKENS,
    }
    if ATTN_IMPLEMENTATION:
        kwargs["attn_implementation"] = ATTN_IMPLEMENTATION

    _MODEL = Qwen3ASRModel.from_pretrained(str(MODEL_PATH), **kwargs)
    return _MODEL


@app.on_event("startup")
async def startup_event() -> None:
    get_model()


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "backend": "transformers",
        "model_path": str(MODEL_PATH),
        "model_loaded": _MODEL is not None,
        "device_map": DEVICE_MAP,
        "max_new_tokens": MAX_NEW_TOKENS,
    }


@app.get("/v1/models")
async def models() -> dict[str, Any]:
    return {
        "object": "list",
        "data": [
            {
                "id": MODEL_ID,
                "object": "model",
                "owned_by": "local",
                "backend": "transformers",
                "path": str(MODEL_PATH),
            }
        ],
    }


@app.post("/v1/audio/transcriptions")
async def transcriptions(
    file: UploadFile = File(...),
    model: str | None = Form(default=None),
    language: str | None = Form(default=None),
    prompt: str | None = Form(default=None),  # accepted for compatibility
    response_format: str | None = Form(default=None),
    temperature: str | None = Form(default=None),
) -> dict[str, Any]:
    del model, prompt, response_format, temperature

    suffix = Path(file.filename or "audio.wav").suffix or ".wav"
    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=400, detail="Uploaded audio file is empty")

    normalized_language = _normalize_language(language)
    model_instance = get_model()

    with tempfile.NamedTemporaryFile(prefix="xangi-stt-", suffix=suffix, delete=True) as temp_file:
        temp_file.write(payload)
        temp_file.flush()

        try:
            result = model_instance.transcribe(
                audio=temp_file.name,
                language=normalized_language,
            )
        except Exception as exc:  # pragma: no cover - runtime safety
            raise HTTPException(status_code=500, detail=f"transformers STT failed: {exc}") from exc

    text, detected_language = _extract_result_item(result)
    if not text:
        raise HTTPException(status_code=500, detail="transformers STT returned empty text")

    return {
        "object": "transcription",
        "backend": "transformers",
        "text": text,
        "language": detected_language or normalized_language,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)
