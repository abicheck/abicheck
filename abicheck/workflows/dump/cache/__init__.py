# Copyright 2026 Nikolay Petrov
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The whole-snapshot dump cache: one owner for "dump request -> cached snapshot,
or compute and store".

:func:`~.api.cached_run_dump` is the only entry point callers use. The rest of
the package is private, so every input a dump's output depends on is keyed in
exactly one place:

* :mod:`.api` -- the cacheability gate, the normalised request
  (:class:`~.api.DumpCacheRequest`), the single key derivation for a request
  (``_cache_key_for``), and the lookup-or-compute-and-store flow;
* :mod:`.key` -- the key itself: which request fields, header/include content
  and tool identities reach it (``_dump_cache_extra_key``) and the content hash
  (``_cache_key``);
* :mod:`.salt` -- ``SNAPSHOT_CACHE_VERSION``, the per-change salt that
  invalidates every entry on an upgrade, with its history;
* :mod:`.store` -- the on-disk entries, LRU eviction and the write policy.

The schema version and the abicheck code fingerprint are also folded into every
key (:mod:`.key`), so a schema bump or an extractor change misses without a hand
bump.

This lives in ``workflows/dump`` rather than ``storage/`` (ADR-061): deriving a
key reads the dump pipeline's own resolution -- the compile context, the dump
manifest, the header-AST and toolchain identities -- and ``storage`` may import
only ``model``. ``run_dump`` is passed in as a callable, so this package never
imports ``workflows.dump.native``, which calls it.
"""
