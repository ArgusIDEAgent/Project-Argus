"""Fixed adversarial programs. None read developer source or host credentials."""
COMMON = "const test=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');const cp=require('node:child_process');\n"


def fixtures(host_path, host_port):
    import json
    return {
        'controls': COMMON + """
test('kernel boundary and resource policy',()=>{
 assert.equal(process.getuid(),65532);
 const status=fs.readFileSync('/proc/self/status','utf8');
 assert.match(status,/NoNewPrivs:\\s+1/);assert.match(status,/Seccomp:\\s+2/);assert.match(status,/CapEff:\\s+0000000000000000/);
 assert.equal(fs.readFileSync('/sys/fs/cgroup/memory.max','utf8').trim(),'268435456');
 assert.equal(fs.readFileSync('/sys/fs/cgroup/memory.swap.max','utf8').trim(),'0');
 assert.equal(fs.readFileSync('/sys/fs/cgroup/pids.max','utf8').trim(),'48');
 assert.equal(fs.readFileSync('/sys/fs/cgroup/cpu.max','utf8').trim(),'50000 100000');
 assert.throws(()=>fs.writeFileSync('/etc/codemind-denied','x'));
 assert.throws(()=>cp.execFileSync('/usr/bin/unshare',['--mount','true'],{stdio:'ignore'}));
 const keys=Object.keys(process.env).sort();assert.deepEqual(keys,['CODEMIND_TEST','HOME','LANG','NODE_TEST_CONTEXT','NODE_TEST_WORKER_ID','PATH','TZ']);
 for(const target of ['/var/run/docker.sock','/run/containerd/containerd.sock','/root/.ssh','/Users']) assert.equal(fs.existsSync(target),false);
});
""",
        'host_and_symlink': COMMON + """
test('host reads/writes and symlink escape denied',()=>{
 const target=HOST_PATH;
 assert.throws(()=>fs.readFileSync(target));assert.throws(()=>fs.writeFileSync(target,'corrupt'));
 fs.symlinkSync(target,'/work/escape');assert.throws(()=>fs.readFileSync('/work/escape'));
 assert.throws(()=>fs.writeFileSync('/work/escape','corrupt'));
 fs.symlinkSync('/etc','/work/rootescape');assert.throws(()=>fs.writeFileSync('/work/rootescape/escape','x'));
 const original=fs.readFileSync('/work/case.cjs','utf8');
 fs.writeFileSync('/work/case.cjs',original+'\\n// changed inside disposable tmpfs');
});
""".replace('HOST_PATH', json.dumps(host_path)),
        'network': COMMON + """
const net=require('node:net');
async function denied(host,port){await new Promise((resolve,reject)=>{const s=net.connect({host,port});s.setTimeout(500);s.on('connect',()=>{s.destroy();reject(Error('unexpected host/network connection'))});s.on('error',()=>resolve());s.on('timeout',()=>{s.destroy();resolve()});});}
test('no external/host routes; same-sandbox loopback works',async()=>{
 assert.deepEqual(Object.keys(require('node:os').networkInterfaces()),['lo']);
 assert.equal(fs.readFileSync('/proc/net/route','utf8').trim().split('\\n').length,1);
 await denied('1.1.1.1',443);await denied('2606:4700:4700::1111',443);
 await denied('192.168.5.2',HOST_PORT);await denied('127.0.0.1',HOST_PORT);
 const server=net.createServer(s=>s.end('ok'));await new Promise(r=>server.listen(0,'127.0.0.1',r));
 await new Promise((resolve,reject)=>{const client=net.connect(server.address().port,'127.0.0.1');client.on('data',data=>assert.equal(data.toString(),'ok'));client.on('end',resolve);client.on('error',reject);});
 await new Promise(r=>server.close(r));
});
""".replace('HOST_PORT', str(host_port)),
        'disk': COMMON + """
test('bounded writable workspace output tmpfs',()=>{
 const block=Buffer.alloc(1024*1024,1);
 for(const [dir,max] of [['/work',64],['/tmp',16],['/out',8]]){
   let denied=false;for(let i=0;i<max+3;i++){try{fs.writeFileSync(dir+'/fill-'+i,block)}catch(e){assert.equal(e.code,'ENOSPC');denied=true;break}}
   assert.ok(denied,'tmpfs limit not enforced for '+dir);
   for(const name of fs.readdirSync(dir)){if(name.startsWith('fill-'))fs.unlinkSync(dir+'/'+name)}
 }
});
""",
        'processes': COMMON + """
test('PID limit enforced',async()=>{
 const children=[];let denied=false;
 try{for(let i=0;i<70;i++){
   const child=cp.spawn('/bin/sleep',['30'],{stdio:'ignore'});
   const outcome=await new Promise(resolve=>{child.once('spawn',()=>resolve('ok'));child.once('error',e=>resolve(e.code))});
   if(outcome!=='ok'){assert.equal(outcome,'EAGAIN');denied=true;break}children.push(child);
 }assert.ok(denied);assert.ok(Number(fs.readFileSync('/sys/fs/cgroup/pids.current','utf8'))<=48);
 }finally{for(const child of children)child.kill('SIGKILL')}
});
""",
        'memory': COMMON + """
test('cgroup memory exhaustion contained',()=>{
 const child=cp.spawnSync('/usr/local/bin/node',['-e','const a=[];for(;;)a.push(Buffer.alloc(16*1024*1024,1));'],{stdio:'ignore',timeout:5000});
 assert.equal(child.signal,'SIGKILL');
 assert.match(fs.readFileSync('/sys/fs/cgroup/memory.events','utf8'),/oom_kill [1-9]/);
});
""",
        'cpu': COMMON + """
test('CPU quota throttles',async()=>{
 const get=()=>Object.fromEntries(fs.readFileSync('/sys/fs/cgroup/cpu.stat','utf8').trim().split('\\n').map(x=>x.split(' ')));
 const before=get();const end=Date.now()+1300;while(Date.now()<end){}
 const after=get();assert.ok(Number(after.nr_throttled)>Number(before.nr_throttled));
});
""",
        'timeout': COMMON + """
test('persistent descendants',()=>{const child=cp.spawn('/bin/sh',['-c','trap "" TERM; sleep 30 & wait'],{detached:true,stdio:'ignore'});child.unref();setInterval(()=>{},1000);});
""",
        'output': COMMON + """
test('output flood bounded',()=>{for(;;)process.stdout.write('x'.repeat(8192));});
""",
        'malicious_output': COMMON + """
test('malicious output stays text',()=>{process.stdout.write('\\x1b[2J<script>alert(1)</script>\\x00\\npassword=pretend-secret\\n');});
""",
    }
