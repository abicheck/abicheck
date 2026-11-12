/-
  Formal model of abicheck's `TYPE_FIELD_OFFSET_CHANGED` rule.

  Implementation being modelled (see `formal/README.md` for the full mapping):
    abicheck/diff_types.py, `_diff_type_field_pair`:
        if f_old.offset_bits is not None
           and f_new.offset_bits is not None
           and f_old.offset_bits != f_new.offset_bits:
            emit TYPE_FIELD_OFFSET_CHANGED          (default verdict BREAKING)
    abicheck/compare/opaque_struct_downgrade.py / diff_filtering.py:
        the finding is suppressed when the record is opaque (forward-declared
        only) in both snapshots.

  Scope boundaries of the model (deliberate, documented):
  * a field is an atomic cell identified by its start offset; widths, partial
    overlap and torn reads are not modelled;
  * the new layout must have unique field names and (for the "safe" theorem)
    unique offsets, i.e. a struct, not a union;
  * the consumer is a fixed list of operations compiled against the old
    layout; the library stores values into an object laid out with the new
    layout (library-allocated object, no out-of-bounds access, no UB);
  * offsets are abstract naturals (abicheck stores bits; the unit is
    irrelevant to the argument).
-/
namespace AbiLean

/-! ## ABI profile: the part of a record layout the rule needs -/

structure Field where
  name : String
  off  : Nat
  deriving DecidableEq, Repr

/-- Concrete (ground-truth) layout of one record. -/
abbrev Layout := List Field

/-- Offset of a named field in a layout (first match). -/
def offOf (L : Layout) (f : String) : Option Nat :=
  (L.find? (fun x => x.name == f)).map Field.off

/-- Name of the field starting at offset `o` (first match). -/
def fieldAt (L : Layout) (o : Nat) : Option String :=
  (L.find? (fun x => x.off == o)).map Field.name

def NamesUnique (L : Layout) : Prop :=
  ∀ x ∈ L, ∀ y ∈ L, x.name = y.name → x = y

/-- Unique start offsets: a struct, not a union. -/
def OffsetsUnique (L : Layout) : Prop :=
  ∀ x ∈ L, ∀ y ∈ L, x.off = y.off → x = y

/-! ## Minimal semantics of the consumer operation -/

/-- The values the library stores into the fields of one object. -/
abbrev Valuation := String → Nat

/-- What a load at offset `o` observes in an object that the library laid out
with `lib` and filled with `val`; bytes covered by no field hold `pad`. -/
def readAt (lib : Layout) (pad : Nat) (val : Valuation) (o : Nat) : Nat :=
  match fieldAt lib o with
  | some g => val g
  | none   => pad

/-- Consumer operations on an object of the record type. `passOpaque` models
every use that does not dereference a field (passing the pointer back to the
library, storing it, comparing it). -/
inductive Op where
  | read (f : String)
  | passOpaque
  deriving DecidableEq, Repr

/-- A consumer previously compiled against layout `built`. -/
structure Consumer where
  built : Layout
  ops   : List Op

/-- Per-operation contract: a read of `f` returns what the library stored in `f`. -/
def opOk (c : Consumer) (lib : Layout) (pad : Nat) (val : Valuation) : Op → Prop
  | .read f =>
    match offOf c.built f with
    | some o => readAt lib pad val o = val f
    | none   => True   -- the consumer cannot name a field its layout lacks
  | .passOpaque => True

def ContractHolds (c : Consumer) (lib : Layout) (pad : Nat) (val : Valuation) : Prop :=
  ∀ op ∈ c.ops, opOk c lib pad val op

/-- (b) The contract of *this* consumer is violated: some input breaks it. -/
def Violated (c : Consumer) (lib : Layout) (pad : Nat) : Prop :=
  ∃ val, ¬ ContractHolds c lib pad val

/-- (c) Every input breaks the contract of this consumer. -/
def EveryCallFails (c : Consumer) (lib : Layout) (pad : Nat) : Prop :=
  ∀ val, ¬ ContractHolds c lib pad val

/-! ## Header visibility (consumer-specific admissibility) -/

/-- `opaque`: forward-declared only in the public headers of both versions,
so a consumer cannot name the fields. -/
inductive Visibility where
  | exposed
  | opaque
  deriving DecidableEq, Repr

def Admissible (v : Visibility) (c : Consumer) : Prop :=
  v = .opaque → ∀ op ∈ c.ops, op = .passOpaque

/-- (a) Some admissible consumer compiled against `old` has its contract
violated by some input. -/
def SomeConsumerBroken (v : Visibility) (old lib : Layout) (pad : Nat) : Prop :=
  ∃ c : Consumer, c.built = old ∧ Admissible v c ∧ Violated c lib pad

/-! ## abicheck evidence and the rule -/

/-- Per-field evidence as stored in `TypeField.offset_bits` on each side:
`none` = not collected / unknown. -/
structure FieldEvidence where
  name   : String
  oldOff : Option Nat
  newOff : Option Nat
  deriving DecidableEq, Repr

/-- Extraction soundness assumption: a *present* value is the true offset. -/
def EvSound (e : FieldEvidence) (old new : Layout) : Prop :=
  (∀ a, e.oldOff = some a → offOf old e.name = some a) ∧
  (∀ b, e.newOff = some b → offOf new e.name = some b)

/-- The rule exactly as `_diff_type_field_pair` implements it. -/
def fires (e : FieldEvidence) : Bool :=
  match e.oldOff, e.newOff with
  | some a, some b => a != b
  | _, _           => false

/-- Final, policy-level emission: raw rule plus the opaque downgrade. -/
def emitsBreaking (v : Visibility) (e : FieldEvidence) : Bool :=
  fires e && v == .exposed

/-! ## Lemmas about lookups -/

theorem offOf_some {L : Layout} {f : String} {o : Nat} (h : offOf L f = some o) :
    ∃ y ∈ L, y.name = f ∧ y.off = o := by
  unfold offOf at h
  cases hf : L.find? (fun x => x.name == f) with
  | none => simp [hf] at h
  | some y =>
    simp [hf] at h
    refine ⟨y, List.mem_of_find?_eq_some hf, ?_, h⟩
    have := List.find?_some hf
    simpa using this

theorem fieldAt_some {L : Layout} {o : Nat} {g : String} (h : fieldAt L o = some g) :
    ∃ y ∈ L, y.off = o ∧ y.name = g := by
  unfold fieldAt at h
  cases hf : L.find? (fun x => x.off == o) with
  | none => simp [hf] at h
  | some y =>
    simp [hf] at h
    refine ⟨y, List.mem_of_find?_eq_some hf, ?_, h⟩
    have := List.find?_some hf
    simpa using this

/-- In a struct (unique names and offsets) the field at the offset of `f` is `f`. -/
theorem fieldAt_offOf {L : Layout} {f : String} {o : Nat}
    (ho : OffsetsUnique L) (h : offOf L f = some o) :
    fieldAt L o = some f := by
  obtain ⟨y, hy, hyn, hyo⟩ := offOf_some h
  unfold fieldAt
  cases hf : L.find? (fun x => x.off == o) with
  | none =>
    have := List.find?_eq_none.mp hf y hy
    simp [hyo] at this
  | some z =>
    have hz := List.mem_of_find?_eq_some hf
    have hzo : z.off = o := by simpa using List.find?_some hf
    have : z = y := ho z hz y hy (by rw [hzo, hyo])
    subst this
    simp [hyn]

/-! ## Main soundness theorem -/

/-- **Soundness (b).** If the rule fires on sound evidence for field `f` and the
new layout has unique names, then *every* consumer compiled against the old
layout that reads `f` has its contract violated on some input. -/
theorem fires_sound (e : FieldEvidence) (old new : Layout) (pad : Nat)
    (hev : EvSound e old new) (hwf : NamesUnique new) (hfire : fires e = true)
    (c : Consumer) (hbuilt : c.built = old) (hreads : Op.read e.name ∈ c.ops) :
    Violated c new pad := by
  -- extract the two known, distinct offsets
  unfold fires at hfire
  cases ha : e.oldOff with
  | none => simp [ha] at hfire
  | some a =>
  cases hb : e.newOff with
  | none => simp [ha, hb] at hfire
  | some b =>
  simp [ha, hb] at hfire
  have hold : offOf old e.name = some a := hev.1 a ha
  have hnew : offOf new e.name = some b := hev.2 b hb
  -- witness input: `f` gets pad+1, every other field gets pad
  refine ⟨fun x => if x = e.name then pad + 1 else pad, ?_⟩
  intro hall
  have hop := hall _ hreads
  simp only [opOk, hbuilt, hold, readAt] at hop
  cases hfa : fieldAt new a with
  | none => simp [hfa] at hop
  | some g =>
    simp only [hfa] at hop
    -- g is the field at offset a in new; it cannot be `f`, which sits at b ≠ a
    have hg : g ≠ e.name := by
      intro hge
      obtain ⟨y, hy, hyo, hyn⟩ := fieldAt_some hfa
      obtain ⟨z, hz, hzn, hzo⟩ := offOf_some hnew
      have : y = z := hwf y hy z hz (by rw [hyn, hzn, hge])
      subst this
      exact hfire (by rw [← hyo, hzo])
    simp [hg] at hop

/-- **Soundness (a).** With exposed visibility the rule's BREAKING claim means
"there exists an admissible old consumer and an input that violate its contract". -/
theorem emitsBreaking_sound (v : Visibility) (e : FieldEvidence) (old new : Layout)
    (pad : Nat) (hev : EvSound e old new) (hwf : NamesUnique new)
    (h : emitsBreaking v e = true) : SomeConsumerBroken v old new pad := by
  unfold emitsBreaking at h
  simp only [Bool.and_eq_true, beq_iff_eq] at h
  obtain ⟨hf, hv⟩ := h
  refine ⟨⟨old, [.read e.name]⟩, rfl, ?_, ?_⟩
  · intro ho; rw [hv] at ho; cases ho
  · exact fires_sound e old new pad hev hwf hf _ rfl (by simp)

/-! ## (c) is *not* implied: no layout change makes every call fail -/

theorem not_every_call_fails (c : Consumer) (lib : Layout) (pad : Nat) :
    ¬ EveryCallFails c lib pad := by
  intro h
  apply h (fun _ => pad)
  intro op _
  cases op with
  | passOpaque => trivial
  | read f =>
    simp only [opOk]
    cases offOf c.built f with
    | none => trivial
    | some o =>
      simp only [readAt]
      cases fieldAt lib o <;> rfl

/-! ## Explicit model boundaries: when no consumer is broken -/

/-- Opaque types: no admissible consumer is ever broken (justifies the downgrade). -/
theorem opaque_never_broken (old new : Layout) (pad : Nat) :
    ¬ SomeConsumerBroken .opaque old new pad := by
  rintro ⟨c, _, hadm, val, hnot⟩
  apply hnot
  intro op hop
  rw [hadm rfl op hop]
  trivial

/-- Consumers whose read fields kept their offsets are never broken (unused
fields, opaque handles). Needs a struct: unique start offsets (no union). -/
theorem unchanged_reads_safe (c : Consumer) (new : Layout) (pad : Nat)
    (ho : OffsetsUnique new)
    (hsame : ∀ f, Op.read f ∈ c.ops → offOf c.built f = offOf new f) :
    ∀ val, ContractHolds c new pad val := by
  intro val op hop
  cases op with
  | passOpaque => trivial
  | read f =>
    simp only [opOk]
    cases hc : offOf c.built f with
    | none => trivial
    | some o =>
      have hnew : offOf new f = some o := by rw [← hsame f hop, hc]
      simp [readAt, fieldAt_offOf ho hnew]

/-! ## Unknown data never yields an exact verdict -/

theorem unknown_old_not_fires (n : String) (b : Option Nat) :
    fires ⟨n, none, b⟩ = false := by cases b <;> rfl

theorem unknown_new_not_fires (n : String) (a : Option Nat) :
    fires ⟨n, a, none⟩ = false := by cases a <;> rfl

/-- With the old offset unknown, the evidence is consistent with both a broken
and an unbroken world, so neither BREAKING nor "compatible" is entailed. -/
theorem unknown_is_undetermined :
    let e : FieldEvidence := ⟨"b", none, some 8⟩
    let new : Layout := [⟨"a", 0⟩, ⟨"x", 4⟩, ⟨"b", 8⟩]
    let oldBroken : Layout := [⟨"a", 0⟩, ⟨"b", 4⟩]
    let oldSame : Layout := [⟨"a", 0⟩, ⟨"x", 4⟩, ⟨"b", 8⟩]
    let reader (L : Layout) : Consumer := ⟨L, [.read "b"]⟩
    EvSound e oldBroken new ∧ EvSound e oldSame new ∧
    Violated (reader oldBroken) new 0 ∧
    (∀ val, ContractHolds (reader oldSame) new 0 val) := by
  refine ⟨⟨?_, ?_⟩, ⟨?_, ?_⟩, ?_, ?_⟩
  · intro a h; cases h
  · intro b h; cases h; rfl
  · intro a h; cases h
  · intro b h; cases h; rfl
  · refine ⟨fun x => if x = "b" then 1 else 0, ?_⟩
    intro h
    have := h (.read "b") (by simp)
    simp [opOk, offOf, readAt, fieldAt, List.find?] at this
  · intro val op hop
    simp at hop; subst hop
    simp [opOk, offOf, readAt, fieldAt, List.find?]

/-! ## Monotonicity and order independence -/

/-- Extending a consumer's requirements never improves compatibility. -/
theorem violated_mono (c c' : Consumer) (lib : Layout) (pad : Nat)
    (hb : c.built = c'.built) (hsub : ∀ op ∈ c.ops, op ∈ c'.ops)
    (h : Violated c lib pad) : Violated c' lib pad := by
  obtain ⟨val, hnot⟩ := h
  refine ⟨val, fun hall => hnot fun op hop => ?_⟩
  have := hall op (hsub op hop)
  cases op with
  | passOpaque => trivial
  | read f => simpa [opOk, hb] using this

def findings (es : List FieldEvidence) : List FieldEvidence := es.filter fires

def anyBreaking (es : List FieldEvidence) : Bool := es.any fires

/-- Processing the same evidence in a different order yields the same findings. -/
theorem findings_perm {es es' : List FieldEvidence} (hp : es.Perm es') (e : FieldEvidence) :
    e ∈ findings es ↔ e ∈ findings es' := by
  simp only [findings, List.mem_filter, hp.mem_iff]

theorem anyBreaking_perm {es es' : List FieldEvidence} (hp : es.Perm es') :
    anyBreaking es = anyBreaking es' := by
  cases h : anyBreaking es <;> cases h' : anyBreaking es' <;> try rfl
  · simp only [anyBreaking, List.any_eq_true, List.any_eq_false] at h h'
    obtain ⟨x, hx, hfx⟩ := h'
    exact absurd hfx (by simpa using h x (hp.mem_iff.mpr hx))
  · simp only [anyBreaking, List.any_eq_true, List.any_eq_false] at h h'
    obtain ⟨x, hx, hfx⟩ := h
    exact absurd hfx (by simpa using h' x (hp.mem_iff.mp hx))

/-- Duplicate identical evidence does not change the aggregated verdict. -/
theorem anyBreaking_dup (e : FieldEvidence) (es : List FieldEvidence) :
    anyBreaking (e :: e :: es) = anyBreaking (e :: es) := by
  simp only [anyBreaking, List.any_cons]
  cases fires e <;> rfl

end AbiLean
