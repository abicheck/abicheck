"""By-value return probe using the complete public common.hpp."""
from pathlib import Path
import os, sys, subprocess as sp, csv, json
root=Path.cwd()
header=(Path(sys.argv[1])/"cpp/oneapi/dal/common.hpp").read_text()
pair="std::int64_t start_idx;\n    std::int64_t end_idx;"
assert header.count(pair)==1
cases={"old":header,"swap":header.replace(pair,"std::int64_t end_idx;\n    std::int64_t start_idx;"),"alias":header.replace(pair,"using index_t = std::int64_t;\n    index_t start_idx;\n    index_t end_idx;")}
provider='#include "api.hpp"\noneapi::dal::range normalized() { return oneapi::dal::range(3,-1).normalize_range(17); }\n'
client='#include "api.hpp"\nint main(){ auto r=normalized(); return r.start_idx==3 && r.end_idx==17 ? 0 : 1; }\n'
rows=[]
old=root/'out/range_old'
for case,source in cases.items():
    d=root/('out/range_'+case); d.mkdir(parents=True,exist_ok=True)
    (d/'common.hpp').write_text(source)
    (d/'api.hpp').write_text('#include "common.hpp"\noneapi::dal::range normalized();\n')
    (d/'provider.cpp').write_text(provider)
    sp.run(['g++','-std=c++17','-O0','-g','-shared','-fPIC','provider.cpp','-o','libpilot.so'],cwd=d,check=True)
    sp.run(['strip','--strip-debug','-o','minimal.so','libpilot.so'],cwd=d,check=True)
    if case=='old':
        (d/'client.cpp').write_text(client)
        sp.run(['g++','-std=c++17','client.cpp','-L.','-lpilot','-o','client'],cwd=d,check=True)
    rc=sp.run([old/'client'],env={**os.environ,'LD_LIBRARY_PATH':str(d)}).returncode
    for lane in ['symbols','dwarf','headers']:
        lib='minimal.so' if lane=='symbols' else 'libpilot.so'
        opts=['--header','old='+str(old/'api.hpp'),'--header','new='+str(d/'api.hpp')] if lane=='headers' else []
        with (d/(lane+'.log')).open('w') as log:
            status=sp.run([sys.executable,'-m','abicheck.cli','compare',old/lib,d/lib,*opts,'-o','json='+str(d/(lane+'.json'))],stdout=log,stderr=log).returncode
        data=json.loads((d/(lane+'.json')).read_text())
        rows.append([case,lane,rc,status,data['verdict'],','.join(c['kind'] for c in data['changes'])])
with open('range_results.csv','w') as f:
    w=csv.writer(f);w.writerow(['case','lane','client_exit','checker_exit','verdict','kinds']);w.writerows(rows)
