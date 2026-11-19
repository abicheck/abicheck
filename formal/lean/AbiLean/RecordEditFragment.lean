/-
  E16: closed record-edit fragments — remove one field, swap two fields, or
  change one field's type — for C structs of scalar fields on LP64 SysV
  (x86-64), no bit-fields, no packing. Companion of the insertion fragment
  E10 (`AbiLean.Fragment`); formal/RECORD_EDIT_FRAGMENT.md states the claims.

  Parts, kept separate as in E10:

  1. `Ty`, `Edit`, `Case`, `InSpace`, `space` — the case space, defined
     without the generator. `mem_space` (structural) proves the enumeration is
     exactly `InSpace`; `space_nodup` that it lists no case twice (via a code
     that strictly increases along `space`, checked by `decide`, linear);
     `space_length` its size, 9252.
  2. `gen`, `decode` — the abstract generator (case ↦ old/new field
     declaration lists, field identity = the old index) and its inverse.
     `decode_gen_space` is checked by the kernel (`decide`) on every case of
     the space; `gen_decode` holds by construction; with them
     `generated_in_exactly_one` and `every_case_generated`.
  3. `findings`, `verdict` — the oracle from `AbiLean.Layout`: the product
     contract for a record passed by pointer, header lane.
  4. Layout facts: `remove_prefix_not_moved` and `replace_prefix_not_moved`
     (general proofs: fields before the edit point never move);
     `swap_align_unchanged`, `every_case_breaking`, `swap_moves_one` (checked
     on the whole space by `decide`).
-/
import AbiLean.Layout

set_option maxRecDepth 100000

namespace AbiLean.RecordEdit
open AbiLean.Layout

/-! ## 1. The case space -/

/-- Field types. `c s i d` are the base types (size = alignment 1, 2, 4, 8 on
LP64 SysV); `u` (`unsigned int`) and `f` (`float`) are extra replacement
targets of size and alignment 4 — the same-size retypes. -/
inductive Ty | c | s | i | d | u | f
  deriving DecidableEq, Repr

def Ty.bytes : Ty → Nat | .c => 1 | .s => 2 | .i => 4 | .d => 8 | .u => 4 | .f => 4
def Ty.idx : Ty → Nat | .c => 0 | .s => 1 | .i => 2 | .d => 3 | .u => 4 | .f => 5
def Ty.cty : Ty → String
  | .c => "char" | .s => "short" | .i => "int" | .d => "double"
  | .u => "unsigned int" | .f => "float"

/-- Types an old struct's field may have. -/
def baseTys : List Ty := [.c, .s, .i, .d]
/-- Types a replacement may introduce. -/
def allTys : List Ty := [.c, .s, .i, .d, .u, .f]

theorem Ty.mem_allTys (t : Ty) : t ∈ allTys := by cases t <;> decide

/-- One edit of the old struct's fields, by old index. -/
inductive Edit
  | remove (k : Nat)
  | swap (i j : Nat)
  | replace (k : Nat) (t : Ty)
  deriving DecidableEq, Repr

structure Case where
  base : List Ty
  edit : Edit
  deriving DecidableEq, Repr

def maxFields : Nat := 4

def EditOk (b : List Ty) : Edit → Prop
  | .remove k => 2 ≤ b.length ∧ k < b.length
  | .swap i j => i < j ∧ j < b.length
  | .replace k t => ∃ h : k < b.length, t ≠ b[k]

instance (b : List Ty) (e : Edit) : Decidable (EditOk b e) := by
  cases e <;> unfold EditOk <;> infer_instance

/-- **The space**, as a predicate. Nothing here mentions the generator. -/
def InSpace (c : Case) : Prop :=
  1 ≤ c.base.length ∧ c.base.length ≤ maxFields ∧ (∀ t ∈ c.base, t ∈ baseTys) ∧
    EditOk c.base c.edit

instance (c : Case) : Decidable (InSpace c) := by unfold InSpace; infer_instance

def words : Nat → List (List Ty)
  | 0 => [[]]
  | n + 1 => baseTys.flatMap fun t => (words n).map (t :: ·)

def removes (b : List Ty) : List Edit :=
  if 2 ≤ b.length then (List.range b.length).map .remove else []

def swaps (b : List Ty) : List Edit :=
  (List.range b.length).flatMap fun i =>
    ((List.range b.length).filter (i < ·)).map (.swap i)

def replaces (b : List Ty) : List Edit :=
  (List.range b.length).flatMap fun k =>
    (allTys.filter (fun t => some t ≠ b[k]?)).map (.replace k)

def edits (b : List Ty) : List Edit := removes b ++ swaps b ++ replaces b

def casesOfLen (n : Nat) : List Case :=
  (words n).flatMap fun b => (edits b).map (⟨b, ·⟩)

/-- **The explicit enumeration** of the space. -/
def space : List Case := (List.range maxFields).flatMap fun m => casesOfLen (m + 1)

theorem mem_words {n : Nat} {b : List Ty} :
    b ∈ words n ↔ b.length = n ∧ ∀ t ∈ b, t ∈ baseTys := by
  induction n generalizing b with
  | zero => cases b <;> simp [words]
  | succ n ih =>
    cases b with
    | nil => simp [words]
    | cons t0 bs =>
      simp only [words, List.mem_flatMap, List.mem_map, List.cons.injEq, ih,
        List.length_cons, List.mem_cons, forall_eq_or_imp]
      constructor
      · rintro ⟨s', hs', bs', ⟨hl, hall⟩, rfl, rfl⟩; exact ⟨by omega, hs', hall⟩
      · rintro ⟨hl, hs, hall⟩; exact ⟨t0, hs, bs, ⟨by omega, hall⟩, rfl, rfl⟩

theorem mem_edits (b : List Ty) (e : Edit) : e ∈ edits b ↔ EditOk b e := by
  cases e with
  | remove k =>
    simp only [edits, removes, swaps, replaces, List.mem_append, List.mem_flatMap,
      List.mem_map, List.mem_filter, List.mem_range, EditOk]
    constructor
    · rintro ((h | ⟨_, _, _, _, h⟩) | ⟨_, _, _, _, h⟩)
      · split at h
        · simp only [List.mem_map, List.mem_range, Edit.remove.injEq] at h
          obtain ⟨a, ha, rfl⟩ := h; exact ⟨by omega, ha⟩
        · simp at h
      · cases h
      · cases h
    · rintro ⟨h2, hk⟩
      left; left; rw [if_pos h2]; exact List.mem_map.2 ⟨k, List.mem_range.2 hk, rfl⟩
  | swap i j =>
    simp only [edits, removes, swaps, replaces, List.mem_append, List.mem_flatMap,
      List.mem_map, List.mem_filter, List.mem_range, EditOk, decide_eq_true_eq]
    constructor
    · rintro ((h | ⟨i', hi', j', ⟨hj', hij⟩, h⟩) | ⟨_, _, _, _, h⟩)
      · split at h
        · simp at h
        · simp at h
      · cases h; exact ⟨hij, hj'⟩
      · cases h
    · rintro ⟨hij, hj⟩
      left; right; exact ⟨i, by omega, j, ⟨hj, hij⟩, rfl⟩
  | replace k t =>
    simp only [edits, removes, swaps, replaces, List.mem_append, List.mem_flatMap,
      List.mem_map, List.mem_filter, List.mem_range, EditOk, decide_eq_true_eq]
    constructor
    · rintro ((h | ⟨_, _, _, _, h⟩) | ⟨k', hk', t', ⟨_, ht'⟩, h⟩)
      · split at h
        · simp at h
        · simp at h
      · cases h
      · cases h
        refine ⟨hk', fun heq => ht' ?_⟩
        rw [List.getElem?_eq_getElem hk', heq]
    · rintro ⟨hk, ht⟩
      right
      refine ⟨k, hk, t, ⟨Ty.mem_allTys t, fun heq => ht ?_⟩, rfl⟩
      rw [List.getElem?_eq_getElem hk] at heq
      exact (Option.some.inj heq)

theorem mem_casesOfLen {n : Nat} {c : Case} :
    c ∈ casesOfLen n ↔ c.base.length = n ∧ (∀ t ∈ c.base, t ∈ baseTys) ∧ EditOk c.base c.edit := by
  rcases c with ⟨b, e⟩
  simp only [casesOfLen, List.mem_flatMap, List.mem_map, mem_words, mem_edits]
  constructor
  · rintro ⟨b', ⟨hl, hall⟩, e', he, h⟩; cases h; exact ⟨hl, hall, he⟩
  · rintro ⟨hl, hall, he⟩; exact ⟨b, ⟨hl, hall⟩, e, he, rfl⟩

/-- **Completeness and soundness of the enumeration.** -/
theorem mem_space (c : Case) : c ∈ space ↔ InSpace c := by
  simp only [space, List.mem_flatMap, List.mem_range, mem_casesOfLen, InSpace, maxFields]
  constructor
  · rintro ⟨m, hm, hl, hall, he⟩; exact ⟨by omega, by omega, hall, he⟩
  · rintro ⟨h1, h2, hall, he⟩; exact ⟨c.base.length - 1, by omega, by omega, hall, he⟩

/-! ### No duplicates, via a strictly increasing code -/

def Edit.code : Edit → Nat
  | .remove k => k
  | .swap i j => 4 + i * 4 + j
  | .replace k t => 20 + k * 6 + t.idx

def baseCode : List Ty → Nat
  | [] => 0
  | t :: ts => t.idx * 4 ^ ts.length + baseCode ts

/-- Mixed-radix code: length, then the base word (head most significant), then
the edit. `space` lists cases in increasing code order. -/
def Case.code (c : Case) : Nat :=
  c.base.length * 100000 + baseCode c.base * 44 + c.edit.code

/-- Adjacent elements strictly increase. -/
def increasing : List Nat → Bool
  | a :: b :: rest => a < b && increasing (b :: rest)
  | _ => true

theorem increasing_cons {a : Nat} {l : List Nat} (h : increasing (a :: l) = true) :
    increasing l = true ∧ ∀ b, l.head? = some b → a < b := by
  cases l with
  | nil => simp [increasing]
  | cons b bs =>
    simp only [increasing, Bool.and_eq_true, decide_eq_true_eq] at h
    exact ⟨h.2, fun b' hb => by simp at hb; subst hb; exact h.1⟩

theorem pairwise_of_increasing : ∀ {l : List Nat}, increasing l = true → List.Pairwise (· < ·) l
  | [], _ => List.Pairwise.nil
  | [_], _ => by simp
  | a :: b :: bs, h => by
    obtain ⟨ht, hab⟩ := increasing_cons h
    have hab : a < b := hab b rfl
    have hp := pairwise_of_increasing ht
    refine List.Pairwise.cons ?_ hp
    intro x hx
    rcases List.mem_cons.1 hx with rfl | hx
    · exact hab
    · exact Nat.lt_trans hab (List.rel_of_pairwise_cons hp hx)

theorem codes_increasing : increasing (space.map Case.code) = true := by decide +kernel

theorem space_nodup : space.Nodup := by
  have h := List.pairwise_map.1 (pairwise_of_increasing codes_increasing)
  exact h.imp fun hlt heq => Nat.ne_of_lt hlt (congrArg Case.code heq)

theorem space_length : space.length = 9252 := by decide +kernel

/-- Per-operation sizes: 1248 removals, 1744 swaps, 6260 replacements. -/
def isRemove : Case → Bool | ⟨_, .remove _⟩ => true | _ => false
def isSwap : Case → Bool | ⟨_, .swap _ _⟩ => true | _ => false
def isReplace : Case → Bool | ⟨_, .replace _ _⟩ => true | _ => false

theorem op_sizes :
    (space.filter isRemove).length = 1248 ∧ (space.filter isSwap).length = 1744 ∧
    (space.filter isReplace).length = 6260 := by decide +kernel

/-! ## 2. The abstract generator and its inverse -/

/-- A field declaration: the field's identity (`j` renders as `f<j>`, the old
index — stable across the edit) and its type. -/
structure Decl where
  name : Nat
  ty   : Ty
  deriving DecidableEq, Repr

structure Program where
  old : List Decl
  new : List Decl
  deriving DecidableEq, Repr

def oldDecls (b : List Ty) : List Decl :=
  (List.range b.length).zipWith (fun j t => ⟨j, t⟩) b

def swapAt (l : List Decl) (i j : Nat) : List Decl :=
  match l[i]?, l[j]? with
  | some a, some b => (l.set i b).set j a
  | _, _ => l

def newDecls (b : List Ty) : Edit → List Decl
  | .remove k => (oldDecls b).eraseIdx k
  | .swap i j => swapAt (oldDecls b) i j
  | .replace k t => (oldDecls b).set k ⟨k, t⟩

def gen (c : Case) : Program := ⟨oldDecls c.base, newDecls c.base c.edit⟩

/-- Read the old struct back: names must be `0, 1, …` in order. -/
def decodeOld : Nat → List Decl → Option (List Ty)
  | _, [] => some []
  | j, d :: ds => if d.name = j then (decodeOld (j + 1) ds).map (d.ty :: ·) else none

/-- First index where two declaration lists differ (or the shorter length). -/
def firstDiff : List Decl → List Decl → Nat
  | a :: as, b :: bs => if a = b then firstDiff as bs + 1 else 0
  | _, _ => 0

/-- Read the edit back from the old and new declaration lists. -/
def decodeEdit (o n : List Decl) : Option Edit :=
  let k := firstDiff o n
  if n.length + 1 = o.length then some (.remove k)
  else if n.length = o.length then
    match n[k]? with
    | some d => if d.name = k then some (.replace k d.ty) else some (.swap k d.name)
    | none => none
  else none

def decode (p : Program) : Option Case :=
  match decodeOld 0 p.old, decodeEdit p.old p.new with
  | some b, some e => if InSpace ⟨b, e⟩ ∧ gen ⟨b, e⟩ = p then some ⟨b, e⟩ else none
  | _, _ => none

theorem gen_decode {p : Program} {c : Case} (h : decode p = some c) :
    InSpace c ∧ gen c = p := by
  unfold decode at h
  split at h
  · split at h
    · cases h; assumption
    · contradiction
  · contradiction

/-- Checked by the kernel on all 9252 cases. -/
theorem decode_gen_space : ∀ c ∈ space, decode (gen c) = some c := by decide +kernel

theorem decode_gen (c : Case) (hc : InSpace c) : decode (gen c) = some c :=
  decode_gen_space c ((mem_space c).2 hc)

theorem gen_injective {c₁ c₂ : Case} (h₁ : InSpace c₁) (h₂ : InSpace c₂)
    (h : gen c₁ = gen c₂) : c₁ = c₂ := by
  have := decode_gen c₁ h₁; rw [h, decode_gen c₂ h₂] at this
  exact (Option.some.inj this).symm

theorem generated_in_exactly_one (p : Program) (hp : p ∈ space.map gen) :
    ∃ c, (InSpace c ∧ gen c = p) ∧ ∀ c', InSpace c' ∧ gen c' = p → c' = c := by
  obtain ⟨c, hc, rfl⟩ := List.mem_map.1 hp
  have hc' := (mem_space c).1 hc
  exact ⟨c, ⟨hc', rfl⟩, fun c' ⟨h', hg⟩ => gen_injective h' hc' hg⟩

theorem every_case_generated (c : Case) (hc : InSpace c) :
    ∃ p ∈ space.map gen, decode p = some c :=
  ⟨gen c, List.mem_map.2 ⟨c, (mem_space c).2 hc, rfl⟩, decode_gen c hc⟩

/-! ## 3. The oracle -/

def Ty.ft (t : Ty) : FType := ⟨t.cty, t.bytes, t.bytes⟩

def tys (ds : List Decl) : List FType := ds.map (·.ty.ft)

def oldOffsets (c : Case) : List Nat := offsets (tys (gen c).old)
def newOffsets (c : Case) : List Nat := offsets (tys (gen c).new)
def oldSize (c : Case) : Nat := Layout.sizeOf (tys (gen c).old)
def newSize (c : Case) : Nat := Layout.sizeOf (tys (gen c).new)
def oldAlign (c : Case) : Nat := maxAlign (tys (gen c).old)
def newAlign (c : Case) : Nat := maxAlign (tys (gen c).new)

/-- Index of field `j` in a declaration list. -/
def indexOf (ds : List Decl) (j : Nat) : Option Nat := ds.findIdx? (·.name == j)

/-- New offset of old field `j` (none: removed). -/
def newOffsetOf (c : Case) (j : Nat) : Option Nat :=
  (indexOf (gen c).new j).bind fun k => (newOffsets c)[k]?

/-- Old field `j` is still present and its offset changed. -/
def moved (c : Case) (j : Nat) : Bool :=
  match newOffsetOf c j with
  | some o => (oldOffsets c)[j]? != some o
  | none => false

def movedFields (c : Case) : List Nat := (List.range c.base.length).filter (moved c)
def sizeChanged (c : Case) : Bool := oldSize c != newSize c
def alignChanged (c : Case) : Bool := oldAlign c != newAlign c

inductive Kind
  | typeFieldRemoved        -- type_field_removed,        BREAKING
  | typeFieldTypeChanged    -- type_field_type_changed,   BREAKING
  | typeFieldOffsetChanged  -- type_field_offset_changed, BREAKING
  | typeSizeChanged         -- type_size_changed,         BREAKING
  | typeAlignmentChanged    -- type_alignment_changed,    BREAKING
  deriving DecidableEq, Repr

inductive Subject | member (j : Nat) | record
  deriving DecidableEq, Repr

structure Finding where
  kind : Kind
  subj : Subject
  deriving DecidableEq, Repr

inductive Verdict | compatible | breaking
  deriving DecidableEq, Repr

/-- The edit's own finding: the removed or retyped field. A swap has none of
its own: it is observed only as the moved fields. -/
def editFindings : Edit → List Finding
  | .remove k => [⟨.typeFieldRemoved, .member k⟩]
  | .swap _ _ => []
  | .replace k _ => [⟨.typeFieldTypeChanged, .member k⟩]

/-- **Expected findings** (header lane): the edit's own finding; an offset
change on every old field still present whose offset changed — the retyped
field included; sizeof and alignment changes on the record. -/
def findings (c : Case) : List Finding :=
  editFindings c.edit ++
  (movedFields c).map (fun j => ⟨.typeFieldOffsetChanged, .member j⟩) ++
  (if sizeChanged c then [⟨.typeSizeChanged, .record⟩] else []) ++
  (if alignChanged c then [⟨.typeAlignmentChanged, .record⟩] else [])

def verdict (c : Case) : Verdict := if (findings c).isEmpty then .compatible else .breaking

/-! ## 4. Layout facts -/

theorem offsets_take (e : Nat) (k : Nat) (ts : List FType) :
    (offsetsFrom e ts).take k = offsetsFrom e (ts.take k) := by
  induction ts generalizing e k with
  | nil => simp [offsetsFrom]
  | cons t ts ih =>
    cases k with
    | zero => simp [offsetsFrom]
    | succ k => simp [offsetsFrom, ih]

/-- Two field lists that agree on their first `k` types place those fields at
the same offsets. -/
theorem prefix_offsets_eq (a b : List FType) (k : Nat) (h : a.take k = b.take k) :
    (offsets a).take k = (offsets b).take k := by
  unfold offsets; rw [offsets_take, offsets_take, h]

theorem tys_take (ds : List Decl) (k : Nat) : (tys ds).take k = tys (ds.take k) := by
  simp [tys, List.map_take]

theorem take_eraseIdx_self {α} : ∀ (l : List α) (k : Nat), (l.eraseIdx k).take k = l.take k
  | [], _ => rfl
  | _ :: _, 0 => rfl
  | a :: as, k + 1 => by simp [List.eraseIdx, take_eraseIdx_self as k]

theorem take_set_self {α} : ∀ (l : List α) (k : Nat) (x : α), (l.set k x).take k = l.take k
  | [], _, _ => rfl
  | _ :: _, 0, _ => rfl
  | a :: as, k + 1, x => by simp [List.set, take_set_self as k x]

/-- **Removal: fields before the removed one never move.** -/
theorem remove_prefix_not_moved (b : List Ty) (k : Nat) :
    (offsets (tys (newDecls b (.remove k)))).take k = (offsets (tys (oldDecls b))).take k := by
  apply prefix_offsets_eq
  rw [tys_take, tys_take]
  simp only [newDecls, take_eraseIdx_self]

/-- **Replacement: fields before the retyped one never move.** -/
theorem replace_prefix_not_moved (b : List Ty) (k : Nat) (t : Ty) :
    (offsets (tys (newDecls b (.replace k t)))).take k = (offsets (tys (oldDecls b))).take k := by
  apply prefix_offsets_eq
  rw [tys_take, tys_take]
  simp only [newDecls, take_set_self]

/-- A swap never changes the record's alignment (on the whole space). -/
theorem swap_align_unchanged : ∀ c ∈ space, isSwap c → alignChanged c = false := by decide +kernel

/-- Every edit of the space is breaking under the contract: a removal and a
retype report themselves, and a swap always moves a swapped field. -/
theorem every_case_breaking : ∀ c ∈ space, verdict c = .breaking := by decide +kernel

def swapMovesOne (c : Case) : Bool :=
  match c.edit with
  | .swap i j => moved c i || moved c j
  | _ => true

/-- A swap always moves at least one of the two swapped fields (on the whole
space). Not necessarily both: in `{char f0; char f1; short f2}`, swapping
`f1` and `f2` leaves `f2` at offset 2 (`swap_can_keep_one`). -/
theorem swap_moves_one : ∀ c ∈ space, swapMovesOne c = true := by decide +kernel

theorem swap_can_keep_one :
    let c : Case := ⟨[.c, .c, .s], .swap 1 2⟩
    InSpace c ∧ moved c 1 = true ∧ moved c 2 = false := by decide

/-- A same-size retype can leave every offset and the size unchanged: the
layout alone does not decide whether a retype breaks. `{int f0}`, `f0 : int →
float`: same layout, still a type change. -/
theorem same_layout_retype_exists :
    let c : Case := ⟨[.i], .replace 0 .f⟩
    InSpace c ∧ movedFields c = [] ∧ sizeChanged c = false ∧ alignChanged c = false ∧
      verdict c = .breaking := by decide

end AbiLean.RecordEdit
