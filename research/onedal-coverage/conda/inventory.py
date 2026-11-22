import csv,json,hashlib,sys
from pathlib import Path
from elftools.elf.elffile import ELFFile
rows=[]
for prefix in map(Path,sys.argv[1:]):
    pkg=next((prefix/'conda-meta').glob('dal-[0-9]*.json'))
    meta=json.loads(pkg.read_text())
    for file in meta['files']:
        path=prefix/file
        if '.so' not in path.name or path.is_symlink() or not path.is_file(): continue
        with path.open('rb') as f:
            elf=ELFFile(f)
            tags=list(elf.get_section_by_name('.dynamic').iter_tags())
            sym=elf.get_section_by_name('.dynsym')
            exports={s.name for s in sym.iter_symbols() if s.name and s['st_shndx']!='SHN_UNDEF' and s['st_info']['bind'] in ('STB_GLOBAL','STB_WEAK','STB_GNU_UNIQUE','STB_LOOS') and s['st_other']['visibility'] in ('STV_DEFAULT','STV_PROTECTED')}
            key=next((t.soname for t in tags if t.entry.d_tag=='DT_SONAME'),'')
            sections=[s.name for s in elf.iter_sections()]
            rows.append([meta['version'],file,key,path.stat().st_size,len(exports),','.join(n for n in sections if n.startswith(('.debug_info','.zdebug_info','.gnu_debug'))),','.join(t.needed for t in tags if t.entry.d_tag=='DT_NEEDED')])
            Path(f"{meta['version']}-{path.name}.exports").write_text('\n'.join(sorted(exports))+'\n')
with open('inventory.csv','w') as f:
    w=csv.writer(f);w.writerow(['release','file','soname','bytes','export_names','debug_sections','needed']);w.writerows(rows)
