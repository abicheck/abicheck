### Fixed

- **BTF float type parsing no longer misaligns the type table** —
  `BTF_KIND_FLOAT` was treated as carrying a trailing 4-byte encoding
  word, but per `include/uapi/linux/btf.h` it has none at all (unlike
  `BTF_KIND_INT`, which does). This shifted every subsequent type
  record's own offset, spuriously truncating (or misparsing) an
  otherwise perfectly valid type table containing a real float type.
- **CFI alternate entry points now receive their own frame-register
  facts, not just coverage** — an exported symbol inside another
  function's FDE range was previously marked "covered" without
  attaching that FDE's decoded CFA-register/callee-saved-register facts
  to it, so a later CFI change specific to that one entry point would
  go undetected despite reporting complete evidence.
- **CFI coverage is now restricted to a binary's real exported symbols**
  — it previously iterated `.dynsym` and `.symtab` unconditionally,
  admitting any `STB_GLOBAL`/`STB_WEAK` symbol regardless of visibility.
  An unstripped library's `.symtab` also carries hidden/internal-
  visibility global functions that are real code but never part of the
  DSO's ABI surface; such a function commonly has no FDE of its own,
  wrongly downgrading otherwise-complete evidence. Now uses the same
  export predicate and `.dynsym`/`.symtab` selection as the canonical
  ELF export parser.
