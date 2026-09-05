/* hook_point has STV_PROTECTED visibility — this prevents interposition
   for references made from within the defining shared object, but does
   not affect external symbol resolution. */
int hook_point(int x);
int compute(int x);
