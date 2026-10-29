/* Version 2 of the public header: normal_fn's return type widens (matches v2.c). */
#ifndef LIVE_STORED_API_H
#define LIVE_STORED_API_H

long normal_fn(int x);

#if 0
int hidden_fn(int x);
#endif

#ifdef LIVE_STORED_NOT_DEFINED
int guarded_fn(int x);
#endif

#endif /* LIVE_STORED_API_H */
