### Removed

- `buildsource.inputs_emit.append_source_facts` no longer takes `compress=`;
  name the facts file `.gz` to compress it, which the function already
  honoured. The flag was a second spelling of the suffix with no production
  caller (dead-code plan, Stage H).
