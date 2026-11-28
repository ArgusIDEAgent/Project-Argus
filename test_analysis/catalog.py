"""Incremental session catalog over the existing capture inventory, never live disk."""
from collections import OrderedDict
import json
from pathlib import PurePosixPath

from change_analysis.metadata_policy import POLICY
from .contracts import TestProfile, DiscoveredTest
from .discovery import ADAPTER_VERSION, inspect, source_ref, test_filename, digest

MAX_TEST_FILES = 100
MAX_TESTS = 200


class TestCatalog:
    def __init__(self):
        self.files = OrderedDict()
        self.parse_count = 0

    def clear(self):
        self.files.clear()

    def discover(self, snapshot):
        metadata = {v.path: v for v in snapshot.after_metadata if v.data is not None}
        excluded_metadata = [v for v in snapshot.after_metadata if v.data is None]
        source_map = {v.path: v for v in snapshot.after if v.data is not None}
        package_paths = sorted(path for path in metadata if PurePosixPath(path).name == 'package.json')
        roots = sorted({str(PurePosixPath(path).parent) for path in package_paths} | {'.'})
        if len(roots) > 20:
            raise ValueError('More than 20 package profiles; selected-scope planning budget exceeded')
        profiles = []
        limitations = ['Discovery is static. Import association is not execution coverage; no tests have run.']
        if snapshot.metadata_policy != POLICY:
            limitations.append('This older capture has no qualified test metadata; recapture to discover manifests and dependencies.')
        limitations += [f'{v.path}: metadata unavailable ({v.exclusion}).' for v in excluded_metadata]
        input_identity = [(v.path, v.digest, v.exclusion) for v in (*snapshot.after, *snapshot.after_metadata)]
        catalog_id = digest(json.dumps([ADAPTER_VERSION, snapshot.repository_id, snapshot.metadata_policy, input_identity], sort_keys=True))
        inspected = {}
        candidates = sorted((v for v in source_map.values() if test_filename(v.path)
                             or b'node:test' in v.data or b'vitest' in v.data), key=lambda v: v.path)
        omitted_files = max(0, len(candidates) - MAX_TEST_FILES)
        for source in candidates[:MAX_TEST_FILES]:
            key = (source.path, source.digest, ADAPTER_VERSION)
            if key not in self.files:
                self.files[key] = inspect(source)
                self.parse_count += 1
                if len(self.files) > 64:
                    self.files.popitem(last=False)
            else:
                self.files.move_to_end(key)
            inspected[source.path] = self.files[key]
        for package_root in roots:
            prefix = '' if package_root == '.' else package_root + '/'
            manifest = metadata.get(prefix + 'package.json')
            parsed = {}
            issues = []
            if manifest:
                try:
                    parsed = json.loads(manifest.data)
                    if not isinstance(parsed, dict): raise ValueError('Expected object')
                except (ValueError, RecursionError):
                    issues.append('Invalid captured package.json; requirements are unknown.')
                    parsed = {}
            deps = {**(parsed.get('dependencies') if isinstance(parsed.get('dependencies'), dict) else {}),
                    **(parsed.get('devDependencies') if isinstance(parsed.get('devDependencies'), dict) else {})}
            engines = parsed.get('engines') if isinstance(parsed.get('engines'), dict) else {}
            runtime = engines.get('node')[:200] if isinstance(engines.get('node'), str) else None
            if runtime is None:
                hint = next((metadata.get(prefix + name) for name in ('.node-version', '.nvmrc') if metadata.get(prefix + name)), None)
                runtime = hint.data.decode('utf-8').strip()[:200] if hint else None
            local_sources = [path for path in inspected if max((r for r in roots if r == '.' or path.startswith(r + '/')), key=lambda r: (r != '.', len(r))) == package_root]
            node = any('node:test' in inspected[path][1] for path in local_sources)
            vitest=any('vitest' in inspected[path][1] for path in local_sources)
            framework = 'unsupported' if node and vitest else 'node:test' if node else 'vitest' if vitest and deps.get('vitest')=='5.1.2' else 'unsupported' if any(name in deps for name in ('vitest', 'jest', 'mocha', '@playwright/test')) else 'unknown'
            lockfiles = [metadata[prefix + name] for name in ('package-lock.json', 'npm-shrinkwrap.json', 'pnpm-lock.yaml', 'yarn.lock', 'bun.lock') if prefix + name in metadata]
            configs = [v for path, v in source_map.items() if str(PurePosixPath(path).parent) == package_root
                       and ('.config.' in PurePosixPath(path).name or PurePosixPath(path).name.startswith('test.config.'))]
            config_refs = [source_ref(v) for v in configs]
            config_refs += [source_ref(v) for path, v in metadata.items() if str(PurePosixPath(path).parent) == package_root and PurePosixPath(path).name.startswith('tsconfig')]
            relevant = [v for path, v in metadata.items() if str(PurePosixPath(path).parent) == package_root]
            profile_id = digest(json.dumps([ADAPTER_VERSION, package_root, [(v.path, v.digest) for v in relevant],
                                            [(v.path, v.digest) for v in configs], framework], sort_keys=True))
            if configs: issues.append('Executable configuration files were inventoried but not evaluated; hooks and services are unknown.')
            if package_root != '.': issues.append('Nested package detected; monorepo execution orchestration is unsupported.')
            if framework not in ('node:test','vitest'): issues.append('No supported single-framework Node/Vitest profile found.')
            issues += ['Execution requires a separately qualified offline sandbox and explicit authorization.',
                       'Package scripts and lifecycle hooks are untrusted metadata and are never executed.']
            profiles.append(TestProfile(profile_id=profile_id, package_root=package_root, framework=framework,
                runtime_requirement=runtime, dependency_identity=digest(json.dumps([(v.path, v.digest) for v in lockfiles])) if lockfiles else None,
                dependency_state='unknown' if snapshot.metadata_policy != POLICY or any(str(PurePosixPath(v.path).parent) == package_root for v in excluded_metadata) else 'lockfile_captured' if lockfiles else 'no_lockfile',
                manifests=[source_ref(v) for v in relevant], configuration=config_refs,
                command=(['node', '--test'] if framework=='node:test' else ['vitest','run']) if framework in ('node:test','vitest') and package_root=='.' else None,
                limitations=issues))
        tests = []
        omitted = 0
        for path, (entries, imports, issues) in inspected.items():
            limitations += [f'{path}: {item}' for item in issues]
            profile = max((p for p in profiles if p.package_root == '.' or path.startswith(p.package_root + '/')),
                          key=lambda p: (p.package_root != '.', len(p.package_root)))
            if not entries and test_filename(path):
                entries = [{'key': digest(path + source_map[path].digest), 'name': PurePosixPath(path).name,
                            'source': source_ref(source_map[path]), 'assertions': [], 'imports': imports,
                            'enabled': False, 'limitations': ['Filename association only; no supported Node declaration established.']}]
            for item in entries:
                if len(tests) >= MAX_TESTS:
                    omitted += 1; continue
                tests.append(DiscoveredTest(test_id=item['key'], name=item['name'], source=item['source'],
                    framework=('node:test' if 'node:test' in imports else 'vitest' if 'vitest' in imports else 'unknown') if item['enabled'] else 'unknown',
                    profile_id=profile.profile_id, assertions=item['assertions'], imports=imports,
                    enabled=item['enabled'], limitations=item['limitations']))
        if omitted_files:
            limitations.append(f'{omitted_files} candidate test files omitted by the {MAX_TEST_FILES}-file discovery budget.')
        if omitted:
            limitations.append(f'{omitted} test declarations omitted by the {MAX_TESTS}-test catalog budget.')
        return catalog_id, profiles, tests, sorted(inspected), omitted, limitations
