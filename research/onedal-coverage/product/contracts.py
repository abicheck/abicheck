"""Compile old clients once, replace the provider, then compare stripped ELF with/without headers."""
import csv
import json
import os
import subprocess as sp
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

prefix=Path(sys.argv[1])
common=(prefix/'include/oneapi/dal/common.hpp').read_text()
fields='std::int64_t start_idx;\n    std::int64_t end_idx;'
assert common.count(fields)==1
extra='\nextern "C" long consume(oneapi::dal::range);\nextern "C" oneapi::dal::range produce();\n'
provider='extern "C" long consume(oneapi::dal::range r){return 10*r.start_idx+r.end_idx;}\nextern "C" oneapi::dal::range produce(){return {3,7};}'
template='template<class T> T twice(T x){return x+x;}\nextern template int twice(int);\n'
rows=[]
def run(args,**kw):
    return sp.run(args,check=True,**kw)
with TemporaryDirectory() as tmp:
    root=Path(tmp)
    for family,base,broken,twin,impl,client in [
        ('range_argument',common+extra,common.replace(fields,'std::int64_t end_idx; std::int64_t start_idx;')+extra,common.replace(fields,'using index_t=std::int64_t; index_t start_idx; index_t end_idx;')+extra,provider,'return consume({3,7})!=37;'),
        ('range_return',common+extra,common.replace(fields,'std::int64_t end_idx; std::int64_t start_idx;')+extra,common.replace(fields,'using index_t=std::int64_t; index_t start_idx; index_t end_idx;')+extra,provider,'auto r=produce(); return r.start_idx!=3 || r.end_idx!=7;'),
        ('range_register_argument',common+extra,common.replace(fields,'double start_idx; std::int64_t end_idx;')+extra,common.replace(fields,'using index_t=std::int64_t; index_t start_idx; index_t end_idx;')+extra,provider,'return consume({3,7})!=37;'),
        ('range_register_return',common+extra,common.replace(fields,'double start_idx; std::int64_t end_idx;')+extra,common.replace(fields,'using index_t=std::int64_t; index_t start_idx; index_t end_idx;')+extra,provider,'auto r=produce(); return r.start_idx!=3 || r.end_idx!=7;'),
        ('template_provider',template,template,template,'template int twice(int);','return twice(4)!=8;'),
        ('symbol_version','extern "C" int value();','extern "C" int value();','extern "C" int value();','extern "C" int value(){return 7;}','return value()!=7;')]:
        old=root/family/'old'
        for case,header in [('old',base),('break',broken),('control',twin)]:
            d=old.parent/case; d.mkdir(parents=True)
            (d/'public.hpp').write_text(header)
            source=impl
            if family=='template_provider' and case=='break':
                source='template long twice(long);'
            if family=='template_provider' and case=='control':
                source+='\ntemplate long twice(long);'
            flags=[]
            if family=='symbol_version':
                script='V2 { global: value; };' if case=='break' else 'V1 { global: value; };'
                if case=='control':
                    script+=' V2 { } V1;'
                (d/'version.map').write_text(script)
                flags=['-Wl,--version-script=version.map']
            (d/'provider.cpp').write_text('#include "public.hpp"\n'+source)
            run(['g++','-std=c++17','-O1','-fPIC','-shared','-s','provider.cpp',*flags,'-o','libprobe.so'],cwd=d)
            if case=='old':
                (d/'client.cpp').write_text('#include "public.hpp"\nint main(){'+client+'}')
                run(['g++','-std=c++17','client.cpp','-L.','-lprobe','-o','client'],cwd=d)
            result=sp.run([old/'client'],env={**os.environ,'LD_LIBRARY_PATH':str(d)},capture_output=True)
            assert (result.returncode!=0)==(case=='break'),(family,case,result)
            if case=='old':
                continue
            for lane in ['elf','headers']:
                opts=[] if lane=='elf' else ['--header','old='+str(old/'public.hpp'),'--header','new='+str(d/'public.hpp')]
                report=d/(lane+'.json')
                sp.run([sys.executable,'-m','abicheck.cli','compare',old/'libprobe.so',d/'libprobe.so',*opts,'-o','json='+str(report)],capture_output=True)
                data=json.loads(report.read_text())
                rows.append([family,case,lane,result.returncode,data['verdict'],','.join(c['kind'] for c in data['changes'])])
with open('contracts.csv','w') as f:
    w=csv.writer(f);w.writerow(['family','case','lane','client_exit','checker','findings']);w.writerows(rows)
