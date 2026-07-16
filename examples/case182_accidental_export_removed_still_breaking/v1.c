#include "v1.h"

/* Never declared in v1.h — an accidental export (default visibility, no
 * header). A tool that only trusts headers has no way to prove this symbol
 * is "supported" or "unsupported" from source alone.
 */
int internal_helper(int x) { return x * 2; }

int public_api(int x) { return internal_helper(x) + 1; }
