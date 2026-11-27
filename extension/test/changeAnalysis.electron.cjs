const assert = require('node:assert/strict');
const http = require('node:http');
const vscode = require('vscode');
exports.run = async function () {
  let starts = 0, explanations = 0, statuses = 0, plans = 0;
  const root = vscode.workspace.workspaceFolders[0].uri.fsPath;
  const descriptor = { executable: '/usr/bin/true', cwd: root, args: [] };
  const job = { id: 'qualification-job', repo_id: 'qualification-repo', state: 'preparing', terminal: descriptor, error: null, stale_for_workspace: false };
  const listener = http.createServer((request, response) => {
    let value;
    if (request.headers['x-codemind-session'] !== 'p3-host-fixture') { response.writeHead(401); response.end(); return; }
    if (request.url === '/repos/register') value = { repository: { id: job.repo_id, rootPath: root, defaultBranch: 'main' } };
    else if (request.url === '/v1/change-analyses') { starts++; value = job; }
    else if (request.url.includes('/test-plans')) { plans++; value = {plan_id:'fixture-plan',capture_id:'fixture',execution_available:false,stale_for_workspace:false,profiles:[],tests:[],scenarios:[],proposed_checks:[],selected_check_ids:[],search_scope:[],no_matching_test:[],omitted_tests:0,omitted_scenarios:0,limitations:['No tests have run.'],sources:{}}; }
    else if (request.url.includes('/explanations')) { explanations++; value = { capture_id: 'fixture', mode: 'simple', stale_for_workspace: false, sections: [{ title: 'Saved changes', text: 'Fixture captured source.', items: [] }], sources: {}, limitations: [], model_status: 'disabled_deterministic', freshness: 'historical_capture' }; }
    else if (request.url.includes('?repo_id=')) { statuses++; value = { ...job, state: 'completed' }; }
    else value = { ...job, state: 'cancelled' };
    response.writeHead(200, { 'content-type': 'application/json' }); response.end(JSON.stringify(value));
  });
  await new Promise(resolve => listener.listen(0, '127.0.0.1', resolve));
  process.env.CODEMIND_PORT = String(listener.address().port);
  process.env.CODEMIND_SESSION_TOKEN = 'p3-host-fixture';
  try {
    const extension = vscode.extensions.getExtension('codemind-local.codemind-extension');
    assert.ok(extension);
    await extension.activate();
    assert.ok((await vscode.commands.getCommands(true)).includes('codemind.analyzeChange'));
    await vscode.commands.executeCommand('codemind.analyzeChange');
    for (let i = 0; i < 50 && explanations === 0; i++) await new Promise(resolve => setTimeout(resolve, 100));
    assert.equal(starts, 1);
    assert.ok(statuses >= 1 && explanations >= 1);
    await vscode.commands.executeCommand('codemind.reviewTestPlan');
    assert.equal(plans, 1);
    await vscode.commands.executeCommand('workbench.action.closeAllEditors');
    console.log('Phase 3/4 real VS Code host command/API/render smoke passed (fixture API; no snapshot privilege).');
  } finally {
    listener.closeAllConnections(); await new Promise(resolve => listener.close(resolve));
  }
};
