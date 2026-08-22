### Fixed

- **`abicheck.product_baseline.compare_product_directories`**: a header
  root naming an *existing regular file* rather than a directory was
  silently treated the same as a nonexistent root (tolerated, since the
  library ships no public headers there), running the per-library compare
  with no header evidence for that side -- risking a false-green result
  for a header-only API change. Now rejected outright, at the same
  up-front validation pass that already catches an absolute/escaping
  header root before any library pair is matched.
