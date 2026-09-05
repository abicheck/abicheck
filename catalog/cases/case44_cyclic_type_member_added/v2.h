/* case44 v2: long priority added — sizeof(Node) grows 16→24 bytes — BREAKING */
#ifndef CASE44_V2_H
#define CASE44_V2_H

typedef struct Node {
    int   data;
    int   flags;
    long  priority;      /* NEW: +8 bytes — struct grows from 16 to 24 bytes */
    struct Node *next;
} Node;

int   node_sum(Node n);
Node *node_create(int data);
void  node_free(Node *n);

#endif
