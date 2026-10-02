'use strict';

const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { randomBytes } = require('node:crypto');
const path = require('node:path');
const { after, before, test } = require('node:test');

const token = randomBytes(32).toString('hex');
let child;
let port;

before(async () => {
  child = spawn(process.execPath, [path.join(__dirname, '..', 'src', 'server.js')], {
    env: { ...process.env, CODEMIND_SESSION_TOKEN: token },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  port = await new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error('Server did not start.')), 5000);
    let output = '';
    child.once('error', reject);
    child.once('exit', (code) => reject(new Error(`Server exited with ${code}.`)));
    child.stdout.on('data', (chunk) => {
      output += chunk.toString();
      const newline = output.indexOf('\n');
      if (newline < 0) return;
      clearTimeout(timeout);
      resolve(JSON.parse(output.slice(0, newline)).port);
    });
  });
});

after(() => child?.kill());

test('requires a session token', async () => {
  const response = await fetch(`http://127.0.0.1:${port}/health`);
  assert.equal(response.status, 401);
});

test('reports health with the session token', async () => {
  const response = await fetch(`http://127.0.0.1:${port}/health`, {
    headers: { 'x-codemind-session': token },
  });
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { status: 'ok', service: 'codemind', version: 1 });
});

test('replies to a hello message', async () => {
  const response = await fetch(`http://127.0.0.1:${port}/hello`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', 'x-codemind-session': token },
    body: JSON.stringify({ message: 'Hello' }),
  });
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), {
    reply: 'Hello from the CodeMind server! You said: Hello',
  });
});

test('rejects an empty message', async () => {
  const response = await fetch(`http://127.0.0.1:${port}/hello`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', 'x-codemind-session': token },
    body: JSON.stringify({ message: '  ' }),
  });
  assert.equal(response.status, 400);
});
