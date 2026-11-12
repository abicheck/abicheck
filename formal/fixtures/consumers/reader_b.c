/* Old consumer that reads the moved field directly (model: [.read "b"]). */
#include <stdio.h>
#include "rec.h"
int main(void) { struct rec *r = rec_new(); printf("%d\n", r->b); return 0; }
