---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# A labelled side-scoped `--include` appeared to suppress unlabelled global include roots

Index: [Known gaps — closed and moot](../archive/known-gaps-closed.md)

**Status (2026-11-01): CLOSED.** Reproduced with castxml and fixed by #1314's additive composition rule (`abicheck/model/sided_inputs.py`), which the labelled form reaches too. Pinned by `tests/test_sided_include_labelled_composition.py`; see the archive entry for the matrix and the not-comparable boundary for asymmetric labels.


Observed once, on a real PVXS comparison, and **not yet reduced to a minimal
reproduction** — recorded so the next person does not lose the observation.
Invoking:

```
compare old.so new.so --include old:public=OLD_INC --include new:public=NEW_INC \
  --include EPICS_INC --include EPICS_INC/os/Linux --include EPICS_INC/compiler/gcc ...
```

failed with `fatal error: 'epicsTime.h' file not found`, despite `EPICS_INC`
being passed as an unlabelled (both-sides) include root and genuinely containing
that header. The same roots passed to a bare `dump` worked. Re-expressing every
root in the per-side labelled form made the comparison succeed.

So either unlabelled roots are dropped once any labelled side-scoped root is
given for that side, or they are ordered such that castxml does not see them.
`--include`'s own help text documents the two forms as composable, so if this
reproduces it is a defect rather than a usage error. Next step: a minimal
two-header fixture mixing one labelled and one unlabelled root.

## Resolution (2026-11-01)

The minimal reproduction is `tests/test_sided_include_labelled_composition.py`:
each side's public header includes `sic_dep.h`, which exists only in a
shared root and itself includes `sic_os.h` from a nested shared root (the
`EPICS_INC` / `EPICS_INC/os/Linux` shape). Every combination of side
spelling (`old=`/`new=`, `old:public=`/`new:public=`) and shared spelling
(bare, `both=`, `both:support=`), in either argument order, parses both
sides and reports the header-only parameter change. Switching
`compose_sided_paths` back to replacement makes every cell fail, which is
the PVXS symptom: the observation was bug class
`config.sided_shared_input_dropped`, fixed in #1314 for every spelling.

Labelling one side's root and not the other's is refused as not comparable
(exit 16, `include_sequence`): a label is part of the extraction contract,
so that is two contracts, not a dropped root.
