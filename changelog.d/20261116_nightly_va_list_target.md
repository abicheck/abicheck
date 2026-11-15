### Fixed

- **A clang header parse on a pointer-valued `va_list` target now recognises the
  `va_list` parameter.** On i386 (`-m32`) `__builtin_va_list` is `char *`, so the
  decayed parameter desugars to a plain pointer and the x86-64 struct spelling
  never matches. The parse keeps the `va_list` name on the parameter, and the
  recognizer now reads that name as well, so `PARAM_BECAME_VA_LIST` and
  `PARAM_LOST_VA_LIST` are reported on 32-bit targets. Extractor generation 75.
