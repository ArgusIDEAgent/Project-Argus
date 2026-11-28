'use strict';
// Trusted, deliberately narrow USTAR installer. Runs inside the offline sandbox.
module.exports = function install(packages) {
  const fs = require('node:fs'), path = require('node:path'), crypto = require('node:crypto');
  if (!Array.isArray(packages) || packages.length > 8) throw Error('package count');
  let expanded = 0, files = 0, compressed = 0;
  const names = new Set();
  for (const pkg of packages) {
    if (!/^(?:@[a-z0-9_-]+\/)?[a-z0-9_-][a-z0-9._-]*$/.test(pkg.name) || names.has(pkg.name)) throw Error('package name');
    names.add(pkg.name);
    const archive = Buffer.from(pkg.content, 'base64'); compressed += archive.length;
    if (archive.length > 262144 || compressed > 1048576 || 'sha512-' + crypto.createHash('sha512').update(archive).digest('base64') !== pkg.integrity) throw Error('package integrity');
    const tar = require('node:zlib').gunzipSync(archive, {maxOutputLength:16777216});
    const root = '/work/node_modules/' + pkg.name;
    const seen = new Set(); let manifest;
    for (let offset=0; offset+512<=tar.length;) {
      const h = tar.subarray(offset,offset+512); offset+=512;
      if (h.every(b=>b===0)) break;
      const string = (start,len) => h.subarray(start,start+len).toString('utf8').replace(/\0.*$/s,'');
      const octal = (start,len) => { const s=string(start,len).trim(); if(!/^[0-7]+$/.test(s))throw Error('tar number');return parseInt(s,8); };
      const checksum=octal(148,8);
      if (h.reduce((sum,b,i)=>sum+(i>=148&&i<156?32:b),0)!==checksum) throw Error('tar checksum');
      let name=string(0,100); const prefix=string(345,155); if(prefix)name=prefix+'/'+name;
      const type=h[156];
      if (type!==0 && type!==48 && type!==53) throw Error('tar links/extensions/devices unsupported');
      const pieces=name.replace(/\/$/,'').split('/');
      if (pieces.shift()!=='package' || pieces.some(p=>!p||p==='.'||p==='..'||p==='node_modules'||p==='.git'||p.includes('\\'))) throw Error('tar path');
      const relative=pieces.join('/'); const size=octal(124,12);
      if (size>1048576 || offset+size>tar.length || ++files>1024) throw Error('tar bounds');
      expanded+=size; if(expanded>8388608)throw Error('expanded package budget');
      if (relative && (seen.has(relative)||/\.(node|gyp)$/.test(relative)))throw Error('duplicate/native file');
      if(relative)seen.add(relative);
      const target=path.join(root,relative);
      if(type===53) { if(size)throw Error('directory size');fs.mkdirSync(target,{recursive:true,mode:0o700}); }
      else {
        if(!relative)throw Error('empty file path');
        fs.mkdirSync(path.dirname(target),{recursive:true,mode:0o700});
        fs.writeFileSync(target,tar.subarray(offset,offset+size),{flag:'wx',mode:0o400});
        if(relative==='package.json')manifest=JSON.parse(tar.subarray(offset,offset+size).toString('utf8'));
      }
      offset+=Math.ceil(size/512)*512;
    }
    if (!manifest || manifest.name!==pkg.name || manifest.version!==pkg.version)throw Error('package manifest identity');
    const scripts=manifest.scripts||{};
    if(manifest.gypfile || manifest.os || manifest.cpu || manifest.bin || manifest.workspaces || manifest.bundleDependencies || manifest.bundledDependencies || manifest.optionalDependencies || manifest.peerDependencies || ['preinstall','install','postinstall'].some(k=>scripts[k]))throw Error('unsupported package setup');
    const sorted = o=>JSON.stringify(Object.entries(o||{}).sort(([a],[b])=>a.localeCompare(b)));
    if(sorted(manifest.dependencies)!==sorted(pkg.dependencies))throw Error('package dependencies differ from lock');
  }
};
