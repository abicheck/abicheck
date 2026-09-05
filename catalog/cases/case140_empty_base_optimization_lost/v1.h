#pragma once

// v1: `Tag` is an empty class, so the Empty Base Optimization (EBO) folds it to
// offset 0 and it costs zero bytes. The `Payload` base therefore *also* begins
// at offset 0, and the whole object is 16 bytes.
//
//   Widget layout (v1):  [Payload::value @0][extra @8]   sizeof = 16
//                         Tag subobject @0 (empty, folded)
struct Tag {};

struct Payload {
    long value;
};

struct Widget : Tag, Payload {
    long extra;
};

// Factory + accessor live in the library so a consumer never has to see the
// definitions to use them — but the *layout* still leaks into any caller that
// upcasts a `Widget*` to a `Payload*`.
extern "C" Widget* make_widget();
extern "C" long widget_payload_value(const Widget* w);
