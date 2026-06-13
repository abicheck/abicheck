/* Exported ABI surface — identical in v1 and v2. The only difference
   between the two builds is a security-hardening build/link flag, not the
   symbols or their types. */
int compute(int x) { return x * x + 1; }
int transform(int x, int y) { return x + y * 2; }
