/* case84 — synthetic stand-in for libonedal_core. The same source is
 * compiled into the v1 and v2 release directories; only the SONAME
 * differs (set via -Wl,-soname=libonedal_core.so.X at link time). */
int onedal_core_compute(int n) { return n + 1; }
