"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
exports.ServerManager = void 0;
const node_crypto_1 = require("node:crypto");
const http = __importStar(require("node:http"));
class ServerManager {
    extensionPath;
    dataDir;
    output;
    child;
    port;
    starting;
    token = (0, node_crypto_1.randomBytes)(32).toString('hex');
    disposed = false;
    constructor(extensionPath, dataDir, output) {
        this.extensionPath = extensionPath;
        this.dataDir = dataDir;
        this.output = output;
    }
    start() {
        if (this.disposed)
            return Promise.reject(new Error("CodeMind is shutting down."));
        this.port = 8000;
        this.output.appendLine("Connected to Python ai-engine on port 8000.");
        return Promise.resolve();
    }
    async hello(message) {
        const result = await this.request('POST', '/hello', { message });
        return result.reply;
    }
    async registerRepository(rootPath) {
        const result = await this.request('POST', '/repos/register', { rootPath });
        return result.repository;
    }
    async indexRepository(id) {
        const result = await this.request('POST', `/repos/${id}/index`);
        return result.jobId;
    }
    async refreshRepository(id) {
        const result = await this.request('POST', `/repos/${id}/refresh`);
        return result.jobId;
    }
    async getJob(id) {
        return this.request('GET', `/jobs/${id}`);
    }
    async liveState(id) {
        return this.request('GET', `/repos/${id}/live-state`);
    }
    async search(repoId, query) {
        return this.request('POST', '/search/code', { repoId, query, limit: 5 });
    }
    async reuse(repoId, input) {
        return this.request('POST', '/analysis/reuse', { repoId, ...input, limit: 3 });
    }
    async graphFile(repoId, filePath) {
        return this.request('GET', `/graph/file/${encodeURIComponent(filePath)}?repoId=${repoId}`);
    }
    async feedback(repoId, queryHash, entityId, decision) {
        await this.request('POST', '/analysis/reuse/feedback', { repoId, queryHash, entityId, decision });
    }
    async docsOverview(repoId) {
        try {
            return await this.request('GET', `/docs/overview?repoId=${repoId}`);
        }
        catch (error) {
            if (error instanceof Error && /Documentation not found/.test(error.message))
                return null;
            throw error;
        }
    }
    async docSection(repoId, sectionId) {
        return this.request('GET', `/docs/section/${sectionId}?repoId=${repoId}`);
    }
    async docSections(repoId) {
        return this.request('GET', `/docs/sections?repoId=${repoId}`);
    }
    async staleDocs(repoId) {
        return this.request('GET', `/docs/stale?repoId=${repoId}`);
    }
    async syncDocs(repoId, mode) {
        return this.request('POST', '/docs/sync', { repoId, mode }, 600000);
    }
    async request(method, requestPath, payload, timeoutMs = 90000) {
        await this.start();
        if (!this.port)
            throw new Error('Local server is unavailable.');
        const body = payload === undefined ? undefined : JSON.stringify(payload);
        const port = this.port;
        return new Promise((resolve, reject) => {
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
                const chunks = [];
                response.on('data', (chunk) => chunks.push(chunk));
                response.on('end', () => {
                    try {
                        const result = JSON.parse(Buffer.concat(chunks).toString());
                        if (!response.statusCode || response.statusCode < 200 || response.statusCode >= 300) {
                            reject(new Error(result.error ?? `Server returned ${response.statusCode}.`));
                            return;
                        }
                        resolve(result);
                    }
                    catch {
                        reject(new Error('Local server returned an invalid response.'));
                    }
                });
            });
            request.setTimeout(timeoutMs, () => request.destroy(new Error('Local server did not respond.')));
            request.on('error', reject);
            request.end(body);
        });
    }
    dispose() {
        this.disposed = true;
        this.child?.kill();
        this.child = undefined;
        this.port = undefined;
    }
}
exports.ServerManager = ServerManager;
//# sourceMappingURL=serverManager.js.map