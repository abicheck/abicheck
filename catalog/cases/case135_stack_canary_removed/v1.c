/* Exported ABI surface — identical in v1 and v2. process() has a stack
   buffer, so -fstack-protector inserts a canary (a reference to
   __stack_chk_fail). v2 is built with -fno-stack-protector, removing it.
   The symbol signatures are unchanged. */
#include <string.h>
void process(char *out, const char *in) { char buf[64]; strncpy(buf, in, sizeof buf - 1); buf[63] = 0; strcpy(out, buf); }
int compute(int x) { return x * x + 1; }
