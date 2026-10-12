"""Source-aware semantic units, with exact saved-source line references.

JS/TS uses the existing syntax parser. Python uses its standard AST for chunk
boundaries only; this does not invent Python graph symbols or relationships.
Other languages retain a clearly labelled text fallback.
"""
import ast
from dataclasses import dataclass
import hashlib
import json
from pathlib import PurePosixPath
import re
import uuid

from knowledge_base.identity import relative_path
from knowledge_base.parser import LANGUAGES, parse_source

CHUNKING_VERSION = 1


@dataclass
class CodeChunk:
    id: str
    text: str
    start_line: int
    end_line: int
    language: str
    module: str
    kind: str
    symbol_key: str = ''
    symbol_kind: str = ''
    name: str = ''
    qualified_name: str = ''
    signature: str = ''
    comments: str = ''

    def embedding_text(self, path):
        # Short context leaves the bulk of the embedding window for source.
        terms = ' '.join(re.sub(r'([a-z0-9])([A-Z])',r'\1 \2',self.qualified_name).replace('_',' ').split())
        context = f'File: {path}\nLanguage: {self.language}\nSymbol: {self.qualified_name}\nKind: {self.symbol_kind or self.kind}\nSignature: {self.signature[:200]}\nMeaning terms: {terms}\nComments: {self.comments[:300]}\n'
        return context+self.text


def _python_units(source):
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1]+len(line))
    units = []

    def position(line,column):
        return offsets[line-1]+len(lines[line-1].encode()[:column].decode('utf-8'))

    def visit(node,parent='',parent_key=None,parent_kind=''):
        if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):
            qualified = f'{parent}.{node.name}' if parent else node.name
            kind = 'class' if isinstance(node,ast.ClassDef) else 'method' if parent_kind == 'class' else 'function'
            start = position(node.lineno,node.col_offset)
            end = position(node.end_lineno,node.end_col_offset)
            if node.decorator_list:
                start = offsets[node.decorator_list[0].lineno-1]
            signature = source[position(node.lineno,node.col_offset):offsets[node.lineno]].strip()
            key = hashlib.sha256(json.dumps([parent_key,kind,qualified]).encode()).hexdigest()
            units.append(dict(start=start,end=end,name=node.name,qualified_name=qualified,kind=kind,
                              key=key,parent_key=parent_key,signature=signature,comments=ast.get_docstring(node) or ''))
            parent,parent_key,parent_kind = qualified,key,kind
        for child in ast.iter_child_nodes(node):
            visit(child,parent,parent_key,parent_kind)
    visit(tree)
    return units


def _js_units(source, path, repo):
    raw = source.encode()
    parsed = parse_source(raw,suffix=PurePosixPath(path).suffix.lower(),repository_id=repo,relative_path=path)
    units = []
    for symbol in parsed.symbols:
        parameters = ', '.join((p['name'] or '?')+(p['type'] or '') for p in symbol['parameters'])
        start = len(raw[:symbol['start_byte']].decode())
        line_start = source.rfind('\n',0,start)+1
        if re.fullmatch(r'\s*export(?:\s+default)?\s+',source[line_start:start]):
            start = line_start
        # Attached comments travel with the symbol's embedding context. They
        # are not separate search hits that compete with their own function.
        attached = next((c for c in reversed(parsed.comments)
            if c['text'] == symbol['doc_comment'] and c['end_byte'] <= symbol['start_byte']
            and not source[len(raw[:c['end_byte']].decode()):start].strip()),None)
        units.append(dict(start=start,end=len(raw[:symbol['end_byte']].decode()),
            name=symbol['name'],qualified_name=symbol['qualified_name'],kind=symbol['kind'],key=symbol['id'],
            parent_key=symbol['parent_symbol_id'],signature=f"{symbol['qualified_name']}({parameters}) {symbol['return_annotation'] or ''}",
            comments=symbol['doc_comment'] or '',
            comment_start=len(raw[:attached['start_byte']].decode()) if attached else start))
    return units


def chunk_source(source, *, path, repo_id, language, max_chars=1200, overlap=150):
    relative_path(path)
    if max_chars < 64 or not 0 <= overlap < max_chars:
        raise ValueError('Chunk size must be >=64 and overlap smaller than chunk size')
    module = str(PurePosixPath(path).with_suffix(''))
    suffix = PurePosixPath(path).suffix.lower()
    units = []
    fallback = False
    try:
        if suffix in LANGUAGES:
            units = _js_units(source,path,repo_id)
        elif suffix == '.py':
            units = _python_units(source)
    except (SyntaxError,ValueError,UnicodeError):
        fallback = True
    chunks = []
    ordinals = {}

    def emit(start,end,key,kind,unit=None):
        ordinal = 0
        while start < end:
            stop = min(end,start+max_chars)
            if stop < end:
                boundary = source.rfind('\n',start+max_chars//2,stop)
                if boundary >= start:
                    stop = boundary+1
            left,right = start,stop
            while left < right and source[left].isspace(): left += 1
            while right > left and source[right-1].isspace(): right -= 1
            if right > left:
                repeated = ordinals.get((key,ordinal),0)
                ordinals[key,ordinal] = repeated+1
                identity = f'{repo_id}::{path}::{key}::{ordinal}::{repeated}'
                identifier = str(uuid.uuid5(uuid.UUID('a3f1b2c4-d5e6-7890-abcd-ef1234567890'),identity))
                details = unit or {}
                chunks.append(CodeChunk(identifier,source[left:right],source.count('\n',0,left)+1,
                    source.count('\n',0,right-1)+1,language,module,kind,
                    details.get('key',''),details.get('kind',''),details.get('name',''),
                    details.get('qualified_name',''),details.get('signature',''),details.get('comments','')))
                ordinal += 1
            if stop == end:
                break
            # Child pieces stay within their parent declaration.
            start = max(start+1,stop-overlap)

    if suffix in ('.md','.markdown','.json','.yaml','.yml','.toml','.ini'):
        if suffix == '.json':
            properties = list(re.finditer(r'(?m)^([ \t]*)"[^"\n]+"\s*:',source))
            depth = min((len(m.group(1)) for m in properties),default=0)
            starts = [m.start() for m in properties if len(m.group(1)) == depth]
        else:
            pattern = r'(?m)^#{1,6}\s+' if suffix in ('.md','.markdown') else r'(?m)^(?:[A-Za-z_][\w.-]*\s*:|\[[^\]\n]+\])'
            starts = [m.start() for m in re.finditer(pattern,source)]
        boundaries = sorted({0,len(source),*starts})
        for start,end in zip(boundaries,boundaries[1:]):
            label = source[start:source.find('\n',start) if '\n' in source[start:] else end].strip()
            emit(start,end,'section:'+label,'documentation' if suffix in ('.md','.markdown') else 'configuration',
                 dict(name=label,qualified_name=label))
        return chunks

    if units:
        # Small classes are useful units; methods remain separately searchable.
        # Large classes use their methods plus class-level gaps, never one blob.
        for unit in units:
            if unit['kind'] == 'class' and unit['end']-unit['start'] > max_chars:
                children = sorted([u for u in units if u['parent_key'] == unit['key']],key=lambda u:u['start'])
                cursor = unit['start']
                for child in children:
                    if cursor < child['start']:
                        emit(cursor,child['start'],unit['key']+':class_context','class_context',unit)
                    cursor = max(cursor,child['end'])
                if cursor < unit['end']:
                    emit(cursor,unit['end'],unit['key']+':class_context','class_context',unit)
            else:
                emit(unit['start'],unit['end'],unit['key'], 'symbol_child' if unit['end']-unit['start'] > max_chars else 'symbol',unit)
        # Imports, module comments and executable module-level statements must
        # not disappear just because a file also contains functions.
        outer = sorted([u for u in units if u['parent_key'] is None],key=lambda u:u['start'])
        cursor = 0
        for unit in outer:
            gap_end = unit.get('comment_start',unit['start'])
            if cursor < gap_end:
                emit(cursor,gap_end,'module:'+str(len(chunks)),'module_context')
            cursor = max(cursor,unit['end'])
        if cursor < len(source):
            emit(cursor,len(source),'module:tail','module_context')
    else:
        emit(0,len(source),'module','syntax_fallback' if fallback else 'module_context')
    return sorted(chunks,key=lambda c:(c.start_line,c.end_line,c.kind,c.id))
