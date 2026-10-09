#ifndef MATHLIB_CONFIG_H
#define MATHLIB_CONFIG_H

/* Calling convention of every exported mathlib entry point.
   v2: switched to the Microsoft x64 convention. The public header is
   textually unchanged -- only this macro's expansion differs. */
#define MATHLIB_CALL __attribute__((ms_abi))

#endif
