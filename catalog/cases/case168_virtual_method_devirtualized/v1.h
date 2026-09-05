#ifndef CODEC_H
#define CODEC_H

/* v1 vtable (Itanium, after offset-to-top/typeinfo):
   [~Codec D1] [~Codec D0] [encode] [flush] [reset] */
class Codec {
public:
    Codec();
    virtual ~Codec();
    virtual int encode(int value);
    virtual int flush();
    virtual int reset();
    int pending;
};

extern "C" Codec* codec_create();
extern "C" void   codec_destroy(Codec* c);

#endif
