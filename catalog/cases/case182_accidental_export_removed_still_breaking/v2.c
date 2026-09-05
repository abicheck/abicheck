#include "v2.h"

/* internal_helper() is gone: its logic was inlined directly into
 * public_api(). Nothing in v1.h ever promised internal_helper() would stay
 * around, so from a "public contract" point of view this looks like cleaning
 * up an implementation detail.
 */
int public_api(int x) { return x * 2 + 1; }
