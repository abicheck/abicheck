#ifndef CASE198_H
#define CASE198_H

/* ---- Public API: the struct itself is part of the published contract ---- */
typedef struct {
    int  id;
    int  flags;
    long timestamp;
} Record;

void record_init(Record *r, int id);
int  record_id(const Record *r);

#endif
