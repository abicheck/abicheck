struct V { long marker; virtual int phase(); };
struct L : virtual V { L(); int phase(); };
struct R : virtual V { virtual int right(); };
#ifdef PRIVATE_RIGHT
struct D : L, private R { int phase(); };
#else
struct D : L, R { int phase(); };
#endif
struct Error { virtual ~Error(); };
#ifdef PRIVATE_ERROR
struct Specific : private Error {};
#else
struct Specific : Error {};
#endif
typedef void (*Probe)(L*, V*);
extern "C" L* create(Probe);
extern "C" V* as_v(L*);
extern "C" void raise_error();
