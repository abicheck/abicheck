### Performance

- Decoding a stored surface graph shares each interned fact row's flat
  `attrs` dict across the entities that cite it instead of copying it per
  entity; the identity normalization that used to mutate those dicts in place
  is now copy-on-write. On a oneDAL snapshot (91,721 graph entities) the
  decoded graph retains 35.8 MiB instead of 48.9 MiB.
