"""Phase 8 quality gate over hand-authored synthetic JS/TS repositories.

Each benchmark runs in a fresh process and temporary private storage. No answer
LLM, user-repository indexing, or mocked embeddings are used by the benchmark.
--structural-only reports retrieval/citations as unmeasured, never as passing.
"""
import argparse
from contextlib import ExitStack, closing
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

from knowledge_base.graph import build_graph, KnowledgeGraph
from knowledge_base.metadata import audit, trace
from knowledge_base.identity import relative_path

FIXTURES = Path(__file__).with_name('fixtures')/'phase8'
RELATION_SCOPE = ('CALLS','IMPORTS','DEPENDS_ON','EXTENDS','IMPLEMENTS')


def fixture_expectations(language):
    return json.loads((FIXTURES/'expected.json').read_text())[language]


def prepare_fixture(root,language,extra_files=0):
    shutil.copytree(FIXTURES/language,root)
    subprocess.run(['git','init','-q',str(root)],check=True,capture_output=True)
    if language == 'typescript':
        (root/'ignored.ts').write_text('export function ignoredFixture() {}\n')
    suffix = '.ts' if language=='typescript' else '.js'
    for number in range(extra_files):
        path = root/'src'/f'probe_{number:04d}{suffix}'
        path.write_text(f'/** Count items in synthetic performance probe {number}. */\n'
                        f'export function probe_{number:04d}(value) {{ return value + {number}; }}\n')


def compare_sets(expected,actual):
    expected,actual = set(expected),set(actual)
    matched = len(expected & actual)
    return {'expected':len(expected),'observed':len(actual),'matched':matched,
            'precision':matched/len(actual) if actual else (1. if not expected else 0.),
            'recall':matched/len(expected) if expected else 1.,
            'missing':sorted(expected-actual),'unexpected':sorted(actual-expected)}


def evaluate_graph(database,repo_id,language,extra_files=0):
    gold = fixture_expectations(language)
    suffix = '.ts' if language=='typescript' else '.js'
    files = gold['files']+[f'src/probe_{n:04d}{suffix}' for n in range(extra_files)]
    definitions = gold['definitions']+[[f'src/probe_{n:04d}{suffix}',f'probe_{n:04d}','function',2,2] for n in range(extra_files)]
    expected_edges = {('CALLS',*c) for c in gold['calls']} | {tuple(c) for c in gold['heritage']}
    for source,target in gold['imports']:
        for kind in ('IMPORTS','DEPENDS_ON'):
            expected_edges.add((kind,source,source,target,target))
    with closing(sqlite3.connect(database)) as db:
        actual_files = {r[0] for r in db.execute("SELECT relative_path FROM files WHERE repository_id=? AND status='included' AND kind!='directory'",(repo_id,))}
        actual_defs = {tuple(r) for r in db.execute('SELECT relative_path,qualified_name,kind,start_line,end_line FROM symbols WHERE repository_id=?',(repo_id,))}
        edges = {tuple(r) for r in db.execute('''SELECT e.relationship,a.relative_path,a.label,b.relative_path,b.label
            FROM graph_relationships e JOIN graph_nodes a ON a.id=e.source_id JOIN graph_nodes b ON b.id=e.target_id
            WHERE e.repository_id=? AND e.resolution='resolved' AND e.relationship IN (?,?,?,?,?)''',(repo_id,*RELATION_SCOPE))}
        unresolved = {tuple(r) for r in db.execute('''SELECT e.evidence_file,n.label,e.relationship FROM graph_unresolved e
            JOIN graph_nodes n ON n.id=e.source_id WHERE e.repository_id=?''',(repo_id,))}
        failures = [dict(zip(('file','status'),r)) for r in db.execute("SELECT relative_path,parse_status FROM files WHERE repository_id=? AND parse_status IN ('partial','error')",(repo_id,))]
        identifiers = [r[0] for r in db.execute('SELECT id FROM relationships WHERE repository_id=?',(repo_id,))]
    invalid = []
    for identifier in identifiers:
        try: trace(database,repo_id,identifier)
        except (ValueError,OSError,sqlite3.Error): invalid.append(identifier)
    inventory = compare_sets(files,actual_files)
    symbols = compare_sets(map(tuple,definitions),actual_defs)
    relationships = compare_sets(expected_edges,edges)
    unsupported = compare_sets(map(tuple,gold['unresolved']),unresolved)
    provenance = {'checked':len(identifiers),'invalid':invalid,'passed':bool(identifiers) and not invalid}
    integrity = audit(database)
    constructs = {}
    for name,paths in gold['constructs'].items():
        constructs[name] = {
            'symbols':compare_sets((tuple(d) for d in definitions if d[0] in paths),(d for d in actual_defs if d[0] in paths)),
            'relationships':compare_sets((e for e in expected_edges if e[1] in paths),(e for e in edges if e[1] in paths))}
    gates = {'inventory':inventory['precision']==inventory['recall']==1.,
             'symbols':symbols['precision']>=.98 and symbols['recall']>=.98,
             'resolved_relationships':relationships['precision']>=.98 and relationships['recall']>=.98,
             'unsupported_explicit':unsupported['recall']==1.,
             'critical_gold_relationships':not relationships['missing'],
             'provenance':provenance['passed'],
             'metadata_integrity':not integrity['dangling_references'],
             'parser_failures':not failures}
    return {'inventory':inventory,'symbols':symbols,'relationships':relationships,
            'unsupported':unsupported,'provenance':provenance,'integrity':integrity,
            'parser_failures':failures,'by_construct':constructs,'gates':gates,
            'passed':all(gates.values()),'relationship_scope':list(RELATION_SCOPE)}


def percentile(samples,fraction):
    return sorted(samples)[max(0,math.ceil(len(samples)*fraction)-1)] if samples else None


def memory_usage():
    try:
        import resource
        multiplier = 1 if sys.platform=='darwin' else 1024
        return {'process_peak_rss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*multiplier,
                'largest_child_peak_rss_bytes':resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss*multiplier,
                'scope':'per worker process and largest finished child, not simultaneous combined RAM'}
    except ImportError:
        return {'process_peak_rss_bytes':None,'largest_child_peak_rss_bytes':None,'scope':'RSS unavailable on this platform'}


def run_case(language,extra_files=0,structural_only=False):
    if language not in ('javascript', 'typescript') or type(extra_files) is not int or not 0 <= extra_files <= 1000:
        raise ValueError('Expected JavaScript/TypeScript and 0–1000 extra files')
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix='cm-validation-') as temporary:
        root,data = Path(temporary)/'repo',Path(temporary)/'private'
        prepare_fixture(root,language,extra_files)
        if structural_only:
            indexing = time.perf_counter()
            built = build_graph(root,data_dir=data)
            elapsed = time.perf_counter()-indexing
            quality = evaluate_graph(built['database'],built['repository_id'],language,extra_files)
            retrieval,citations,updates = None,None,None
            warmup = None
            database = Path(built['database'])
            latencies = []
            initial_sizes = storage_sizes(data,database)
        else:
            sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ai-engine'))
            import api
            import config
            from indexer import index_directory
            from knowledge_base.synchronization import SnapshotStore
            from knowledge_base.vector_store import FaissClient
            # The only reusable user resource is the public embedding-model cache.
            warming = time.perf_counter()
            config.embed_texts(['Warm the local validation embedding model.'])
            warmup = time.perf_counter()-warming
            repo_id = config.make_repo_id(str(root))
            store = SnapshotStore(root,repo_id,data)
            with ExitStack() as stack:
                client = FaissClient(data/'faiss'); stack.callback(client.close)
                for module in ('api','indexer'): stack.enter_context(patch(module+'.get_qdrant_client',return_value=client))
                stack.enter_context(patch.object(api,'DEFAULT_DATA_DIR',str(data)))
                stack.enter_context(patch.object(api,'_SYNC_MANAGER',None))
                stack.enter_context(patch.dict(os.environ,{'CODEMIND_LOCAL_MODEL':'','CODEMIND_AUTO_SYNC':'0'}))
                stack.enter_context(patch('answers.urlopen',side_effect=AssertionError('Quality gate must not call an answer model')))
                api._REGISTERED_REPOS[repo_id] = {'rootPath':str(root)}
                stack.callback(api._REGISTERED_REPOS.pop,repo_id)
                def publish():
                    return store.publish(client=client,collection=config.COLLECTION_NAME,index_directory=index_directory,build_graph=build_graph)
                indexing = time.perf_counter(); publish(); elapsed = time.perf_counter()-indexing
                database = store.data_for(store.active())/repo_id/'metadata.db'
                initial_sizes = storage_sizes(data,database)
                quality = evaluate_graph(database,repo_id,language,extra_files)
                hits,details,latencies,valid_citations,total_citations = 0,[],[],0,0
                for query,path,name,start,end in fixture_expectations(language)['questions']:
                    request = api.SearchRequest(repoId=repo_id,query=query,retrieval='hybrid',limit=5)
                    tick = time.perf_counter(); response = api.search_code(request); latencies.append((time.perf_counter()-tick)*1000)
                    # Match qualified identity and expected declaration overlap,
                    # not a generic same-file hit or arbitrary nearby chunk.
                    matches = [r for r in response['results'] if r['path']==path and r['name']==name
                               and r['startLine']<=start and r['endLine']>=end]
                    hit = bool(matches); hits += hit
                    details.append({'query':query,'expected':f'{path}::{name}','hit_at_5':hit,
                        'results':[{'file':r['path'],'symbol':r['name'],'start':r['startLine'],'end':r['endLine']} for r in response['results']],
                        'warning':response.get('warning')})
                    answer = api.ask(request)
                    total_citations += abs(len(answer['citations'])-len(answer['results']))
                    for citation,item in zip(answer['citations'],answer['results']):
                        total_citations += 1
                        valid_citations += citation_valid(root,database,repo_id,citation,item)
                retrieval = {'questions':len(details),'hits':hits,'recall_at_5':hits/len(details),
                             'target':.90,'passed':hits/len(details)>=.90,'details':details}
                citations = {'checked':total_citations,'valid':valid_citations,
                             'accuracy':valid_citations/total_citations if total_citations else None,
                             'passed':total_citations>0 and valid_citations==total_citations,
                             'scope':'source-context citation spans, not verification of generated claims'}
                updates = exercise_updates(root,store,publish,api,repo_id,language)
                database = store.data_for(store.active())/repo_id/'metadata.db'
        sizes = storage_sizes(data,database)
        complete = not structural_only
        passed = quality['passed'] and (not complete or retrieval['passed'] and citations['passed'] and updates['passed'])
        return {'language':language,'extra_files':extra_files,'quality':quality,'retrieval':retrieval,
                'citations':citations,'updates':updates,'status':'failed' if not passed else 'passed' if complete else 'partial',
                'performance':{'model_warmup_seconds':warmup,'initial_index_seconds':elapsed,
                    'retrieval_ms':{'samples':len(latencies),'p50':percentile(latencies,.5),'p95':percentile(latencies,.95),'max':max(latencies) if latencies else None},
                    'initial_disk_bytes':initial_sizes,'disk_bytes_after_update_checks':sizes,
                    **memory_usage(),'case_seconds':time.perf_counter()-started},
                'limitations':['Synthetic repositories; not a production performance budget or general accuracy guarantee.',
                               'Python and other languages are outside the verified JS/TS graph quality denominator.',
                               'Remote Qdrant is not live-tested by this default-local FAISS benchmark.']}


def storage_sizes(data,database):
    return {'total':sum(p.stat().st_size for p in data.rglob('*') if p.is_file()),
            'active_metadata':database.stat().st_size,
            'vector_journal_and_cache':sum(p.stat().st_size for p in (data/'faiss').glob('*') if p.is_file()) if (data/'faiss').exists() else 0}


def citation_valid(root,database,repo_id,citation,item):
    """Validate identity as well as location; same-span wrong IDs cannot pass."""
    try:
        source = root.resolve()/relative_path(citation['path'])
        if source.resolve(strict=True)!=source:
            return False
        raw = source.read_bytes()
        span = (citation['path'],citation['startLine'],citation['endLine'])
        keys = ('entityId','fileId','symbolId','snapshotId','sourceOrigin','schemaVersion')
        valid = (hashlib.sha256(raw).hexdigest()==citation['sourceHash']
                 and span==(item['path'],item['startLine'],item['endLine'])
                 and 1<=span[1]<=span[2]<=len(raw.decode().splitlines())
                 and all(citation.get(key)==item.get(key) for key in keys))
        if item.get('sourceOrigin')!='current_disk':
            info = trace(database,repo_id,item['entityId'])
            valid = valid and span==(info['path'],info['startLine'],info['endLine']) and all(info.get(key)==item.get(key) for key in keys)
        return valid
    except (KeyError,ValueError,OSError,sqlite3.Error):
        return False


def exercise_updates(root,store,publish,api,repo_id,language):
    """Change-impact/update gate uses real immutable publication, not mocks."""
    path = 'src/auth.ts' if language=='typescript' else 'src/events.js'
    name = 'validateCredential' if language=='typescript' else 'deduplicateEvent'
    source = root/path; original = source.read_text()
    cases = {}
    database = store.data_for(store.active())/repo_id/'metadata.db'
    graph = KnowledgeGraph(database,repo_id)
    target = graph.symbol(name)['symbol_id']
    if language == 'typescript':
        caller = graph.symbol('applicationEntry')['symbol_id']
        with closing(sqlite3.connect(database)) as db:
            method = db.execute("SELECT id FROM symbols WHERE qualified_name='SecureService.check' AND kind='method'").fetchone()[0]
        cases['method_callers'] = bool(graph.direct_callers(method))
        cases['module_dependency_paths'] = bool(graph.dependency_paths('src/entry.ts','src/auth.ts'))
        cases['unresolved_not_guessed'] = not graph.direct_callees(graph.symbol('unionDispatch')['symbol_id']) and bool(graph.unresolved_references(symbol_id=graph.symbol('unionDispatch')['symbol_id']))
    else:
        caller = graph.symbol('consumeEvent')['symbol_id']
        cases['module_dependency_paths'] = bool(graph.dependency_paths('src/consumer.js','src/events.js'))
        cases['unresolved_not_guessed'] = not graph.direct_callees(graph.symbol('unknownCallback')['symbol_id']) and bool(graph.unresolved_references(symbol_id=graph.symbol('unknownCallback')['symbol_id']))
    cases['function_dependency_paths'] = bool(graph.symbol_dependency_paths(caller,target)['paths'])
    cases['test_references'] = bool(graph.test_references(target)['references'])
    cases['module_dependents'] = bool(graph.reverse_dependencies(path))
    before = store.active()['id']
    source.write_text('// Saved edit; old spans must be invalidated.\n'+original)
    response = api.search_code(api.SearchRequest(repoId=repo_id,query='who calls '+name+'?'))
    cases['stale_graph_blocked'] = not response['results'] and not response['synchronization']['fresh']
    changed = publish()
    cases['modification'] = changed['activeSnapshotId']!=before and store.view()['fresh']
    source.rename(source.with_name('renamed'+source.suffix)); publish()
    cases['rename'] = not api.search_code(api.SearchRequest(repoId=repo_id,query='who calls '+name+'?'))['results']
    destination = root/'moved'/('source'+source.suffix); destination.parent.mkdir()
    source.with_name('renamed'+source.suffix).rename(destination); publish()
    cases['move'] = not api.search_code(api.SearchRequest(repoId=repo_id,query='explain '+path))['results']
    destination.unlink(); publish()
    cases['deletion'] = not api.search_code(api.SearchRequest(repoId=repo_id,query='Where is '+name+' defined?'))['results']
    noop = publish()
    cases['no_op'] = noop['unchanged'] and noop['chunks_embedded']==0
    database = store.data_for(store.active())/repo_id/'metadata.db'
    cases['no_dangling_metadata'] = not audit(database)['dangling_references']
    return {'checks':cases,'passed':all(cases.values())}


def regression_tests(output):
    suites = (('knowledge_base',['discover','-s','knowledge_base','-p','test_*.py','-q']),
              ('engine',['discover','-s','ai-engine/tests','-p','test_*.py','-q']),
              ('query_system',['discover','-s','query_system','-p','test_*.py','-q']))
    results = {}
    with tempfile.TemporaryDirectory(prefix='cm-regressions-') as private:
        for name,args in suites:
            try:
                process = subprocess.run([sys.executable,'-m','unittest',*args],cwd=Path(__file__).resolve().parents[1],
                    capture_output=True,text=True,timeout=300,
                    env={**os.environ,'CODEMIND_AUTO_SYNC':'0','CODEMIND_DATA_DIR':private,
                         'PYTHONPATH':str(Path(__file__).resolve().parents[1]/'ai-engine'),
                         'LITELLM_LOCAL_MODEL_COST_MAP':'True'})
            except (subprocess.TimeoutExpired, OSError) as error:
                message = 'Regression suite timed out after 300 seconds.' if isinstance(error, subprocess.TimeoutExpired) else 'Regression process could not start.'
                (output/(name+'-tests.log')).write_text(message+'\n')
                results[name] = {'passed':False,'tests':None,'exit_code':None,'log':name+'-tests.log'}
                continue
            (output/(name+'-tests.log')).write_text(process.stdout+process.stderr)
            count = re.search(r'Ran (\d+) tests',process.stderr)
            results[name] = {'passed':process.returncode==0 and bool(count and int(count[1])>0),'tests':int(count[1]) if count else None,
                             'exit_code':process.returncode,'log':name+'-tests.log'}
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',help='Directory for JSON/Markdown report and regression logs; outside the repository')
    parser.add_argument('--sizes',default='0,32,128',help='Comma-separated numbers of extra synthetic source files')
    parser.add_argument('--structural-only',action='store_true')
    parser.add_argument('--skip-regressions',action='store_true',help='Mark the full regression gate unmeasured')
    parser.add_argument('--worker',choices=('javascript','typescript'),help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        sizes = [int(v) for v in args.sizes.split(',')]
        if not sizes or len(sizes)>8 or len(set(sizes))!=len(sizes) or any(v<0 or v>1000 for v in sizes): raise ValueError()
    except ValueError: parser.error('--sizes requires 1–8 distinct integers between 0 and 1000')
    if not args.output: parser.error('--output is required')
    output = Path(args.output).expanduser().resolve()
    workspace = Path(__file__).resolve().parents[1]
    if output==workspace or workspace in output.parents: parser.error('Keep validation outputs outside the source repository')
    if output.exists() and (not output.is_dir() or any(output.iterdir())): parser.error('Use a new or empty output directory; existing reports are never overwritten')
    try: output.mkdir(parents=True,exist_ok=True,mode=0o700)
    except OSError: parser.error('Cannot create validation output directory')
    if args.worker:
        try: report = run_case(args.worker,sizes[0],args.structural_only)
        except Exception as error:
            report = {'language':args.worker,'extra_files':sizes[0],'status':'failed','error':type(error).__name__+': '+str(error)}
        (output/'case.json').write_text(json.dumps(report,indent=2))
        raise SystemExit(1 if report['status']=='failed' else 0)
    cases = []
    for language in ('javascript','typescript'):
        for size in sizes:
            folder = output/(language+'-'+str(size))
            command = [sys.executable,'-m','knowledge_base.validation','--worker',language,'--sizes',str(size),'--output',str(folder)]
            if args.structural_only: command.append('--structural-only')
            print(f'Validating {language}, {size} extra files...',flush=True)
            try:
                process = subprocess.run(command,cwd=workspace,capture_output=True,text=True,timeout=300)
                folder.mkdir(parents=True,exist_ok=True)
                (folder/'worker.log').write_text(process.stdout+process.stderr)
                case = json.loads((folder/'case.json').read_text())
                if (not isinstance(case, dict) or case.get('language') != language or case.get('extra_files') != size
                    or case.get('status') not in ('failed', 'partial' if args.structural_only else 'passed')):
                    raise ValueError('Invalid worker report identity or status')
                if case['status'] != 'failed' and (not isinstance(case.get('quality'), dict)
                    or not isinstance(case.get('performance'), dict) or case['quality'].get('passed') is not True):
                    raise ValueError('Worker did not supply successful measured gates')
                if 'quality' in case:
                    quality, performance = case['quality'], case['performance']
                    if (type(quality['inventory']['expected']) is not int or quality['inventory']['expected'] < 0
                        or any(not 0 <= quality[name][metric] <= 1 for name in ('symbols', 'relationships') for metric in ('precision', 'recall'))
                        or not math.isfinite(performance['initial_index_seconds']) or performance['initial_index_seconds'] < 0):
                        raise ValueError('Invalid worker measurements')
                    latency = performance['retrieval_ms']['p95']
                    if latency is not None and (not math.isfinite(latency) or latency < 0): raise ValueError('Invalid retrieval latency')
                    for key, metric in (('retrieval', 'recall_at_5'), ('citations', 'accuracy')):
                        if case[key] is not None and case[key][metric] is not None and not 0 <= case[key][metric] <= 1:
                            raise ValueError('Invalid measured accuracy')
                if case['status'] == 'passed' and any(not isinstance(case.get(key), dict) or case[key].get('passed') is not True
                    for key in ('retrieval', 'citations', 'updates')):
                    raise ValueError('Worker did not pass the complete quality gate')
                if process.returncode != (1 if case['status']=='failed' else 0):
                    case.update(status='failed',error='Worker did not complete normally')
            except (subprocess.TimeoutExpired,OSError,ValueError,KeyError,TypeError):
                case = {'language':language,'extra_files':size,'status':'failed','error':'Benchmark worker failed or timed out'}
            cases.append(case)
    regressions = {} if args.skip_regressions else regression_tests(output)
    complete = not args.structural_only and not args.skip_regressions
    failed = any(c['status']=='failed' for c in cases) or any(not r['passed'] for r in regressions.values())
    report = {'status':'failed' if failed else 'passed' if complete else 'partial',
              'mode':'structural-only' if args.structural_only else 'full',
              'targets':{'eligible_inventory':1.,'symbol_precision_and_recall':.98,'resolved_relationship_precision':.98,
                         'retrieval_recall_at_5':.90,'source_citation_accuracy':1.},
              'environment':{'python':platform.python_version(),'system':platform.platform(),'architecture':platform.machine(),
                             'dependencies':dependency_versions(workspace), 'embedding_model':'BAAI/bge-small-en-v1.5',
                             'vector_backend':'private local FAISS', 'answer_model':'disabled'},
              'implementation_sha256':implementation_fingerprints(workspace),
              'cases':cases,'regressions':regressions,
              'performance_budgets':'Not established: synthetic observations only; representative repository testing is still required.'}
    (output/'quality-report.json').write_text(json.dumps(report,indent=2))
    write_markdown(output/'quality-report.md',report)
    print(f"Validation {report['status']}: {output/'quality-report.md'}")
    raise SystemExit(1 if failed else 0)


def implementation_fingerprints(workspace):
    paths = [workspace/'knowledge_base'/name for name in ('validation.py','scanner.py','parser.py','semantic_resolver.cjs',
        'graph.py','metadata.py','retrieval.py','synchronization.py','vector_store.py','semantic.py','chunking.py','identity.py')]
    paths += [workspace/'ai-engine'/name for name in ('api.py','config.py','indexer.py','answers.py')]
    paths += [path for path in FIXTURES.rglob('*') if path.is_file()]
    return {str(path.relative_to(workspace)):hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(paths)}


def dependency_versions(workspace):
    versions = {}
    for package in ('faiss-cpu','fastembed','tree-sitter','qdrant-client','watchdog'):
        try: versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError: versions[package] = 'not installed'
    try: versions['typescript'] = json.loads((workspace/'extension/node_modules/typescript/package.json').read_text())['version']
    except (OSError,ValueError,KeyError): versions['typescript'] = 'unavailable'
    return versions


def write_markdown(path,report):
    lines = ['# Knowledge base quality report','',f"Overall gate: **{report['status']}**",'',
        ('Structural-only run: embeddings, retrieval, citations and update gates are unmeasured.' if report['mode']=='structural-only'
         else 'Real local BGE embeddings, hybrid retrieval and immutable snapshots. No answer LLM.'),
        'Synthetic fixture results are not general accuracy or production performance guarantees.','',
        '| Language | Eligible files | Symbol precision / recall | Relationship precision / recall | Recall@5 | Citation accuracy | Index seconds | p95 retrieval ms | Gate |',
        '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |']
    for case in report['cases']:
        if 'quality' not in case:
            lines.append(f"| {case['language']} | — | — | — | — | — | — | — | failed: {case.get('error','unknown')} |")
            continue
        quality,perf = case['quality'],case['performance']
        metric = lambda name: f"{quality[name]['precision']:.1%} / {quality[name]['recall']:.1%}"
        recall = f"{case['retrieval']['recall_at_5']:.1%}" if case['retrieval'] else 'unmeasured'
        citation = f"{case['citations']['accuracy']:.1%}" if case['citations'] and case['citations']['accuracy'] is not None else 'unmeasured'
        latency = f"{perf['retrieval_ms']['p95']:.2f}" if perf['retrieval_ms']['p95'] is not None else 'unmeasured'
        lines.append(f"| {case['language']} | {quality['inventory']['expected']} | {metric('symbols')} | {metric('relationships')} | {recall} | {citation} | {perf['initial_index_seconds']:.3f} | {latency} | {case['status']} |")
    lines += ['','## Regression gates','']
    for name,result in report['regressions'].items():
        lines.append(f"- {name}: {result['tests']} tests; {'passed' if result['passed'] else 'FAILED'}; log `{result['log']}`.")
    if not report['regressions']: lines.append('Unmeasured: full regression suites were skipped.')
    lines += ['','## Scope and limitations','',
        '- Exact gold definitions include path, qualified name, kind and source span.',
        '- Relationship precision covers CALLS, IMPORTS, DEPENDS_ON, EXTENDS and IMPLEMENTS; structural/data-reference edges are provenance-checked separately.',
        '- Per-construct differences, unsupported coverage, update checks, RSS, storage, parser failures and missed retrieval questions are in the JSON report.',
        '- Static call paths and test references indicate potentially affected components, not guaranteed runtime behaviour.',
        '- Source citations verify the supplied context, not generated-model claims. An empty citation set cannot pass the citation gate.',
        '- RSS is each isolated worker and its largest finished child, not their simultaneous combined memory.',
        '- No production performance budgets are asserted; cold model warmup and warm indexed retrieval are reported separately.',
        '- Default local FAISS is tested. Remote Qdrant and resolved graphs for other languages require separate validation.','']
    path.write_text('\n'.join(lines))


if __name__=='__main__': main()
