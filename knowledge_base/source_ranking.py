"""Query-aware source roles and stable file diversity; never deletes evidence."""
from pathlib import PurePosixPath
import re


def source_role(path):
    path = PurePosixPath(str(path).replace("\\", "/").lower())
    if any(part in {"benchmark", "benchmarks"} for part in path.parts) or path.name.startswith("benchmark_"):
        return "benchmark"
    if (any(part in {"test", "tests", "__tests__", "fixtures"} for part in path.parts)
            or path.name.startswith("test_") or re.search(r"\.(?:test|spec)\.", path.name)):
        return "test"
    if path.suffix in {".md", ".markdown", ".rst", ".txt"} or "docs" in path.parts:
        return "documentation"
    if path.suffix in {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg"}:
        return "configuration"
    return "implementation"


def preferred_role(query):
    # Code implementing a documentation/test tool is still implementation.
    if re.search(r"\b(?:code|implementation|function|method|logic|renders?|parses?|generates?|processes)\b", query, re.I):
        return "implementation"
    for role, pattern in (
        ("benchmark", r"\b(?:benchmarks?|evaluation results|evaluation metrics)\b"),
        ("test", r"\b(?:tests?|fixtures?|coverage)\b"),
        ("documentation", r"\b(?:documentation|docs|readme)\b"),
        ("configuration", r"\b(?:configuration|config|settings)\b"),
    ):
        if re.search(pattern, query, re.I):
            return role
    return "implementation"


def role_priority(path, preferred):
    roles = ["implementation", "documentation", "configuration", "test", "benchmark"]
    roles.remove(preferred)
    return [preferred, *roles].index(source_role(path))


def source_priority(path, query):
    return role_priority(path, preferred_role(query))


def rank_sources(items, query, *, path=lambda item: item["path"], score=None):
    """Role first, relevance within role, distinct files before repeat chunks.

    All supplied items remain present. Explicit identity/graph result sets must
    bypass this discovery ranking so ambiguity and path orientation survive.
    """
    preferred = preferred_role(query)
    ordered = sorted(enumerate(items), key=lambda pair: (
        role_priority(path(pair[1]), preferred), -score(pair[1]) if score else pair[0], pair[0]))
    output = []
    for tier in range(5):
        group = [item for _, item in ordered if role_priority(path(item), preferred) == tier]
        seen, repeated = set(), []
        for item in group:
            key = path(item)
            if key in seen:
                repeated.append(item)
            else:
                seen.add(key)
                output.append(item)
        output.extend(repeated)
    return output
