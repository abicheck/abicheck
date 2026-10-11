---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# A labelled side-scoped `--include` appeared to suppress unlabelled global include roots

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** unverified: the entry itself has no minimal reproduction. The labelled/unlabelled --include merge in frontends/cli/options/params.py:187+ was not exercised with castxml here.


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
