/* Old consumer that reads only an unmoved field (model: [.read "a"]). */
#include <stdio.h>
#include "rec.h"
int main(void) { struct rec *r = rec_new(); printf("%d\n", r->a); return 0; }
