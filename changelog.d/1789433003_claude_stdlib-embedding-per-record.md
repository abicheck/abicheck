### Performance

- Attributing an owner's layout change to an embedded standard-library
  member scans each record's fields once instead of once per finding: a
  wide record (one field-offset change per field) was quadratic in its
  width -- 14.0 s to 1.1 s on a 1600-field record. Found by the new
  record-width axis of the call-count complexity gate.
