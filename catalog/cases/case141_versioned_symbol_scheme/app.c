/* Consumer built against v1's versioned symbols. After the v2 major bump every
 * `_3` symbol is gone, so this app fails to load against v2 (undefined symbol). */
#include "v1.h"
int main(void) { return mylib_init_3(0) + mylib_open_3(1); }
