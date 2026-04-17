import { spawn, ChildProcess } from 'child_process';
import { randomUUID } from 'crypto';
import { processManager } from './process-manager.js';
import type { AgentRunner, RunOptions, RunResult, StreamCallbacks } from './agent-runner.js';
import { DEFAULT_TIMEOUT_MS } from './constants.js';
import { buildSystemPrompt, getSafeEnv } from './base-runner.js';
import type { BaseRunnerOptions } from './base-runner.js';
import { getGitHubEnv } from './github-auth.js';
import { logPrompt, logResponse } from './transcript-logger.js';

interface ClawCodeJsonResponse {
  message?: string;
  result?: string;
  session_id?: string;
}

const CLAW_PROVIDER_ENV_KEYS = [
  'ANTHROPIC_API_KEY',
  'ANTHROPIC_AUTH_TOKEN',
  'ANTHROPIC_BASE_URL',
  'OPENAI_API_KEY',
  'OPENAI_BASE_URL',
  'XAI_API_KEY',
  'XAI_BASE_URL',
] as const;

/**
 * Claw Code CLI を実行するランナー
 *
 * claw currently supports prompt-mode JSON output, but not Claude Code's
 * --append-system-prompt or stream-json contract. This runner adapts xangi's
 * prompt/session surface to claw's prompt JSON shape.
 */
export class ClawCodeRunner implements AgentRunner {
  private readonly command: string;
  private readonly model?: string;
  private readonly timeoutMs: number;
  private readonly workdir?: string;
  private readonly skipPermissions: boolean;
  private readonly systemPrompt: string;
  private currentProcess: ChildProcess | null = null;

  constructor(
    options?: BaseRunnerOptions & { platform?: import('./prompts/index.js').ChatPlatform }
  ) {
    this.command = process.env.CLAW_CODE_COMMAND || 'claw';
    this.model = options?.model;
    this.timeoutMs = options?.timeoutMs ?? DEFAULT_TIMEOUT_MS;
    this.workdir = options?.workdir;
    this.skipPermissions = options?.skipPermissions ?? false;
    this.systemPrompt = buildSystemPrompt(options?.platform);
  }

  private buildPrompt(prompt: string): string {
    return this.systemPrompt
      ? `<system-context>\n${this.systemPrompt}\n</system-context>\n\n${prompt}`
      : prompt;
  }

  private buildArgs(prompt: string, options?: RunOptions): string[] {
    const args: string[] = ['--output-format', 'json'];

    const skip = options?.skipPermissions ?? this.skipPermissions;
    if (skip) {
      args.push('--dangerously-skip-permissions');
    }

    if (this.model) {
      args.push('--model', this.model);
    }

    // claw's -p consumes all trailing args as the prompt, so it must be last.
    args.push('-p', prompt);
    return args;
  }

  async run(prompt: string, options?: RunOptions): Promise<RunResult> {
    const fullPrompt = this.buildPrompt(prompt);
    const args = this.buildArgs(fullPrompt, options);
    const fallbackSessionId = options?.sessionId || randomUUID();

    const sessionInfo = options?.sessionId
      ? ` (xangi session: ${options.sessionId.slice(0, 8)}...)`
      : ' (new)';
    console.log(`[claw-code] Executing in ${this.workdir || 'default dir'}${sessionInfo}`);

    if (options?.channelId && this.workdir) {
      logPrompt(this.workdir, options.channelId, fullPrompt, options?.sessionId);
    }

    const stdout = await this.execute(args, options?.channelId);
    const response = this.parseJsonResponse(stdout);
    const result = response.message ?? response.result ?? stdout.trim();
    const sessionId = response.session_id ?? fallbackSessionId;

    if (options?.channelId && this.workdir) {
      logResponse(this.workdir, options.channelId, { result, sessionId });
    }

    return { result, sessionId };
  }

  private execute(args: string[], channelId?: string): Promise<string> {
    const safeEnv = getSafeEnv();
    return new Promise((resolve, reject) => {
      const proc = spawn(this.command, args, {
        stdio: ['ignore', 'pipe', 'pipe'],
        cwd: this.workdir,
        env: { ...safeEnv, ...this.getProviderEnv(), ...getGitHubEnv(safeEnv) },
      });
      this.currentProcess = proc;

      if (channelId) {
        processManager.register(channelId, proc);
      }

      let stdout = '';
      let stderr = '';

      proc.stdout.on('data', (data) => {
        stdout += data.toString();
      });

      proc.stderr.on('data', (data) => {
        stderr += data.toString();
      });

      const timeout = setTimeout(() => {
        proc.kill();
        this.currentProcess = null;
        reject(new Error(`Claw Code CLI timed out after ${this.timeoutMs}ms`));
      }, this.timeoutMs);

      proc.on('close', (code) => {
        clearTimeout(timeout);
        this.currentProcess = null;

        if (code !== 0) {
          reject(new Error(`Claw Code CLI exited with code ${code}: ${stderr}`));
          return;
        }

        resolve(stdout);
      });

      proc.on('error', (err) => {
        clearTimeout(timeout);
        this.currentProcess = null;
        reject(new Error(`Failed to spawn Claw Code CLI: ${err.message}`));
      });
    });
  }

  private getProviderEnv(): NodeJS.ProcessEnv {
    const env: NodeJS.ProcessEnv = {};
    for (const key of CLAW_PROVIDER_ENV_KEYS) {
      if (process.env[key] !== undefined) {
        env[key] = process.env[key];
      }
    }
    return env;
  }

  private parseJsonResponse(output: string): ClawCodeJsonResponse {
    try {
      return JSON.parse(output.trim()) as ClawCodeJsonResponse;
    } catch (err) {
      if (err instanceof SyntaxError) {
        throw new Error(`Failed to parse Claw Code CLI response: ${output}`);
      }
      throw err;
    }
  }

  async runStream(
    prompt: string,
    callbacks: StreamCallbacks,
    options?: RunOptions
  ): Promise<RunResult> {
    try {
      const result = await this.run(prompt, options);
      callbacks.onText?.(result.result, result.result);
      callbacks.onComplete?.(result);
      return result;
    } catch (err) {
      const error = err instanceof Error ? err : new Error(String(err));
      callbacks.onError?.(error);
      throw error;
    }
  }

  cancel(): boolean {
    if (!this.currentProcess) {
      return false;
    }

    console.log('[claw-code] Cancelling current request');
    this.currentProcess.kill();
    this.currentProcess = null;
    return true;
  }
}
