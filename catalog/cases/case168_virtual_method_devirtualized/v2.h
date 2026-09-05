#ifndef CODEC_H
#define CODEC_H

/* v2: flush() was devirtualized ("nobody overrides it, and non-virtual
   calls are faster"). The symbol _ZN5Codec5flushEv still exists with an
   identical signature — link and load succeed — but flush() left the
   vtable, so every slot after it shifts up one position:
   v1: [D1] [D0] [encode] [flush] [reset]
   v2: [D1] [D0] [encode] [reset]
   An app compiled against v1 that calls flush() virtually dispatches
   through the old slot index and lands in reset(). */
class Codec {
public:
    Codec();
    virtual ~Codec();
    virtual int encode(int value);
    int flush(); /* was virtual */
    virtual int reset();
    int pending;
};

extern "C" Codec* codec_create();
extern "C" void   codec_destroy(Codec* c);

#endif
