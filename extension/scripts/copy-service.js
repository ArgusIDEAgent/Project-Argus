'use strict';

const fs = require('node:fs');
const path = require('node:path');

const extensionRoot = path.join(__dirname, '..');
const source = path.join(extensionRoot, '..', 'service', 'src', 'server.js');
const destination = path.join(extensionRoot, 'server', 'server.js');

fs.mkdirSync(path.dirname(destination), { recursive: true });
fs.copyFileSync(source, destination);
process.stdout.write('Copied the local server into the extension.\n');
