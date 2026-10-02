'use strict';

const http = require('node:http');

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

const server = http.createServer(async (request, response) => {
  if (request.headers['x-codemind-session'] !== token) {
    sendJson(response, 401, { error: 'Unauthorized' });
    return;
  }

  if (request.method === 'GET' && request.url === '/health') {
    sendJson(response, 200, { status: 'ok', service: 'codemind', version: 1 });
    return;
  }

  if (request.method === 'POST' && request.url === '/hello') {
    try {
      const body = await readJson(request);
      if (typeof body.message !== 'string' || !body.message.trim()) {
        sendJson(response, 400, { error: 'A nonempty message is required.' });
        return;
      }
      const message = body.message.trim().slice(0, 2000);
      sendJson(response, 200, {
        reply: `Hello from the CodeMind server! You said: ${message}`,
      });
    } catch (error) {
      const reason = error instanceof Error ? error.message : 'Invalid request.';
      sendJson(response, reason === 'Message is too large.' ? 413 : 400, { error: reason });
    }
    return;
  }

  sendJson(response, 404, { error: 'Not found' });
});

server.listen(0, '127.0.0.1', () => {
  const address = server.address();
  process.stdout.write(JSON.stringify({ type: 'ready', port: address.port }) + '\n');
});

function shutdown() {
  server.close(() => process.exit(0));
}

process.on('SIGTERM', shutdown);
process.on('SIGINT', shutdown);
