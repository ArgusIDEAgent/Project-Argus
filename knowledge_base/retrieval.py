"""Deterministic local retrieval. No answer LLM or similarity-invented edges.

Providers return the existing source-evidence dictionaries. This interface adds
query planning, exact identity lookup, bounded structural traversal, rank fusion
and a final independent provenance check, without owning or changing any index.
"""
from collections import deque
from contextlib import closing
from dataclasses import asdict, dataclass
import hashlib
from pathlib import Path, PurePosixPath
import re
import sqlite3
import unicodedata

from knowledge_base.graph import KnowledgeGraph
from knowledge_base.identity import relative_path
from knowledge_base.metadata import trace
from knowledge_base.semantic import matches_filters

RRF_K = 60
MAX_CANDIDATES = 100
MAX_GRAPH_DEPTH = 3
STOP_WORDS = frozenset('where what how why which who is are the a an of to in on for and or does do can i we it this that implemented implementation logic code function method file module explain describe tell me checked raised handled'.split())
IDENTIFIER = r'(?:[^\W\d]|[$])[\w$]*(?:\.(?:[^\W\d]|[$])[\w$]*)*'
PATH = r'[\w@./-]+\.(?:[cm]?[jt]sx?|py|md|markdown|json|ya?ml|toml|ini|c|cpp|h|java|go|rs|rb)'


@dataclass
class QueryPlan:
    query: str
    intent: str = 'explore'
    reference: str = ''
    file: str = ''
    keywords: str = ''
    module: str = ''


def plan_query(query):
    normalized = ' '.join(unicodedata.normalize('NFKC',query).split())
    if len(normalized)>2000 or not normalized:
        raise ValueError('Query must contain 1–2000 characters')
    if normalized[:1] in ('"',"'",'`') and normalized[-1:] == normalized[:1]:
        normalized = normalized[1:-1].strip()
    if not normalized:
        raise ValueError('Query must contain searchable text')
    plan = QueryPlan(normalized)
    qualifier = r'(?:(?:the|function|method|symbol|file|module)\s+)*'
    patterns = (
        ('callers',rf'\b(?:who calls|what calls|callers of|calls to)\s+{qualifier}[`\'\"]?({IDENTIFIER})'),
        ('callees',rf'\b(?:callees of|what does)\s+{qualifier}[`\'\"]?({IDENTIFIER})(?:[`\'\"]?\s+call\b)?'),
        ('impact',rf'\b(?:impact of|affected by changing|(?:change|modify|changing|modifying))\s+{qualifier}[`\'\"]?({IDENTIFIER})'),
        ('imports',rf'\b(?:imports of|dependencies of|what does)\s+{qualifier}[`\'\"]?({PATH}|{IDENTIFIER})(?:[`\'\"]?\s+import\b)?'),
        ('dependents',rf'\b(?:dependents of|(?:depends?|depend) on)\s+{qualifier}[`\'\"]?({PATH}|{IDENTIFIER})'),
    )
    for intent,pattern in patterns:
        match = re.search(pattern,normalized,re.I)
        if not match:
            continue
        # "what does X do?" is not a callee/dependency query.
        if intent in ('callees','imports') and match.group(0).lower().startswith('what does'):
            if not re.search(r'\b'+('call' if intent=='callees' else 'import')+r'\b',normalized,re.I):
                continue
        if intent=='impact' and not re.search(r'\b(?:impact|breaks?|affect(?:ed|s)?|happens|changes\s+if|if\s+(?:I|we|you)\s+(?:change|modify))\b',normalized,re.I):
            continue
        plan.intent,plan.reference = intent,match.group(1)
        break
    file_match = re.search(PATH,normalized,re.I)
    if file_match:
        plan.file = file_match.group(0)
        if plan.file.startswith('./'):
            original = plan.file
            plan.file = plan.file[2:]
            if plan.reference==original: plan.reference=plan.file
        if plan.intent=='impact':
            plan.intent,plan.reference = 'dependents',plan.file
    module_match = re.search(r'\bmodule\s+[`\'\"]?([\w./-]+)',normalized,re.I)
    if module_match:
        plan.module = module_match.group(1).rstrip('.').removeprefix('./')
    if not plan.reference:
        quoted = re.search(r'[`\'"]('+IDENTIFIER+r')[`\'"]',normalized)
        named = next((m for m in re.finditer(r'\b(?:explain|describe|definition of|where is|defined|function|method|symbol)\s+(?:(?:the|function|method|symbol|file|module)\s+)*[`\'"]?('+IDENTIFIER+r')',normalized,re.I)
                      if m.group(1).lower() not in STOP_WORDS),None)
        if (re.fullmatch(IDENTIFIER,normalized) or re.fullmatch(r'[a-f0-9]{64}',normalized)) and normalized.lower() not in STOP_WORDS:
            plan.reference = normalized
        elif quoted:
            plan.reference = quoted.group(1)
        elif named and named.group(1).lower() not in STOP_WORDS:
            plan.reference = named.group(1)
        if re.search(r'\b(?:explain|describe)\b',normalized,re.I):
            plan.intent = 'explain'
        elif re.search(r'\b(?:defined|definition)\b',normalized,re.I):
            plan.intent = 'definition'
    plan.keywords = ' '.join(t for t in re.findall(r'\w+',normalized) if t.lower() not in STOP_WORDS)
    return plan


def evidence_key(item):
    # Independent channel hits for the same source unit fuse once. Separate
    # children and relationship call sites retain distinct evidence ranges.
    return (item['path'],item['startLine'],item['endLine'],item['sourceHash'])


def fuse_rankings(rankings, *, limit=MAX_CANDIDATES):
    """RRF combines rank positions, never BM25/cosine/identity raw scores."""
    merged = {}
    for method,items in rankings.items():
        seen = set()
        for rank,item in enumerate(items,1):
            key = evidence_key(item)
            if key in seen:
                continue
            seen.add(key)
            if key not in merged:
                merged[key] = {**item,'fusionScore':0.,'retrievalMethods':[],'rankingSignals':{}}
            result = merged[key]
            result['fusionScore'] += 1/(RRF_K+rank)
            result['retrievalMethods'].append(method)
            result['rankingSignals'][method] = {'rank':rank,'rrf':1/(RRF_K+rank)}
            if method=='graph':
                for field in ('graphEdges','graphDistance','graphContext','relationship','resolution'):
                    if field in item: result[field] = item[field]
    return sorted(merged.values(),key=lambda r:(-r['fusionScore'],r['path'],r['startLine'],r['entityId']))[:limit]


class HybridRetriever:
    def __init__(self,root,repo_id,data_dir,*,exact_search,keyword_search,semantic_search):
        self.root = Path(root).resolve(strict=True)
        self.repo_id = repo_id
        self.database = Path(data_dir)/repo_id/'metadata.db'
        self.exact_search = exact_search
        self.keyword_search = keyword_search
        self.semantic_search = semantic_search
        self.warnings = []

    def _read(self,sql,args=()):
        if not self.database.is_file():
            return []
        with closing(sqlite3.connect(self.database.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute(sql,args)]

    def _symbol_result(self,row):
        info = trace(self.database,self.repo_id,row['id'])
        return {**{k:info[k] for k in ('entityId','fileId','symbolId','snapshotId','sourceOrigin','schemaVersion',
            'path','startLine','endLine','sourceHash')},'name':row['qualified_name'],
            'language':row['language'],'module':str(PurePosixPath(info['path']).with_suffix('')),
            'symbolType':row['kind'],'reason':'Exact source definition; not proof of runtime behavior',
            'resolution':'syntax_only','confidence':1.,'revision':'latest','callers':[],'tests':[]}

    def _lookup(self,reference,filters,file=''):
        def restrictions(columns):
            clauses,parameters = [],[]
            for key,column in columns.items():
                values = filters.get(key)
                if values:
                    clauses.append(f'{column} IN ({",".join("?" for _ in values)})')
                    parameters.extend(values)
            if file:
                column = columns['files']
                clauses.append(f"({column}=? OR substr({column},-length(?)-1)='/' || ?)")
                parameters.extend((file,file,file))
            return ''.join(' AND '+clause for clause in clauses),parameters
        # Apply scope before the candidate bound: a common name elsewhere must
        # not hide an explicitly filtered definition later in repository order.
        scope,args = restrictions({'files':'s.relative_path','languages':'f.language','symbolTypes':'s.kind',
            'modules':"substr(f.relative_path,1,length(f.relative_path)-length(coalesce(f.extension,'')))"})
        rows = self._read(f'''SELECT s.*,f.language FROM symbols s JOIN files f
            ON f.repository_id=s.repository_id AND f.relative_path=s.relative_path
            WHERE s.repository_id=? AND (s.name=? OR s.qualified_name=? OR s.id=?)
            {scope} ORDER BY s.relative_path,s.start_line LIMIT 100''',(self.repo_id,reference,reference,reference,*args))
        results = []
        for row in rows:
            try:
                result = self._symbol_result(row)
                if file and result['path'] != file and PurePosixPath(result['path']).name != file:
                    continue
                if self._matches(result,filters): results.append(result)
            except (ValueError,OSError):
                self.warnings.append('Some exact definitions changed. Refresh the repository index.')
        # Python has syntax-aware chunks, but no invented resolved graph symbols.
        scope,args = restrictions({'files':'x.path','languages':'x.language','symbolTypes':'x.symbol_kind','modules':'x.module'})
        contexts = self._read(f'''SELECT x.*,c.symbol_id FROM chunk_context x JOIN code_chunks c ON c.id=x.id
            JOIN index_snapshots s ON s.id=c.snapshot_id WHERE x.repo_id=? AND s.state='ready'
            AND c.symbol_id IS NULL AND x.language='python' AND x.symbol_kind IN ('function','method','class')
            AND (x.symbol_name=? OR x.qualified_name=?) {scope} ORDER BY x.path,c.start_line LIMIT 100''',
            (self.repo_id,reference,reference,*args))
        seen = set()
        for row in contexts:
            key = (row['path'],row['parent_symbol_key'])
            if key in seen: continue
            seen.add(key)
            try:
                result = self._symbol_result({**row,'kind':row['symbol_kind']})
                if file and result['path'] != file and PurePosixPath(result['path']).name != file: continue
                if self._matches(result,filters): results.append(result)
            except (ValueError,OSError):
                self.warnings.append('Some exact definitions changed. Refresh the repository index.')
        return results

    def _matches(self,item,filters):
        return matches_filters({'file_path':item['path'],'module':item.get('module',''),
            'language':item.get('language',''),'symbol_kind':item.get('symbolType','')},filters)

    def _modules(self,reference):
        return [r['relative_path'] for r in self._read('''SELECT relative_path FROM files
            WHERE repository_id=? AND kind!='directory' AND status IN ('included','unsupported')
            ORDER BY relative_path''',(self.repo_id,)) if r['relative_path']==reference
            or PurePosixPath(r['relative_path']).name==reference
            or str(PurePosixPath(r['relative_path']).with_suffix(''))==reference
            or PurePosixPath(r['relative_path']).stem==reference]

    def _verify(self,item):
        try:
            path = self.root/relative_path(item['path'])
            if path.resolve(strict=True)!=path or not path.is_file(): return False
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=item['sourceHash']: return False
            if not 1<=item['startLine']<=item['endLine']<=max(1,len(raw.splitlines())): return False
            if item.get('sourceOrigin')!='current_disk':
                info = trace(self.database,self.repo_id,item['entityId'])
                if any(item.get(k)!=info.get(k) for k in ('path','sourceHash','startLine','endLine','snapshotId','fileId','symbolId','schemaVersion','sourceOrigin')):
                    return False
                if info['kind']=='relationship' and item.get('resolution')!=info.get('resolution'):
                    return False
            for context in item.get('graphContext',[]):
                info = trace(self.database,self.repo_id,context['entityId'])
                if any(context.get(k)!=info.get(k) for k in ('path','sourceHash','startLine','endLine','snapshotId','fileId','symbolId')):
                    return False
            for identifier in item.get('graphEdges',[]):
                if trace(self.database,self.repo_id,identifier).get('resolution')!='resolved': return False
            return True
        except (OSError,ValueError,KeyError,TypeError,sqlite3.Error):
            return False

    def _graph(self,plan,definitions,filters,limit):
        if not self.database.is_file():
            self.warnings.append('Graph is not indexed. Refresh the repository index.')
            return [],'not_found',[]
        graph = KnowledgeGraph(self.database,self.repo_id)
        results,unresolved = [],[]
        try:
            with closing(graph._connection()) as db:
                if plan.intent in ('imports','dependents'):
                    reference = plan.reference
                    files = [dict(r) for r in db.execute('''SELECT id,relative_path FROM files WHERE repository_id=?
                        AND kind!='directory' AND status='included' ORDER BY relative_path''',(self.repo_id,))
                        if r['relative_path']==reference or PurePosixPath(r['relative_path']).name==reference
                        or str(PurePosixPath(r['relative_path']).with_suffix(''))==reference
                        or PurePosixPath(r['relative_path']).stem==reference]
                    if len(files)!=1:
                        if len(files)>1:
                            self.warnings.append('Module is ambiguous. Use its repository-relative path.')
                            candidates = [self._symbol_result({**f,'qualified_name':f['relative_path'],
                                'language':'','kind':'file'}) for f in files[:limit]]
                            return candidates,'ambiguous',[]
                        return [],'not_found',[]
                    node = db.execute("SELECT id FROM graph_nodes WHERE repository_id=? AND kind='Module' AND relative_path=?",
                        (self.repo_id,files[0]['relative_path'])).fetchone()
                else:
                    if not definitions or not definitions[0].get('symbolId'):
                        self.warnings.append('Verified structural resolution is unavailable for this symbol/language.')
                        return [],'not_found',[]
                    node = db.execute('SELECT id FROM graph_nodes WHERE repository_id=? AND symbol_id=?',
                        (self.repo_id,definitions[0]['symbolId'])).fetchone()
                if not node: return [],'not_found',[]
                relation = 'IMPORTS' if plan.intent=='imports' else 'DEPENDS_ON' if plan.intent=='dependents' else 'CALLS'
                reverse = plan.intent in ('callers','impact','dependents')
                current_column,other_column = ('target_id','source_id') if reverse else ('source_id','target_id')
                queue,visited = deque([(node[0],0,[])]),{node[0]}
                examined = 0
                while queue and len(results)<limit and examined<MAX_CANDIDATES:
                    current,depth,chain = queue.popleft()
                    unknown = db.execute('''SELECT id,expression AS target,reason,resolution,
                        evidence_file AS file,evidence_line AS line FROM graph_unresolved
                        WHERE repository_id=? AND source_id=? LIMIT 10''',(self.repo_id,current)).fetchall()
                    for occurrence in unknown:
                        try:
                            info = trace(self.database,self.repo_id,occurrence['id'])
                            unresolved.append({**dict(occurrence),**{k:info[k] for k in ('entityId','fileId','symbolId',
                                'snapshotId','sourceOrigin','schemaVersion','path','startLine','endLine','sourceHash')},'source_verified':True})
                        except (ValueError,OSError,sqlite3.Error):
                            self.warnings.append('Some unresolved reference evidence changed. Refresh the index.')
                    edges = db.execute(f'''SELECT e.*,n.label AS other_name,n.symbol_id AS other_symbol,
                        s.kind AS symbol_kind,f.language FROM graph_relationships e
                        JOIN graph_nodes n ON n.id=e.{other_column} LEFT JOIN symbols s ON s.id=n.symbol_id
                        LEFT JOIN files f ON f.repository_id=e.repository_id AND f.relative_path=e.evidence_file
                        WHERE e.repository_id=? AND e.{current_column}=? AND e.relationship=?
                        AND (e.resolution='resolved' OR (?=1 AND e.resolution='inferred'))
                        ORDER BY e.resolution='resolved' DESC,e.evidence_file,e.evidence_line,e.id LIMIT 100''',
                        (self.repo_id,current,relation,int(plan.intent=='imports'))).fetchall()
                    for edge in edges:
                        examined += 1
                        if examined>MAX_CANDIDATES: break
                        edge = dict(edge)
                        other = edge[other_column]
                        if other in visited: continue
                        visited.add(other)
                        info = trace(self.database,self.repo_id,edge['id'])
                        if info.get('structural'): continue
                        result = {**{k:info[k] for k in ('entityId','fileId','symbolId','snapshotId','sourceOrigin',
                            'schemaVersion','path','startLine','endLine','sourceHash')},
                            'name':edge['other_name'],'relatedSymbolId':edge['other_symbol'],
                            'language':edge['language'],'module':str(PurePosixPath(info['path']).with_suffix('')),
                            'symbolType':edge['symbol_kind'] or '', 'relationship':relation,'resolution':edge['resolution'],
                            'graphDistance':depth+1,'graphEdges':chain+[edge['id']] if edge['resolution']=='resolved' else [],
                            'reason':f"Verified static {relation} relationship ({plan.intent}); distance {depth+1}" if edge['resolution']=='resolved'
                                else 'Inferred external import specifier; not proof that the package is installed or executed',
                            'confidence':1.,'revision':'latest','callers':[],'tests':[]}
                        if edge['other_symbol']:
                            target = self._lookup(edge['other_symbol'],{})
                            result['graphContext'] = target[:1]
                        if self._matches(result,filters): results.append(result)
                        if plan.intent in ('impact','dependents') and depth+1<MAX_GRAPH_DEPTH:
                            queue.append((other,depth+1,chain+[edge['id']]))
                        if len(results)>=limit: break
                if queue or examined>=MAX_CANDIDATES or any(r.get('graphDistance')==MAX_GRAPH_DEPTH for r in results):
                    self.warnings.append('Graph traversal reached its depth/work/result bound; results may be incomplete.')
            return results,'found' if results else 'not_found',unresolved[:10]
        except (RuntimeError,ValueError,OSError,sqlite3.Error):
            self.warnings.append('Graph is stale or unavailable. Refresh the repository index.')
            return [],'not_found',[]

    def _expand(self,plan,rankings,filters,budget):
        if plan.intent!='explain' and not re.search(r'\b(?:dependencies|dependency|relationships|calls)\b',plan.query,re.I):
            return rankings
        seeds = fuse_rankings({k:[r for r in values if self._verify(r)] for k,values in rankings.items()})
        expanded,seen = [],set()
        for seed in seeds:
            symbol = seed.get('symbolId')
            if not symbol or symbol in seen: continue
            seen.add(symbol)
            if len(seen)>3: break
            edges,_,_ = self._graph(QueryPlan(plan.query,'callees',symbol),[seed],{},min(10,budget))
            for edge in edges:
                for target in edge.get('graphContext',[]):
                    if not self._matches(target,filters): continue
                    expanded.append({**target,'graphDistance':edge['graphDistance'],'graphEdges':edge['graphEdges'],
                        'resolution':'resolved','relationship':'CALLS',
                        'reason':f"Verified static callee of {seed['name']}; not proof of runtime dispatch"})
        if expanded: rankings = {**rankings,'graph':expanded}
        return rankings

    def _finish(self,plan,rankings,limit,*,mode=None,status=None,unresolved=None):
        verified = {}
        for method,items in rankings.items():
            verified[method] = [item for item in items if self._verify(item)]
            if len(verified[method])!=len(items):
                self.warnings.append('Some source evidence changed or is unverifiable. Refresh the repository index.')
        results = fuse_rankings(verified)
        # Exact evidence takes precedence. Context then uses rank-based evidence
        # reliability/implementation ordering; raw score scales are never mixed.
        results.sort(key=lambda r:('exact' not in r['retrievalMethods'],
            plan.intent=='explore' and (r['path'].endswith(('.md','.markdown')) or
                any(p in ('tests','test','__tests__') for p in r['path'].split('/'))),
            bool(re.search(r'\b(?:function|method)\b',plan.query,re.I)) and r.get('symbolType') not in ('function','method'),
            -r['fusionScore'],r['path'],r['startLine']))
        final = [r for r in results if self._verify(r)]
        if len(final)!=len(results):
            self.warnings.append('Source evidence changed during final verification. Refresh the repository index.')
        results = final[:limit]
        methods = {method for method,items in verified.items() if items}
        mode = mode or ('hybrid' if len(methods)>1 else next(iter(methods),'hybrid'))
        mode = 'lexical' if mode=='literal' else mode
        for item in results:
            item['rankingSignals']['context'] = {'symbolType':item.get('symbolType'),
                'sourceVerified':True,'graphDistance':item.get('graphDistance')}
            if mode=='hybrid':
                item['reason'] = 'Hybrid evidence ('+' + '.join(item['retrievalMethods'])+'); reciprocal rank fusion; '+item['reason']
            item.update(symbol_id=item.get('symbolId'),symbol=item['name'],file=item['path'],
                start_line=item['startLine'],end_line=item['endLine'],retrieval_method=mode,
                resolution=item.get('resolution','not_applicable'),source_verified=True,
                retrievalKind=mode,index_version=item.get('schemaVersion'),
                index_snapshot=item.get('snapshotId'))
        warning = ' '.join(dict.fromkeys(self.warnings)) or None
        if not results and not warning:
            warning = 'No sufficiently relevant verified matches found. Try a precise symbol or file path.'
        return {'query':plan.query,'status':status if status=='ambiguous' and results else 'found' if results else 'not_found',
            'mode':mode,'intent':plan.intent,'detected':asdict(plan),'results':results,'warning':warning,
            'uncertainty':[u for u in unresolved or [] if self._verify(u)],'indexVersion':max((r.get('schemaVersion',0) for r in results),default=None),
            'bounds':{'candidateLimit':MAX_CANDIDATES,'graphDepth':MAX_GRAPH_DEPTH,'graphEdgeLimit':MAX_CANDIDATES}}

    def retrieve(self,query,*,filters=None,limit=5,retrieval='auto'):
        if not 1<=limit<=25 or retrieval not in ('auto','hybrid','keyword','semantic'):
            raise ValueError('Invalid retrieval options')
        self.warnings = []
        filters = filters or {}
        if set(filters)-{'files','modules','languages','symbolTypes'}:
            raise ValueError('Unknown metadata filter')
        for values in filters.values():
            if not isinstance(values,list) or len(values)>25 or any(not isinstance(v,str) or not v for v in values):
                raise ValueError('Invalid metadata filter values')
        filters = {k:[v.lower() if k in ('languages','symbolTypes') else v for v in values] for k,values in filters.items()}
        for values in (filters.get('files',[]),filters.get('modules',[])):
            for value in values: relative_path(value)
        plan = plan_query(query)
        budget = min(MAX_CANDIDATES,limit*3)
        if retrieval in ('auto','hybrid'):
            try:
                structural = plan.intent in ('callers','callees','impact','imports','dependents')
                definitions = self._lookup(plan.reference,{} if structural else filters,plan.file) if plan.reference and not plan.module else []
            except sqlite3.Error:
                definitions = []
                self.warnings.append('Exact symbol metadata is unavailable. Refresh the repository index.')
            if len(definitions)>1:
                self.warnings.append('Symbol is ambiguous. Use its qualified name and/or a file filter.')
                return self._finish(plan,{'exact':definitions},limit,status='ambiguous')
            if plan.intent in ('callers','callees','impact','imports','dependents'):
                results,status,unresolved = self._graph(plan,definitions,filters,budget)
                if not results and not self.warnings:
                    self.warnings.append('No verified static relationships found. Dynamic or unresolved relationships may still exist.')
                return self._finish(plan,{'graph':results},limit,mode='graph',status=status,unresolved=unresolved)
            if definitions:
                return self._finish(plan,self._expand(plan,{'exact':definitions},filters,budget),limit,mode='exact')
            if plan.module:
                literal = []
                for path in self._modules(plan.module)[:budget]:
                    scoped = {**filters,'files':[path]} if not filters.get('files') or path in filters['files'] else None
                    if scoped is not None:
                        literal.extend(r for r in self.exact_search('explain '+path,scoped,1)
                            if r['path']==path and r['reason'].startswith('Requested file:'))
                ambiguous = len(literal)>1
                if ambiguous: self.warnings.append('Module is ambiguous. Use its repository-relative path.')
                return self._finish(plan,{'literal':literal},limit,status='ambiguous' if ambiguous else None)
            literal = self.exact_search(plan.query,filters,budget)
            conversational = bool(re.match(r'^(?:where|what|how|why|which|explain|describe|tell|who)\b',plan.query,re.I))
            if conversational: literal = [r for r in literal if r['reason'].startswith('Requested file:')]
            if plan.file:
                literal = [r for r in literal if not '/' in plan.file or r['path']==plan.file]
            if literal:
                file_matches = [r for r in literal if r['reason'].startswith('Requested file:')]
                ambiguous = len({r['path'] for r in file_matches})>1
                if ambiguous: self.warnings.append('Filename is ambiguous. Use its repository-relative path.')
                return self._finish(plan,{'literal':file_matches or literal},limit,status='ambiguous' if ambiguous else None)
            if plan.file or plan.intent=='definition':
                return self._finish(plan,{},limit,mode='exact')
        rankings = {}
        if retrieval!='semantic' and plan.keywords:
            keyword = self.keyword_search(plan.keywords,filters,budget)
            rankings['keyword'] = keyword['results']
            if keyword.get('warning') and 'No keyword' not in keyword['warning']:
                self.warnings.append(keyword['warning'])
        semantic = None
        if retrieval!='keyword':
            semantic = self.semantic_search(plan.query,filters,budget)
            rankings['semantic'] = semantic['results']
            if semantic.get('warning') and 'No sufficiently relevant' not in semantic['warning']:
                self.warnings.append(semantic['warning'])
            if semantic.get('mode')=='unavailable':
                if 'keyword' not in rankings and plan.keywords:
                    rankings['keyword'] = self.keyword_search(plan.keywords,filters,budget)['results']
                if rankings.get('keyword'):
                    self.warnings.append('Semantic embeddings are unavailable; showing keyword evidence instead.')
        if retrieval in ('auto','hybrid'):
            rankings = self._expand(plan,rankings,filters,budget)
        return self._finish(plan,rankings,limit,mode='keyword' if retrieval=='keyword' else
            'semantic' if retrieval=='semantic' and semantic and semantic['mode']!='unavailable' else
            'unavailable' if semantic and semantic['mode']=='unavailable' and not any(rankings.values()) else None)
