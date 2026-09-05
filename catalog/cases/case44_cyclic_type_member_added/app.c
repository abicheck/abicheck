#include "v1.h"
#include <stdio.h>

int main(void) {
    Node n = {0};
    n.data = 10;
    n.flags = 3;
    n.next = node_create(1); /* non-NULL pointer makes v2 by-value mismatch visible */

    int sum = node_sum(n);
    printf("node_sum = %d\n", sum);
    printf("expected = 13\n");

    if (n.next) node_free(n.next);

    if (sum != 13) {
        printf("CORRUPTION: Node by-value ABI changed (sizeof/layout mismatch)\n");
        return 1;
    }
    return 0;
}
