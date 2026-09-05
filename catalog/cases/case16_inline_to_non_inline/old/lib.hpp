#pragma once
// v1: fast_hash is inline — lives in the header, NOT compiled into .so
inline int fast_hash(int x) {
    return static_cast<int>(static_cast<unsigned>(x) * 2654435761U);
}
