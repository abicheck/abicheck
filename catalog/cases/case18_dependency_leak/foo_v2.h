#pragma once
#include "thirdparty_v2.h"   /* same public API, but ThirdPartyHandle grew */

/* libfoo v2 public API — source unchanged, but ABI broke via dependency */
void process(ThirdPartyHandle* h);
int  get_value(const ThirdPartyHandle* h);
