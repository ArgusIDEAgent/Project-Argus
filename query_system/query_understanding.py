"""Read-only deterministic query understanding. Never executes answering engines."""

from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
import re
import sqlite3
from time import monotonic
from types import SimpleNamespace
import unicodedata

from knowledge_base.identity import relative_path
from knowledge_base.metadata import trace
from knowledge_base.parser import LANGUAGES
from knowledge_base.retrieval import IDENTIFIER, PATH, STOP_WORDS, plan_query
from query_system.contracts import (
    ContractError, ConversationalReply, EditorSelection, EngineError, EntityCandidate, Evidence, IntentRequirement, NormalizedQuery,
    QueryContext, QueryEntity, QueryRequest, SnapshotContext, StaleIndexError,
)
from query_system.graph_engine import GRAPH_OPERATIONS, SymbolParameters, _BudgetExceeded, _Run
from query_system.rule_registry import RULES
from query_system.semantic_engine import SEMANTIC_OPERATIONS


_QUOTES = re.compile(r"(?<!\w)(`(?:\\.|[^`\\\n])*`|\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*')")
_PRONOUN = re.compile(r"\b(?:this(?:\s+(?:function|method|class|module|file|code))?|that|it|its|their|both|them)\b", re.I)
_IGNORE = STOP_WORDS | frozenset("logic code workflow feature functionality defined declared implemented callers callees dependencies contract signature parameters arguments tests overview its their that cover loginflow resource request pipeline returns return type do call named end class symbol".split())
_PATTERNS = (
    ("DIRECT_CALLEES", r"\b(?:callees\b|what (?:functions? )?does\b(?:(?!\band\b)[^?,;])*?\bcall\b|which functions (?:are called by|does)\b|calls made by\b)"),
    ("DIRECT_CALLERS", r"\b(?:who calls|what calls|which functions call|callers of\b|(?:show|list|find|what are)\s+(?:(?:me|the|its|their|known|direct|all)\s+)*callers\b|calls to\b|where\b(?:(?!\band\b)[^?,;])*?\bcalled\b)|^(?:its|their) callers\b"),
    ("FUNCTION_CONTRACT", r"\b(?:contract|signature|parameters|arguments|return (?:type|annotation)|what does\s+(?:(?!\band\b)[^?,;])+?\s+return\b|declared async|throws|exceptions)\b"),
    ("CHANGE_IMPACT", r"\b(?:impacts?\b|affected\b|affect\b|break\b|breaks\b|what happens if\b)"),
    ("DEPENDENCY_PATH", r"\b(?:dependency path|call path|path from|dependencies|dependents|depends on|imports|importers)\b"),
    ("MODULE_OVERVIEW", r"\b(?:module overview|overview of|summarize (?:the )?module|list (?:the )?(?:exports|definitions))\b"),
    ("TEST_DISCOVERY", r"\b(?:tests? (?:cover|covers|for|reference|references)|which (?:related )?tests|what (?:related )?tests|test coverage)\b"),
    ("WORKFLOW_EXPLANATION", r"\b(?:workflow|flow through|request flow|end.to.end|pipeline)\b"),
    ("BEHAVIOR_INVESTIGATION", r"\b(?:why\b|debug\b|investigate\b|runtime behavior|unexpected behavior)"),
    ("CODE_COMPARISON", r"\b(?:compare|difference between)\b"),
    ("FUNCTION_EXPLANATION", r"\b(?:explain|describe|what does\b[^?;]*?\bdo\b)"),
    ("SYMBOL_LOCATION", r"\b(?:find|locate|where is|where are|definition of|defined|declared)\b"),
)


def _masked(text):
    """Keep offsets; pasted code/comments and quoted prose are not instructions."""
    base = list(text)
    for match in re.finditer(r"```[\s\S]*?(?:```|$)", text):
        base[match.start():match.end()] = " " * len(match.group())
    quoted = list("".join(base))
    for match in _QUOTES.finditer("".join(base)):
        quoted[match.start():match.end()] = " " * len(match.group())
    for match in re.finditer(r"/\*[\s\S]*?\*/|//[^\n]*", "".join(quoted)):
        base[match.start():match.end()] = " " * len(match.group())
        quoted[match.start():match.end()] = " " * len(match.group())
    return "".join(base), "".join(quoted)


def _normalized_text(text):
    # Unlike NFKC in the legacy search planner, code spelling and quoted spaces
    # stay byte-for-byte intact. The legacy plan remains available separately.
    result, start = [], 0
    for match in re.finditer(r"```[\s\S]*?(?:```|$)|" + _QUOTES.pattern, text):
        result.append(re.sub(r"\s+", " ", text[start:match.start()]))
        result.append(match.group())
        start = match.end()
    result.append(re.sub(r"\s+", " ", text[start:]))
    return "".join(result).strip()


def _path(name):
    return bool(re.search(r"\.(?:[cm]?[jt]sx?|py|md|json|ya?ml|toml|ini|c|cpp|h|java|go|rs|rb)$", name, re.I))


_REQUEST_START = re.compile(r"^(?:(?:and\s+)?then\s+|please\s+|tell me\s+)?(?:compare|difference between|find|locate|explain|describe|who|what|where|why|which|contract|signature|impact|overview|dependencies|dependents|imports|callers|callees|tests?|(?:show|list)\s+(?:me\s+)?(?:the\s+)?(?:code|logic|callers|callees|tests?|dependencies|exports|definitions|its|their)|(?:its|their|both)\s+(?:callers|callees|dependencies|tests))\b", re.I)
_UNKNOWN_REQUEST = re.compile(r"^(?:deploy|execute|delete|predict|prove|count|how many|benchmark|run command)\b", re.I)
_TARGET_LIST = re.compile(r"\b(?:who calls|what calls|callers of|callees of|tests? (?:for|cover|covers|reference|references))\b", re.I)

_CONVERSATIONAL_PATTERNS = (
    ("greeting", re.compile(r"(?:hi|hello|hey|good\s+(?:morning|afternoon|evening)|(?:hi|hello|hey)\s+there)", re.I),
     "Hi! How can I help you understand this codebase?"),
    ("thanks", re.compile(r"(?:thanks|thank\s+you|thanks\s+a\s+lot|many\s+thanks|much\s+appreciated)", re.I),
     "You're welcome! Let me know if you'd like to explore another part of the repository."),
    ("farewell", re.compile(r"(?:bye|goodbye|see\s+you|see\s+you\s+later|talk\s+to\s+you\s+later)", re.I),
     "Goodbye! Come back whenever you'd like to explore the codebase."),
    ("acknowledgment", re.compile(r"(?:ok|okay|got\s+it|understood|sounds\s+good|all\s+right|how\s+are\s+you)", re.I),
     "I'm ready to help with the repository whenever you are."),
    ("capabilities", re.compile(r"(?:help|what\s+can\s+you\s+do|how\s+can\s+you\s+help|what\s+are\s+your\s+capabilities|who\s+are\s+you)", re.I),
     "I can find definitions, callers, callees, dependencies, tests, and feature implementations; inspect contracts and change impact; and explain code or repository workflows using current indexed evidence."),
    ("out_of_scope", re.compile(
        r"(?:what(?:'s|\s+is)\s+the\s+weather(?:\s+(?:today|tomorrow|outside))?|weather\s+forecast|will\s+it\s+rain(?:\s+(?:today|tomorrow))?|"
        r"what\s+time\s+is\s+it|tell\s+me\s+a\s+joke|(?:what(?:'s|\s+is)|show\s+me)\s+the\s+(?:latest\s+)?(?:news|sports\s+score|stock\s+price)|"
        r"(?:give\s+me|find)\s+(?:a\s+)?(?:recipe|restaurant|flight|movie\s+showtime)s?(?:\s+.*)?)", re.I),
     "I'm focused on helping you understand the selected repository, so I can't answer that general request. Ask me about the codebase, its structure, behavior, dependencies, or tests."),
)


def classify_conversational_intent(message: str) -> ConversationalReply | None:
    """Recognize bounded whole-message chat intents without retrieval or model inference."""
    if not isinstance(message, str) or len(message) > 2000 or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", message):
        return None
    normalized = unicodedata.normalize("NFKC", message).replace("’", "'").strip()
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = re.sub(r"[.!?]+$", "", normalized).strip()
    for category, pattern, text in _CONVERSATIONAL_PATTERNS:
        if pattern.fullmatch(normalized):
            return ConversationalReply(category=category, text=text)
    return None


def _discovery_clause(base, masked):
    """A functional description is not a request for an exact symbol named by prose."""
    text = masked.strip()
    # Architecture/algorithm questions describe functionality, not a canonical
    # symbol named after an uppercase domain acronym (e.g. CNN). Keep explicit
    # quoted/qualified code targets on their existing exact-resolution path.
    if (re.match(r"(?:which|what)\s+(?!does\b|calls\b|happens\b)(?:\w+\s+){0,5}(?:architecture|algorithms?|hyperparameters?)\b", text, re.I)
            and not (_QUOTES.search(base) or "::" in base or re.search(r"\w\.\w|[a-z][A-Z]", base))):
        return True
    if not re.match(r"(?:find|locate|show|where (?:is|are)|which part)\b", text, re.I):
        return False
    if re.match(r"(?:find|locate|show)\s+(?:the\s+)?(?:callers|callees|tests?|dependencies|dependents|imports|exports)\b", text, re.I):
        return False
    if re.search(r"\b(?:defined|declared|named)\b", text, re.I):
        return False
    if re.search(r"\b(?:code|logic|implementation|functionality|feature)\b|\b(?:function|method)\s+(?:responsible for|that)\b|^which part\b", text, re.I):
        return True
    if re.match(r"where (?:is|are)\b", text, re.I) and re.search(r"\bimplemented\b", text, re.I):
        # Bare natural-language functionality may use "implemented"; explicit
        # quoted/CamelCase/qualified/named targets still use canonical lookup.
        return not (_QUOTES.search(base) or re.search(r"\b(?:function|method|class|symbol)\b|::|\w_\w|\w\.\w|[a-z][A-Z]", base))
    return False


def _separators(base, masked):
    boundaries, left = [], 0
    code_spans = [(item.start(), item.end()) for item in re.finditer(rf"{PATH}(?:::{IDENTIFIER})?(?![\w.])|{IDENTIFIER}", masked)
                  if any(char in item.group() for char in "/.:")]
    for match in re.finditer(r"[,;]|\b(?:and(?:\s+then)?|then)\b", masked, re.I):
        if any(start <= match.start() < end for start, end in code_spans):
            continue
        clause, tail = masked[left:match.start()], masked[match.end():].lstrip()
        new_request = _REQUEST_START.match(tail) or _UNKNOWN_REQUEST.match(tail)
        comparison = re.search(r"\b(?:compare|difference between)\b", clause, re.I)
        target_list = _TARGET_LIST.search(clause)
        description = _discovery_clause(base[left:match.start()], clause)
        if match.group().lower() == "and" and (comparison or target_list or description) and not new_request:
            continue
        if match.group() == "," and description and not new_request:
            continue
        boundaries.append(match)
        left = match.end()
    return boundaries


def parse_question(question: str) -> NormalizedQuery:
    """Pure lexical front end; canonical IDs are only assigned by pinned lookup."""
    if not isinstance(question, str) or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", question):
        raise ValueError("Question contains invalid control characters")
    legacy = plan_query(question)  # Preserve, reuse and expose the existing plan.
    base, masked = _masked(question)
    separators = _separators(base, masked)
    def clause_bounds(position):
        left = max([match.end() for match in separators if match.end() <= position] or [0])
        right = min([match.start() for match in separators if match.start() > position] or [len(question)])
        return left, right
    spans = [(left, right) for left, right in dict.fromkeys(clause_bounds(pos) for pos in [0, *[match.end() for match in separators]])]
    discovery = [(left, right) for left, right in spans if _discovery_clause(base[left:right], masked[left:right])]
    mentions = []
    def add(name, kind, start, end, explicit=False, file_path=None, role=False):
        if any(left <= start < right for left, right in discovery) and (kind not in ("file", "module") or not _path(name)):
            return
        if not explicit and (name.casefold() in _IGNORE or _PRONOUN.fullmatch(name)):
            return
        if any(a <= start < b and entity.name == name for a, b, entity in mentions):
            return
        if not role and any(item[2].name == name and clause_bounds(item[0])[0] == clause_bounds(start)[0] for item in mentions):
            return
        mentions.append((start, end, QueryEntity(name=name, kind=kind, file_path=file_path)))
    operand = rf"(?:(?:the\s+)?(?:function|method|class|symbol)\s+)?(?P<ref>{_QUOTES.pattern}|{PATH}::{IDENTIFIER}|{PATH}(?![\w.])|{IDENTIFIER})(?:\s*\(\s*\))?(?:\s+in\s+(?P<scope>{_QUOTES.pattern}|{PATH}(?![\w.])))?"
    def extract_operands(header, limit, connectors):
        _, right = clause_bounds(header.start())
        offset = header.end() + re.match(r"\s*", base[header.end():right]).end()
        for ordinal in range(limit):
            match = re.match(operand, base[offset:right])
            if not match:
                break
            raw = match.group("ref")
            name = raw[1:-1] if raw[:1] in "`\"'" else raw
            scope = match.group("scope")
            if scope and scope[:1] in "`\"'": scope = scope[1:-1]
            if "::" in name:
                scope, name = name.rsplit("::", 1)
            if re.fullmatch(IDENTIFIER, name) or _path(name):
                start, end = offset + match.start("ref"), offset + match.end("ref")
                add(name, "file" if _path(name) else "method" if "." in name else "function", start, end,
                    explicit=raw[:1] in "`\"'", file_path=scope, role=True)
                if scope:
                    at = offset + (match.start("scope") if match.group("scope") else match.start("ref"))
                    add(scope, "file", at, offset + match.end(), explicit=True, role=True)
            offset += match.end()
            connector = re.match(rf"\s+(?:{connectors})\s+", base[offset:right], re.I)
            if ordinal < limit - 1 and connector:
                offset += connector.end()
            else:
                break
    for header in re.finditer(r"\b(?:compare|difference between)\b", masked, re.I):
        extract_operands(header, 2, r"and|with|versus|vs\.?")
    for header in _TARGET_LIST.finditer(masked):
        extract_operands(header, 13, r"and")
    for match in _QUOTES.finditer(base):
        name = match.group()[1:-1]
        if _path(name):
            add(name, "file", match.start(), match.end(), explicit=True)
        elif re.fullmatch(IDENTIFIER, name) or re.fullmatch(r"[a-f0-9]{64}", name):
            add(name, "method" if "." in name else "function", match.start(), match.end(), explicit=True)
    for match in re.finditer(rf"{PATH}(?![\w.])", masked, re.I):
        prefix = masked[max(0, match.start()-16):match.start()]
        add(match.group(), "module" if re.search(r"\bmodule\s*$", prefix, re.I) else "file", match.start(), match.end())
    for match in re.finditer(rf"\b(function|method|class|symbol|module|file)\s+(?:named\s+)?({IDENTIFIER})", masked, re.I):
        if match.group(2).casefold() not in {"and", "or", "under", "with", "without"} and not any(a <= match.start(2) < b for a, b, _ in mentions):
            add(match.group(2), match.group(1).lower(), match.start(2), match.end(2), explicit=True)
    for match in re.finditer(rf"\b({IDENTIFIER})\s+(function|method|class|module)\b", masked, re.I):
        if _REQUEST_START.fullmatch(match.group(1)):
            continue  # "Find function login" is not a function named "Find".
        add(match.group(1), match.group(2).lower(), match.start(1), match.end(1))
    target_prefix = (r"who calls|what calls|callers of|callees of|explain|describe|overview of|contract of|signature of|"
                     r"parameters of|return type of|change|modify|changing|find|locate|where is|what does|"
                     r"dependencies of|tests for|tests? (?:cover|covers|reference|references)|references to|path from|"
                     r"why does|behavior (?:of|in)|called by|calls made by|what functions? does|\bthrough|\bcall")
    if re.search(r"\b(?:dependency path|call path|path from|pipeline|workflow|flow)\b", masked, re.I):
        target_prefix += r"|\bfrom|\bto"
    for match in re.finditer(rf"(?=\b(?:{target_prefix})\s+(?:the\s+)?({IDENTIFIER}))", masked, re.I):
        following = re.match(r"\s+(\w+)", masked[match.end(1):])
        prefix = masked[match.start():match.start(1)]
        if (re.search(r"\b(?:find|locate|where is|explain|describe)\b", prefix, re.I)
                and match.group(1).islower() and "_" not in match.group(1) and "." not in match.group(1) and following
                and following.group(1).lower() not in {"defined", "declared", "called", "function", "method", "class", "module", "in", "from", "to", "and", "then", "call", "do", "fail", "workflow"}):
            continue
        if not any(a <= match.start(1) < b for a, b, _ in mentions):
            add(match.group(1), "method" if "." in match.group(1) else "function", match.start(1), match.end(1))
    for match in re.finditer(rf"\b{IDENTIFIER}\b|\b[a-f0-9]{{64}}\b", masked):
        token = match.group()
        if ("_" in token or any(c.isupper() for c in token[1:]) or "." in token or re.fullmatch(r"[a-f0-9]{64}", token)) and not any(
                a <= match.start() < b for a, b, _ in mentions):
            add(token, "symbol" if re.fullmatch(r"[a-f0-9]{64}", token) else "method" if "." in token else "function",
                match.start(), match.end())
    if not mentions and re.fullmatch(IDENTIFIER, base.strip()) and base.strip().casefold() not in _IGNORE:
        add(base.strip(), "function", 0, len(base))
    mentions.sort(key=lambda item: (item[0], item[1]))
    truncated = len(mentions) > 12
    mentions = mentions[:12]
    entities = [item[2] for item in mentions]
    events = []
    handled = set()
    for intent, pattern in _PATTERNS:
        for match in re.finditer(pattern, masked, re.I):
            position = match.start()
            left, right = clause_bounds(position)
            clause = masked[left:right]
            if (intent, left) in handled or any(a <= position < b for a, b in discovery):
                continue
            if (intent == "SYMBOL_LOCATION" and not any(left <= a < right for a, _, _ in mentions)
                    and not _PRONOUN.search(clause) and not re.search(r"\b(?:logic|code|feature|functionality)\b", clause, re.I)):
                continue
            if intent == "SYMBOL_LOCATION" and any(start == position for start, recognized in events if recognized in (
                    "DIRECT_CALLERS", "TEST_DISCOVERY", "MODULE_OVERVIEW", "DEPENDENCY_PATH", "FUNCTION_CONTRACT")):
                continue
            if intent == "SYMBOL_LOCATION" and re.match(r"(?:find|locate)\s+(?:the\s+)?(?:tests|callers|callees|dependencies)\b",
                                                        masked[position:], re.I):
                continue
            if intent == "FUNCTION_EXPLANATION" and re.search(r"\b(?:workflow|pipeline|request flow|end.to.end|flow through|contract|signature)\b", clause, re.I):
                continue
            events.append((position, intent))
            handled.add((intent, left))
    events.extend((left, "FEATURE_DISCOVERY") for left, _ in discovery)
    events.extend((left, "UNRESOLVED") for left, right in spans if _UNKNOWN_REQUEST.match(masked[left:right].strip()) and not any(left <= position < right for position, _ in events))
    if not events:
        intent = "SYMBOL_LOCATION" if entities else "FEATURE_DISCOVERY"
        # Existing imports/dependents planning maps to structural requirements,
        # not a new answering-engine route.
        intent = {"imports": "DEPENDENCY_PATH", "dependents": "DEPENDENCY_PATH"}.get(legacy.intent, intent)
        if not entities and _PRONOUN.search(masked):
            intent = "UNRESOLVED"
        events = [(0, intent)]
    events.sort(key=lambda event: event[0])
    for index, (position, intent) in enumerate(events):
        left, right = clause_bounds(position)
        if (intent == "SYMBOL_LOCATION" and re.search(r"\b(?:logic|code|functionality|feature)\b", masked[left:right], re.I)
                and not any(left <= start < right and entity.kind not in ("file", "module") for start, _, entity in mentions)):
            events[index] = (position, "FEATURE_DISCOVERY")
    if len(events) > 12:
        truncated = True
        events = events[:12]
    intents = list(dict.fromkeys(event[1] for event in events))
    if (intents == ["FEATURE_DISCOVERY"] and len(events) == 1) or (not entities and not _PRONOUN.search(masked) and set(intents) & {
            "WORKFLOW_EXPLANATION", "BEHAVIOR_INVESTIGATION", "FUNCTION_EXPLANATION"}):
        if len(mentions) >= 12:
            mentions = mentions[:11]
            entities = [item[2] for item in mentions]
            truncated = True
        feature = QueryEntity(kind="feature", name=_normalized_text(question))
        entities.insert(0, feature)
        mentions.insert(0, (0, len(question), feature))
    elif "FEATURE_DISCOVERY" in intents:
        for position, intent in events:
            if intent != "FEATURE_DISCOVERY":
                continue
            left, right = clause_bounds(position)
            if len(mentions) < 12:
                mentions.append((left, right, QueryEntity(kind="feature", name=_normalized_text(question[left:right]))))
            else:
                truncated = True
        mentions.sort(key=lambda item: (item[0], item[1]))
        entities = [item[2] for item in mentions]
    requirements, previous_indices = [], []
    for position, intent in events:
        if len(requirements) >= 12:
            truncated = True
            break
        order = len(requirements)
        left, right = clause_bounds(position)
        indices = [i for i, (start, end, _) in enumerate(mentions) if left <= start < right]
        # File entities qualify symbols in a clause rather than replace them.
        symbols = [i for i in indices if entities[i].kind not in ("file", "module")]
        files = [i for i in indices if entities[i].kind in ("file", "module")]
        if symbols and len(files) == 1:
            for index in symbols:
                if entities[index].file_path is None:
                    entities[index].file_path = entities[files[0]].name.removeprefix("./")
        if symbols and intent not in ("MODULE_OVERVIEW",):
            indices = symbols
        if intent == "DEPENDENCY_PATH" and re.search(r"\b(?:from|between|to)\b", masked, re.I) and len(events) == 1:
            indices = list(range(len(entities)))
        inherited = intent not in ("CODE_COMPARISON", "UNRESOLVED") and not indices and bool(previous_indices)
        if inherited:
            indices = list(previous_indices)
        comparison_inputs = inherited and (requirements[-1].intent == "CODE_COMPARISON" or
                                           requirements[-1].interpretation == "comparison_input_reference")
        requirements.append(IntentRequirement(intent=intent, order=order, entity_indices=indices,
            depends_on=[order-1] if inherited and not comparison_inputs else [], interpretation="direct_dependency_summary" if
            intent == "DEPENDENCY_PATH" and len(indices) < 2 else None))
        if intent == "UNRESOLVED" and _UNKNOWN_REQUEST.match(masked[left:right].strip()):
            requirements[-1].interpretation = "unrecognized_requirement"
        if inherited and len(indices) > 1 and not re.search(r"\b(?:their|both|them)\b", masked[left:right], re.I):
            requirements[-1].interpretation = "ambiguous_local_reference"
        elif comparison_inputs:
            requirements[-1].interpretation = "comparison_input_reference"
            if intent == "DEPENDENCY_PATH" and len(indices) > 1 and not re.search(r"\b(?:path|from|between|to)\b", masked[left:right], re.I):
                # Two inputs requested for summaries are not a source/target path.
                # Emit existing single-symbol summary contracts; router unchanged.
                requirements[-1].entity_indices = [indices[0]]
                for index in indices[1:]:
                    if len(requirements) >= 12:
                        truncated = True
                        break
                    requirements.append(requirements[-1].model_copy(update={"order": len(requirements), "entity_indices": [index]}))
        if indices:
            previous_indices = indices
    result = NormalizedQuery(intents=intents, primary_intent=intents[0], secondary_intents=intents[1:],
        original_question=question, normalized_question=_normalized_text(question), entities=entities,
        requirements=requirements, legacy_plan=asdict(legacy), requires_explanation=bool(set(intents) & {
            "FUNCTION_EXPLANATION", "WORKFLOW_EXPLANATION", "BEHAVIOR_INVESTIGATION", "FUNCTION_CONTRACT"}))
    if truncated:
        result.status = "partial"
        result.warnings.append("Recognition reached its 12-entity/requirement bound; omitted targets require clarification")
    return result


_CAPABILITIES = {
    "SYMBOL_LOCATION": ("graph.find_definition",), "DIRECT_CALLERS": ("graph.find_callers",),
    "DIRECT_CALLEES": ("graph.find_callees",), "DEPENDENCY_PATH": ("rules.dependency_summary",),
    "FEATURE_DISCOVERY": ("semantic.hybrid_search",), "FUNCTION_CONTRACT": ("rules.function_contract",),
    "CHANGE_IMPACT": ("rules.change_impact",), "MODULE_OVERVIEW": ("rules.module_overview",),
    "TEST_DISCOVERY": ("rules.reference_test_analysis",),
    "FUNCTION_EXPLANATION": ("metadata.trace", "existing_rag.answer_question"),
    "WORKFLOW_EXPLANATION": ("graph.find_paths", "existing_rag.answer_question"),
    "BEHAVIOR_INVESTIGATION": ("metadata.trace", "existing_rag.answer_question"), "CODE_COMPARISON": (),
    "UNRESOLVED": (),
}


class QueryUnderstandingEngine:
    """Caller-owned pinned normalizer, with read-only metadata resolution only."""
    max_candidates = 25
    max_execution_ms = 5000

    def __init__(self, store, view, context):
        self.store, self.context = store, context
        self.database = Path(view["data_dir"]) / context.repository_id / "metadata.db"
        self._active = False

    @classmethod
    @contextmanager
    def pinned(cls, store):
        with store.pin_view() as view:
            engine = cls(store, view, SnapshotContext.from_pinned_view(view, store.repo_id))
            engine._active = True
            try:
                yield engine
            finally:
                engine._active = False

    def normalize(self, request: QueryRequest, conversation: QueryContext | None = None) -> NormalizedQuery:
        # Malformed requests fail at the shared strict input boundary.
        started = monotonic()
        request = QueryRequest.model_validate(request.model_dump() if isinstance(request, QueryRequest) else request, strict=True)
        if conversation is not None:
            conversation = QueryContext.model_validate(conversation.model_dump() if isinstance(conversation, QueryContext) else conversation, strict=True)
        parsed = parse_question(request.question)
        parsed.repository_id, parsed.snapshot_id, parsed.depth = self.context.repository_id, self.context.snapshot_id, request.depth
        def reject(status, code, message):
            parsed.status, parsed.error = status, EngineError(code=code, message=message)
            parsed.entities, parsed.requirements, parsed.context_used = [], [], []
            parsed.warnings.append(message)
            parsed.normalization_ms = int((monotonic() - started) * 1000)
            return parsed
        if not self._active or request.snapshot_id not in (None, self.context.snapshot_id):
            return reject("stale_index", "stale_index", "Question is outside its pinned generation")
        if request.repository_id != self.context.repository_id:
            return reject("error", "invalid_operation", "Question belongs to another repository")
        try:
            self.context.validate_request(request)
            if not self.store.matches(self.context.snapshot_id):
                raise StaleIndexError("Pinned saved sources changed")
            reader = _Run(SimpleNamespace(database=self.database), self.context, started + self.max_execution_ms / 1000)
            context = _Understanding(self, reader, parsed, request, conversation)
            context.resolve()
            context.capabilities()
            for entity in parsed.entities:
                for previous in ([entity.evidence] if entity.evidence else []) + [candidate.evidence for candidate in entity.candidates]:
                    reader.check()
                    current = Evidence.from_trace(trace(self.database, self.context.repository_id, previous.evidence_id),
                        self.context, evidence_type=previous.evidence_type, resolution=previous.resolution)
                    if current != previous:
                        raise StaleIndexError("Canonical resolution proof changed")
            if not self.store.matches(self.context.snapshot_id):
                raise StaleIndexError("Saved sources changed during normalization")
            reader.check()
            parsed.normalization_ms = int((monotonic() - started) * 1000)
            return NormalizedQuery.model_validate(parsed.model_dump(), strict=True)
        except _BudgetExceeded:
            return reject("partial", "budget_exceeded", "Read-only query resolution exceeded its budget")
        except sqlite3.OperationalError as error:
            return reject("partial" if "interrupted" in str(error) else "error",
                          "budget_exceeded" if "interrupted" in str(error) else "unavailable", "Metadata lookup is unavailable or interrupted")
        except sqlite3.Error:
            return reject("error", "unavailable", "Metadata lookup is unavailable")
        except (ContractError, ValueError, KeyError, TypeError, OSError):
            return reject("stale_index", "stale_index", "Resolution evidence is stale, invalid, or mismatched")


class _Understanding:
    def __init__(self, engine, reader, parsed, request, conversation):
        self.engine, self.reader, self.result, self.request = engine, reader, parsed, request
        self.conversation = conversation
        self.invalid_context = False
        if conversation is not None:
            if (conversation.repository_id != engine.context.repository_id or conversation.snapshot_id != engine.context.snapshot_id
                    or conversation.active_file is not None and conversation.active_file not in engine.context.indexed_files):
                self.invalid_context = True
                self.result.warnings.append("Conversation context is stale or outside the pinned repository; it was not used")
        self.active_file = request.active_file or (conversation.active_file if conversation and not self.invalid_context else None)

    def candidate(self, node):
        identifier = node["symbol_id"] or node["id"]
        proof = Evidence.from_trace(trace(self.engine.database, self.engine.context.repository_id, identifier), self.engine.context,
            evidence_type="symbol_definition" if node["symbol_id"] else "source_range", resolution="syntax_only")
        kind = "module" if node["kind"] == "Module" else "file" if node["kind"] == "File" else "class" if node["kind"] == "Class" else "symbol" if node["kind"] in ("Interface", "Type") else "method" if node["kind"] == "Method" else "function"
        return EntityCandidate(entity_id=identifier, name=node["label"], kind=kind, symbol_id=node["symbol_id"],
                               file_path=node["relative_path"], evidence=proof)

    def assign(self, entity, candidates, missing="not_found"):
        unique = {candidate.entity_id: candidate for candidate in candidates}
        values = sorted(unique.values(), key=lambda item: (item.file_path, item.evidence.start_line or 0, item.entity_id))
        if len(values) == 1:
            value = values[0]
            entity.kind, entity.entity_id, entity.symbol_id = value.kind, value.entity_id, value.symbol_id
            entity.file_path, entity.file_id, entity.evidence = value.file_path, value.evidence.file_id, value.evidence
            entity.resolution = "resolved"
        elif values:
            entity.resolution = "ambiguous"
            entity.candidates = values[:self.engine.max_candidates]
            entity.candidates_truncated = len(values) > self.engine.max_candidates
            entity.reason = "Choose a canonical ID or an exact file scope; no candidate was selected"
        else:
            entity.resolution, entity.reason = missing, "No canonical indexed target matches this textual reference"

    def file(self, entity):
        try:
            name = relative_path(entity.name.removeprefix("./"))
        except ValueError:
            entity.reason = "Path is not a normalized repository-relative scope"
            return
        if entity.kind == "module":
            if Path(name).suffix and Path(name).suffix not in LANGUAGES:
                entity.resolution, entity.reason = "unsupported", "Resolved graph modules are JS/TS only"
                return
            node, candidates, status = self.reader.module(name, self.engine.max_candidates + 1)
            nodes = [node] if node else [self.reader.node(candidate.target_node_id) for candidate in candidates]
            self.assign(entity, [self.candidate(node) for node in nodes])
            return
        paths = sorted(path for path in self.engine.context.indexed_files if path == name or path.endswith("/" + name))
        if name in paths:
            paths = [name]
        candidates = []
        for path in paths[:self.engine.max_candidates + 1]:
            rows = self.reader.read("SELECT * FROM graph_nodes WHERE repository_id=? AND kind='File' AND relative_path=? LIMIT 1", (path,))
            if rows:
                candidates.append(self.candidate(rows[0]))
            else:
                rows = self.reader.read("SELECT id FROM code_chunks WHERE repository_id=? AND file_id=(SELECT id FROM files WHERE repository_id=? AND relative_path=?) ORDER BY chunk_index LIMIT 1",
                                        (self.engine.context.repository_id, path))
                if rows:
                    proof = Evidence.from_trace(trace(self.engine.database, self.engine.context.repository_id, rows[0]["id"]),
                        self.engine.context, evidence_type="code_chunk", resolution="not_applicable")
                    candidates.append(EntityCandidate(entity_id=proof.file_id, name=path, kind="file", file_path=path, evidence=proof))
        self.assign(entity, candidates)

    def symbol(self, entity, selected=None):
        scope = entity.file_path or self.active_file
        if scope and Path(scope).suffix not in LANGUAGES:
            entity.resolution, entity.reason = "unsupported", "Canonical symbol analysis is JS/TS only; use semantic discovery for other languages"
            return
        scopes = [scope]
        # An ambiguous textual file is not silently converted into an arbitrary scope.
        files = [item for item in self.result.entities if item.kind in ("file", "module")]
        if scope and scope not in self.engine.context.indexed_files:
            file_entity = next((item for item in files if item.name.removeprefix("./") == scope), None)
            if file_entity and file_entity.resolution == "ambiguous":
                scopes = [candidate.file_path for candidate in file_entity.candidates]
            elif file_entity and file_entity.resolution == "resolved":
                scopes = [file_entity.file_path]
            else:
                entity.resolution, entity.reason = "unresolved_text", "File scope is missing or ambiguous"
                return
        values = []
        for path in scopes:
            parameters = SymbolParameters(reference=None if selected else entity.name, selected_symbol_id=selected,
                                          file_path=path, limit=self.engine.max_candidates + 1)
            node, candidates, status = self.reader.symbol(parameters)
            nodes = [node] if node else [self.reader.node(candidate.target_node_id) for candidate in candidates]
            values.extend(self.candidate(node) for node in nodes)
        self.assign(entity, values)

    def contextual_entity(self):
        conversation = self.conversation if not self.invalid_context else None
        current_selection = self.request.selection or (EditorSelection(start=self.request.cursor, end=self.request.cursor)
                                                       if self.request.cursor else None)
        selected = self.request.selected_symbol_id or (conversation.selected_symbol_id if conversation and current_selection is None else None)
        if selected:
            origin = "selected_symbol"
            entity = QueryEntity(kind="symbol", name=selected, origin=origin)
            self.symbol(entity, selected)
            self.result.context_used.append(origin)
            return entity
        selection = current_selection or (conversation.selection if conversation else None)
        if selection and self.active_file:
            entity = QueryEntity(kind="selection", name="editor selection", file_path=self.active_file,
                                 selection=selection, origin="selection")
            if Path(self.active_file).suffix not in LANGUAGES:
                entity.resolution, entity.reason = "unsupported", "Selection-to-symbol resolution is JS/TS only"
                return entity
            file = QueryEntity(kind="file", name=self.active_file)
            self.file(file)
            if file.resolution != "resolved":
                entity.reason = "Selection file has no uniquely verified source"
                return entity
            lines = (self.engine.store.root / self.active_file).read_text().splitlines()
            if any(position.line > len(lines) or position.column > len(lines[position.line-1]) + 1
                   for position in (selection.start, selection.end)):
                entity.reason = "Selection is outside saved-source bounds; unsaved editor content is not indexed"
                return entity
            rows = self.reader.read("""SELECT id FROM symbols WHERE repository_id=? AND relative_path=?
                AND (start_line<? OR start_line=? AND start_column<=?)
                AND (end_line>? OR end_line=? AND end_column>=?) ORDER BY start_line,end_line,id LIMIT ?""",
                (self.active_file, selection.start.line, selection.start.line, selection.start.column,
                 selection.end.line, selection.end.line, selection.end.column, self.engine.max_candidates + 1))
            values = []
            for row in rows:
                node, _, _ = self.reader.symbol(SymbolParameters(selected_symbol_id=row["id"]))
                values.append(self.candidate(node))
            self.assign(entity, values, "unresolved_text")
            self.result.context_used.append("cursor" if self.request.cursor and self.request.selection is None else "selection")
            return entity
        if conversation and conversation.previous_target:
            previous = conversation.previous_target
            proof = previous.evidence
            current = Evidence.from_trace(trace(self.engine.database, self.engine.context.repository_id, proof.evidence_id), self.engine.context,
                evidence_type=proof.evidence_type, resolution=proof.resolution)
            if current != proof:
                raise StaleIndexError("Previously resolved target no longer matches the pinned generation")
            entity = QueryEntity(kind=previous.kind, name=previous.name, file_path=previous.file_path, origin="previous_target")
            if previous.symbol_id:
                self.symbol(entity, previous.symbol_id)
            else:
                self.file(entity)
            self.result.context_used.append("previous_target")
            return entity
        if self.active_file and any(item.intent == "MODULE_OVERVIEW" for item in self.result.requirements):
            entity = QueryEntity(kind="module", name=self.active_file, origin="active_file")
            self.file(entity)
            self.result.context_used.append("active_file")
            return entity
        return QueryEntity(kind="symbol", name="this/it", reason="No fresh, explicit context identifies the textual target")

    def resolve(self):
        if self.result.intents == ["UNRESOLVED"] and self.conversation and not self.invalid_context and not any(
                item.interpretation == "unrecognized_requirement" for item in self.result.requirements):
            if len(self.conversation.previous_intents) == 1 and self.conversation.previous_intents[0] != "UNRESOLVED":
                intent = self.conversation.previous_intents[0]
                self.result.intents = [intent]
                self.result.primary_intent = intent
                self.result.requirements[0].intent = intent
                self.result.context_used.append("previous_intents")
        for entity in self.result.entities:
            if entity.kind in ("file", "module"):
                self.file(entity)
        for entity in self.result.entities:
            if entity.kind == "feature" and entity.file_path:
                scoped = next((item for item in self.result.entities if item.kind in ("file", "module")
                               and item.name.removeprefix("./") == entity.file_path and item.resolution == "resolved"), None)
                if scoped:
                    entity.file_path = scoped.file_path
            if entity.kind not in ("file", "module", "feature"):
                if self.active_file and entity.file_path is None:
                    self.result.context_used.append("active_file")
                self.symbol(entity)
                if (entity.resolution == "not_found" and entity.name.islower() and "_" not in entity.name
                        and "." not in entity.name and not re.search(r"[`'\"]|\b(?:function|method|class|symbol)\b", self.request.question, re.I)
                        and self.result.intents == ["WORKFLOW_EXPLANATION"]):
                    entity.kind, entity.name = "feature", self.result.normalized_question
                    entity.resolution, entity.reason = "unresolved_text", "Unknown workflow description retained for discovery, not a canonical symbol"
        missing = [item for item in self.result.requirements if not item.entity_indices and item.interpretation != "unrecognized_requirement"]
        if missing:
            entity = self.contextual_entity()
            index = len(self.result.entities)
            if index < 12:
                self.result.entities.append(entity)
                for requirement in missing:
                    requirement.entity_indices = [index]
        self.result.context_used = list(dict.fromkeys(self.result.context_used))
        self.result.scope = "selected_symbol" if any(item.symbol_id and item.origin != "text" for item in self.result.entities) else "active_file" if self.active_file else "repository"
        self.result.unresolved_references = [item.name for item in self.result.entities if item.resolution in ("not_found", "unresolved_text") and item.kind != "feature"]

    def capabilities(self):
        known = {"graph." + name for name in GRAPH_OPERATIONS} | {"semantic." + name for name in SEMANTIC_OPERATIONS} | {"rules." + name for name in RULES} | {"metadata.trace", "existing_rag.answer_question"}
        for requirement in self.result.requirements:
            intent = requirement.intent
            requirement.required_capabilities = list(_CAPABILITIES[intent])
            targets = [self.result.entities[index] for index in requirement.entity_indices]
            if intent == "SYMBOL_LOCATION" and targets and all(target.kind in ("file", "module") for target in targets):
                requirement.required_capabilities = ["metadata.trace"]
            if intent == "DEPENDENCY_PATH" and len(targets) >= 2:
                requirement.required_capabilities = ["graph.find_dependency_paths" if all(
                    target.kind in ("file", "module") for target in targets) else "graph.find_paths"]
            requirement.support = "supported"
            if requirement.interpretation == "ambiguous_local_reference":
                requirement.support = "unresolved"
                requirement.limitations.append("A singular local pronoun refers to multiple targets; choose a target")
            elif intent == "UNRESOLVED":
                requirement.support = "unresolved"
                requirement.limitations.append("This explicit request has no declared query operation; clarify or rephrase it" if
                    requirement.interpretation == "unrecognized_requirement" else
                    "The follow-up has no explicit intent and no unique fresh previous intent; clarification is required")
            elif intent == "CODE_COMPARISON":
                requirement.support = "unsupported"
                requirement.limitations.append("No dedicated comparison operation is declared by the existing engines")
            elif intent == "FEATURE_DISCOVERY":
                requirement.limitations.append("Semantic similarity is discovery evidence, not a structural dependency")
            elif targets and all(target.kind == "feature" for target in targets) and intent in (
                    "FUNCTION_EXPLANATION", "WORKFLOW_EXPLANATION", "BEHAVIOR_INVESTIGATION"):
                requirement.support = "partial"
                requirement.required_capabilities.append("semantic.hybrid_search")
                requirement.limitations.append("Natural-language functionality is a discovery description, not a resolved canonical symbol")
            elif any(target.resolution == "unsupported" or target.file_path and Path(target.file_path).suffix not in LANGUAGES for target in targets):
                requirement.support = "partial" if intent in ("FUNCTION_EXPLANATION", "WORKFLOW_EXPLANATION", "BEHAVIOR_INVESTIGATION") else "unsupported"
                requirement.limitations.append("JS/TS graph/static analysis is unavailable for this language; semantic code discovery remains separate")
            elif not targets or any(target.resolution in ("ambiguous", "not_found", "unresolved_text") for target in targets):
                requirement.support = "unresolved"
                requirement.limitations.append("Resolve the textual target or choose an ambiguity candidate before future execution")
            elif intent in ("DIRECT_CALLERS", "DIRECT_CALLEES", "FUNCTION_CONTRACT", "TEST_DISCOVERY") and any(
                    target.symbol_id is None for target in targets):
                requirement.support = "unsupported"
                requirement.limitations.append("This declared capability requires a canonical symbol, not a file or module")
            elif intent == "FUNCTION_CONTRACT" and any(target.kind not in ("function", "method") for target in targets):
                requirement.support = "unsupported"
                requirement.limitations.append("The function-contract rule does not analyze non-function declarations")
            if intent in ("FUNCTION_CONTRACT", "CHANGE_IMPACT", "TEST_DISCOVERY", "FUNCTION_EXPLANATION", "WORKFLOW_EXPLANATION", "BEHAVIOR_INVESTIGATION"):
                if requirement.support == "supported":
                    requirement.support = "partial"
                requirement.limitations.append({
                    "FUNCTION_CONTRACT": "Declared contracts only; complete return/exception behavior is unavailable",
                    "CHANGE_IMPACT": "Static impact locations are hypotheses, not guaranteed breakage",
                    "TEST_DISCOVERY": "Static test references do not establish execution or coverage",
                    "WORKFLOW_EXPLANATION": "No workflow/runtime engine is declared; existing bounded static paths and RAG offer partial support",
                    "BEHAVIOR_INVESTIGATION": "Static evidence cannot establish runtime causality without runtime observations",
                    "FUNCTION_EXPLANATION": "Existing grounded RAG is available but not invoked or claim-verified here",
                }[intent])
            if intent in ("DIRECT_CALLERS", "DIRECT_CALLEES", "DEPENDENCY_PATH"):
                requirement.limitations.append("Known static graph relationships do not establish runtime behavior")
            if set(requirement.required_capabilities) - known:
                requirement.support = "unsupported"
                requirement.limitations.append("A required capability is not declared by the current engines")
        if any(item.resolution == "ambiguous" for item in self.result.entities) or any(
                item.interpretation == "ambiguous_local_reference" for item in self.result.requirements):
            self.result.status = "ambiguous"
        elif any(item.support == "unresolved" for item in self.result.requirements):
            self.result.status = "partial" if any(item.support in ("supported", "partial") for item in self.result.requirements) else "unresolved"
        elif all(item.support == "unsupported" for item in self.result.requirements):
            self.result.status = "unsupported"
        elif any(item.support != "supported" for item in self.result.requirements) or self.invalid_context or self.result.warnings:
            self.result.status = "partial"
