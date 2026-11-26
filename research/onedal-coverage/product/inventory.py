"""Usage: python inventory.py PREFIX OUT CLIENT... (Linux, clang Python bindings 19)."""
import argparse
import csv
import os
import subprocess
from collections import defaultdict
from pathlib import Path

from clang import cindex as ci
from variants import variant_reader

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('prefix', type=Path)
parser.add_argument('out', type=Path)
parser.add_argument('clients', nargs='*', type=Path)
parser.add_argument('--source', type=Path, help='also allow the oneDAL source tree and its IDE dispatcher context')
parser.add_argument('--all-providers', action='store_true', help='join host-parsed SDK declarations to all installed CPU and DPC SONAMEs')
parser.add_argument('--extended', action='store_true', help='inventory protected declarations and provider-matched exported variables')
parser.add_argument('--include', action='append', type=Path, default=[], help='optional SDK include root, repeatable')
parser.add_argument('--legacy-umbrella', action='store_true', help='parse legacy headers after daal.h')
parser.add_argument('--roots', type=Path, help='read one installed include path per line')
options = parser.parse_args()
prefix, out = options.prefix, options.out
inc = prefix.resolve() / 'include'
out.mkdir(parents=True, exist_ok=True)
ci.Config.set_library_file(os.environ.get('LIBCLANG', '/usr/lib/x86_64-linux-gnu/libclang-19.so.1'))
variants = variant_reader(ci.Config.library_file)
roots = ['daal.h'] + sorted(str(p.relative_to(inc)) for p in (inc/'oneapi/dal').rglob('*.hpp') if 'detail' not in p.parts and 'backend' not in p.parts and 'gpu' not in p.parts and 'spmd' not in p.parts)
if options.extended:
    roots += sorted(str(p.relative_to(inc)) for p in (inc/'dal').rglob('*.h')
                    if not {'internal', 'backend', 'detail'} & set(p.relative_to(inc).parts))
if options.roots:
    roots = options.roots.read_text().splitlines()
extra_flags = ['-I' + str(options.source.resolve() / 'cpp'), '-D__ONEDAL_IDE_MODE__'] if options.source else []
extra_flags.extend('-I' + str(p.resolve()) for p in options.include)
providers = defaultdict(set)
clients = defaultdict(set)
def symbols(path, flag):
    lines = subprocess.check_output(['nm','-D',flag,str(path)],text=True).splitlines()
    return {line.split()[-1].split('@')[0] for line in lines if line.split()}
for lib in sorted((prefix/'lib').glob('libonedal*.so.*')):
    # Use the runtime SONAME, excluding unversioned links and fully versioned aliases.
    if not lib.name.split('.so.')[-1].isdigit():
        continue
    if options.all_providers or '_dpc' not in lib.name:
        for sym in symbols(lib,'--defined-only'):
            providers[sym].add(lib.name)
for binary in options.clients:
    for sym in symbols(binary,'--undefined-only'):
        clients[sym].add(binary.name)
errors = []
rows = set()
def parse(group):
    source = ''.join(f'#include <{h}>\n' for h in group)
    if options.legacy_umbrella and any(h.startswith('dal/') for h in group):
        source = '#include <daal.h>\n' + source
    unit = ci.Index.create().parse('inventory.cpp', args=['-std=c++17','-I'+str(inc),'-I'+str(inc/'dal'),*extra_flags,'-isystem',subprocess.check_output(['gcc','-print-file-name=include'],text=True).strip()],unsaved_files=[('inventory.cpp',source)], options=ci.TranslationUnit.PARSE_SKIP_FUNCTION_BODIES)
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
            if c.access_specifier == ci.AccessSpecifier.PRIVATE or (c.access_specifier == ci.AccessSpecifier.PROTECTED and not options.extended):
                return
            kind=c.kind.name
            if options.extended and kind == 'VAR_DECL' and c.linkage == ci.LinkageKind.EXTERNAL:
                symbol = c.mangled_name
                if symbol in providers:
                    rows.add((c.get_usr(), 'exported_variable', str(rel), c.spelling, symbol,
                              ','.join(sorted(providers[symbol])), ','.join(sorted(clients.get(symbol, ())))))
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
