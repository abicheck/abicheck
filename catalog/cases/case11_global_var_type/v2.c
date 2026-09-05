/* type widened int→long — symbol size changes */
/* Note: 5000000000L requires LP64 (64-bit long). On LP64 (Linux x86-64),
   sizeof(long)=8 and this value fits. On ILP32 (32-bit), long is 4 bytes
   and this literal would overflow — demo is intended for LP64 targets only. */
long lib_version = 5000000000L;
