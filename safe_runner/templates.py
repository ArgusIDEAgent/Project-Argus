"""One literal JSON call/equality template; no arbitrary generated programs."""
from hashlib import sha256
import json
from pathlib import PurePosixPath
import re


def preview(snapshot, analysis, module_path, export_name, arguments, expected):
    source = next((v for v in snapshot.after if v.path == module_path and v.data is not None), None)
    if source is None or PurePosixPath(module_path).suffix not in ('.js','.mjs'):
        raise ValueError('Select a captured native ESM JS module')
    if not re.fullmatch(r'[A-Za-z_$][A-Za-z0-9_$]{0,79}', export_name):
        raise ValueError('Export name must be a bounded identifier')
    if not any(s.path == module_path and s.name == export_name for s in analysis.evidence.after.symbols):
        raise ValueError('Named declaration not present in captured structural evidence; export/runtime behavior is not inferred')
    if not isinstance(arguments, list) or len(arguments)>8:
        raise ValueError('At most eight literal JSON arguments are supported')
    values = json.dumps([arguments,expected],allow_nan=False,ensure_ascii=True)
    if len(values)>4000:
        raise ValueError('Template literals exceed review budget')
    def depth(value, level=0):
        if level>8: raise ValueError('Template literals too deeply nested')
        if isinstance(value,dict):
            for v in value.values(): depth(v,level+1)
        elif isinstance(value,list):
            for v in value: depth(v,level+1)
    depth([arguments,expected])
    text = "import test from 'node:test';\nimport assert from 'node:assert/strict';\n"
    text += 'import * as target from '+json.dumps('./'+PurePosixPath(module_path).name)+';\n'
    text += "test('user-confirmed literal expectation', async () => {\n  assert.deepStrictEqual(await target["+json.dumps(export_name)+'](...'+json.dumps(arguments,ensure_ascii=True)+'), '+json.dumps(expected,ensure_ascii=True)+');\n});\n'
    digest=sha256(text.encode()).hexdigest()
    path=str(PurePosixPath(module_path).parent / ('.codemind-temporary-'+digest[:16]+'.test.mjs'))
    if any(v.path==path for v in (*snapshot.before,*snapshot.after)):
        raise ValueError('Temporary check path collides with captured source')
    return {'template_version':'literal-call-equality-v1','path':path,'source':text,'source_hash':digest,
            'expectation_provenance':'user_supplied_literal; requires explicit source authorization; not an independently verified specification',
            'target':{'version':'after','path':module_path,'source_hash':source.digest},
            'limitation':'Missing export, asynchronous side effects or incorrect expectations may fail. A temporary-check failure is not an automatic defect/regression claim.'}
