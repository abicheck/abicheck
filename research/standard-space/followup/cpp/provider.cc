#include "api.hpp"

static Probe observer;
int V::phase() {return 10;}
int L::phase() {return 20;}
int D::phase() {return 30;}
int R::right() {return 40;}

L::L() { observer(this, static_cast<V*>(this)); }
extern "C" L* create(Probe callback) {
    observer = callback;
    static D d;
    return &d;
}
extern "C" V* as_v(L* p) {return static_cast<V*>(p);}
Error::~Error() {}
extern "C" void raise_error() {throw Specific();}
