/* Consumer of the opaque API: cannot name fields at all. */
#include <stdio.h>
#include "orec.h"
int main(void) { struct orec *r = orec_new(); printf("%d\n", orec_get_b(r)); return 0; }
