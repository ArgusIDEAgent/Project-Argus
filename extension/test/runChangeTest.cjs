const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { runTests } = require('@vscode/test-electron');
(async () => {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'cm-p3-host-'));
  try {
    const executable = process.env.CODEMIND_TEST_VSCODE || '/Applications/Visual Studio Code.app/Contents/MacOS/Code';
    if (!fs.existsSync(executable)) throw new Error('Local VS Code binary unavailable');
    const workspace = path.join(temporary, 'fixture'); fs.mkdirSync(workspace);
    await runTests({ vscodeExecutablePath: executable, extensionDevelopmentPath: path.resolve(__dirname, '..'),
      extensionTestsPath: path.join(__dirname, 'changeAnalysis.electron.cjs'),
      launchArgs: [workspace, '--user-data-dir', path.join(temporary, 'profile'), '--disable-extensions',
        '--disable-workspace-trust', '--skip-welcome', '--skip-release-notes'] });
  } finally { fs.rmSync(temporary, { recursive: true, force: true }); }
})().catch(error => { console.error(error); process.exitCode = 1; });
