#ifndef CASE198_H
#define CASE198_H

/* ---- Public API: the first two fields swapped places ----
 * Nothing was added or removed and sizeof(Record) is unchanged, so a
 * size-only check sees nothing. Every already-compiled consumer still reads
 * `id` at offset 0 -- where `flags` now lives.
 */
typedef struct {
    int  flags;
    int  id;
    long timestamp;
} Record;

void record_init(Record *r, int id);
int  record_id(const Record *r);

#endif
