/* Old consumer that never dereferences fields (model: [.passOpaque]). */
#include <stdio.h>
#include "rec.h"
int main(void) { struct rec *r = rec_new(); printf("%d\n", rec_get_b(r)); return 0; }
