/* Version 2: normal_fn's return type widens, a source-breaking ABI change. */
long normal_fn(int x) { return x; }
int hidden_fn(int x) { return x + 1; }
int guarded_fn(int x) { return x + 2; }
