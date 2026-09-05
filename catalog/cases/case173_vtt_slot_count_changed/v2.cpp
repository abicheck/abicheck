#include "v2.h"

int Base::id() { return 0; }
Base::~Base() {}

int Left::left() { return 1; }
int Right::right() { return 2; }
int Mixin::mixin() { return 3; }

extern "C" Diamond* make_diamond() { return new Diamond(); }
extern "C" unsigned long diamond_size() { return sizeof(Diamond); }
