// v2: fast_hash now lives in the library
int fast_hash(int x) {
    return static_cast<int>(static_cast<unsigned>(x) * 2654435761U);
}
