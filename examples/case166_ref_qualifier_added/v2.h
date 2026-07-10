#ifndef MSGBUILDER_H
#define MSGBUILDER_H

/* v2: str() gained an lvalue ref-qualifier (&) so that
   `MessageBuilder().str()` — a pointer into a dying temporary —
   no longer compiles. Sound API hardening, but the ref-qualifier
   is part of the mangled name: _ZN14MessageBuilder3strEv becomes
   _ZNR14MessageBuilder3strEv, so every existing binary breaks. */
class MessageBuilder {
public:
    MessageBuilder();
    MessageBuilder& append(const char* part);
    const char* str() &;

private:
    char buf_[256];
    unsigned len_;
};

#endif
