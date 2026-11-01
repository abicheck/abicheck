---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# A one-sided release member with a damaged native body reads as unmatched

Index: [Known gaps](../known-gaps.md)

**Status (recorded 2026-11-01): OPEN.** Hygiene.

## Evidence

A release `compare` extracts only the members that pair. Since 2026-11-01 an
unmatched member is probed by content
(`workflows/release_member_readability.unreadable_unmatched_members`), and one
that no reader recognizes is recorded `failed`. The probe
(`extract/member_identity.is_content_readable_member`) reads the format only:
ELF, PE or Mach-O magic bytes, a snapshot fingerprint, or a linker script
resolving to one of those. A file with valid native magic and a truncated or
corrupt body passes the probe, is never extracted because it has no
counterpart, and reads as a one-sided `not_supplied` member. Once paired, it
fails at extraction and is `failed`.

## Intended resolution

A bounded structural parse of each unmatched native member (its section or
load-command table), so a damaged body is `failed` without a full dump.
Measure its cost on a large release first: it reads every one-sided member.
