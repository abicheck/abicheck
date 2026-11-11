/-
  Non-vacuity witnesses, negative checks (counterexamples to weakened rules)
  and the axiom audit for `AbiLean.FieldOffset`.

  The concrete layouts mirror the C fixtures in `formal/fixtures/`:
    v1: struct rec { int a; int b; };          a@0 b@4   (bytes)
    v2: struct rec { int a; int x; int b; };   a@0 x@4 b@8
-/
import AbiLean.FieldOffset

namespace AbiLean.Examples
open AbiLean

def v1 : Layout := [⟨"a", 0⟩, ⟨"b", 4⟩]
def v2 : Layout := [⟨"a", 0⟩, ⟨"x", 4⟩, ⟨"b", 8⟩]

/-- What abicheck extracts for field `b` (DWARF/castxml offsets, in bytes here). -/
def evB : FieldEvidence := ⟨"b", some 4, some 8⟩
def evA : FieldEvidence := ⟨"a", some 0, some 0⟩

theorem v2_names : NamesUnique v2 := by
  intro x hx y hy h
  simp [v2] at hx hy
  rcases hx with rfl | rfl | rfl <;> rcases hy with rfl | rfl | rfl <;> first | rfl | simp at h

theorem v2_offsets : OffsetsUnique v2 := by
  intro x hx y hy h
  simp [v2] at hx hy
  rcases hx with rfl | rfl | rfl <;> rcases hy with rfl | rfl | rfl <;> first | rfl | simp at h

theorem evB_sound : EvSound evB v1 v2 := by
  constructor <;> intro n h <;> cases h <;> rfl

/-! ### Positive example: the premises are satisfiable (theorem is not vacuous) -/

example : fires evB = true := rfl
example : fires evA = false := rfl

/-- The fixture's `reader_b` consumer. -/
def readerB : Consumer := ⟨v1, [.read "b"]⟩
/-- The fixture's `reader_a` consumer (reads only an unmoved field). -/
def readerA : Consumer := ⟨v1, [.read "a"]⟩
/-- The fixture's `opaque_user` consumer (uses only accessor functions). -/
def opaqueUser : Consumer := ⟨v1, [.passOpaque]⟩

theorem readerB_broken : Violated readerB v2 0 :=
  fires_sound evB v1 v2 0 evB_sound v2_names rfl readerB rfl (by simp [readerB, evB])

theorem exposed_broken : SomeConsumerBroken .exposed v1 v2 0 :=
  emitsBreaking_sound .exposed evB v1 v2 0 evB_sound v2_names rfl

/-- The concrete input the fixture uses: library stores a=1, x=7, b=42; the old
consumer reads 7 where it expects 42. -/
example : readAt v2 0 (fun n => if n = "a" then 1 else if n = "x" then 7 else 42)
    ((offOf v1 "b").getD 0) = 7 := by decide

/-! ### Near miss: premise removed, conclusion not allowed -/

theorem readerA_safe : ∀ val, ContractHolds readerA v2 0 val :=
  unchanged_reads_safe readerA v2 0 v2_offsets (by
    intro f hf; simp [readerA] at hf; subst hf; rfl)

theorem opaqueUser_safe : ∀ val, ContractHolds opaqueUser v2 0 val :=
  unchanged_reads_safe opaqueUser v2 0 v2_offsets (by intro f hf; simp [opaqueUser] at hf)

example : ¬ SomeConsumerBroken .opaque v1 v2 0 := opaque_never_broken v1 v2 0
example : emitsBreaking .opaque evB = false := rfl

/-! ### (c) does not follow even when the rule fires -/

example : ¬ EveryCallFails readerB v2 0 := not_every_call_fails readerB v2 0

/-! ## Negative checks: weakened rules are refuted by concrete counterexamples -/

/-- N1: treat unknown offsets as 0 (drops the `is not None` guards). -/
def firesWeak (e : FieldEvidence) : Bool := e.oldOff.getD 0 != e.newOff.getD 0

theorem N1_unknown_as_zero_unsound :
    ¬ (∀ (e : FieldEvidence) (old new : Layout), EvSound e old new → NamesUnique new →
        firesWeak e = true → ∀ c : Consumer, c.built = old → Op.read e.name ∈ c.ops →
        Violated c new 0) := by
  intro h
  -- old offset unknown, truly 4 = new offset: nothing moved
  let L : Layout := [⟨"b", 4⟩]
  have hv := h ⟨"b", none, some 4⟩ L L
    ⟨fun _ h => (by cases h), fun _ h => (by cases h; rfl)⟩
    (by intro x hx y hy _; simp [L] at hx hy; rw [hx, hy]) rfl ⟨L, [.read "b"]⟩ rfl (by simp)
  obtain ⟨val, hnot⟩ := hv
  apply hnot
  intro op hop
  simp at hop; subst hop
  simp [opOk, offOf, readAt, fieldAt, L, List.find?]

/-- N2: claim (b) for *every* old consumer, dropping "the consumer reads `f`". -/
theorem N2_any_consumer_unsound :
    ¬ (∀ (e : FieldEvidence) (old new : Layout), EvSound e old new → NamesUnique new →
        fires e = true → ∀ c : Consumer, c.built = old → Violated c new 0) := by
  intro h
  obtain ⟨val, hnot⟩ := h evB v1 v2 evB_sound v2_names rfl readerA rfl
  exact hnot (readerA_safe val)

/-- N3: ignore visibility (drop the opaque downgrade): the claimed
`SomeConsumerBroken` is false for an opaque record. -/
theorem N3_ignore_opacity_unsound :
    ¬ (∀ (v : Visibility) (e : FieldEvidence) (old new : Layout), EvSound e old new →
        NamesUnique new → fires e = true → SomeConsumerBroken v old new 0) := by
  intro h
  exact opaque_never_broken v1 v2 0 (h .opaque evB v1 v2 evB_sound v2_names rfl)

/-- N4: strengthen the conclusion to (c) "every call fails". -/
theorem N4_every_call_unsound :
    ¬ (∀ (e : FieldEvidence) (old new : Layout), EvSound e old new → NamesUnique new →
        fires e = true → ∀ c : Consumer, c.built = old → Op.read e.name ∈ c.ops →
        EveryCallFails c new 0) := by
  intro h
  exact not_every_call_fails readerB v2 0
    (h evB v1 v2 evB_sound v2_names rfl readerB rfl (by simp [readerB, evB]))

/-- N5: drop `OffsetsUnique` from the safe-direction theorem (unions): a field
whose offset did not change can still read another member's value. -/
theorem N5_union_breaks_safe_direction :
    ¬ (∀ (c : Consumer) (new : Layout), NamesUnique new →
        (∀ f, Op.read f ∈ c.ops → offOf c.built f = offOf new f) →
        ∀ val, ContractHolds c new 0 val) := by
  intro h
  let U : Layout := [⟨"a", 0⟩, ⟨"b", 0⟩]
  have := h ⟨U, [.read "b"]⟩ U
    (by intro x hx y hy hn; simp [U] at hx hy
        rcases hx with rfl | rfl <;> rcases hy with rfl | rfl <;> first | rfl | simp at hn)
    (by intro f _; rfl) (fun n => if n = "a" then 1 else 2) (.read "b") (by simp)
  simp [opOk, offOf, readAt, fieldAt, U, List.find?] at this

/-- N6: drop `NamesUnique` from the main theorem: a layout listing `b` twice
(at 8 and at the old offset 4) defeats it. -/
theorem N6_duplicate_names_unsound :
    ¬ (∀ (e : FieldEvidence) (old new : Layout), EvSound e old new →
        fires e = true → ∀ c : Consumer, c.built = old → Op.read e.name ∈ c.ops →
        Violated c new 0) := by
  intro h
  let bad : Layout := [⟨"b", 8⟩, ⟨"b", 4⟩]
  obtain ⟨val, hnot⟩ := h evB v1 bad
    ⟨fun _ h => (by cases h; rfl), fun _ h => (by cases h; rfl)⟩ rfl readerB rfl (by simp [readerB, evB])
  apply hnot
  intro op hop
  simp [readerB] at hop; subst hop
  simp [opOk, offOf, readAt, fieldAt, bad, v1, List.find?]

end AbiLean.Examples

/-! ## Axiom audit -/
#print axioms AbiLean.fires_sound
#print axioms AbiLean.emitsBreaking_sound
#print axioms AbiLean.not_every_call_fails
#print axioms AbiLean.opaque_never_broken
#print axioms AbiLean.unchanged_reads_safe
#print axioms AbiLean.unknown_is_undetermined
#print axioms AbiLean.violated_mono
#print axioms AbiLean.findings_perm
#print axioms AbiLean.anyBreaking_perm
#print axioms AbiLean.Examples.readerB_broken
#print axioms AbiLean.Examples.N1_unknown_as_zero_unsound
#print axioms AbiLean.Examples.N5_union_breaks_safe_direction
#print axioms AbiLean.Examples.N6_duplicate_names_unsound

/-! ## Truth table of the rule, consumed by tests/test_lean_field_offset_rule.py -/
def AbiLean.Examples.domain : List (Option Nat) := [none, some 0, some 32, some 64]
#eval (AbiLean.Examples.domain.flatMap fun a => AbiLean.Examples.domain.map fun b =>
  (a, b, AbiLean.fires ⟨"f", a, b⟩))
