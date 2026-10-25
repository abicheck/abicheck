#include "api.h"
int algo_a::fa(Parameter* p) { return p->a; }
int algo_b::fb(Parameter* p) { return p->b; }
int algo_c::fc(Parameter* p) { return p->c; }
int algo_d::fd(inner::Parameter* p) { return p->d; }
int fh(Holder* h) { return h->p.h; }
