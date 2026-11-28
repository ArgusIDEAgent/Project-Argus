"""Python AST definitions and conservative lexical call bindings for the snapshot graph."""

import ast
from collections import defaultdict
from dataclasses import dataclass, field

from knowledge_base.identity import symbol_identity


@dataclass
class Scope:
    parent: "Scope | None" = None
    symbol_id: str | None = None
    kind: str = "module"
    qualified_name: str = ""
    bindings: dict = field(default_factory=lambda: defaultdict(list))
    wildcard: bool = False

    def resolve(self, name, position):
        scope = self
        while scope is not None:
            if scope.wildcard:
                return None
            candidates = scope.bindings.get(name)
            if candidates:
                if len(candidates) != 1 or candidates[0] is None:
                    return None
                binding_position, target = candidates[0]
                if scope is self and binding_position >= position:
                    return None
                return target
            scope = scope.parent
        return None


class PythonSyntax(ast.NodeVisitor):
    def __init__(self, source, repository_id, relative_path, result):
        self.repository_id = repository_id
        self.relative_path = relative_path
        self.result = result
        bom = 3 if source.startswith(b"\xef\xbb\xbf") else 0
        self.lines = source[bom:].split(b"\n")
        self.offsets = [bom]
        for line in self.lines:
            self.offsets.append(self.offsets[-1] + len(line) + 1)
        self.module = self.scope = Scope()
        self.direct = set()
        self.ordinals = defaultdict(int)
        self.calls = []
        self.symbol_scopes = {}

    def span(self, node):
        start, end = node.lineno, node.end_lineno
        left, right = node.col_offset, node.end_col_offset
        return {
            "start_line": start, "end_line": end,
            "start_column": len(self.lines[start - 1][:left].decode("utf-8", "replace")) + 1,
            "end_column": len(self.lines[end - 1][:right].decode("utf-8", "replace")) + 1,
            "start_byte": self.offsets[start - 1] + left,
            "end_byte": self.offsets[end - 1] + right,
        }

    def reference(self, kind, node, expression, metadata):
        row = {"kind": kind, "scope_symbol_id": self.scope.symbol_id, "expression": expression,
               "metadata": metadata, **self.span(node)}
        self.result.references.append(row)
        return row

    def body(self, statements):
        self.direct.update(statements)
        for statement in statements:
            self.visit(statement)

    def definition(self, node, kind):
        parent = self.scope
        key = (parent.symbol_id, kind, node.name)
        self.ordinals[key] += 1
        identifier, identity = symbol_identity(
            self.repository_id, self.relative_path, parent.symbol_id, kind, node.name, "", self.ordinals[key])
        qualified = f"{parent.qualified_name}.{node.name}" if parent.qualified_name else node.name
        parameters = []
        if kind != "class":
            parameters = [{"name": arg.arg, "type": ast.unparse(arg.annotation) if arg.annotation else None}
                          for arg in (*node.args.posonlyargs, *node.args.args, node.args.vararg,
                                      *node.args.kwonlyargs, node.args.kwarg) if arg is not None]
        self.symbol_scopes[identifier] = parent
        self.result.symbols.append({
            "id": identifier, "identity_key": identity, "kind": kind, "name": node.name,
            "qualified_name": qualified, "parent_symbol_id": parent.symbol_id,
            "parameters": parameters,
            "return_annotation": ast.unparse(node.returns) if getattr(node, "returns", None) else None,
            "modifiers": ["async"] if isinstance(node, ast.AsyncFunctionDef) else [],
            "exported": False, "default_export": False, "doc_comment": ast.get_docstring(node),
            **self.span(node),
        })
        self.bind(node, node.name, {"symbol": identifier})
        # Class namespaces are not enclosing lexical scopes for their methods.
        enclosing = parent.parent if parent.kind == "class" else parent
        self.scope = Scope(enclosing, identifier, kind, qualified)
        for parameter in parameters:
            self.scope.bindings[parameter["name"]].append(None)
        self.body(node.body)
        self.scope = parent

    def bind(self, node, name, target):
        # A conditional declaration or import does not establish one binding.
        binding = (self.offsets[node.lineno - 1] + node.col_offset, target) if node in self.direct else None
        self.scope.bindings[name].append(binding)

    def visit_Module(self, node):
        self.body(node.body)

    def visit_ClassDef(self, node):
        self.definition(node, "class")

    def visit_FunctionDef(self, node):
        self.definition(node, "method" if self.scope.kind == "class" else "function")

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Name(self, node):
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.scope.bindings[node.id].append(None)

    def visit_Global(self, node):
        for name in node.names:
            self.scope.bindings[name].append(None)
            if self.scope is not self.module:
                self.module.bindings[name].append(None)

    def visit_Nonlocal(self, node):
        for name in node.names:
            scope = self.scope
            while scope is not self.module:
                scope.bindings[name].append(None)
                scope = scope.parent

    def visit_ExceptHandler(self, node):
        if node.name:
            self.scope.bindings[node.name].append(None)
        self.generic_visit(node)

    def visit_MatchAs(self, node):
        if node.name:
            self.scope.bindings[node.name].append(None)
        self.generic_visit(node)

    visit_MatchStar = visit_MatchAs

    def visit_MatchMapping(self, node):
        if node.rest:
            self.scope.bindings[node.rest].append(None)
        self.generic_visit(node)

    def visit_Lambda(self, node):
        # Calls in dynamically invoked lambdas have no named caller.
        for value in (*node.args.defaults, *node.args.kw_defaults):
            if value is not None:
                self.visit(value)

    def visit_Import(self, node):
        for alias in node.names:
            local = alias.asname or alias.name.split(".")[0]
            target = {"module": alias.name, "level": 0}
            self.reference("import", alias, alias.name, target)
            prefix = [] if alias.asname else alias.name.split(".")[1:]
            self.bind(node, local, {**target, "prefix": prefix})

    def visit_ImportFrom(self, node):
        for alias in node.names:
            target = {"module": node.module or "", "level": node.level, "member": alias.name}
            self.reference("import", alias, alias.name, target)
            if alias.name == "*":
                self.scope.wildcard = True
            else:
                self.bind(node, alias.asname or alias.name, target)

    def visit_Call(self, node):
        target = node.func
        attributes = []
        while isinstance(target, ast.Attribute):
            attributes.append(target.attr)
            target = target.value
        name = target.id if isinstance(target, ast.Name) else None
        ref = self.reference("call", node, ast.unparse(node.func), {})
        if name:
            self.calls.append((ref, self.scope, name, list(reversed(attributes))))
        self.generic_visit(node)

    def finish(self):
        for symbol in self.result.symbols:
            parent = self.symbol_scopes[symbol["id"]]
            if parent.kind in {"module", "class"}:
                # A public binding must still name the original declaration.
                binding = parent.resolve(symbol["name"], float("inf"))
                symbol["exported"] = bool(binding and binding.get("symbol") == symbol["id"])
        for ref, scope, name, attributes in self.calls:
            binding = scope.resolve(name, ref["start_byte"])
            if binding:
                ref["metadata"]["binding"] = {**binding, "attributes": attributes}
