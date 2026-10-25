namespace sycl { class queue {}; }
int base(int x) { return x; }
int foo_one(int x) { return x + 1; }
int foo_two(double) { return 2; }
int on_queue(sycl::queue&) { return 3; }
