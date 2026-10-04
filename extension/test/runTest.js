'use strict';

const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');
const { runTests } = require('@vscode/test-electron');

async function main() {
  const extensionDevelopmentPath = path.resolve(__dirname, '..');
  const extensionTestsPath = path.resolve(__dirname, 'suite.js');
  const vscodeExecutablePath = process.env.CODEMIND_VSCODE_EXECUTABLE ||
    (process.platform === 'darwin' ? '/Applications/Visual Studio Code.app/Contents/MacOS/Code' : undefined);

  const testProfile = fs.mkdtempSync(path.join(process.platform === 'darwin' ? '/tmp' : os.tmpdir(), 'cm-vscode-'));
  try {
    await runTests({
      vscodeExecutablePath,
      extensionDevelopmentPath,
      extensionTestsPath,
      launchArgs: [
        '--disable-extensions',
        '--skip-welcome',
        `--user-data-dir=${testProfile}`,
      ],
    });
  } finally {
    fs.rmSync(testProfile, { recursive: true, force: true });
  }
}

main().catch((error) => {
  process.stderr.write(String(error) + '\n');
  process.exitCode = 1;
});
