#include "v1.h"

MessageBuilder::MessageBuilder() : buf_{}, len_(0) {}

MessageBuilder& MessageBuilder::append(const char* part) {
    while (*part && len_ + 1 < sizeof(buf_)) {
        buf_[len_++] = *part++;
    }
    buf_[len_] = '\0';
    return *this;
}

const char* MessageBuilder::str() { return buf_; }
