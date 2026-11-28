'use strict';
// Fresh profile + actual packaged extension. No Marketplace or runtime download.
const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {spawnSync}=require('node:child_process');
const {runTests}=require('@vscode/test-electron');
(async()=>{
  const root=path.resolve(__dirname,'../..');
  const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'cm-installed-release-'));
  try{
    const executable='/Applications/Visual Studio Code.app/Contents/MacOS/Code';
    if(!fs.existsSync(executable))throw Error('Existing local VS Code runtime required; no download fallback');
    const cli='/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code';
    const profile=path.join(tmp,'profile'),extensions=path.join(tmp,'extensions'),workspace=path.join(tmp,'fixture'),driver=path.join(tmp,'driver');
    fs.mkdirSync(workspace);fs.mkdirSync(driver);
    fs.writeFileSync(path.join(driver,'package.json'),JSON.stringify({name:'codemind-release-driver',publisher:'codemind-fixture',version:'0.0.1',engines:{vscode:'^1.93.0'},main:'index.js'}));
    fs.writeFileSync(path.join(driver,'index.js'),'exports.activate=()=>{};');
    const installed=spawnSync(cli,['--user-data-dir',profile,'--extensions-dir',extensions,'--install-extension',path.join(root,'codemind-extension-0.4.1.vsix'),'--force'],{encoding:'utf8',timeout:30000});
    if(installed.status!==0)throw Error('Fresh VSIX installation failed: '+installed.stderr);
    await runTests({vscodeExecutablePath:executable,extensionDevelopmentPath:driver,
      extensionTestsPath:path.join(__dirname,'installedRelease.electron.cjs'),
      launchArgs:[workspace,'--user-data-dir',profile,'--extensions-dir',extensions,'--disable-workspace-trust','--skip-welcome','--skip-release-notes'],
      extensionTestsEnv:{CODEMIND_RELEASE_INSTALLED_ROOT:extensions,CODEMIND_LOCAL_MODEL:'',CODEMIND_CHANGE_EXECUTION:'0'}});
    console.log(JSON.stringify({gate:'fresh-installed-vsix',passed:true,scope:'Real installed extension commands/webviews against fixture API; native APFS/API qualified separately; no user tests or programs'}));
  }finally{fs.rmSync(tmp,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exitCode=1;});
