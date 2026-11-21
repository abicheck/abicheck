# Union and bit-field fragment campaign receipt

`receipt.json` and `cells.csv` come from

```bash
python -m abiforge.run.fragment_campaign OUT
```

(defaults: `--family gcc,clang --pack none,1 --opt O0,O2
--evidence dwarf,headers.stripped,headers`), run on Linux x86-64 with
gcc 13.3.0 and clang 18.1.3 (Ubuntu 24.04), castxml 0.7.0, Python 3.13.
The recorded `revision` is the base commit the working tree was built on
(the operators themselves were uncommitted at run time); rerun on the
merged revision to refresh it.

Counts are kept apart: 696 generated cells (29 operators x 24
configurations), 0 unsupported, 696 built and compared, 1,380 exact rows
judged, 98 cells with rows left unknown (alignment below header evidence,
or `-fpack-struct` unrecorded at stripped `{B,H}`). The 12 mismatches are
all the recorded castxml/gcc zero-width-barrier gap
(`docs/contribute/known-gaps.md`). These are finite results over this
fragment, not a percentage of C or C++.
