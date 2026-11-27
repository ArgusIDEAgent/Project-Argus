'use strict';
// Trusted broker-owned materializer. Repository code runs only after all inputs
// have been validated and copied into a fresh capped tmpfs, as a non-root user.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {spawn} = require('node:child_process');
let chunks = [], bytes = 0;
process.stdin.on('data', chunk => {
  bytes += chunk.length;
  if (bytes > 6000000) process.exit(120);
  chunks.push(chunk);
});
process.stdin.on('end', () => {
  try {
    const input = JSON.parse(Buffer.concat(chunks).toString('utf8'));
    if (!Array.isArray(input.files) || input.files.length > 128) throw Error('input count');
    const seen = new Set(); let size = 0;
    for (const file of input.files) {
      if (typeof file.path !== 'string' || !file.path || file.path.startsWith('/') || file.path.includes('\\') || file.path.includes('\0')
          || file.path.split('/').some(p => !p || ['.', '..', '.git', 'node_modules'].includes(p)) || seen.has(file.path)) throw Error('path');
      seen.add(file.path);
      const data = Buffer.from(file.content, 'base64'); size += data.length;
      if (size > 4194304 || crypto.createHash('sha256').update(data).digest('hex') !== file.digest) throw Error('bytes');
      const target = path.join('/work', file.path);
      fs.mkdirSync(path.dirname(target), {recursive:true, mode:0o700});
      fs.writeFileSync(target, data, {flag:'wx', mode:0o600});
    }
    if (!seen.has(input.check) || !/\.(js|mjs|cjs)$/.test(input.check)) throw Error('check');
    const child = spawn('/usr/local/bin/node', ['--test', '--test-reporter=tap', '--', '/work/' + input.check], {
      cwd:'/work', stdio:['ignore','inherit','inherit'],
      env:{PATH:'/usr/local/bin:/usr/bin:/bin',HOME:'/work/home',LANG:'C.UTF-8',TZ:'UTC',CODEMIND_TEST:'1'},
    });
    child.on('error', () => process.exit(121));
    child.on('exit', (code, signal) => process.exit(code === null ? 122 : code));
  } catch (error) { process.stderr.write('Sandbox input rejected\n'); process.exit(120); }
});
