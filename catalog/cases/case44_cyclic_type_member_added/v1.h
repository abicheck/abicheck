/* case44: Cyclic type — member added to a self-referential struct
 *
 * A linked-list node gains a new `long priority` member. On 64-bit systems
 * the existing padding is consumed, so sizeof(Node) grows from 16 to 24 bytes.
 * node_sum() accepts Node by value — so the size is ABI-visible.
 *
 * BREAKING: TYPE_SIZE_CHANGED (sizeof(Node): 16 → 24 bytes on 64-bit)
 * libabigail equivalent: Added_Non_Virtual_Member_Variable (in cyclic type)
 */
#ifndef CASE44_V1_H
#define CASE44_V1_H

typedef struct Node {
    int   data;
    int   flags;
    struct Node *next;
} Node;

/* by-value: sizeof(Node) is part of the ABI call contract */
int   node_sum(Node n);
Node *node_create(int data);
void  node_free(Node *n);

#endif
