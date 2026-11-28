"""Resolve Python references only against declarations in the indexed repository."""

from collections import defaultdict
from pathlib import PurePosixPath


class PythonBindings:
    def __init__(self, files, symbols):
        self.modules = defaultdict(list)
        for file in files:
            path = PurePosixPath(file)
            parts = path.parts[:-1] if path.name == "__init__.py" else path.with_suffix("").parts
            self.modules[".".join(parts)].append(file)
        self.exports = {(item["relative_path"], item["name"]): item["id"] for item in symbols
                        if item["exported"] and item["parent_symbol_id"] is None}
        self.methods = {(item["parent_symbol_id"], item["name"]): item["id"] for item in symbols
                        if item["kind"] == "method" and item["exported"]}

    def module_name(self, file, binding):
        module = binding["module"]
        level = binding["level"]
        if not level:
            return module
        package = PurePosixPath(file).parts[:-1]
        if level > len(package):
            return None
        parts = list(package[:len(package) - level + 1])
        return ".".join([*parts, module] if module else parts)

    def module_file(self, name):
        candidates = self.modules.get(name, [])
        return candidates[0] if len(candidates) == 1 else None

    def import_target(self, file, binding):
        name = self.module_name(file, binding)
        if name is None:
            return None
        if member := binding.get("member"):
            child = f"{name}.{member}" if name else member
            if target := self.module_file(child):
                return target
        return self.module_file(name)

    def call_target(self, file, binding):
        attributes = binding["attributes"]
        if symbol := binding.get("symbol"):
            return self.member_target(symbol, attributes)
        name = self.module_name(file, binding)
        if name is None:
            return None
        module_file = self.module_file(name)
        if member := binding.get("member"):
            symbol = self.exports.get((module_file, member))
            child = self.module_file(f"{name}.{member}" if name else member)
            if child and symbol:
                return None
            if symbol:
                return self.member_target(symbol, attributes)
            module_file = child
        else:
            prefix = binding["prefix"]
            if attributes[:len(prefix)] != prefix:
                return None
            attributes = attributes[len(prefix):]
        if not module_file or not attributes:
            return None
        return self.member_target(self.exports.get((module_file, attributes[0])), attributes[1:])

    def member_target(self, symbol, attributes):
        if not attributes:
            return symbol
        if len(attributes) == 1:
            return self.methods.get((symbol, attributes[0]))
        return None
