'use strict';

const fs = require('node:fs');
const path = require('node:path');

const extensionRoot = path.join(__dirname, '..');
const source = path.join(extensionRoot, '..', 'service', 'src');
const destination = path.join(extensionRoot, 'server');

fs.mkdirSync(destination, { recursive: true });
for (const file of fs.readdirSync(source)) {
  if (file.endsWith('.js')) fs.copyFileSync(path.join(source, file), path.join(destination, file));
}
process.stdout.write('Copied the local server into the extension.\n');
