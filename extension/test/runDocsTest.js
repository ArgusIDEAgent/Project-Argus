'use strict';
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { runTests } = require('@vscode/test-electron');

async function fixture(project) {
  const child = spawn(path.join(project, '.venv/bin/python'), [path.join(project, 'ai-engine/tests/phase10_server_fixture.py')],
    { cwd: project, stdio: ['pipe', 'pipe', 'pipe'] });
  let diagnostics = '', output = '';
  child.stderr.on('data', data => { diagnostics += data.toString(); });
  const options = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => { child.kill(); reject(new Error('Documentation fixture timeout: ' + diagnostics)); }, 60000);
    child.stdout.on('data', data => {
      output += data.toString();
      if (output.includes('\n')) {
        clearTimeout(timer);
        try { resolve(JSON.parse(output.split('\n')[0])); } catch (error) { reject(error); }
      }
    });
    child.on('error', error => { clearTimeout(timer); reject(error); });
    child.on('exit', code => { if (!output) { clearTimeout(timer); reject(new Error('Documentation fixture failed: ' + code + ' ' + diagnostics)); } });
  });
  return { child, options };
}

(async () => {
  const project = path.resolve(__dirname, '../..');
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'cm-docs-test-'));
  const executable = process.env.CODEMIND_TEST_VSCODE || '/Applications/Visual Studio Code.app/Contents/MacOS/Code';
  let owner;
  try {
    if (!fs.existsSync(executable)) throw new Error('No local VS Code executable; set CODEMIND_TEST_VSCODE.');
    owner = await fixture(project);
    await runTests({
      vscodeExecutablePath: executable,
      extensionDevelopmentPath: path.resolve(__dirname, '..'),
      extensionTestsPath: path.resolve(__dirname, 'docsSuiteEntry.js'),
      launchArgs: [owner.options.rootPath, '--user-data-dir', profile, '--disable-extensions',
        '--disable-workspace-trust', '--skip-welcome', '--skip-release-notes'],
      extensionTestsEnv: { CODEMIND_PORT: String(owner.options.port), CODEMIND_SESSION_TOKEN: owner.options.token,
        CODEMIND_DOCS_TEST_REPO: owner.options.repo, CODEMIND_DOCS_TEST_ROOT: owner.options.rootPath },
    });
  } catch (error) {
    console.error(error);
    process.exitCode = 1;
  } finally {
    if (owner) await new Promise(resolve => { owner.child.once('exit', resolve); owner.child.stdin.end(); });
    fs.rmSync(profile, { recursive: true, force: true });
  }
})();
