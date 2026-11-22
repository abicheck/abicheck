from pathlib import Path
import sys
root=Path('/tmp/dal-2025.9.0/include/dal')
rel=Path('algorithms/tree_utils/tree_utils.h')
src=(root/rel).read_text()
for name,match in [('slots',('virtual bool onSplitNode','virtual bool onLeafNode')),('fields',('size_t featureIndex;', 'double featureValue;')),('alias',None)]:
    p=Path(name)/rel;p.parent.mkdir(parents=True,exist_ok=True)
    lines=src.splitlines(True)
    if match:
        i,j=[next(i for i,l in enumerate(lines) if token in l) for token in match];lines[i],lines[j]=lines[j],lines[i]
    else:
        lines=[l.replace('double featureValue;', 'using feature_t = double; feature_t featureValue;') for l in lines]
    p.write_text(''.join(lines))
