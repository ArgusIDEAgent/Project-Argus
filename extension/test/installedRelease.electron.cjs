'use strict';
const assert=require('node:assert/strict');
const path=require('node:path');
const vscode=require('vscode');
exports.run=async()=>{
  const extension=vscode.extensions.getExtension('codemind-local.codemind-extension');
  assert.ok(extension,'Packaged extension must be installed');
  assert.equal(extension.packageJSON.version,'0.4.1');
  assert.ok(path.relative(process.env.CODEMIND_RELEASE_INSTALLED_ROOT,extension.extensionPath).split(path.sep)[0]!=='..');
  // The fixture tests real registration, Analyze Change polling, plan rendering,
  // Terminal dispatch and cleanup through the INSTALLED extension's commands.
  await require('./changeAnalysis.electron.cjs').run();
  await vscode.commands.executeCommand('codemind.openChat',true);
  for(let i=0;i<50&&!vscode.window.tabGroups.all.some(g=>g.tabs.some(t=>t.label==='CodeMind Chat'));i++)await new Promise(resolve=>setTimeout(resolve,100));
  assert.ok(vscode.window.tabGroups.all.some(g=>g.tabs.some(t=>t.label==='CodeMind Chat')));
  await vscode.commands.executeCommand('workbench.action.closeAllEditors');
};
