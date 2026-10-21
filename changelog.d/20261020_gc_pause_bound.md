### Fixed

- Overlapping `gc_paused` scopes (snapshot-cache loads on parallel member threads) could keep the cyclic collector off for a whole warm release run, so cyclic garbage from compare threads was never collected — 28 GiB vs 7.9 GiB cold on python3.15t. Collection is now forced once collection has been off for 0.5 s continuously (`abicheck.storage.acyclic_json.MAX_CONTINUOUS_PAUSE_S`).
