### Changed

- Faster policy evaluation, spelling vocabularies, closure-marker scans and
  stored-snapshot I/O, with byte-identical reports and snapshot files:
  - `reclassify:` rules are bucketed by their `kind` selector once per rule
    list, so each finding is checked only against rules that can match its
    kind (first-match order unchanged). On a oneDAL compare with a 10-rule
    policy, rule matching fell from 6.9 s to 0.3 s.
  - The prefix-trie spelling pattern is built from the sorted vocabulary
    (each trie node a slice found by bisection) instead of a character-by-
    character dict trie; the regex text is identical.
  - The anonymous/lambda closure-marker scan visits only parenthesis
    positions and memoizes per name.
  - Loading a stored snapshot no longer deep-copies the types, sparse and
    semantic-IR sections through a throwaway frozen DTO (oneDAL baseline
    load 4.05 s to 3.14 s), and saving one canonicalizes each section once
    instead of twice (4.19 s to 2.86 s).
