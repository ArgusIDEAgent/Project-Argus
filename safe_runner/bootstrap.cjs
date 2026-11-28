'use strict';
// Trusted broker-owned materializer. Repository code runs only after all inputs
// have been validated and copied into a fresh capped tmpfs, as a non-root user.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {spawn} = require('node:child_process');
let chunks = [], bytes = 0;
process.stdin.on('data', chunk => {
  bytes += chunk.length;
  if (bytes > 6000000) process.exit(120);
  chunks.push(chunk);
});
process.stdin.on('end', () => {
  try {
    const input = JSON.parse(Buffer.concat(chunks).toString('utf8'));
    if (!Array.isArray(input.files) || input.files.length > 128) throw Error('input count');
    const seen = new Set(); let size = 0;
    for (const file of input.files) {
      if (typeof file.path !== 'string' || !file.path || file.path.startsWith('/') || file.path.includes('\\') || file.path.includes('\0')
          || file.path.split('/').some(p => !p || ['.', '..', '.git', 'node_modules'].includes(p)) || seen.has(file.path)) throw Error('path');
      seen.add(file.path);
      const data = Buffer.from(file.content, 'base64'); size += data.length;
      if (size > 4194304 || crypto.createHash('sha256').update(data).digest('hex') !== file.digest) throw Error('bytes');
      const target = path.join('/work', file.path);
      fs.mkdirSync(path.dirname(target), {recursive:true, mode:0o700});
      fs.writeFileSync(target, data, {flag:'wx', mode:0o600});
    }
    installDependencies(input.packages || []);
    if (!seen.has(input.check) || !/\.(js|mjs|cjs)$/.test(input.check)) throw Error('check');
    if (input.framework === 'vitest') {
      if(!/^[a-zA-Z0-9_./-]+$/.test(input.check))throw Error('Vitest path must not contain glob/control characters');
      fs.mkdirSync('/work/node_modules',{recursive:true,mode:0o700});
      for(const name of ['vitest','vite'])fs.symlinkSync('/opt/vitest/node_modules/'+name,'/work/node_modules/'+name);
      const config={configFile:false,envDir:false,plugins:[],cacheDir:'/tmp/vitest-cache',test:{root:'/work',include:[input.check],environment:'node',pool:'forks',maxWorkers:1,fileParallelism:false,isolate:true,watch:false,globals:false,reporters:[['json',{outputFile:'/out/vitest-result.json'}]],testTimeout:2500,hookTimeout:2500,teardownTimeout:1000,passWithNoTests:false,coverage:{enabled:false},typecheck:{enabled:false}}};
      fs.writeFileSync('/out/codemind-vitest.config.mjs','export default '+JSON.stringify(config),{flag:'wx',mode:0o400});
      const child=spawn('/usr/local/bin/node',['/opt/vitest/node_modules/vitest/vitest.mjs','run','--config','/out/codemind-vitest.config.mjs','--configLoader','native'],{
        cwd:'/work',stdio:['ignore','inherit','inherit'],env:{PATH:'/usr/local/bin:/usr/bin:/bin',HOME:'/work/home',LANG:'C.UTF-8',TZ:'UTC',CODEMIND_TEST:'1'}});
      child.on('error',()=>process.exit(121));
      child.on('exit',(code)=>{
        let fd;
        try{
          fd=fs.openSync('/out/vitest-result.json',fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW);
          const st=fs.fstatSync(fd);if(!st.isFile()||st.size>32768||st.nlink!==1)throw Error('report bounds');
          const bytes=Buffer.alloc(32769);const count=fs.readSync(fd,bytes,0,bytes.length,0);if(count>32768)throw Error('report grew');
          const raw=bytes.subarray(0,count);JSON.parse(raw.toString('utf8'));
          process.stdout.write('\nCM_VITEST_REPORT:'+raw.toString('base64')+'\n');
        }catch{process.stdout.write('\nCM_VITEST_REPORT_UNAVAILABLE\n');}
        finally{if(fd!==undefined)fs.closeSync(fd);}
        process.exitCode=code===null?122:code;
      });
      return;
    }
    let reporter = 'tap';
    if (input.structured === true) {
      reporter = '/out/codemind-reporter.cjs';
      fs.writeFileSync(reporter, `module.exports = async function* (events) {
        for await (const event of events) {
          const d = event.data;
          if (event.type === 'test:summary')
            yield JSON.stringify({type:d.file ? 'file_summary' : 'summary', counts:d.counts, success:d.success}) + '\\n';
          else if (event.type === 'test:fail')
            yield JSON.stringify({type:'failure', name:String(d.name).slice(0,500), message:String(d.details?.error?.cause?.message || d.details?.error?.message || '').slice(0,1000), failure_digest:require('node:crypto').createHash('sha256').update(JSON.stringify([d.name,d.details?.error?.cause?.message || d.details?.error?.message,d.details?.error?.cause?.code || d.details?.error?.code])).digest('hex'), error_name:d.details?.error?.cause?.name || d.details?.error?.name || '', code:d.details?.error?.cause?.code || d.details?.error?.code || '', failureType:d.details?.error?.failureType || ''}) + '\\n';
          else if (event.type === 'test:stdout' || event.type === 'test:stderr')
            yield JSON.stringify({type:'console', text:String(d.message)}) + '\\n';
        }
      };`, {flag:'wx',mode:0o400});
    }
    const child = spawn('/usr/local/bin/node', ['--test', '--test-reporter=' + reporter, '--', '/work/' + input.check], {
      cwd:'/work', stdio:['ignore','inherit','inherit'],
      env:{PATH:'/usr/local/bin:/usr/bin:/bin',HOME:'/work/home',LANG:'C.UTF-8',TZ:'UTC',CODEMIND_TEST:'1'},
    });
    child.on('error', () => process.exit(121));
    child.on('exit', (code, signal) => process.exit(code === null ? 122 : code));
  } catch (error) { process.stderr.write('Sandbox input rejected: ' + String(error.message).slice(0,160) + '\n'); process.exit(120); }
});
