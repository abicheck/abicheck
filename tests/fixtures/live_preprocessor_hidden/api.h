/* Public header for the live-vs-stored census fixture (see
 * tests/test_live_stored_census_parity.py). `hidden_fn` and `guarded_fn` are
 * exported by the library but declared only under preprocessor branches the
 * header parse does not take. */
#ifndef LIVE_STORED_API_H
#define LIVE_STORED_API_H

int normal_fn(int x);

#if 0
int hidden_fn(int x);
#endif

#ifdef LIVE_STORED_NOT_DEFINED
int guarded_fn(int x);
#endif

#endif /* LIVE_STORED_API_H */
