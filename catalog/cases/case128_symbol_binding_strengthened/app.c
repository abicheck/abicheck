#include <stdio.h>
#include "v1.h"

/* The consumer simply calls both symbols. It resolves and behaves identically
 * against v1 and v2 — strengthening a weak symbol to global is backward
 * compatible: every binary that resolved to the weak definition still resolves
 * to the same address, and the symbol can no longer be silently replaced. */
int main(void)
{
    int r = compute(5) + helper(5);
    printf("result = %d\n", r);
    return r == 16 ? 0 : 1;
}
