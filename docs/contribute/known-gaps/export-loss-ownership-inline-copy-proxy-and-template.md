---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# Export-loss ownership: inline-copy proxy and template-specialization owner remain open (see export-loss-ownership-root-cause.md)

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** This is a pointer stub to export-loss-ownership-root-cause.md. It still lists two open items: is_inline is used as a proxy, and there is no Itanium type decoder for specialization owners. Not re-verified in code beyond that.


`func_removed_elf_only`'s ctor/dtor exemption resolved its owner through an
index keyed by the *unqualified* record name, and then treated "the owning
class is still declared" as a reason to drop a concrete export loss. Both were
falsified by measurement (a loader running a client built once against OLD),
and the mislabelled `-O0` vs `-O2` corpus entry the exemption was built to
satisfy was itself a true positive under its own header. Full account, the
runtime controls, the intentional semantic change and the remaining gaps:
[`export-loss-ownership-root-cause.md`](../export-loss-ownership-root-cause.md).
What is still open there: the inline-definition fact is read from the
declaration's `is_inline`, which is a fact about declaration linkage, not
proof that a consumer emitted its own copy; and the recovered owner path for
a class-template specialization names only the primary template, since this
repository has no Itanium type decoder.
