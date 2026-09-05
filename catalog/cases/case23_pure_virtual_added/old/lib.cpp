#include "lib.h"
#include <cstdio>

void Processor::process() { printf("processing\n"); }

extern "C" Processor* make_proc() { return new Processor(); }
