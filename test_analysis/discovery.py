"""Bounded Node metadata extraction from captured ASTs; no config evaluation."""
from hashlib import sha256
from pathlib import PurePosixPath
import re
from importlib.metadata import version

from tree_sitter import Parser
from knowledge_base.parser import LANGUAGES, parse_source
from .contracts import SourceRef, Assertion

ADAPTER_VERSION = 'node-vitest-static-v3:' + ':'.join(version(name) for name in ('tree-sitter', 'tree-sitter-javascript', 'tree-sitter-typescript'))


def digest(value):
    return sha256(value.encode()).hexdigest()


def source_ref(source, start=1, end=None):
    return SourceRef(path=source.path, source_hash=source.digest, start_line=start,
                     end_line=end or max(start, len(source.data.splitlines())))


def test_filename(path):
    value = PurePosixPath(path)
    return bool(re.search(r'\.(test|spec)\.[cm]?[jt]sx?$', value.name) or
                any(part in ('test', 'tests', '__tests__') for part in value.parts[:-1]))


def inspect(source):
    data = source.data
    tree = Parser(LANGUAGES[PurePosixPath(source.path).suffix.lower()]).parse(data)
    syntax = parse_source(data, suffix=PurePosixPath(source.path).suffix.lower(),
                          repository_id='0' * 24, relative_path=source.path)
    bindings, asserts, imports = {}, set(), []
    shadowed_require = False
    direct_asserts = set()
    expects = set()
    for reference in syntax.references:
        if reference['kind'] != 'import':
            continue
        metadata = reference['metadata']
        module = (metadata.get('module') or '').strip('"\'')
        imports.append(module)
        local = metadata.get('local')
        imported = metadata.get('imported')
        if local == 'require': shadowed_require = True
        if local and module == 'node:test':
            bindings[local] = imported or 'default'
        if local and module == 'vitest':
            if imported in ('test','it','describe','*'): bindings[local]=imported
            elif imported=='expect': expects.add(local)
        if local and module in ('node:assert', 'node:assert/strict'):
            asserts.add(local)
            if imported not in (None, 'default', '*'):
                direct_asserts.add(local)
    def text(node):
        return data[node.start_byte:node.end_byte].decode('utf-8') if node else ''
    stack, nodes = [tree.root_node], []
    while stack and len(nodes) < 100000:
        node = stack.pop()
        nodes.append(node)
        stack.extend(reversed(node.named_children))
    limitations = []
    if stack:
        limitations.append('AST node budget reached; discovery is partial.')
    if tree.root_node.has_error:
        return [], sorted(set(imports)), ['Partial syntax; Node test declarations are not established.']
    # Qualify simple CommonJS bindings. Destructuring stays unsupported rather
    # than guessing alias ownership. Count other bindings to avoid shadowed names.
    declarations = []
    for node in nodes:
        if node.type == 'variable_declarator':
            name, value = node.child_by_field_name('name'), node.child_by_field_name('value')
            declarations.append(text(name))
            if name and value and value.type == 'call_expression':
                fn = text(value.child_by_field_name('function'))
                args = value.child_by_field_name('arguments')
                children = args.named_children if args else []
                if fn == 'require' and len(children) == 1 and children[0].type == 'string':
                    module = text(children[0]).strip('"\'')
                    imports.append(module)
                    if module == 'node:test' and name.type == 'identifier': bindings[text(name)] = 'default'
                    if module in ('node:assert', 'node:assert/strict') and name.type == 'identifier': asserts.add(text(name))
        elif node.type in ('function_declaration', 'class_declaration'):
            declarations.append(text(node.child_by_field_name('name')))
        elif node.type in ('formal_parameters', 'required_parameter', 'optional_parameter'):
            declarations.append(text(node))
    for alias in list(bindings.keys() | asserts | expects):
        # A conflicting local parameter or declaration invalidates file-wide
        # alias recognition. False negatives are safer than fake test metadata.
        count = sum(bool(re.search(r'\b' + re.escape(alias) + r'\b', name)) for name in declarations)
        commonjs = any(n.type == 'variable_declarator' and text(n.child_by_field_name('name')) == alias for n in nodes)
        if count > (1 if commonjs else 0):
            bindings.pop(alias, None); asserts.discard(alias)
            expects.discard(alias)
            limitations.append('Shadowed Node binding excluded: ' + alias)
    if shadowed_require or any(re.search(r'\brequire\b', name) for name in declarations):
        bindings = {}; asserts = set()
        limitations.append('Locally declared require is unsupported.')
    imports = sorted(set(imports))
    if len(imports) > 64 or any(len(item) > 200 for item in imports):
        limitations.append('Import metadata budget reached (64 specifiers, 200 characters each); association is partial.')
    imports = [item for item in imports if len(item) <= 200][:64]
    if len(bindings) + len(asserts) > 64:
        limitations.append('Node binding budget reached (64); unsupported aliases omitted.')
        bindings = dict(list(sorted(bindings.items()))[:32])
        asserts = set(sorted(asserts)[:32])
    tests = []
    suite_options = any(n.type == 'call_expression' and ('skip' in text(n.child_by_field_name('function')) or 'todo' in text(n.child_by_field_name('function'))) for n in nodes)
    for node in nodes:
        if node.type != 'call_expression': continue
        fn = text(node.child_by_field_name('function'))
        parts = fn.split('.')
        binding = bindings.get(parts[0])
        if binding is None: continue
        if len(parts) > 1 and parts[1] not in ('test', 'it', 'describe', 'only', 'skip', 'todo'):
            continue
        action = parts[1] if len(parts) > 1 and parts[1] in ('test', 'it', 'describe') else binding
        if action not in ('default', 'test', 'it') or len(parts) > 2: continue
        args = node.child_by_field_name('arguments')
        arguments = args.named_children if args else []
        if not arguments or arguments[0].type != 'string': continue
        callback = next((n for n in reversed(arguments) if n.type in ('arrow_function', 'function_expression')), None)
        if callback is None: continue
        name = text(arguments[0])[1:-1][:200]
        assertion_items = []
        for child in nodes:
            if not (callback.start_byte <= child.start_byte < child.end_byte <= callback.end_byte): continue
            if child.type != 'call_expression': continue
            expression = text(child.child_by_field_name('function'))
            member=child.child_by_field_name('function')
            if member and member.type=='member_expression':
                obj=member.child_by_field_name('object'); prop=member.child_by_field_name('property')
                if obj and obj.type=='call_expression' and text(obj.child_by_field_name('function')) in expects and text(prop) in ('toBe','toEqual','toStrictEqual','toThrow','toMatch'):
                    snippet=text(child)
                    if len(snippet.encode())<=500:
                        assertion_items.append(Assertion(source=source_ref(source,child.start_point.row+1,child.end_point.row+1),expression=snippet))
                    continue
            if expression.split('.')[0] not in asserts: continue
            if len(expression.split('.')) > 2 or (expression.split('.')[0] in direct_asserts and '.' in expression):
                continue
            if expression not in asserts and expression.split('.')[-1] not in ('ok', 'equal', 'strictEqual', 'deepEqual', 'deepStrictEqual', 'throws', 'rejects', 'doesNotThrow', 'doesNotReject', 'match'):
                continue
            # Assertions in nested test callbacks are not attributed to a parent.
            ancestors = child.parent
            nested = False
            while ancestors and ancestors.id != callback.id:
                if ancestors.type in ('arrow_function', 'function_expression', 'function_declaration'):
                    nested = True; break
                ancestors = ancestors.parent
            if nested: continue
            snippet = text(child)
            if len(snippet.encode()) > 500: continue
            assertion_items.append(Assertion(source=source_ref(source, child.start_point.row + 1, child.end_point.row + 1), expression=snippet))
        options = text(arguments[1]) if len(arguments) > 2 else ''
        enabled = len(parts) == 1 or parts[-1] not in ('skip', 'todo')
        if re.search(r'\b(skip|todo)\s*:', options) or suite_options: enabled = False
        item_limits = ['Static declaration and assertions only; fixtures, runtime reach and assertion relevance are unverified.']
        if len(assertion_items) > 20:
            item_limits.append(f'{len(assertion_items) - 20} assertions omitted by the per-test budget.')
        if len(text(arguments[0])[1:-1]) > 200:
            item_limits.append('Displayed test name truncated to 200 characters.')
        tests.append({'key': digest(source.path + ':' + str(node.start_byte) + ':' + source.digest),
                      'name': name, 'source': source_ref(source, node.start_point.row + 1, node.end_point.row + 1),
                      'assertions': assertion_items[:20], 'imports': sorted(set(imports)), 'enabled': enabled,
                      'limitations': item_limits})
        if len(tests) >= 200:
            limitations.append('Per-file test declaration budget reached (200).'); break
    return tests, sorted(set(imports)), limitations
