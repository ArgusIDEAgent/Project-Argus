#!/usr/bin/env bash
# Rebuild the CodeMind demo repository from the in-repo fixture.
#
# WHY THIS EXISTS
#   The TypeScript fixture at knowledge_base/fixtures/phase8/typescript cannot be
#   indexed in place: knowledge_base/synchronization.py:56 raises
#   "Git is required to evaluate ignore rules" whenever the target is not its own
#   git repository AND contains a .gitignore, and knowledge_base/scanner.py:65
#   requires git's toplevel to equal the target directory exactly. The fixture is a
#   subdirectory of this repo, so it always fails. Copying it to a standalone git
#   repo fixes both conditions.
#
#   The JWT module below is added so the README's own demo question
#   ("Where is JWT authentication implemented?") resolves inside a multi-file
#   project, which gives a real two-hop impact chain to demonstrate.
#
# Usage:  ./setup_demo_repo.sh [target-dir]      (default: ~/codemind-demo)
set -euo pipefail

TARGET="${1:-$HOME/codemind-demo}"
SOURCE="$(cd "$(dirname "$0")" && pwd)/knowledge_base/fixtures/phase8/typescript"

if [ ! -d "$SOURCE" ]; then
  echo "Fixture not found at $SOURCE" >&2
  exit 1
fi

rm -rf "$TARGET"
mkdir -p "$TARGET/src" "$TARGET/tests"
cp "$SOURCE/src/"*.ts "$TARGET/src/"
cp "$SOURCE/tests/"*.ts "$TARGET/tests/"
cp "$SOURCE/tsconfig.json" "$TARGET/"

cat > "$TARGET/src/jwt.ts" <<'TYPESCRIPT'
/**
 * JWT authentication for the protected resource API.
 * Demo fixture for CodeMind structural queries.
 */

const TOKEN_TTL_SECONDS = 3600;
const SIGNING_KEY = "demo-signing-key";

function digest(input: string): string {
  let hash = 0;
  const salted = SIGNING_KEY + input;
  for (let index = 0; index < salted.length; index += 1) {
    hash = (hash * 31 + salted.charCodeAt(index)) | 0;
  }
  return (hash >>> 0).toString(16);
}

export function signToken(subject: string, issuedAt: number): string {
  return digest(`${subject}.${issuedAt + TOKEN_TTL_SECONDS}`);
}

/** Verify an incoming bearer token's signature and expiry. */
export function verifyToken(token: string, issuedAt: number, now: number): boolean {
  if (issuedAt + TOKEN_TTL_SECONDS <= now) return false;
  return signToken("subject", issuedAt) === token;
}

export function authenticateRequest(token: string, issuedAt: number, now: number): boolean {
  return verifyToken(token, issuedAt, now);
}
TYPESCRIPT

cat > "$TARGET/src/controller.ts" <<'TYPESCRIPT'
import {authorizeRequest as allow} from "@auth";
import {SecureService} from "./service";
import {authenticateRequest} from "./jwt";
/** Validate a login and check permission to enter a protected resource. */
export function requestLogin(role: string, token: string) {
  const service = new SecureService();
  return allow(role) && service.check(token);
}
/** Full protected-resource gate: verify the bearer token, then authorise the role. */
export function guardProtectedResource(role: string, token: string, issuedAt: number, now: number) {
  if (!authenticateRequest(token, issuedAt, now)) return false;
  return requestLogin(role, token);
}
TYPESCRIPT

printf 'node_modules\nignored.ts\n' > "$TARGET/.gitignore"

cd "$TARGET"
git init -q
git add -A
git -c user.email=demo@local -c user.name=student commit -qm "CodeMind demo repo"
echo "Demo repo ready: $TARGET"
echo
echo "Register this path in CodeMind (CodeMind: Select Repository), then it can be indexed."
echo
echo "Suggested demo questions (all verified):"
echo "  Where is JWT authentication implemented?"
echo "  What happens if I modify signToken?"
echo "  Who calls requestLogin?"
echo "  Who calls authorizeRequest?          <- reports ambiguity instead of guessing"
