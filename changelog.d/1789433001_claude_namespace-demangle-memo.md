### Performance

- The namespace-shape detectors (experimental-namespace, `using std::X`
  re-export and inline-namespace version checks) demangle each snapshot's
  public names once per pass instead of once per detector per side, via the
  existing `compare.detection_memo` scope. Found by the new repeated-call
  audit, `scripts/audit_repeated_calls.py`.
