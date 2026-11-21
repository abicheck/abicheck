// D has a virtual base V; L's constructor makes a virtual call through V
// while D is being built, so it dispatches through the VTT entry for
// V-in-L-in-D (a construction vtable). Retargeting that one entry to V's own
// vtable keeps the VTT's size and changes the call's result: 20 -> 10.
struct V { long marker; virtual int phase(); };
struct L : virtual V { L(); int phase(); };
struct R : virtual V { virtual int right(); };
struct D : L, R { int phase(); };
typedef void (*Probe)(L*, V*);
extern "C" L* create(Probe);
extern "C" V* as_v(L*);
