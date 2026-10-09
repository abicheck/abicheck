#ifndef MATHLIB_CONFIG_H
#define MATHLIB_CONFIG_H

/* Calling convention of every exported mathlib entry point.
   v1: the default System V AMD64 convention, spelled out explicitly. */
#define MATHLIB_CALL __attribute__((sysv_abi))

#endif
