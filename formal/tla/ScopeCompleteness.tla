---------------------------- MODULE ScopeCompleteness ----------------------------
(* E8: the ADR-065 completeness axis of a directory/package release comparison.

   Transcribed from the implementation, not from the ADR prose:
     * member states and their assignment:
         abicheck/workflows/release_scope.py  build_release_scope_record
         abicheck/model/scope_acquisition.py  AcquisitionState, UNCHECKED_STATES,
                                              proven_removed_members
     * D6 (incomplete scope, policy-dependent) and D7 (no comparison completed,
       policy-independent):
         abicheck/policy/scope_completeness.py
     * the exit fold (max over axes): abicheck/policy/exit_decision.py
         resolve_exit_decision, abicheck/policy/release_exit_decision.py

   Vocabulary. The task's six member states are used, plus `not_supplied`,
   because "absence" is the thing I2 is about and the code has that state:
     unselected            code OUT_OF_SCOPE (not selected by this run)
     expected_not_produced code EXPECTED_NOT_PRODUCED (declared, not extracted)
     failed                code FAILED (operational error, ADR-065 D8)
     not_supplied          code NOT_SUPPLIED with NEW's inventory unproven
     retired               code NOT_SUPPLIED with NEW's inventory PROVEN
                           (the code keeps one state and derives this from the
                           proof, see proven_removed_members; the spec makes the
                           derivation an explicit transition, Classify)
     compared_ok           code AVAILABLE, verdict NO_CHANGE
     compared_break        code AVAILABLE, verdict BREAKING/API_BREAK
   Out of vocabulary: UNSUPPORTED and AMBIGUOUS (both unchecked in the code;
   excluded from this spec, see EXPERIMENTS.md E8).

   Acquisition is nondeterministic (Acquire): any member may end in any
   acquisition state. NewProven and Policy are nondeterministic constants of
   the run. Classify is the only deterministic step.

   Exit (the spec's fold, intent-level, max over axes):
     Compat   = 4  if some member is compared_break            (ABI break)
     Removal  = 8  if some member is retired                   (fail-on-removed)
     Ops      = 4  if some member is failed                    (D8 operational)
     D6       = 1  if Policy = "block" and the scope is incomplete
     D7       = 1  if no member is completed (policy-independent)
     Exit     = max(Compat, Removal, Ops, D6, D7)

   Invariants (checked on every reachable state):
     I1  NoCleanZeroComparison: no completed member  =>  Exit # 0
     I2  RemovalNeedsProof:     retired => NewProven, and Removal # 0 => NewProven
                                (absence alone never reads as removal)
     I3  FailedNeverClean:      some member failed  =>  Exit >= 1

   Checked by formal/tla/check_scope.py (explicit-state BFS over this same
   relation) and by formal/experiments/scope_completeness_audit.py (the real
   code). TLC: java -cp tla2tools.jar tlc2.TLC -config MC_scope.cfg
   ScopeCompleteness.tla *)
EXTENDS FiniteSets, Naturals

CONSTANTS Members

VARIABLES state, newProven, policy

vars == <<state, newProven, policy>>

AcquireStates == {"unselected", "expected_not_produced", "failed",
                  "not_supplied", "compared_ok", "compared_break"}

States == AcquireStates \cup {"retired"}

Policies == {"warn", "block"}

Init ==
  /\ state = [i \in Members |-> "absent"]
  /\ newProven \in BOOLEAN
  /\ policy \in Policies

Acquire ==
  /\ \E i \in Members :
       /\ state[i] = "absent"
       /\ \E s \in AcquireStates : state' = [state EXCEPT ![i] = s]
  /\ UNCHANGED <<newProven, policy>>

Classify ==
  /\ newProven
  /\ \E i \in Members : state[i] = "not_supplied"
  /\ state' = [i \in Members |-> IF state[i] = "not_supplied" THEN "retired" ELSE state[i]]
  /\ UNCHANGED <<newProven, policy>>

Next == Acquire \/ Classify

Spec == Init /\ [][Next]_vars

(* ---- derived quantities ------------------------------------------------ *)

Completed == {i \in Members : state[i] \in {"compared_ok", "compared_break"}}
Unchecked == {i \in Members : state[i] \in {"expected_not_produced", "failed", "not_supplied"}}
Failed    == {i \in Members : state[i] = "failed"}
Retired   == {i \in Members : state[i] = "retired"}
Breaks    == {i \in Members : state[i] = "compared_break"}

NoComparisonCompleted == Completed = {}
Incomplete == Unchecked # {} \/ NoComparisonCompleted

Compat  == IF Breaks # {} THEN 4 ELSE 0
Removal == IF Retired # {} THEN 8 ELSE 0
Ops     == IF Failed # {} THEN 4 ELSE 0
D6      == IF policy = "block" /\ Incomplete THEN 1 ELSE 0
D7      == IF NoComparisonCompleted THEN 1 ELSE 0

Exit == Max({Compat, Removal, Ops, D6, D7})

Max(S) == CHOOSE x \in S : \A y \in S : x >= y

(* ---- invariants -------------------------------------------------------- *)

Done == \A i \in Members : state[i] # "absent"

I1_NoCleanZeroComparison ==
  Done => (NoComparisonCompleted => Exit # 0)

I2_RemovalNeedsProof ==
  /\ (Retired # {} => newProven)
  /\ (Removal # 0 => newProven)

I3_FailedNeverClean ==
  Done => (Failed # {} => Exit >= 1)

TypeOK ==
  /\ state \in [Members -> States \cup {"absent"}]
  /\ newProven \in BOOLEAN
  /\ policy \in Policies
=============================================================================
