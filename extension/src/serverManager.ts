import * as fs from 'node:fs';
import * as http from 'node:http';
import * as os from 'node:os';
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

const MAX_RESPONSE_BYTES = 10 * 1024 * 1024;

export class ServerManager implements vscode.Disposable {
  private port: number | undefined;
  private disposed = false;

  constructor(
    private readonly extensionPath: string,
    private readonly dataDir: string,
    private readonly output: vscode.OutputChannel,
  ) {}

  private readToken(): string {
    const fromEnv = process.env.CODEMIND_SESSION_TOKEN?.trim();
    if (fromEnv) return fromEnv;
    const base = process.env.CODEMIND_DATA_DIR
      ? path.resolve(process.env.CODEMIND_DATA_DIR.replace(/^~(?=$|[\\/])/, os.homedir()))
      : path.join(os.homedir(), '.codemind', 'data');
    try {
      const token = fs.readFileSync(path.join(base, 'session_token'), 'utf8').trim();
      if (token) return token;
    } catch { /* fall through */ }
    throw new Error('CodeMind AI engine is not running (no session token found). Start it with "python api.py" inside ai-engine/.');
  }

  async start(): Promise<void> {
    if (this.disposed) throw new Error('CodeMind is shutting down.');
    if (this.port) return;
    this.port = Number.parseInt(process.env.CODEMIND_PORT ?? '', 10) || 8000;
    this.output.appendLine(`Using Python AI engine at http://127.0.0.1:${this.port}`);
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
    const result = await this.request<{ jobId: string }>('POST', `/repos/${encodeURIComponent(id)}/index`);
    return result.jobId;
  }

  async refreshRepository(id: string): Promise<string> {
    const result = await this.request<{ jobId: string }>('POST', `/repos/${encodeURIComponent(id)}/refresh`);
    return result.jobId;
  }

  async getJob(id: string): Promise<Job> {
    return this.request<Job>('GET', `/jobs/${encodeURIComponent(id)}`);
  }

  async liveState(id: string): Promise<{ commitHash: string; branch: string }> {
    return this.request('GET', `/repos/${encodeURIComponent(id)}/live-state`);
  }

  async search(repoId: string, query: string): Promise<SearchResult> {
    return this.request('POST', '/search/code', { repoId, query, limit: 5 });
  }

  async reuse(repoId: string, input: { query: string } | { entityId: string }): Promise<SearchResult> {
    return this.request('POST', '/analysis/reuse', { repoId, ...input, limit: 3 });
  }

  async graphFile(repoId: string, filePath: string): Promise<{ nodes: { id: string; type: string; kind: string; startLine: number; endLine: number }[] }> {
    return this.request('GET', `/graph/file/${encodeURIComponent(filePath)}?repoId=${encodeURIComponent(repoId)}`);
  }

  async feedback(repoId: string, queryHash: string, entityId: string, decision: 'use' | 'ignore'): Promise<void> {
    await this.request('POST', '/analysis/reuse/feedback', { repoId, queryHash, entityId, decision });
  }

  async docsOverview(repoId: string): Promise<DocSection | null> {
    try { return await this.request('GET', `/docs/overview?repoId=${encodeURIComponent(repoId)}`); }
    catch (error) { if (error instanceof Error && /Documentation not found/.test(error.message)) return null; throw error; }
  }

  async docSection(repoId: string, sectionId: string): Promise<DocSection> {
    return this.request('GET', `/docs/section/${encodeURIComponent(sectionId)}?repoId=${encodeURIComponent(repoId)}`);
  }

  async docSections(repoId: string): Promise<DocList> {
    return this.request('GET', `/docs/sections?repoId=${encodeURIComponent(repoId)}`);
  }

  async staleDocs(repoId: string): Promise<{ repoId: string; sections: DocSection[]; status: DocsStatus }> {
    return this.request('GET', `/docs/stale?repoId=${encodeURIComponent(repoId)}`);
  }

  async syncDocs(repoId: string, mode: 'baseline' | 'incremental'): Promise<{ updated: string[]; affected: string[]; status: DocsStatus; overview: DocSection | null }> {
    return this.request('POST', '/docs/sync', { repoId, mode }, 600000);
  }

  private async request<T>(method: 'GET' | 'POST', requestPath: string, payload?: unknown, timeoutMs = 90000): Promise<T> {
    await this.start();
    if (!this.port) throw new Error('Local server is unavailable.');
    const token = this.readToken();

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
          'x-codemind-session': token,
        },
      }, (response) => {
        const chunks: Buffer[] = [];
        let received = 0;
        response.on('data', (chunk: Buffer) => {
          received += chunk.length;
          if (received > MAX_RESPONSE_BYTES) { request.destroy(new Error('Local server response was too large.')); return; }
          chunks.push(chunk);
        });
        response.on('error', reject);
        response.on('close', () => { if (!response.complete) reject(new Error('Local server closed the connection early.')); });
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
      request.on('error', (error: NodeJS.ErrnoException) => reject(error.code === 'ECONNREFUSED'
        ? new Error(`CodeMind AI engine is not running on 127.0.0.1:${port}. Start it with "python api.py" inside ai-engine/.`)
        : error));
      request.end(body);
    });
  }

  dispose(): void {
    this.disposed = true;
    this.port = undefined;
  }
}
