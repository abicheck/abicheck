#pragma once
#include "thirdparty_v1.h"   /* exposes ThirdPartyHandle in public API */

/* libfoo v1 public API */
void process(ThirdPartyHandle* h);
int  get_value(const ThirdPartyHandle* h);
