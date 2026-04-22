import fs from 'fs';
import os from 'os';
import path from 'path';

const DEFAULT_AUDIO_BASE_URL = 'http://localhost:8000';

function getAudioBaseUrl(): string {
  return (
    process.env.XANGI_AUDIO_BASE_URL ||
    process.env.AUDIO_API_BASE_URL ||
    DEFAULT_AUDIO_BASE_URL
  ).replace(/\/$/, '');
}

function getDataDir(): string {
  return process.env.DATA_DIR || path.join(os.homedir(), '.xangi');
}

function resolveWorkspacePath(inputPath: string): string {
  if (path.isAbsolute(inputPath)) return inputPath;
  const workspace = process.env.XANGI_WORKSPACE_PATH || process.env.WORKSPACE_PATH || process.cwd();
  return path.resolve(workspace, inputPath);
}

function ensureOutputPath(output?: string, extension = '.wav'): string {
  if (output) {
    const resolved = resolveWorkspacePath(output);
    fs.mkdirSync(path.dirname(resolved), { recursive: true });
    return resolved;
  }

  const dir = path.join(getDataDir(), 'media', 'generated-audio');
  fs.mkdirSync(dir, { recursive: true });
  return path.join(dir, `speech-${Date.now()}${extension}`);
}

function contentTypeToExtension(contentType: string | null): string {
  if (!contentType) return '.wav';
  if (contentType.includes('mpeg')) return '.mp3';
  if (contentType.includes('wav')) return '.wav';
  if (contentType.includes('flac')) return '.flac';
  return '.bin';
}

function detectMimeType(filePath: string): string {
  const ext = path.extname(filePath).toLowerCase();
  switch (ext) {
    case '.wav':
      return 'audio/wav';
    case '.mp3':
      return 'audio/mpeg';
    case '.m4a':
      return 'audio/mp4';
    case '.flac':
      return 'audio/flac';
    case '.ogg':
      return 'audio/ogg';
    case '.webm':
      return 'audio/webm';
    default:
      return 'application/octet-stream';
  }
}

async function audioFetch(pathname: string, options?: RequestInit): Promise<Response> {
  const res = await fetch(`${getAudioBaseUrl()}${pathname}`, options);
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    throw new Error(`Audio API error ${res.status}: ${body}`);
  }
  return res;
}

async function audioTranscribe(flags: Record<string, string>): Promise<string> {
  const inputFile = flags['file'];
  if (!inputFile) throw new Error('--file is required');

  const filePath = resolveWorkspacePath(inputFile);
  if (!fs.existsSync(filePath)) {
    throw new Error(`File not found: ${filePath}`);
  }

  const requestedResponseFormat = flags['response-format'] || 'json';
  const form = new FormData();
  const buffer = fs.readFileSync(filePath);
  form.append(
    'file',
    new Blob([buffer], { type: detectMimeType(filePath) }),
    path.basename(filePath)
  );

  for (const [flagKey, formKey] of [
    ['model', 'model'],
    ['language', 'language'],
    ['prompt', 'prompt'],
    ['temperature', 'temperature'],
  ] as const) {
    if (flags[flagKey]) form.append(formKey, flags[flagKey]);
  }
  // llama.cpp transcription currently accepts json only, so normalize here and post-process.
  form.append('response_format', 'json');

  const res = await audioFetch('/v1/audio/transcriptions', {
    method: 'POST',
    body: form,
  });

  const rawText = await res.text();

  try {
    const json = JSON.parse(rawText) as { text?: string };
    if ((requestedResponseFormat === 'json' || requestedResponseFormat === 'text') && json.text) {
      return json.text;
    }
    return JSON.stringify(json, null, 2);
  } catch {
    return rawText.trim();
  }
}

async function audioSpeech(flags: Record<string, string>): Promise<string> {
  const input = flags['input'];
  if (!input) throw new Error('--input is required');

  const payload = {
    model: flags['model'] || process.env.XANGI_AUDIO_TTS_MODEL || 'tsukuyomi-chan-6lang-fp16',
    input,
    voice: flags['voice'] || 'alloy',
    response_format: flags['format'] || 'wav',
    speed: Number(flags['speed'] || '1.0'),
  };

  const res = await audioFetch('/v1/audio/speech', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });

  const extension = contentTypeToExtension(res.headers.get('content-type'));
  const outputPath = ensureOutputPath(flags['output'], extension);
  const arrayBuffer = await res.arrayBuffer();
  fs.writeFileSync(outputPath, Buffer.from(arrayBuffer));

  return `🔊 音声を生成しました: ${outputPath}\nMEDIA:${outputPath}`;
}

async function audioHealth(): Promise<string> {
  const res = await audioFetch('/health');
  return JSON.stringify(await res.json(), null, 2);
}

async function audioModels(): Promise<string> {
  const res = await audioFetch('/v1/models');
  return JSON.stringify(await res.json(), null, 2);
}

async function audioBackends(): Promise<string> {
  const res = await audioFetch('/audio/backends');
  return JSON.stringify(await res.json(), null, 2);
}

export async function audioApi(command: string, flags: Record<string, string>): Promise<string> {
  switch (command) {
    case 'audio_transcribe':
      return audioTranscribe(flags);
    case 'audio_speech':
      return audioSpeech(flags);
    case 'audio_health':
      return audioHealth();
    case 'audio_models':
      return audioModels();
    case 'audio_backends':
      return audioBackends();
    default:
      throw new Error(`Unknown audio command: ${command}`);
  }
}
