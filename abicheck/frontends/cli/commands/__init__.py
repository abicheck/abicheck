"""Command input translation for the Click CLI (ADR-061 Phase 4 item 1).

One module per root command. Each translates already-parsed Click parameters
into a workflow request and a workflow result into a process response; none of
them computes either side's content.
"""
