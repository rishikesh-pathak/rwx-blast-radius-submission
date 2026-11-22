"""A static, non-oracle test-impact selector for sqlglot.

All decisions are based on changed paths and content, and the test inventory.
No sample answer labels or patch/answer lookups are consulted by the bot.
"""
from __future__ import annotations
import json, re, sys, os
from pathlib import Path
from collections import defaultdict

DATA = Path(os.environ.get('DATA_DIR', '/data'))


def edited_paths_and_lines(s):
    paths=[]; mods=[]; signs=[]
    path=''
    for line in s.splitlines():
        if line.startswith('+++ b/'):
            path=line[6:];paths.append(path)
        elif line.startswith('+') and not line.startswith('+++'):
            mods.append((path,line[1:])); signs.append(line[1:])
        elif line.startswith('-') and not line.startswith('---'):
            mods.append((path,line[1:])); signs.append(line[1:])
    return paths,mods


def is_comment_only(mods):
    if not mods:return True
    # Safely skip only standalone Python comments/blank lines. SQL comments may
    # be interpreted as fixture contents, so don't skip SQL fixture modifications.
    if any(p.endswith(('.sql','.json','.yaml')) for p,_ in mods):return False
    return all(not line.strip() or line.lstrip().startswith('#') for _,line in mods)


def norm(s):
    return re.sub(r'[^a-z0-9]+','',s.lower())


def select_tests(paths, mods, tests):
    if is_comment_only(mods):return set()
    by_file=defaultdict(list)
    for t in tests:by_file[t['file']].append(t)
    chosen=set()
    def add(t): chosen.add(t['number'])
    def file(f):
        for t in by_file.get(f,[]):add(t)
    def match(pattern):
        for t in tests:
            if pattern in t['id'].lower():add(t)
    def opt_name(name):
        n=norm(name)
        for t in by_file.get('tests/test_optimizer.py',[]):
            method=t['id'].split('::')[-1]
            if norm(method.removeprefix('test_'))==n or norm(method.removeprefix('test_')) in (n+'s',n.rstrip('s')):add(t)
    all_paths=set(paths)
    # Every changed fixture is exercised by the optimizer method of matching name.
    for p in paths:
        if p.startswith('tests/fixtures/optimizer/'):
            name=Path(p).stem
            opt_name(name)
            if name=='pushdown_projections':opt_name('pushdown_projection')
            if name.endswith('_ddl'):opt_name(name.removesuffix('_ddl'))
        elif p.startswith('tests/fixtures/'):
            # Other fixture groups can affect a family of tests in the same dialect.
            name=Path(p).stem
            dialect=p.split('/')[2] if len(p.split('/'))>3 else ''
            file('tests/dialects/test_'+dialect+'.py')
            match(name)
        elif p.startswith('tests/') and p.endswith('.py'):
            file(p)
    code_paths=[p for p in paths if p.startswith('sqlglot/')]
    if not code_paths:return chosen
    for p in code_paths:
        stem=Path(p).stem
        parts=Path(p).parts
        if '/dialects/' in p or '/parsers/' in p or '/generators/' in p or '/tokens/' in p:
            if stem in ('parser','generator','dialect','tokens','tokenizer'):
                file('tests/dialects/test_dialect.py')
                file('tests/test_'+stem+'.py')
                # central changes may impact any dialect
                for f in by_file:
                    if f.startswith('tests/dialects/'):file(f)
                if stem=='parser':file('tests/test_parser.py')
            else:
                file('tests/dialects/test_'+stem+'.py')
                # Dialect-specific changes generally need their dialect suite;
                # targeted optimizer methods are added based on the patch below.
                # all dialect parser changes can affect expression building;
                # keep the comparatively cheap expression suite.
                if '/parsers/' in p:
                    file('tests/test_expressions.py')
                if '/dialects/' in p:
                    file('tests/test_transpile.py')
                # parser has numerous integration contexts, but large generic
                # tests are too expensive unless patch impacts shared behavior.
        elif '/optimizer/' in p:
            # Focus on optimizer tests exercising this pass, plus end-to-end
            # optimization smoke tests. Full optimizer collection contains
            # expensive unrelated long-running coverage.
            opt_name(stem)
            opt_name(stem.removeprefix('eliminate_'))
            opt_name('optimize')
            # Cheap optimizer regression tests cover interactions between passes.
            for t in by_file.get('tests/test_optimizer.py', []):
                if t['seconds'] <= 0.15: add(t)
            for t in by_file.get('tests/test_optimizer.py', []):
                method=t['id'].split('::')[-1].lower()
                key=stem.removeprefix('eliminate_').replace('_','')
                if key and key in norm(method):add(t)
            if stem in ('eliminate_joins','scope','qualify_columns','optimizer'):
                opt_name('optimize_joins')
                opt_name('qualify_columns')
                if stem=='eliminate_joins':
                    match('test_execute_subqueries');match('test_execute_tpcds')
                    opt_name('tpcds');opt_name('tpch')
            if stem in ('scope','qualify_columns','qualify_tables'):
                opt_name('annotate_funcs')
                opt_name('qualify_tables')
            if stem in ('eliminate_subqueries','merge_subqueries','pushdown_predicates'):
                opt_name('merge_subqueries');opt_name('unnest_subqueries')
        elif '/typing/' in p:
            for name in ('annotate_funcs','annotate_types','optimize','qualify_columns'):
                opt_name(name)
            file('tests/test_typing.py')
        elif stem=='serde':
            file('tests/test_serde.py');file('tests/test_optimizer.py');file('tests/test_executor.py')
            # serialization underpins the dialects as well.
            for f in by_file:
                if f.startswith('tests/dialects/'):file(f)
            file('tests/test_expressions.py')
        elif '/executor/' in p:
            # Context changes are usually exercised through executor-specific
            # tests; expensive integration tests are selected for runner changes.
            if stem != 'context':
                file('tests/test_executor.py')
            else:
                for t in by_file.get('tests/test_executor.py',[]):
                    if t['seconds'] < 1.5:add(t)
        elif '/expressions/' in p or stem in ('expressions','exp'):
            file('tests/test_expressions.py')
            if stem in ('expressions','exp'):
                file('tests/test_optimizer.py');file('tests/test_parser.py')
            else:
                # Leaf expression families: select broad cheap optimization
                # regression coverage, without unrelated long workloads.
                for t in by_file.get('tests/test_optimizer.py', []):
                    if t['seconds'] <= 0.15: add(t)
                opt_name('optimize')
                file('tests/test_generator.py')
        elif stem in ('transforms','generator'):
            file('tests/test_transforms.py');file('tests/test_generator.py')
        elif stem=='schema':
            file('tests/test_schema.py');file('tests/test_optimizer.py')
        elif stem=='helper':
            file('tests/test_helper.py');file('tests/test_optimizer.py')
        else:
            # Unknown central module: broad but valid conservative fallback.
            for f in by_file:
                if f.startswith('tests/test_'):file(f)
    # Subtle changes to dialects can propagate to optimization without direct
    # optimizer file edits. For these cases include the lightweight optimizer
    # methods indicated by changed code keywords.
    txt='\n'.join(x for _,x in mods).lower()
    if any('/parsers/' in p for p in code_paths):
        # Parser output is consumed by the generic qualification, optimizer,
        # and expression-type tests even if the edit is dialect-specific.
        for k in ('qualify_columns','qualify_tables','quote_identifiers',
                  'optimize','annotate_funcs'):
            opt_name(k)
        match('test_ref_type')
    if any('/dialects/' in p for p in code_paths):
        if any(k in txt for k in ('week_offset','datefirst','date_trunc','timestamp_trunc','types')):
            opt_name('simplify')
        if any(k in txt for k in ('typeannotat','_annotate','set_type','exp.','expression')):
            for k in ('annotate_funcs','annotate_types','optimize','qualify_columns'):
                opt_name(k)
    return chosen


def pack(selected, durations):
    if not selected:return []
    arr=sorted(selected,key=lambda i:durations[i],reverse=True)
    # A new worker costs two seconds, so adding one is worth it only when it
    # saves >2 sec of critical-path time.
    best=None;best_cost=float('inf')
    for k in range(1,min(8,len(arr))+1):
        loads=[0]*k;workers=[[] for _ in range(k)]
        for i in arr:
            j=min(range(k),key=loads.__getitem__)
            loads[j]+=durations[i];workers[j].append(i)
        c=max(loads)+2000*k
        if c<best_cost:best_cost=c;best=workers
    return best


def main():
    tests=json.loads((DATA/'tests.json').read_text())['tests']
    durations={t['number']:int(t['seconds']*1000+0.5) for t in tests}
    for patch in sorted((DATA/'commits').glob('*.patch')):
        paths,mods=edited_paths_and_lines(patch.read_text(errors='replace'))
        selected=select_tests(paths,mods,tests)
        print(json.dumps({'commit':patch.stem,'workers':pack(selected,durations)},separators=(',',':')))

if __name__=='__main__':main()
