__attribute__((visibility("default"))) int public_api(int x) { return x; }
__attribute__((visibility("default"))) int internal_helper(int x) { return x * 2; }  /* accidentally exported */
__attribute__((visibility("default"))) int another_impl(int x) { return x + 3; }      /* accidentally exported */
