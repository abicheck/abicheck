# Union and bit-field fragment campaign receipt

`receipt.json` and `cells.csv` come from

```bash
python -m abiforge.run.fragment_campaign OUT
```

(defaults: `--family gcc,clang --pack none,1 --opt O0,O2
--evidence dwarf,headers.stripped,headers`), run on Linux x86-64 with
gcc 13.3.0 and clang 18.1.3 (Ubuntu 24.04), castxml 0.7.0, Python 3.13.
The recorded `revision` is the commit the campaign ran on.

Counts are kept apart: 864 generated cells (36 operators x 24
configurations), 0 unsupported, 864 built and compared, 1,592 exact rows
judged, 118 cells with rows left unknown (alignment below header evidence,
or `-fpack-struct` unrecorded at stripped `{B,H}`). The 16 mismatches are
all the recorded castxml/gcc zero-width-barrier gap
(`docs/contribute/known-gaps.md`). These are finite results over this
fragment, not a percentage of C or C++.
