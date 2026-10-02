import { spawn, ChildProcessWithoutNullStreams } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import * as http from 'node:http';
import * as path from 'node:path';
import * as vscode from 'vscode';

type HelloResponse = { reply: string };
export type Repository = { id: string; rootPath: string; defaultBranch: string };
export type Job = { id: string; state: 'queued' | 'running' | 'completed' | 'failed'; error: string | null; result: { fileCount: number } | null };

export class ServerManager implements vscode.Disposable {
  private child: ChildProcessWithoutNullStreams | undefined;
  private port: number | undefined;
  private starting: Promise<void> | undefined;
  private readonly token = randomBytes(32).toString('hex');
  private disposed = false;

  constructor(
    private readonly extensionPath: string,
    private readonly dataDir: string,
    private readonly output: vscode.OutputChannel,
  ) {}

  start(): Promise<void> {
    if (this.disposed) {
      return Promise.reject(new Error('CodeMind is shutting down.'));
    }
    if (this.child && this.port) {
      return Promise.resolve();
    }
    if (this.starting) {
      return this.starting;
    }

    const pending = new Promise<void>((resolve, reject) => {
      const serverPath = path.join(this.extensionPath, 'server', 'server.js');
      const child = spawn(process.execPath, [serverPath], {
        env: {
          ...process.env,
          ELECTRON_RUN_AS_NODE: '1',
          CODEMIND_SESSION_TOKEN: this.token,
          CODEMIND_DATA_DIR: this.dataDir,
        },
        stdio: ['pipe', 'pipe', 'pipe'],
      });
      this.child = child;
      this.output.appendLine('Starting local CodeMind server…');

      let settled = false;
      let stdout = '';
      const timeout = setTimeout(() => fail(new Error('Local server startup timed out.')), 10000);

      const fail = (error: Error) => {
        if (settled) return;
        settled = true;
        clearTimeout(timeout);
        this.port = undefined;
        if (this.child === child) this.child = undefined;
        child.kill();
        reject(error);
      };

      child.once('error', fail);
      child.once('exit', (code, signal) => {
        if (!settled) {
          fail(new Error(`Local server exited during startup (${code ?? signal}).`));
          return;
        }
        if (this.child === child) {
          this.child = undefined;
          this.port = undefined;
          if (!this.disposed) this.output.appendLine('Local CodeMind server stopped.');
        }
      });

      child.stderr.on('data', (chunk: Buffer) => {
        this.output.appendLine(chunk.toString().trimEnd());
      });

      child.stdout.on('data', (chunk: Buffer) => {
        stdout += chunk.toString();
        let newline = stdout.indexOf('\n');
        while (newline >= 0) {
          const line = stdout.slice(0, newline);
          stdout = stdout.slice(newline + 1);
          try {
            const event = JSON.parse(line) as { type?: string; port?: number };
            if (!settled && event.type === 'ready' && Number.isInteger(event.port) &&
                event.port! > 0 && event.port! < 65536) {
              settled = true;
              clearTimeout(timeout);
              this.port = event.port;
              this.output.appendLine(`Local CodeMind server ready on 127.0.0.1:${event.port}.`);
              resolve();
            }
          } catch {
            this.output.appendLine(`Server output: ${line}`);
          }
          newline = stdout.indexOf('\n');
        }
      });
    });

    this.starting = pending.finally(() => { this.starting = undefined; });
    return this.starting;
  }

  async hello(message: string): Promise<string> {
    const result = await this.request<HelloResponse>('POST', '/hello', { message });
    return result.reply;
  }

  async registerRepository(rootPath: string): Promise<Repository> {
    const result = await this.request<{ repository: Repository }>('POST', '/repos/register', { rootPath });
    return result.repository;
  }

  async indexRepository(id: string): Promise<string> {
    const result = await this.request<{ jobId: string }>('POST', `/repos/${id}/index`);
    return result.jobId;
  }

  async getJob(id: string): Promise<Job> {
    return this.request<Job>('GET', `/jobs/${id}`);
  }

  private async request<T>(method: 'GET' | 'POST', requestPath: string, payload?: unknown): Promise<T> {
    await this.start();
    if (!this.port) throw new Error('Local server is unavailable.');

    const body = payload === undefined ? undefined : JSON.stringify(payload);
    const port = this.port;
    return new Promise<T>((resolve, reject) => {
      const request = http.request({
        hostname: '127.0.0.1',
        port,
        path: requestPath,
        method,
        headers: {
          'content-type': 'application/json',
          ...(body === undefined ? {} : { 'content-length': Buffer.byteLength(body) }),
          'x-codemind-session': this.token,
        },
      }, (response) => {
        const chunks: Buffer[] = [];
        response.on('data', (chunk: Buffer) => chunks.push(chunk));
        response.on('end', () => {
          try {
            const result = JSON.parse(Buffer.concat(chunks).toString()) as T & { error?: string };
            if (!response.statusCode || response.statusCode < 200 || response.statusCode >= 300) {
              reject(new Error(result.error ?? `Server returned ${response.statusCode}.`));
              return;
            }
            resolve(result);
          } catch {
            reject(new Error('Local server returned an invalid response.'));
          }
        });
      });
      request.setTimeout(5000, () => request.destroy(new Error('Local server did not respond.')));
      request.on('error', reject);
      request.end(body);
    });
  }

  dispose(): void {
    this.disposed = true;
    this.child?.kill();
    this.child = undefined;
    this.port = undefined;
  }
}
