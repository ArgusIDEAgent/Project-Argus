'use strict';
const path = require('node:path');
const os = require('node:os');
const fs = require('node:fs');
const { runTests } = require('@vscode/test-electron');

(async () => {
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'cm-test-'));
  const installed = '/Applications/Visual Studio Code.app/Contents/MacOS/Code';
  try {
    await runTests({
      vscodeExecutablePath: process.env.CODEMIND_TEST_VSCODE || (fs.existsSync(installed) ? installed : undefined),
      extensionDevelopmentPath: path.resolve(__dirname, '..'),
      extensionTestsPath: path.resolve(__dirname, 'suite.js'),
      launchArgs: ['--user-data-dir', profile, '--disable-extensions', '--skip-welcome', '--skip-release-notes'],
    });
  } catch (error) {
    console.error(error);
    process.exitCode = 1;
  } finally {
    fs.rmSync(profile, { recursive: true, force: true });
  }
})();
