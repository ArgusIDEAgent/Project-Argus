'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const Module = require('node:module');
const { renderChange, escapeChange } = require('../out/changeAnalysisPresentation');
const { renderTestPlan } = require('../out/testPlanPresentation');
let ChangeAnalysisController, shellQuote;
const commands = new Map(), handlers = [], providers = new Map(), calls = [];
let panel;
const disposable = { dispose() {} };
const vscode = {
  commands: { registerCommand(name, run) { commands.set(name, run); return disposable; } },
  workspace: { isTrusted: true, workspaceFolders: [{ uri: { fsPath: '/tmp/phase3-fixture' } }],
    registerTextDocumentContentProvider(scheme, provider) { providers.set(scheme, provider); return disposable; },
    onDidSaveTextDocument(fn) { handlers.push(fn); return disposable; },
    createFileSystemWatcher() { return { ...disposable, onDidChange() {}, onDidCreate() {}, onDidDelete() {} }; },
    async openTextDocument(uri) { calls.push(['open', uri]); return { uri }; } },
  window: { showWarningMessage: async () => {}, showTextDocument: async () => {},
    createTerminal() { return { show() {}, sendText(command) { calls.push(['terminal', command]); } }; },
    createWebviewPanel() { panel = { webview: { html: '', onDidReceiveMessage(fn) { panel.message = fn; } },
      reveal() {}, onDidDispose() {}, dispose() {} }; return panel; } },
  RelativePattern: class {}, ViewColumn: { Beside: 2 }, Range: class {},
  Uri: { from(value) { return { ...value, toString() { return JSON.stringify(value); } }; } },
};
const native = Module._load;
try {
  Module._load = function(name, ...args) { return name === 'vscode' ? vscode : native.call(this, name, ...args); };
  ({ ChangeAnalysisController, shellQuote } = require('../out/changeAnalysisController'));
} finally { Module._load = native; }
const presentation = { capture_id: 'capture', mode: 'simple', freshness: 'historical_capture', stale_for_workspace: false,
  model_status: 'disabled_deterministic', sections: [{ title: '<script>bad</script>', text: 'safe',
    items: [{ text: '<img src=x onerror=bad()>', reference: 'owned' }, { text: 'no citation', reference: 'unknown' }] }],
  sources: { owned: { version: 'before', path: 'deleted.ts', start_line: 1, end_line: 1 } }, limitations: ['<unsafe>'] };

test('renderer escapes source strings and emits only owned action references', () => {
  const actions = new Map();
  const html = renderChange(presentation, actions);
  assert.doesNotMatch(html, /<script>|<img/);
  assert.match(html, /&lt;script&gt;/);
  assert.deepEqual([...actions.values()], ['owned']);
  assert.equal(escapeChange(`"'&<>`), '&quot;&#39;&amp;&lt;&gt;');
});

test('terminal shell quoting keeps command substitution literal', () => {
  assert.equal(shellQuote("a'b$(x)`z`"), "'a'\\''b$(x)`z`'");
});

test('Analyze Change acknowledges before a slow backend registration', async()=>{
  let release;
  const manager={registerRepository:()=>new Promise(resolve=>{release=resolve;}),changeAction:async()=>{}};
  const controller=new ChangeAnalysisController(manager,()=>({id:'repo',rootPath:'/tmp/release-fixture'}));
  const began=performance.now();const pending=controller.start();
  try{
    assert.match(panel.webview.html,/Starting change analysis/);
    assert.ok(performance.now()-began<300,'Command-side acknowledgement target');
    // Closing/switching epoch prevents registration from starting any capture.
    controller.stop();release({id:'repo',rootPath:'/tmp/release-fixture'});await pending;
  }finally{controller.dispose();handlers.pop();}
});

test('execution requires trust and an accepted native dialog; changed selection cannot submit', async () => {
  const controller = new ChangeAnalysisController({}, () => undefined);
  controller.job = {id:'job',state:'completed'}; controller.repo = {id:'repo',rootPath:'/tmp'};
  controller.plan = {plan_id:'plan',capture_id:'capture',selected_check_ids:['check'],stale_for_workspace:false};
  const review = {capture_id:'capture',plan_id:'plan',notice:'all hooks run',commands:[],limits:{},identity:{}};
  let reviews = 0, submissions = 0; const reviewedKinds=[];
  controller.server = { executionReview: async (_job,_repo,kind) => { reviews++; reviewedKinds.push(kind); return review; },
    submitRun: async () => { submissions++; return {id:'run',state:'completed'}; },
    capturedRun: async () => ({id:'run',state:'completed'}),
    changeAction: async () => {} };
  try {
    vscode.workspace.isTrusted = false;
    await controller.message({type:'execute-run'});
    assert.equal(reviews,0);
    vscode.workspace.isTrusted = true;
    await controller.message({type:'execute-run'});
    assert.equal(submissions,0); // dialog dismissed
    const original = vscode.window.showWarningMessage;
    try {
      vscode.window.showWarningMessage = async () => { controller.changed = true; return 'Run these captured test files'; };
      await controller.message({type:'execute-run'});
      assert.equal(submissions,0);
      controller.changed = false;
      vscode.window.showWarningMessage = async (_text, options, action) => {
        assert.equal(options.modal,true); assert.match(options.detail,/Authorization expires/); return action;
      };
      await controller.message({type:'execute-run'});
      assert.equal(submissions,1);
      await controller.message({type:'compare-run'});
      assert.equal(reviewedKinds.at(-1),'compare');
      assert.equal(submissions,2);
      const count=reviews; controller.changed=true;
      await controller.message({type:'compare-run'});
      assert.equal(reviews,count);
    } finally { vscode.window.showWarningMessage = original; }
  } finally { vscode.workspace.isTrusted = true; controller.dispose(); handlers.pop(); }
});

test('summary export uses the native destination and rejects a changed capture', async()=>{
  const writes=[],requests=[];
  const previousDialog=vscode.window.showSaveDialog,previousFs=vscode.workspace.fs;
  const controller=new ChangeAnalysisController({},()=>undefined);
  controller.job={id:'owned-job'};controller.repo={id:'owned-repo',rootPath:'/tmp/export-fixture'};controller.run={id:'owned-run'};
  controller.server={changeAction:async()=>{},exportRun:async(...args)=>{requests.push(args);return {schema_version:'test-run-export-v1'};}};
  vscode.workspace.fs={writeFile:async(uri,bytes)=>writes.push([uri,bytes.toString()])};
  const destination={scheme:'file',path:'/native/chosen.json'};
  try{
    vscode.window.showSaveDialog=async()=>undefined;
    await controller.message({type:'export-run',path:'/untrusted/webview/path'});
    assert.equal(writes.length,0);
    vscode.window.showSaveDialog=async()=>destination;
    await controller.message({type:'export-run',path:'/untrusted/webview/path'});
    assert.deepEqual(requests.at(-1),['owned-job','owned-repo','owned-run']);
    assert.equal(writes[0][0],destination);
    assert.equal(JSON.parse(writes[0][1]).schema_version,'test-run-export-v1');
    vscode.window.showSaveDialog=async()=>{controller.epoch++;return destination;};
    await controller.message({type:'export-run'});
    assert.equal(writes.length,1);
  }finally{
    controller.run=undefined;controller.dispose();handlers.pop();
    vscode.window.showSaveDialog=previousDialog;vscode.workspace.fs=previousFs;
  }
});

test('controller completes, changes mode without another capture and navigates captured source after save', async () => {
  calls.length = 0;
  const job = { id: 'job', repo_id: 'repo', state: 'preparing', stale_for_workspace: false, terminal: { executable: '/python', args: ['-m', 'worker', "space ' quote"], cwd: '/tmp' } };
  let captures = 0, modes = [];
  const plan = { plan_id: 'plan', capture_id: 'capture', execution_available: false, stale_for_workspace: false,
    profiles: [], tests: [{test_id:'test',name:'existing',source:{path:'deleted.ts'}}], scenarios: [],
    proposed_checks: [{check_id:'owned-check',test_id:'test',profile_id:'profile',argv:['node','--test','--','a.test.js'],eligibility:'reviewable_existing_test',reason:'Review only',execution:'not_run'}],
    selected_check_ids: [], search_scope:['deleted.ts'],no_matching_test:[],omitted_tests:0,omitted_scenarios:0,limitations:[],sources:{} };
  let planRequests = 0;
  const manager = {
    async registerRepository(rootPath) { return { id: 'repo', rootPath }; },
    async startChange() { captures++; return job; },
    async changeStatus() { return { ...job, state: 'completed' }; },
    async changeExplanation(id, repo, mode) { modes.push(mode); return { ...presentation, mode }; },
    async changeAction(id, repo, action) { calls.push(['action', action]); return job; },
    async changeTestPlan(id, repo, selected) { planRequests++; if(selected) plan.selected_check_ids = selected; return structuredClone(plan); },
    async changeSource() { return { content: 'original baseline\n', capture_id: 'capture', version: 'before', path: 'deleted.ts', start_line: 1, end_line: 1 }; },
  };
  const controller = new ChangeAnalysisController(manager, () => undefined);
  try {
    await controller.start();
    assert.match(calls.find(c => c[0] === 'terminal')[1], /'space '\\'' quote'/);
    await new Promise(resolve => setTimeout(resolve, 1150));
    assert.match(panel.webview.html, /Captured historical evidence/);
    assert.match(panel.webview.html, /default-src 'none'/);
    await panel.message({ type: 'mode', value: 'flow' });
    await new Promise(resolve => setTimeout(resolve, 10));
    assert.ok(modes.includes('flow'));
    assert.equal(captures, 1);
    await panel.message({type:'plan'});
    await new Promise(resolve => setTimeout(resolve, 10));
    assert.match(panel.webview.html, /No checks have run/);
    await panel.message({type:'check',value:'check-0'});
    await new Promise(resolve => setTimeout(resolve, 10));
    assert.deepEqual(plan.selected_check_ids, ['owned-check']);
    const requests = planRequests;
    await panel.message({type:'check',value:'forged'});
    await new Promise(resolve => setTimeout(resolve, 10));
    assert.equal(planRequests, requests);
    assert.equal(captures, 1);
    await handlers[0]({ uri: { scheme: 'file', fsPath: '/tmp/phase3-fixture/changed.ts' } });
    await new Promise(resolve => setTimeout(resolve, 10));
    assert.match(panel.webview.html, /stale for the current workspace/);
    await panel.message({ type: 'source', value: 'source-0' });
    await new Promise(resolve => setTimeout(resolve, 10));
    const uri = calls.find(c => c[0] === 'open')[1];
    assert.equal(uri.scheme, 'codemind-change');
    assert.equal(providers.get('codemind-change').provideTextDocumentContent(uri), 'original baseline\n');
    const before = calls.length;
    await panel.message({ type: 'source', value: '../../unowned' });
    await new Promise(resolve => setTimeout(resolve, 10));
    assert.equal(calls.length, before);
    const opens = calls.filter(item => item[0] === 'open').length;
    let release;
    manager.changeSource = () => new Promise(resolve => { release = resolve; });
    panel.message({ type: 'source', value: 'source-0' });
    await new Promise(resolve => setTimeout(resolve, 10));
    const restarting = controller.start();
    release({content:'late response',capture_id:'old',version:'before',path:'deleted.ts',start_line:1,end_line:1});
    await restarting;
    await new Promise(resolve => setTimeout(resolve, 10));
    assert.equal(calls.filter(item => item[0] === 'open').length, opens);
  } finally { controller.dispose(); }
});


test('test plan renderer escapes assertions and cannot enable unsupported checks', () => {
  const plan = { plan_id:'id',capture_id:'capture',stale_for_workspace:false,execution_available:false,
    profiles:[],tests:[],scenarios:[{scenario_id:'x',title:'<script>bad</script>',category:'input',evidence_basis:'source_observation',eligibility:'needs_expectation',evidence:[],expected_outcome:'<img onerror=bad>',preconditions:[],unknowns:['unknown']}],
    proposed_checks:[{check_id:'check',test_id:'test',profile_id:'profile',argv:null,eligibility:'unsupported',reason:'<unsafe>',execution:'not_run'}],
    selected_check_ids:[],search_scope:[],no_matching_test:[],omitted_tests:0,omitted_scenarios:0,limitations:[],sources:{} };
  const html = renderTestPlan(plan,new Map(),new Map());
  assert.doesNotMatch(html, /<script>|<img/);
  assert.match(html, /disabled/);
  assert.match(html, /No checks have run/);
});
