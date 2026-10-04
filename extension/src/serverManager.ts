import { spawn, ChildProcessWithoutNullStreams } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import * as http from 'node:http';
import * as path from 'node:path';
import * as vscode from 'vscode';

type HelloResponse = { reply: string };
export type Repository = { id: string; rootPath: string; defaultBranch: string };
export type Job = { id: string; state: 'queued' | 'running' | 'completed' | 'failed'; error: string | null;
  result: { fileCount: number; commitHash: string; branch: string; dirtyFingerprint: string;
    semantic?: { state: string; error: string | null; chunkCount: number };
    docs?: DocsStatus } | null };
export type CodeResult = { entityId: string; path: string; name: string; startLine: number; endLine: number;
  reason: string; classification?: string; confidence?: number; revision: string; sourceHash: string;
  callers: { name: string }[]; tests: { name: string }[]; docSectionId?: string; docKind?: string };
export type SearchResult = { results: CodeResult[]; mode: string; warning: string | null; queryHash?: string };
export type DocsStatus = { sectionCount: number; currentCount: number; staleCount: number; affectedCount: number; generatedCount: number };
export type DocEvidence = { entityId: string; type: string; path: string; name: string; kind: string;
  relation: string; startLine: number; endLine: number; sourceHash: string | null };
export type DocSection = { repoId: string; sectionId: string; title: string; kind: string;
  contentMarkdown: string; sourceCommit: string | null; generationReason: string; freshness: string;
  generated: boolean; proposedMarkdown: string | null; affectedBy: string[]; evidence: DocEvidence[] };
export type DocList = { repoId: string; sections: { sectionId: string; title: string; kind: string; freshness: string }[]; status: DocsStatus };

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
          CODEMIND_EMBEDDING_URL: vscode.workspace.getConfiguration('codemind').get('embeddingUrl', 'http://127.0.0.1:11434'),
          CODEMIND_EMBEDDING_MODEL: vscode.workspace.getConfiguration('codemind').get('embeddingModel', 'nomic-embed-text'),
          CODEMIND_MODEL_URL: vscode.workspace.getConfiguration('codemind').get('modelUrl', 'http://127.0.0.1:11434'),
          CODEMIND_MODEL_NAME: vscode.workspace.getConfiguration('codemind').get('modelName', 'llama3.2'),
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

  async refreshRepository(id: string): Promise<string> {
    const result = await this.request<{ jobId: string }>('POST', `/repos/${id}/refresh`);
    return result.jobId;
  }

  async getJob(id: string): Promise<Job> {
    return this.request<Job>('GET', `/jobs/${id}`);
  }

  async liveState(id: string): Promise<{ commitHash: string; branch: string }> {
    return this.request('GET', `/repos/${id}/live-state`);
  }

  async search(repoId: string, query: string): Promise<SearchResult> {
    return this.request('POST', '/search/code', { repoId, query, limit: 5 });
  }

  async reuse(repoId: string, input: { query: string } | { entityId: string }): Promise<SearchResult> {
    return this.request('POST', '/analysis/reuse', { repoId, ...input, limit: 3 });
  }

  async graphFile(repoId: string, filePath: string): Promise<{ nodes: { id: string; type: string; kind: string; startLine: number; endLine: number }[] }> {
    return this.request('GET', `/graph/file/${encodeURIComponent(filePath)}?repoId=${repoId}`);
  }

  async feedback(repoId: string, queryHash: string, entityId: string, decision: 'use' | 'ignore'): Promise<void> {
    await this.request('POST', '/analysis/reuse/feedback', { repoId, queryHash, entityId, decision });
  }

  async docsOverview(repoId: string): Promise<DocSection | null> {
    try { return await this.request('GET', `/docs/overview?repoId=${repoId}`); }
    catch (error) { if (error instanceof Error && /Documentation not found/.test(error.message)) return null; throw error; }
  }

  async docSection(repoId: string, sectionId: string): Promise<DocSection> {
    return this.request('GET', `/docs/section/${sectionId}?repoId=${repoId}`);
  }

  async docSections(repoId: string): Promise<DocList> {
    return this.request('GET', `/docs/sections?repoId=${repoId}`);
  }

  async staleDocs(repoId: string): Promise<{ repoId: string; sections: DocSection[]; status: DocsStatus }> {
    return this.request('GET', `/docs/stale?repoId=${repoId}`);
  }

  async syncDocs(repoId: string, mode: 'baseline' | 'incremental'): Promise<{ updated: string[]; affected: string[]; status: DocsStatus; overview: DocSection | null }> {
    return this.request('POST', '/docs/sync', { repoId, mode }, 600000);
  }

  private async request<T>(method: 'GET' | 'POST', requestPath: string, payload?: unknown, timeoutMs = 90000): Promise<T> {
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
      request.setTimeout(timeoutMs, () => request.destroy(new Error('Local server did not respond.')));
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
