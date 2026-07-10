#ifndef MSGBUILDER_H
#define MSGBUILDER_H

/* v1: str() carries no ref-qualifier — callable on lvalues and rvalues. */
class MessageBuilder {
public:
    MessageBuilder();
    MessageBuilder& append(const char* part);
    const char* str();

private:
    char buf_[256];
    unsigned len_;
};

#endif
