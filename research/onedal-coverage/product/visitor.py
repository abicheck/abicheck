"""Run after ../pilot/run.py. Supply its out/ directory."""
import csv
import json
import os
from pathlib import Path
import subprocess
import sys
out=Path(sys.argv[1]).resolve()
old=out/'old'
rows=[]
for case in ('base_level_impurity','slots','field_alias','nonvirtual','identity'):
    new=out/case
    client=subprocess.run([out/'client'],env={**os.environ,'LD_LIBRARY_PATH':str(new)}).returncode
    for side in (old,new):
        subprocess.run(['strip','--strip-debug','-o',side/'stripped/libpilot.so',side/'libpilot.so'],check=True)
    opts=['--header','old='+str(old/'probe.hpp'),'--header','new='+str(new/'probe.hpp'),'--include','old='+str(old),'--include','new='+str(new)]
    report=new/'headers-stripped.json'
    report.unlink(missing_ok=True)
    subprocess.run([sys.executable,'-m','abicheck.cli','compare',old/'stripped/libpilot.so',new/'stripped/libpilot.so',*opts,'-o','json='+str(report)],capture_output=True)
    data=json.loads(report.read_text())
    rows.append([case,client,data['verdict'],','.join(c['kind'] for c in data['changes'])])
with open('visitor.csv','w') as f:
    writer=csv.writer(f);writer.writerow(['case','client_exit','checker','findings']);writer.writerows(rows)
