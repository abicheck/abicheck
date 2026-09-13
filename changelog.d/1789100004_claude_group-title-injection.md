### Security

- **`::group::` titles in `action/run.sh` are sanitized too.** The previous
  fix covered `::error::`/`::warning::`/`::notice::`, but a group title is
  just as much a workflow command and is just as often built from
  caller-controlled data — a baseline asset name derived from
  `baseline-profile`, the resolved mode, an `abi-baseline` tag. A newline in
  one ended the group command and the runner parsed the next line as a
  command of its own: `baseline-profile: "p\n::add-mask::SECRET"` emitted a
  standalone `::add-mask::` line while the error annotation beside it was
  correctly sanitized. All three group emitters now route through a
  `_group_start` helper, and the guard that enforces this checks *any*
  `::name::` command carrying an interpolation, not an enumerated subset.
