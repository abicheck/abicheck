### Fixed

- **Read large Windows DLL export tables and long C++ names.** PE extraction now preserves named exports past the previous 8,192-entry limit and distinguishes decorated names with a common 512-byte prefix. Re-dump older Windows baselines to recover names absent from stored snapshots.
