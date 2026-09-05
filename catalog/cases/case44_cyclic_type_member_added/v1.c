#include "v1.h"
#include <stdlib.h>

int node_sum(Node n) { return n.data + n.flags; }

Node *node_create(int data) {
    Node *n = malloc(sizeof(Node));
    if (!n) return 0;
    n->data = data;
    n->flags = 0;
    n->next = 0;
    return n;
}
void node_free(Node *n) { free(n); }
