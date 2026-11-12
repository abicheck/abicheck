---------------------------- MODULE DispositionPipeline ----------------------------
(* E7: the ADR-067 "record before disposing" guarantee of
   abicheck/policy/finding_disposition/pipeline.py (_record_dropped_duplicates).

   Each pipeline step maps the visible finding list `changes` to a new list.
   It may DROP findings, MOVE findings into side buckets (suppressed,
   redundant, opaque_filtered, out_of_surface), and ADD new finding objects
   (to `changes` or to a bucket). The runner records dropped findings in the
   ledger only when the COUNT trigger fires:

       len(after) + (buckets_after - buckets_before) < len(before)

   Invariant NothingVanishes: every finding that ever existed is still
   visible, in a bucket, or recorded in the ledger. AddsAllowed toggles
   whether a step that does not record its own drops may introduce new
   objects in the same call.

   Checked by formal/tla/check_disposition.py (explicit-state BFS over the
   same transition relation). TLC: `java -cp tla2tools.jar tlc2.TLC
   -config MC_adds.cfg DispositionPipeline.tla` (expected: invariant
   violated) and MC_noadds.cfg (expected: no error). *)
EXTENDS FiniteSets, Naturals

CONSTANTS Ids, MaxSteps, AddsAllowed

VARIABLES changes, buckets, ledger, seen, fresh, steps

vars == <<changes, buckets, ledger, seen, fresh, steps>>

Init ==
  /\ \E init \in SUBSET Ids :
       /\ init # {}
       /\ changes = init
       /\ seen = init
       /\ fresh = Ids \ init
  /\ buckets = {}
  /\ ledger = {}
  /\ steps = 0

Step ==
  /\ steps < MaxSteps
  /\ \E D \in SUBSET changes, M \in SUBSET changes,
        A \in SUBSET fresh, B \in SUBSET fresh :
       /\ D \cap M = {}
       /\ A \cap B = {}
       /\ (~AddsAllowed) => (A = {} /\ B = {})
       /\ LET after   == (changes \ (D \cup M)) \cup A
              trigger == Cardinality(after) + Cardinality(M \cup B) < Cardinality(changes)
          IN /\ changes' = after
             /\ buckets' = buckets \cup M \cup B
             /\ ledger'  = IF trigger THEN ledger \cup D ELSE ledger
             /\ seen'    = seen \cup A \cup B
             /\ fresh'   = fresh \ (A \cup B)
  /\ steps' = steps + 1

Next == Step \/ (steps = MaxSteps /\ UNCHANGED vars)

Spec == Init /\ [][Next]_vars

NothingVanishes == seen \subseteq (changes \cup buckets \cup ledger)
=============================================================================
