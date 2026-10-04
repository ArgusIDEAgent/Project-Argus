import { createHash } from 'node:crypto';
import * as fs from 'node:fs';
import * as path from 'node:path';

export function verifiedSourcePath(rootPath: string, relativePath: string, sourceHash: string): string {
  if (!relativePath || path.isAbsolute(relativePath) || relativePath.includes('\0')) {
    throw new Error('Invalid indexed source path.');
  }
  const root = fs.realpathSync(rootPath);
  const file = path.resolve(root, relativePath);
  if (!file.startsWith(root + path.sep)) {
    throw new Error('Indexed source is outside the selected repository.');
  }
  const real = fs.realpathSync(file);
  if (!real.startsWith(root + path.sep)) throw new Error('Indexed source is outside the selected repository.');
  if (createHash('sha256').update(fs.readFileSync(real)).digest('hex') !== sourceHash) {
    throw new Error('This source changed. Refresh the repository before opening it.');
  }
  return real;
}
