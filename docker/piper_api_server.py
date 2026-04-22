"""
Piper TTS API Server
OpenAI 互換の API エンドポイントを提供
"""
import subprocess
import tempfile
import os
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
import uvicorn
import asyncio

app = FastAPI(title="Piper TTS API")
DEFAULT_SPEED = float(os.getenv("XANGI_AUDIO_TTS_SPEED", "0.9"))

class TTSRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    model_id: str = Field(default="tsukuyomi-chan-6lang-fp16", alias="model")
    input: str
    speaker_id: int = 0
    output_format: str = Field(default="wav", alias="response_format")  # wav or mp3
    voice: str | None = None
    speed: float = DEFAULT_SPEED

# モデルパス設定
MODEL_BASE_PATH = Path("/app/models")
DEFAULT_MODEL = "tsukuyomi-chan-6lang-fp16.onnx"
DEFAULT_CONFIG = "config.json"
OPENAI_MODEL_ALIASES = {"tts-1", "tts-1-hd"}


def _speed_to_length_scale(speed: float) -> float:
    # Piper controls speech rate through phoneme length.
    # Lower requested speed should make utterances longer.
    bounded_speed = min(max(speed, 0.25), 2.0)
    return 1.0 / bounded_speed


@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "piper-tts"}


@app.get("/v1/models")
async def list_models():
    """モデル一覧を返す（OpenAI 互換）"""
    models = []
    for model_file in MODEL_BASE_PATH.glob("*.onnx"):
        models.append({
            "id": model_file.stem,
            "object": "model",
            "created": int(model_file.stat().st_mtime),
            "owned_by": "piper"
        })
    return {"object": "list", "data": models}


@app.post("/v1/audio/speech")
async def create_speech(request: TTSRequest):
    """
    音声を合成して返す（OpenAI 互換）
    
    Request body:
    - model_id: モデル名（デフォルト：tsukuyomi-chan-6lang-fp16）
    - input: 合成するテキスト
    - speaker_id: 話者 ID（デフォルト：0）
    - output_format: 出力形式（wav または mp3）
    """
    requested_model_id = request.model_id
    if requested_model_id in OPENAI_MODEL_ALIASES:
        requested_model_id = DEFAULT_MODEL.removesuffix(".onnx")

    model_path = MODEL_BASE_PATH / f"{requested_model_id}.onnx"
    config_path = MODEL_BASE_PATH / DEFAULT_CONFIG
    
    if not model_path.exists():
        # デフォルトモデルを使用
        model_path = MODEL_BASE_PATH / DEFAULT_MODEL
        if not model_path.exists():
            raise HTTPException(status_code=400, detail=f"Model not found: {request.model_id}")
    
    if not config_path.exists():
        config_path = MODEL_BASE_PATH / DEFAULT_CONFIG
    
    # 一時ファイルに出力
    with tempfile.NamedTemporaryFile(suffix=f".{request.output_format}", delete=False) as tmp:
        output_path = tmp.name
    
    try:
        # piper コマンドを実行
        cmd = [
            "/opt/common-venv/bin/piper",
            "--model", str(model_path),
            "--config", str(config_path),
            "--output_file", output_path,
            "--speaker", str(request.speaker_id),
            "--length_scale", str(_speed_to_length_scale(request.speed)),
        ]
        
        # テキストを標準入力から渡す
        process = subprocess.run(
            cmd,
            input=request.input.encode("utf-8"),
            capture_output=True,
            timeout=60
        )
        
        if process.returncode != 0:
            error_msg = process.stderr.decode("utf-8") or process.stdout.decode("utf-8")
            raise HTTPException(status_code=500, detail=f"Piper error: {error_msg}")
        
        # OpenAI 互換 API に合わせてレスポンス本体で返す
        media_type = "audio/wav" if request.output_format == "wav" else "audio/mpeg"
        with open(output_path, "rb") as audio_file:
            content = audio_file.read()
        return Response(content=content, media_type=media_type)
    
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Speech generation timed out")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # 一時ファイルを削除
        if os.path.exists(output_path):
            os.unlink(output_path)


@app.post("/v1/audio/speech/stream")
async def create_speech_stream(request: TTSRequest):
    """
    音声をストリーミングで合成して返す
    """
    requested_model_id = request.model_id
    if requested_model_id in OPENAI_MODEL_ALIASES:
        requested_model_id = DEFAULT_MODEL.removesuffix(".onnx")

    model_path = MODEL_BASE_PATH / f"{requested_model_id}.onnx"
    config_path = MODEL_BASE_PATH / DEFAULT_CONFIG
    
    if not model_path.exists():
        model_path = MODEL_BASE_PATH / DEFAULT_MODEL
        if not model_path.exists():
            raise HTTPException(status_code=400, detail=f"Model not found: {request.model_id}")
    
    async def generate():
        cmd = [
            "/opt/common-venv/bin/piper",
            "--model", str(model_path),
            "--config", str(config_path),
            "--output_raw",
            "--speaker", str(request.speaker_id),
        ]
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await process.communicate(input=request.input.encode("utf-8"))
        
        if process.returncode != 0:
            error_msg = stderr.decode("utf-8") or stdout.decode("utf-8")
            raise HTTPException(status_code=500, detail=f"Piper error: {error_msg}")
        
        # 生オーディオをチャンクごとに送信
        chunk_size = 4096
        for i in range(0, len(stdout), chunk_size):
            yield stdout[i:i+chunk_size]
    
    return StreamingResponse(
        generate(),
        media_type="audio/raw",
        headers={
            "Content-Type": "audio/raw",
            "Transfer-Encoding": "chunked"
        }
    )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
