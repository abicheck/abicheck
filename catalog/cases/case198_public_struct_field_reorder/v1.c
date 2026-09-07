#include "v1.h"

void record_init(Record *r, int id) { r->id = id; r->flags = 0; r->timestamp = 0; }
int  record_id(const Record *r)     { return r->id; }
