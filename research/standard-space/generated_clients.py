"""Run compiled clients against selected abiforge renders. See checker.py for setup."""
import csv
from pathlib import Path
import subprocess
import tempfile

from abiforge.operators import matrix

cases = {
    "struct_swap_fields": ("", "struct point p={11,22}; printf(\"%.0f\\n\",scale(p,1));"),
    "virtual_reorder": ('extern "C" Shape* shape(){static Shape s;s.w=7;return &s;}',
                        'printf("%d/%d\\n",shape_area(*shape()),shape()->area());'),
    "primary_base_grew": ('extern "C" MI* object(){static MI obj;return &obj;} '
                          'extern "C" B2* base(){return object();}',
                          'printf("%d\\n",static_cast<B2*>(object())==base());'),
}
rows = []
for op, seed in matrix():
    if op.name not in cases:
        continue
    cc, ext = ("gcc","c") if seed.name=="c_core" else ("g++","cc")
    adapter, body = cases[op.name]
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        def run(*args):
            return subprocess.check_output(args,cwd=d,text=True).strip()
        for stage, edits in (("old",{}),("new",op.edit(seed.roles))):
            h, src = seed.render(edits)
            (d/"edit_seed.h").write_text(h)
            (d/("lib."+ext)).write_text(src+"\n"+adapter)
            run(cc,"-shared","-fPIC","lib."+ext,"-o","libp.so")
            if stage=="old":
                decl = 'extern "C" MI* object(); extern "C" B2* base();' if op.name=="primary_base_grew" else ('extern "C" Shape* shape();' if adapter else "")
                (d/("app."+ext)).write_text('#include "edit_seed.h"\n#include <stdio.h>\n'+decl+'int main(){'+body+'return 0;}')
                run(cc,"app."+ext,"-L.","-lp","-Wl,-rpath,$ORIGIN","-o","app")
            rows.append((op.name,seed.name,stage,run("./app")))
with Path(__file__).with_name("generated_clients.csv").open("w") as f:
    writer=csv.writer(f,lineterminator="\n")
    writer.writerow(["operator","seed","provider","observed"])
    writer.writerows(rows)
