#ifndef CRYPTO_H
#define CRYPTO_H

int crypto_hash(const char *data, int len);
int crypto_verify(const char *data, int len, int hash);

#endif
