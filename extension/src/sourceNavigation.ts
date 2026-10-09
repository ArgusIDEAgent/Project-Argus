import { createHash } from 'node:crypto';
import * as fs from 'node:fs';
import * as path from 'node:path';

const MAX_VERIFY_BYTES = 5 * 1024 * 1024;

function isInside(root: string, target: string): boolean {
  const rel = path.relative(root, target);
  return rel !== '' && rel !== '..' && !rel.startsWith('..' + path.sep) && !path.isAbsolute(rel);
}

export function verifiedSourcePath(rootPath: string, relativePath: string, sourceHash: string): string {
  if (!relativePath || path.isAbsolute(relativePath) || relativePath.includes('\0')) {
    throw new Error('Invalid indexed source path.');
  }
  const root = fs.realpathSync(rootPath);
  const file = path.resolve(root, relativePath);
  if (!isInside(root, file)) throw new Error('Indexed source is outside the selected repository.');
  let real: string;
  try { real = fs.realpathSync(file); }
  catch { throw new Error('Indexed source no longer exists. Refresh the repository.'); }
  if (!isInside(root, real)) throw new Error('Indexed source is outside the selected repository.');
  const stat = fs.statSync(real);
  if (!stat.isFile() || stat.size > MAX_VERIFY_BYTES) {
    throw new Error('Indexed source is not a regular file or is too large to open.');
  }
  if (createHash('sha256').update(fs.readFileSync(real)).digest('hex') !== sourceHash) {
    throw new Error('This source changed. Refresh the repository before opening it.');
  }
  return real;
}
