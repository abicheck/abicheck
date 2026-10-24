### Fixed

- **A `--depth` projection can no longer silently starve a detector of its
  input.** Every detector now declares the depth-projectable snapshot facts
  it reads (`compare/detector_inputs.py`), each depth rung declares the facts
  its projection clears (`policy/depth_fact_availability.py`), and a
  detector whose required fact was cleared is reported in the coverage block
  as `disabled: <fact> not available at depth <d>` (not evaluated) instead
  of running on emptied input. At `--depth binary` on header-derived
  snapshots this marks the type/enum/typedef/constant and Python-API
  detectors not evaluated, where they previously reported a silent zero.
