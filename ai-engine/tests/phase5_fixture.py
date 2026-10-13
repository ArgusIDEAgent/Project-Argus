"""Shared Phase 5 test/benchmark fixture; never indexes a user repository."""

import test_phase4_rules as phase4


def fixture():
    owner = phase4.RuleEngineTests()
    owner.setUp()
    for path, source in {
        "service.ts": 'export class AuthService { check(token: string) { return token.length > 0; } }\nexport function snake_case(value: number) { return value; }\n',
        "one/duplicate.ts": 'export function duplicate() { return 1; }\n',
        "two/duplicate.ts": 'export function duplicate() { return 2; }\n',
        "nested.ts": 'export function outer() {\n  function inner() { return 1; }\n  return inner();\n}\n',
    }.items():
        target = owner.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source)
    owner.publish()
    return owner
