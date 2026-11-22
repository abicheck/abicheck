"""Usage: python inventory.py PREFIX OUT CLIENT... (Linux, clang Python bindings 19)."""
import csv
import os
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

from clang import cindex as ci
from variants import variant_reader

prefix, out = map(Path, sys.argv[1:3])
inc = prefix.resolve() / 'include'
out.mkdir(parents=True, exist_ok=True)
ci.Config.set_library_file(os.environ.get('LIBCLANG', '/usr/lib/x86_64-linux-gnu/libclang-19.so.1'))
variants = variant_reader(ci.Config.library_file)
roots = ['daal.h'] + sorted(str(p.relative_to(inc)) for p in (inc/'oneapi/dal').rglob('*.hpp') if 'detail' not in p.parts and 'backend' not in p.parts and 'gpu' not in p.parts and 'spmd' not in p.parts)
providers = defaultdict(set)
clients = defaultdict(set)
def symbols(path, flag):
    lines = subprocess.check_output(['nm','-D',flag,str(path)],text=True).splitlines()
    return {line.split()[-1].split('@')[0] for line in lines if line.split()}
for lib in sorted((prefix/'lib').glob('libonedal*.so.3')):
    if '_dpc' not in lib.name:
        for sym in symbols(lib,'--defined-only'):
            providers[sym].add(lib.name)
for binary in map(Path,sys.argv[3:]):
    for sym in symbols(binary,'--undefined-only'):
        clients[sym].add(binary.name)
errors = []
rows = set()
def parse(group):
    source = ''.join(f'#include <{h}>\n' for h in group)
    unit = ci.Index.create().parse('inventory.cpp', args=['-std=c++17','-I'+str(inc),'-I'+str(inc/'dal'),'-isystem',subprocess.check_output(['gcc','-print-file-name=include'],text=True).strip()],unsaved_files=[('inventory.cpp',source)], options=ci.TranslationUnit.PARSE_SKIP_FUNCTION_BODIES)
    failures = [str(d) for d in unit.diagnostics if d.severity >= ci.Diagnostic.Error]
    if failures:
        if len(group)>1:
            mid=len(group)//2
            parse(group[:mid])
            parse(group[mid:])
        else:
            errors.append((group[0],failures[0]))
        return
    def visit(c):
        if c.location.file:
            path=Path(c.location.file.name)
            if not path.is_relative_to(inc):
                return
            rel=path.relative_to(inc)
            if {'detail','internal','backend'} & set(rel.parts):
                return
            if c.access_specifier in (ci.AccessSpecifier.PRIVATE,ci.AccessSpecifier.PROTECTED):
                return
            kind=c.kind.name
            if kind in ('FUNCTION_DECL','CXX_METHOD','CONSTRUCTOR','DESTRUCTOR','FIELD_DECL','CXX_BASE_SPECIFIER','ENUM_DECL','FUNCTION_TEMPLATE','CLASS_TEMPLATE','STRUCT_DECL','CLASS_DECL'):
                usr=c.get_usr()
                sym=c.mangled_name if kind in ('FUNCTION_DECL','CXX_METHOD') and c.semantic_parent.kind!=ci.CursorKind.CLASS_TEMPLATE else ''
                category='virtual_slot' if kind=='CXX_METHOD' and c.is_virtual_method() else kind.lower()
                key=usr or f'{rel}:{c.location.line}:{c.spelling}'
                def record(cat,detail):
                    rows.add((key,cat,str(rel),detail,sym,','.join(sorted(providers.get(sym,()))),','.join(sorted(clients.get(sym,())))))
                record(category,c.spelling)
                if kind in ('CONSTRUCTOR','DESTRUCTOR') and c.semantic_parent.kind != ci.CursorKind.CLASS_TEMPLATE:
                    for abi_name in variants(c):
                        rows.add((key,kind.lower()+'_abi_variant',str(rel),c.spelling,abi_name,
                                  ','.join(sorted(providers.get(abi_name,()))),
                                  ','.join(sorted(clients.get(abi_name,())))))
                if kind in ('FUNCTION_DECL','CXX_METHOD','FUNCTION_TEMPLATE','CONSTRUCTOR'):
                    for label,typ in [('return',c.result_type)]+[(f'arg{i}',p.type) for i,p in enumerate(c.get_arguments() or [])]:
                        if typ.kind != ci.TypeKind.INVALID and typ.get_canonical().kind==ci.TypeKind.RECORD:
                            record('by_value_'+label,typ.spelling)
        for child in c.get_children():
            visit(child)
    visit(unit.cursor)
for group in ([roots[0]],roots[1:]):
    parse(group)
for name,header,data in [('obligations',['key','kind','header','detail','symbol','providers','client_imports'],rows),('unparsed',['root','diagnostic'],errors)]:
    with (out/(name+'.csv')).open('w') as f:
        w=csv.writer(f);w.writerow(header);w.writerows(sorted(data))
(out/'roots.txt').write_text('\n'.join(roots)+'\n')
