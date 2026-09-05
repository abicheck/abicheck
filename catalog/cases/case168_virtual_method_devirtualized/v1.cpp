#include "v1.h"

Codec::Codec() : pending(0) {}
Codec::~Codec() {}

int Codec::encode(int value) {
    ++pending;
    return value * 2;
}

int Codec::flush() {
    int n = pending;
    pending = 0;
    return n;
}

int Codec::reset() {
    pending = 0;
    return -1;
}

extern "C" Codec* codec_create() { return new Codec(); }
extern "C" void codec_destroy(Codec* c) { delete c; }
