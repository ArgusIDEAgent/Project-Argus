'use strict';

const http = require('node:http');
const { Registry, SCHEMA_VERSION } = require('./registry');
const { prepareGraph } = require('./graphBuilder');
const { EmbeddingProvider } = require('./embeddingProvider');
const { prepareSemantic } = require('./semanticStore');
const { searchCode } = require('./retrievalService');
const { analyzeReuse, recordFeedback } = require('./reuseAnalyzer');
const { calibrate } = require('./semanticCalibration');
const { ModelProvider } = require('./modelProvider');
const embeddingProvider = new EmbeddingProvider();
const modelProvider = new ModelProvider();
const scheduledJobs = new Set();

const token = process.env.CODEMIND_SESSION_TOKEN;
if (!token || token.length < 16) {
  process.stderr.write('CODEMIND_SESSION_TOKEN is required.\n');
  process.exit(1);
}

function sendJson(response, status, data) {
  response.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'cache-control': 'no-store',
  });
  response.end(JSON.stringify(data));
}

async function readJson(request) {
  const chunks = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > 8192) {
      throw new Error('Message is too large.');
    }
    chunks.push(chunk);
  }
  return JSON.parse(Buffer.concat(chunks).toString('utf8'));
}

async function handleRequest(registry, request, response) {
  if (request.headers['x-codemind-session'] !== token) {
    sendJson(response, 401, { error: 'Unauthorized' });
    return;
  }

  const url = new URL(request.url, 'http://127.0.0.1');
  let route = url.pathname;

  if (request.method === 'POST' && route === '/docs/sync') {
    try {
      const body = await readJson(request);
      if (!/^[a-f0-9]{24}$/.test(body.repoId)) throw new Error('Invalid repository ID.');
      sendJson(response, 200, await registry.syncDocs(body.repoId, body.mode || 'incremental', modelProvider, embeddingProvider));
    } catch (error) { sendJson(response, 400, { error: error.message }); }
    return;
  }
  if (request.method === 'GET' && ['/docs/overview', '/docs/stale', '/docs/sections'].includes(route)) {
    const repoId = url.searchParams.get('repoId');
    if (!/^[a-f0-9]{24}$/.test(repoId)) { sendJson(response, 400, { error: 'Invalid repository ID.' }); return; }
    const result = route === '/docs/overview' ? await registry.docsOverview(repoId) :
      route === '/docs/stale' ? await registry.staleDocs(repoId) : await registry.docSections(repoId);
    sendJson(response, result ? 200 : 404, result || { error: 'Documentation not found.' });
    return;
  }
  const docSectionMatch = /^\/docs\/section\/([a-f0-9]{40})$/.exec(route);
  if (request.method === 'GET' && docSectionMatch) {
    const repoId = url.searchParams.get('repoId');
    if (!/^[a-f0-9]{24}$/.test(repoId)) { sendJson(response, 400, { error: 'Invalid repository ID.' }); return; }
    const result = await registry.docSection(repoId, docSectionMatch[1]);
    sendJson(response, result ? 200 : 404, result || { error: 'Documentation section not found.' });
    return;
  }

  if (request.method === 'POST' && ['/search/code', '/analysis/reuse', '/analysis/reuse/feedback', '/analysis/reuse/calibrate'].includes(route)) {
    try {
      const body = await readJson(request);
      const result = route === '/analysis/reuse/calibrate' ? await calibrate(registry, embeddingProvider, body) :
        route === '/search/code' ? await searchCode(registry, embeddingProvider, body) :
        route === '/analysis/reuse' ? await analyzeReuse(registry, embeddingProvider, body) : await recordFeedback(registry, body);
      sendJson(response, 200, result);
    } catch (error) { sendJson(response, 400, { error: error.message }); }
    return;
  }
  if (request.method === 'POST' && route === '/embeddings/refresh') {
    try {
      const body = await readJson(request);
      if (!/^[a-f0-9]{24}$/.test(body.repoId)) throw new Error('Invalid repository ID.');
      route = `/repos/${body.repoId}/refresh`;
    } catch (error) { sendJson(response, 400, { error: error.message }); return; }
  }

  if (request.method === 'GET' && route === '/health') {
    sendJson(response, 200, { status: 'ok', service: 'codemind', version: 1, schemaVersion: SCHEMA_VERSION });
    return;
  }

  if (request.method === 'GET' && route === '/repos') {
    sendJson(response, 200, { repositories: await registry.list() });
    return;
  }

  if (request.method === 'POST' && route === '/repos/register') {
    try {
      const body = await readJson(request);
      const repository = await registry.register(body.rootPath, body.config);
      sendJson(response, 200, { repository });
    } catch (error) {
      sendJson(response, 400, { error: error instanceof Error ? error.message : 'Invalid repository.' });
    }
    return;
  }

  const statusMatch = /^\/repos\/([a-f0-9]{24})\/status$/.exec(route);
  if (request.method === 'GET' && statusMatch) {
    const status = await registry.status(statusMatch[1]);
    sendJson(response, status ? 200 : 404, status || { error: 'Repository not found.' });
    return;
  }

  const liveMatch = /^\/repos\/([a-f0-9]{24})\/live-state$/.exec(route);
  if (request.method === 'GET' && liveMatch) {
    const state = await registry.liveState(liveMatch[1]);
    sendJson(response, state ? 200 : 404, state || { error: 'Repository not found.' });
    return;
  }

  const filesMatch = /^\/repos\/([a-f0-9]{24})\/files$/.exec(route);
  if (request.method === 'GET' && filesMatch) {
    const files = await registry.files(filesMatch[1], url.searchParams.get('includeExcluded') === 'true');
    sendJson(response, files ? 200 : 404, files ? { files } : { error: 'Repository not found.' });
    return;
  }

  const changesMatch = /^\/repos\/([a-f0-9]{24})\/changes$/.exec(route);
  if (request.method === 'GET' && changesMatch) {
    try {
      const result = await registry.changes(changesMatch[1], url.searchParams.get('from'), url.searchParams.get('to'));
      sendJson(response, result ? 200 : 404, result || { error: 'Repository not found.' });
    } catch (error) {
      sendJson(response, 400, { error: error instanceof Error ? error.message : 'Invalid revision.' });
    }
    return;
  }

  const historyMatch = /^\/repos\/([a-f0-9]{24})\/history$/.exec(route);
  if (request.method === 'GET' && historyMatch) {
    try {
      const result = await registry.history(historyMatch[1], url.searchParams.get('path'));
      sendJson(response, result ? 200 : 404, result || { error: 'Repository not found.' });
    } catch (error) {
      sendJson(response, 400, { error: error instanceof Error ? error.message : 'Invalid path.' });
    }
    return;
  }

  const indexMatch = /^\/repos\/([a-f0-9]{24})\/(index|refresh)$/.exec(route);
  if (request.method === 'POST' && indexMatch) {
    const repoId = indexMatch[1];
    let jobId;
    try { jobId = await registry.createJob(repoId, indexMatch[2]); }
    catch (error) { sendJson(response, 409, { error: error.message }); return; }
    if (!jobId) {
      sendJson(response, 404, { error: 'Repository not found.' });
      return;
    }
    if (!scheduledJobs.has(jobId)) {
      scheduledJobs.add(jobId);
      setImmediate(async () => {
        try {
          await registry.updateJob(jobId, 'running');
          const inventory = await registry.scan(repoId);
          inventory.graph = await prepareGraph(registry, repoId, inventory);
          inventory.semantic = await prepareSemantic(registry, repoId, inventory, embeddingProvider);
          await registry.updateJob(jobId, 'completed', inventory);
        } catch (error) {
          await registry.updateJob(jobId, 'failed', undefined, error instanceof Error ? error.message : 'Scan failed.');
        } finally { scheduledJobs.delete(jobId); }
      });
    }
    sendJson(response, 202, { jobId, state: 'queued' });
    return;
  }

  const repositoryMatch = /^\/repos\/([a-f0-9]{24})$/.exec(route);
  if (request.method === 'DELETE' && repositoryMatch) {
    const removed = await registry.remove(repositoryMatch[1]);
    sendJson(response, removed ? 200 : 404, removed ? { removed: true } : { error: 'Repository not found.' });
    return;
  }

  const jobMatch = /^\/jobs\/([a-f0-9]{32})$/.exec(route);
  if (request.method === 'GET' && jobMatch) {
    const job = await registry.job(jobMatch[1]);
    sendJson(response, job ? 200 : 404, job || { error: 'Job not found.' });
    return;
  }

  const graphSymbolMatch = /^\/graph\/symbol\/([a-f0-9]{40})$/.exec(route);
  if (request.method === 'GET' && graphSymbolMatch) {
    const result = await registry.graphSymbol(graphSymbolMatch[1]);
    sendJson(response, result ? 200 : 404, result || { error: 'Graph symbol not found.' });
    return;
  }

  const graphImpactMatch = /^\/graph\/impact\/([a-f0-9]{40})$/.exec(route);
  if (request.method === 'GET' && graphImpactMatch) {
    const depth = Number(url.searchParams.get('depth') || 5);
    if (!Number.isInteger(depth) || depth < 1 || depth > 12) {
      sendJson(response, 400, { error: 'depth must be between 1 and 12.' });
      return;
    }
    const result = await registry.graphImpact(graphImpactMatch[1], depth);
    sendJson(response, result ? 200 : 404, result || { error: 'Graph symbol not found.' });
    return;
  }

  if (request.method === 'POST' && route === '/graph/path') {
    try {
      const body = await readJson(request);
      const depth = body.maxDepth === undefined ? 10 : body.maxDepth;
      if (!/^[a-f0-9]{40}$/.test(body.fromId) || !/^[a-f0-9]{40}$/.test(body.toId) ||
          !Number.isInteger(depth) || depth < 1 || depth > 12) throw new Error('Invalid graph path request.');
      const result = await registry.graphPath(body.fromId, body.toId, depth);
      sendJson(response, result ? 200 : 404, result || { error: 'Graph node not found.' });
    } catch (error) {
      sendJson(response, 400, { error: error instanceof Error ? error.message : 'Invalid graph path request.' });
    }
    return;
  }

  if (request.method === 'GET' && route.startsWith('/graph/file/')) {
    try {
      const repoId = url.searchParams.get('repoId');
      const filePath = decodeURIComponent(route.slice('/graph/file/'.length));
      if (!/^[a-f0-9]{24}$/.test(repoId) || !filePath || filePath.startsWith('/') ||
          filePath.split('/').includes('..') || filePath.includes('\0')) throw new Error('Invalid graph file request.');
      const result = await registry.graphFile(repoId, filePath);
      sendJson(response, result ? 200 : 404, result || { error: 'Graph file not found.' });
    } catch (error) {
      sendJson(response, 400, { error: error instanceof Error ? error.message : 'Invalid graph file request.' });
    }
    return;
  }

  if (request.method === 'POST' && route === '/hello') {
    try {
      const body = await readJson(request);
      if (typeof body.message !== 'string' || !body.message.trim()) {
        sendJson(response, 400, { error: 'A nonempty message is required.' });
        return;
      }
      const message = body.message.trim().slice(0, 2000);
      let replyText = `I am a local AI assistant. I received your message: "${message}", but I am still waiting for the Python AI backend to be fully connected before I can analyze the repository.`;
      if (message.toLowerCase() === 'hello' || message.toLowerCase() === 'hi') {
          replyText = 'Hello! I am ready to help you analyze this codebase. What would you like to know?';
      }
      sendJson(response, 200, {
        reply: replyText,
      });
    } catch (error) {
      const reason = error instanceof Error ? error.message : 'Invalid request.';
      sendJson(response, reason === 'Message is too large.' ? 413 : 400, { error: reason });
    }
    return;
  }

  sendJson(response, 404, { error: 'Not found' });
}

let server;
Registry.open().then(registry => {
  server = http.createServer((request, response) => {
    handleRequest(registry, request, response).catch(error => {
      process.stderr.write(`Request failed: ${error instanceof Error ? error.message : error}\n`);
      if (!response.headersSent) sendJson(response, 500, { error: 'Internal server error.' });
    });
  });
  server.listen(0, '127.0.0.1', () => {
    const address = server.address();
    process.stdout.write(JSON.stringify({ type: 'ready', port: address.port }) + '\n');
  });
}).catch(error => {
  process.stderr.write(`Could not initialize metadata: ${error instanceof Error ? error.message : error}\n`);
  process.exitCode = 1;
});

function shutdown() {
  if (server) server.close(() => process.exit(0));
}

process.on('SIGTERM', shutdown);
process.on('SIGINT', shutdown);
