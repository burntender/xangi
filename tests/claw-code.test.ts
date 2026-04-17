import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { ClawCodeRunner } from '../src/claw-code.js';

vi.mock('child_process', () => {
  const EventEmitter = require('events');

  class MockProcess extends EventEmitter {
    stdin = { write: vi.fn(), end: vi.fn() };
    stdout = new EventEmitter();
    stderr = new EventEmitter();
    killed = false;

    kill() {
      this.killed = true;
      this.emit('close', 0);
    }
  }

  let mockProcess: MockProcess;

  return {
    spawn: vi.fn(() => {
      mockProcess = new MockProcess();
      return mockProcess;
    }),
    getMockProcess: () => mockProcess,
  };
});

vi.mock('fs', async () => {
  const actual = await vi.importActual('fs');
  return {
    ...actual,
    existsSync: vi.fn(() => false),
    readFileSync: vi.fn(() => ''),
  };
});

describe('ClawCodeRunner', () => {
  const originalCommand = process.env.CLAW_CODE_COMMAND;
  const originalAnthropicApiKey = process.env.ANTHROPIC_API_KEY;
  const originalAnthropicBaseUrl = process.env.ANTHROPIC_BASE_URL;

  beforeEach(() => {
    vi.clearAllMocks();
    delete process.env.CLAW_CODE_COMMAND;
  });

  afterEach(() => {
    vi.clearAllMocks();
    if (originalCommand === undefined) {
      delete process.env.CLAW_CODE_COMMAND;
    } else {
      process.env.CLAW_CODE_COMMAND = originalCommand;
    }
    if (originalAnthropicApiKey === undefined) {
      delete process.env.ANTHROPIC_API_KEY;
    } else {
      process.env.ANTHROPIC_API_KEY = originalAnthropicApiKey;
    }
    if (originalAnthropicBaseUrl === undefined) {
      delete process.env.ANTHROPIC_BASE_URL;
    } else {
      process.env.ANTHROPIC_BASE_URL = originalAnthropicBaseUrl;
    }
  });

  async function runAndComplete(
    runner: ClawCodeRunner,
    prompt: string,
    response: Record<string, unknown> = { message: 'ok', session_id: 'claw-session' },
    options?: { sessionId?: string; skipPermissions?: boolean }
  ) {
    const { spawn, getMockProcess } = await import('child_process');

    const runPromise = runner.run(prompt, options);
    await new Promise((resolve) => setTimeout(resolve, 50));

    const spawnMock = spawn as ReturnType<typeof vi.fn>;
    const callArgs = spawnMock.mock.calls[0];
    const command = callArgs[0] as string;
    const args = callArgs[1] as string[];
    const spawnOptions = callArgs[2] as { cwd?: string; env?: NodeJS.ProcessEnv };

    const mockProcess = (getMockProcess as () => any)();
    mockProcess.stdout.emit('data', JSON.stringify(response));
    mockProcess.emit('close', 0);

    const result = await runPromise;
    return { command, args, spawnOptions, result };
  }

  it('spawns claw with prompt-mode JSON args', async () => {
    const runner = new ClawCodeRunner({});
    const { command, args } = await runAndComplete(runner, 'hello');

    expect(command).toBe('claw');
    expect(args[0]).toBe('--output-format');
    expect(args[1]).toBe('json');
    expect(args).toContain('-p');
    expect(args[args.length - 1]).toContain('hello');
  });

  it('allows overriding the claw command path', async () => {
    process.env.CLAW_CODE_COMMAND = '/tmp/claw';
    const runner = new ClawCodeRunner({});
    const { command } = await runAndComplete(runner, 'hello');

    expect(command).toBe('/tmp/claw');
  });

  it('places model and permissions before -p because claw consumes trailing prompt args', async () => {
    const runner = new ClawCodeRunner({ model: 'qwen2.5-coder:32b', skipPermissions: true });
    const { args } = await runAndComplete(runner, 'do stuff');

    const promptIndex = args.indexOf('-p');
    const modelIndex = args.indexOf('--model');
    const permissionIndex = args.indexOf('--dangerously-skip-permissions');

    expect(permissionIndex).toBeGreaterThan(-1);
    expect(modelIndex).toBeGreaterThan(-1);
    expect(modelIndex).toBeLessThan(promptIndex);
    expect(permissionIndex).toBeLessThan(promptIndex);
    expect(args[promptIndex + 1]).toContain('do stuff');
  });

  it('uses workdir as cwd in spawn options', async () => {
    const runner = new ClawCodeRunner({ workdir: '/tmp/test' });
    const { spawnOptions } = await runAndComplete(runner, 'hello');

    expect(spawnOptions.cwd).toBe('/tmp/test');
  });

  it('passes provider environment variables to the claw child process', async () => {
    process.env.ANTHROPIC_API_KEY = 'dummy-key';
    process.env.ANTHROPIC_BASE_URL = 'http://127.0.0.1:8080';
    const runner = new ClawCodeRunner({});
    const { spawnOptions } = await runAndComplete(runner, 'hello');

    expect(spawnOptions.env?.ANTHROPIC_API_KEY).toBe('dummy-key');
    expect(spawnOptions.env?.ANTHROPIC_BASE_URL).toBe('http://127.0.0.1:8080');
  });

  it('maps claw message JSON to RunResult', async () => {
    const runner = new ClawCodeRunner({});
    const { result } = await runAndComplete(runner, 'hello', {
      message: 'claw says hi',
      session_id: 'session-123',
    });

    expect(result).toEqual({ result: 'claw says hi', sessionId: 'session-123' });
  });

  it('falls back to the xangi session id when claw JSON has no session id', async () => {
    const runner = new ClawCodeRunner({});
    const { result } = await runAndComplete(
      runner,
      'hello',
      { message: 'claw says hi' },
      { sessionId: 'xangi-session' }
    );

    expect(result).toEqual({ result: 'claw says hi', sessionId: 'xangi-session' });
  });

  it('adapts runStream through non-streaming claw JSON output', async () => {
    const runner = new ClawCodeRunner({});
    const onText = vi.fn();
    const onComplete = vi.fn();
    const { spawn, getMockProcess } = await import('child_process');

    const runPromise = runner.runStream('hello', { onText, onComplete });
    await new Promise((resolve) => setTimeout(resolve, 50));

    const spawnMock = spawn as ReturnType<typeof vi.fn>;
    const args = spawnMock.mock.calls[0][1] as string[];
    expect(args).toContain('-p');

    const mockProcess = (getMockProcess as () => any)();
    mockProcess.stdout.emit(
      'data',
      JSON.stringify({ message: 'stream fallback', session_id: 's' })
    );
    mockProcess.emit('close', 0);

    const result = await runPromise;
    expect(result).toEqual({ result: 'stream fallback', sessionId: 's' });
    expect(onText).toHaveBeenCalledWith('stream fallback', 'stream fallback');
    expect(onComplete).toHaveBeenCalledWith(result);
  });
});
