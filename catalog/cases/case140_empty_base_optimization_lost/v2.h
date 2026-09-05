#pragma once

// v2: `Tag` gained a single data member. It is no longer empty, so the Empty
// Base Optimization no longer applies: the `Tag` base now occupies 8 bytes at
// offset 0 and pushes the `Payload` base subobject down to offset 8. Every
// caller that upcasts `Widget*` to `Payload*` (or reads `Payload::value`
// through such a pointer) using the v1 offset of 0 now reads the wrong bytes.
//
//   Widget layout (v2):  [Tag::state @0][Payload::value @8][extra @16]  sizeof = 24
//                         Payload subobject moved 0 -> 8
struct Tag {
    long state;  // <-- the only source change; EBO is silently lost
};

struct Payload {
    long value;
};

struct Widget : Tag, Payload {
    long extra;
};

extern "C" Widget* make_widget();
extern "C" long widget_payload_value(const Widget* w);
