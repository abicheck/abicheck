/-
  A closed layout fragment: "insert one scalar field into a struct of scalar
  fields" on LP64 SysV (x86-64 / AArch64), C, no bit-fields, no packing.

  Five parts, kept separate on purpose (formal/FRAGMENT.md states the claims):

  1. `Case`, `InSpace`, `space`   – the case space, defined WITHOUT reference to
     any generator; `mem_space` proves the enumeration is exactly `InSpace`,
     `space_nodup` that it has no duplicates.
  2. `gen`, `decode`              – the abstract generator (a case ↦ a pair of
     field-declaration lists) and its inverse; `decode_gen`, `gen_decode`,
     `generated_in_exactly_one` prove it is total on the space, injective, and
     that every generated program pair belongs to exactly one case.
  3. `findings`, `verdict`, `obs` – the oracle, computed from the layout model
     `AbiLean.Layout` (offsets, sizeof, alignment).
  4. `moved_contiguous`, `obs_eq_iff_key` – the proved equivalence condition:
     two cases have the same observable result iff they have the same `key`
     (moved interval, size changed, alignment changed). `opName_not_enough`
     shows the operation (position + inserted size) alone does not decide it.
  5. The reduced set is exported by FragmentExport.lean; that it hits every
     class is checked by tests/test_formal_layout_fragment.py.
-/
import AbiLean.Layout

namespace AbiLean.Fragment
open AbiLean.Layout

/-! ## 1. The case space -/

/-- A scalar size class. On LP64 SysV every non-16-byte scalar has
alignment = size ∈ {1, 2, 4, 8}; layout depends on nothing else. -/
inductive Sc | s1 | s2 | s4 | s8
  deriving DecidableEq, Repr

def Sc.bytes : Sc → Nat | .s1 => 1 | .s2 => 2 | .s4 => 4 | .s8 => 8
def Sc.all : List Sc := [.s1, .s2, .s4, .s8]
theorem Sc.mem_all (s : Sc) : s ∈ Sc.all := by cases s <;> decide
theorem Sc.bytes_pos (s : Sc) : 0 < s.bytes := by cases s <;> decide

/-- Upper bound on the number of fields of the old struct. -/
def maxFields : Nat := 4

/-- One case: old struct `base` (field `j` is named `f<j>`), insert one field
`ins` at position `pos` (0 = first, `base.length` = append). -/
structure Case where
  base : List Sc
  pos  : Nat
  ins  : Sc
  deriving DecidableEq, Repr

/-- **The space**, as a predicate. Nothing here mentions the generator. -/
def InSpace (c : Case) : Prop :=
  1 ≤ c.base.length ∧ c.base.length ≤ maxFields ∧ c.pos ≤ c.base.length

instance (c : Case) : Decidable (InSpace c) := by unfold InSpace; infer_instance

/-- All words of length `n` over `Sc`. -/
def words : Nat → List (List Sc)
  | 0 => [[]]
  | n + 1 => Sc.all.flatMap fun s => (words n).map (s :: ·)

def casesOfLen (n : Nat) : List Case :=
  (words n).flatMap fun b => (List.range (n + 1)).flatMap fun k =>
    Sc.all.map fun t => ⟨b, k, t⟩

/-- **The explicit enumeration** of the space. -/
def space : List Case := (List.range maxFields).flatMap fun i => casesOfLen (i + 1)

theorem mem_words {n : Nat} {b : List Sc} : b ∈ words n ↔ b.length = n := by
  induction n generalizing b with
  | zero => cases b <;> simp [words]
  | succ n ih =>
    cases b with
    | nil => simp [words]
    | cons s bs => simp [words, ih, Sc.mem_all]

theorem mem_casesOfLen {n : Nat} {c : Case} :
    c ∈ casesOfLen n ↔ c.base.length = n ∧ c.pos ≤ n := by
  rcases c with ⟨b, k, t⟩
  simp only [casesOfLen, List.mem_flatMap, List.mem_range, List.mem_map, mem_words]
  constructor
  · rintro ⟨b', hb, k', hk, t', -, h⟩
    cases h; exact ⟨hb, by omega⟩
  · rintro ⟨hb, hk⟩
    exact ⟨b, hb, k, by omega, t, Sc.mem_all t, rfl⟩

/-- **Completeness and soundness of the enumeration**: a case is listed iff it
is in the space. -/
theorem mem_space (c : Case) : c ∈ space ↔ InSpace c := by
  simp only [space, List.mem_flatMap, List.mem_range, mem_casesOfLen, InSpace, maxFields]
  constructor
  · rintro ⟨i, hi, hl, hp⟩; omega
  · rintro ⟨h1, h2, h3⟩; exact ⟨c.base.length - 1, by omega, by omega, by omega⟩

/-! ### No duplicates -/

theorem nodup_map_of_inj {α β} {f : α → β} (hf : ∀ a b, f a = f b → a = b)
    {l : List α} (h : l.Nodup) : (l.map f).Nodup := by
  unfold List.Nodup at *
  rw [List.pairwise_map]
  exact h.imp fun hne heq => hne (hf _ _ heq)

theorem nodup_flatMap {α β} {l : List α} {f : α → List β} (hl : l.Nodup)
    (hf : ∀ a ∈ l, (f a).Nodup)
    (hd : ∀ a₁ a₂ x, x ∈ f a₁ → x ∈ f a₂ → a₁ = a₂) : (l.flatMap f).Nodup := by
  unfold List.Nodup at *
  rw [List.pairwise_flatMap]
  refine ⟨hf, hl.imp fun {a b} hne x hx y hy hxy => hne (hd a b x hx (by subst hxy; exact hy))⟩

theorem Sc.all_nodup : Sc.all.Nodup := by decide

theorem words_nodup (n : Nat) : (words n).Nodup := by
  induction n with
  | zero => simp [words]
  | succ n ih =>
    apply nodup_flatMap Sc.all_nodup
    · intro s _; exact nodup_map_of_inj (fun a b h => List.cons.inj h |>.2) ih
    · intro s₁ s₂ x h1 h2
      simp only [List.mem_map] at h1 h2
      obtain ⟨_, _, rfl⟩ := h1
      obtain ⟨_, _, h⟩ := h2
      exact (List.cons.inj h).1.symm

theorem casesOfLen_nodup (n : Nat) : (casesOfLen n).Nodup := by
  apply nodup_flatMap (words_nodup n)
  · intro b _
    apply nodup_flatMap (List.nodup_range _)
    · intro k _
      exact nodup_map_of_inj (fun a b h => by cases h; rfl) Sc.all_nodup
    · intro k₁ k₂ x h1 h2
      simp only [List.mem_map] at h1 h2
      obtain ⟨_, _, rfl⟩ := h1; obtain ⟨_, _, h⟩ := h2; cases h; rfl
  · intro b₁ b₂ x h1 h2
    simp only [List.mem_flatMap, List.mem_map] at h1 h2
    obtain ⟨_, _, _, _, rfl⟩ := h1; obtain ⟨_, _, _, _, h⟩ := h2; cases h; rfl

theorem space_nodup : space.Nodup := by
  apply nodup_flatMap (List.nodup_range _)
  · intro i _; exact casesOfLen_nodup _
  · intro i₁ i₂ x h1 h2
    have := (mem_casesOfLen.1 h1).1; have := (mem_casesOfLen.1 h2).1; omega

theorem length_flatMap_const {α β} {l : List α} {f : α → List β} {m : Nat}
    (h : ∀ a ∈ l, (f a).length = m) : (l.flatMap f).length = l.length * m := by
  induction l with
  | nil => simp
  | cons a as ih =>
    rw [List.flatMap_cons, List.length_append, h a (by simp),
      ih (fun x hx => h x (by simp [hx])), List.length_cons, Nat.succ_mul]
    omega

theorem words_length (n : Nat) : (words n).length = 4 ^ n := by
  induction n with
  | zero => rfl
  | succ n ih =>
    simp only [words]
    rw [length_flatMap_const (m := 4 ^ n)]
    · simp [Sc.all, Nat.pow_succ]; omega
    · intro s _; simp [ih]

theorem casesOfLen_length (n : Nat) : (casesOfLen n).length = 4 ^ n * ((n + 1) * 4) := by
  unfold casesOfLen
  rw [length_flatMap_const (m := (n + 1) * 4), words_length]
  intro b _
  rw [length_flatMap_const (m := 4), List.length_range]
  intro k _; simp [Sc.all]

/-- The size of the space: Σ_{n=1..4} 4ⁿ·(n+1)·4 = 6368. -/
theorem space_length : space.length = 6368 := by
  simp only [space, maxFields]
  rw [show List.range 4 = [0, 1, 2, 3] from rfl]
  simp only [List.flatMap_cons, List.flatMap_nil, List.length_append, casesOfLen_length,
    List.length_nil]

/-! ## 2. The abstract generator and its inverse -/

/-- Field names: `f<j>` for the old struct's j-th field, `ins` for the new one. -/
inductive FName | old (j : Nat) | ins
  deriving DecidableEq, Repr

/-- The C spelling the generator uses for each size class. -/
def Sc.cty : Sc → String
  | .s1 => "char" | .s2 => "short" | .s4 => "int" | .s8 => "long"

def scOfCty : String → Option Sc
  | "char" => some .s1 | "short" => some .s2 | "int" => some .s4 | "long" => some .s8
  | _ => none

theorem scOfCty_cty (s : Sc) : scOfCty s.cty = some s := by cases s <;> rfl

structure Decl where
  name : FName
  cty  : String
  deriving DecidableEq, Repr

/-- A generated program pair, abstractly: the old and new struct bodies in
declaration order. The C text is a rendering of this (`struct sN { <cty> <name>; … }`). -/
structure Program where
  old : List Decl
  new : List Decl
  deriving DecidableEq, Repr

def oldDecls (b : List Sc) : List Decl :=
  (List.range b.length).zipWith (fun j s => ⟨.old j, s.cty⟩) b

def gen (c : Case) : Program :=
  let o := oldDecls c.base
  ⟨o, o.take c.pos ++ ⟨.ins, c.ins.cty⟩ :: o.drop c.pos⟩

/-- Read the old struct back: names must be `f0, f1, …` in order. -/
def decodeOld : Nat → List Decl → Option (List Sc)
  | _, [] => some []
  | j, d :: ds =>
    if d.name = .old j then
      match scOfCty d.cty, decodeOld (j + 1) ds with
      | some s, some ss => some (s :: ss)
      | _, _ => none
    else none

/-- Position of the inserted member. -/
def findIns : List Decl → Option Nat
  | [] => none
  | d :: ds => if d.name = .ins then some 0 else (findIns ds).map (· + 1)

/-- Decode a program pair to a case, or `none` if it is not a generated one. -/
def decode (p : Program) : Option Case :=
  match decodeOld 0 p.old, findIns p.new with
  | some b, some k =>
    match (p.new[k]?).bind (fun d => scOfCty d.cty) with
    | some t => if InSpace ⟨b, k, t⟩ ∧ gen ⟨b, k, t⟩ = p then some ⟨b, k, t⟩ else none
    | none => none
  | _, _ => none

theorem gen_decode {p : Program} {c : Case} (h : decode p = some c) :
    InSpace c ∧ gen c = p := by
  unfold decode at h
  split at h
  · split at h
    · split at h
      · cases h; assumption
      · contradiction
    · contradiction
  · contradiction

theorem range_succ_zipWith (b : List Sc) (j : Nat) :
    decodeOld j ((List.range' j b.length).zipWith (fun j s => (⟨.old j, s.cty⟩ : Decl)) b)
      = some b := by
  induction b generalizing j with
  | nil => simp [decodeOld]
  | cons s bs ih =>
    simp only [List.length_cons, List.range'_succ, List.zipWith_cons_cons, decodeOld,
      if_pos, scOfCty_cty, ih]

theorem decodeOld_oldDecls (b : List Sc) : decodeOld 0 (oldDecls b) = some b := by
  unfold oldDecls; rw [List.range_eq_range']; exact range_succ_zipWith b 0

theorem oldDecls_length (b : List Sc) : (oldDecls b).length = b.length := by
  simp [oldDecls]

theorem oldDecls_no_ins (b : List Sc) : ∀ d ∈ oldDecls b, d.name ≠ .ins := by
  intro d hd
  unfold oldDecls at hd
  obtain ⟨i, hi, hget⟩ := List.mem_iff_getElem.1 hd
  simp only [List.getElem_zipWith] at hget
  rw [← hget]; simp

theorem findIns_ins (o r : List Decl) (t : String) (ho : ∀ d ∈ o, d.name ≠ .ins) :
    findIns (o ++ ⟨.ins, t⟩ :: r) = some o.length := by
  induction o with
  | nil => simp [findIns]
  | cons d ds ih =>
    have hd := ho d (by simp)
    rw [List.cons_append, findIns, if_neg hd, ih (fun x hx => ho x (by simp [hx]))]
    rfl

/-- **Totality and injectivity**: decoding a generated program returns exactly
the case it was generated from. -/
theorem decode_gen (c : Case) (hc : InSpace c) : decode (gen c) = some c := by
  rcases c with ⟨b, k, t⟩
  have hk : k ≤ b.length := hc.2.2
  have hlen : ((oldDecls b).take k).length = k := by
    rw [List.length_take, oldDecls_length]; omega
  have hno : ∀ d ∈ (oldDecls b).take k, d.name ≠ .ins :=
    fun d hd => oldDecls_no_ins b d (List.mem_of_mem_take hd)
  have hget : ((oldDecls b).take k ++ ⟨.ins, t.cty⟩ :: (oldDecls b).drop k)[k]? =
      some ⟨.ins, t.cty⟩ := by
    rw [List.getElem?_append_right (by omega), hlen, Nat.sub_self]; simp
  unfold decode
  show (match decodeOld 0 (oldDecls b),
      findIns ((oldDecls b).take k ++ ⟨.ins, t.cty⟩ :: (oldDecls b).drop k) with
    | some b, some k => _
    | _, _ => none) = _
  rw [decodeOld_oldDecls, findIns_ins _ _ _ hno, hlen]
  have hnew : (gen ⟨b, k, t⟩).new = (oldDecls b).take k ++ ⟨.ins, t.cty⟩ :: (oldDecls b).drop k := rfl
  simp only [hnew, hget, Option.some_bind, scOfCty_cty]
  simp [hc]

theorem gen_injective {c₁ c₂ : Case} (h₁ : InSpace c₁) (h₂ : InSpace c₂)
    (h : gen c₁ = gen c₂) : c₁ = c₂ := by
  have := decode_gen c₁ h₁; rw [h, decode_gen c₂ h₂] at this; exact (Option.some.inj this).symm

/-- **Every generated program pair falls in exactly one case of the space.** -/
theorem generated_in_exactly_one (p : Program) (hp : p ∈ space.map gen) :
    ∃ c, (InSpace c ∧ gen c = p) ∧ ∀ c', InSpace c' ∧ gen c' = p → c' = c := by
  obtain ⟨c, hc, rfl⟩ := List.mem_map.1 hp
  have hc' := (mem_space c).1 hc
  exact ⟨c, ⟨hc', rfl⟩, fun c' ⟨h', hg⟩ => gen_injective h' hc' hg⟩

/-- …and every case of the space has a generated program (surjectivity onto
the space, stated as: decode ∘ gen is the identity on it). -/
theorem every_case_generated (c : Case) (hc : InSpace c) :
    ∃ p ∈ space.map gen, decode p = some c :=
  ⟨gen c, List.mem_map.2 ⟨c, (mem_space c).2 hc, rfl⟩, decode_gen c hc⟩

/-! ## 3. The oracle -/

def Sc.ft (s : Sc) : FType := ⟨s.cty, s.bytes, s.bytes⟩

def oldTys (c : Case) : List FType := c.base.map Sc.ft
def newTys (c : Case) : List FType := insertAt c.pos c.ins.ft (oldTys c)

def oldOffsets (c : Case) : List Nat := offsets (oldTys c)
def newOffsets (c : Case) : List Nat := offsets (newTys c)
def oldSize (c : Case) : Nat := Layout.sizeOf (oldTys c)
def newSize (c : Case) : Nat := Layout.sizeOf (newTys c)
def oldAlign (c : Case) : Nat := maxAlign (oldTys c)
def newAlign (c : Case) : Nat := maxAlign (newTys c)

/-- New index of old field `j`. -/
def newIdx (c : Case) (j : Nat) : Nat := if j < c.pos then j else j + 1

/-- Old field `j` moved. -/
def moved (c : Case) (j : Nat) : Bool :=
  (oldOffsets c)[j]? != (newOffsets c)[newIdx c j]?

def sizeChanged (c : Case) : Bool := oldSize c != newSize c
def alignChanged (c : Case) : Bool := oldAlign c != newAlign c

/-- abicheck ChangeKinds this fragment can produce (catalog names). -/
inductive Kind
  | typeFieldAddedCompatible   -- type_field_added_compatible, COMPATIBLE
  | typeFieldOffsetChanged     -- type_field_offset_changed,   BREAKING
  | typeSizeChanged            -- type_size_changed,           BREAKING
  | typeAlignmentChanged       -- type_alignment_changed,      BREAKING
  deriving DecidableEq, Repr

/-- A finding's subject: a member of the record, or the record itself. -/
inductive Subject | member (f : FName) | record
  deriving DecidableEq, Repr

structure Finding where
  kind : Kind
  subj : Subject
  deriving DecidableEq, Repr

inductive Verdict | compatible | breaking
  deriving DecidableEq, Repr

def movedFields (c : Case) : List Nat := (List.range c.base.length).filter (moved c)

/-- **Expected findings.** The rule set is the product contract for a record
passed by pointer: the added member is always reported (compatible); every
old member whose offset changed is an offset change (breaking, justified by
`AbiLean.fires_sound`); a sizeof change and an alignment change are reported
on the record (breaking, catalog `default_verdict`). -/
def findings (c : Case) : List Finding :=
  ⟨.typeFieldAddedCompatible, .member .ins⟩ ::
  (movedFields c).map (fun j => ⟨.typeFieldOffsetChanged, .member (.old j)⟩) ++
  (if sizeChanged c then [⟨.typeSizeChanged, .record⟩] else []) ++
  (if alignChanged c then [⟨.typeAlignmentChanged, .record⟩] else [])

def verdict (c : Case) : Verdict :=
  if (movedFields c).isEmpty && !sizeChanged c && !alignChanged c then .compatible else .breaking

/-- The observable result of a case: what abicheck's report states, without
the numeric old/new values (those are checked separately, per case). -/
structure Obs where
  findings : List Finding
  verdict  : Verdict
  deriving DecidableEq, Repr

def obs (c : Case) : Obs := ⟨findings c, verdict c⟩

/-! ## 4. Equivalence: when do two cases have the same observable result? -/

/-- Placement of a field list from two starts: once one field lands at the same
offset from both starts, every later field does too. -/
theorem offsetsFrom_sync (x y : Nat) (t : FType) (ts : List FType)
    (h : alignUp x t.align = alignUp y t.align) :
    offsetsFrom x (t :: ts) = offsetsFrom y (t :: ts) := by
  simp [offsetsFrom, h]

/-- Monotone "once synced, stays synced" on indices. -/
theorem offsetsFrom_tail_sync (x y : Nat) (ts : List FType) (i : Nat)
    (h : (offsetsFrom x ts)[i]? = (offsetsFrom y ts)[i]?) (hi : i < ts.length) :
    ∀ j, i ≤ j → (offsetsFrom x ts)[j]? = (offsetsFrom y ts)[j]? := by
  induction ts generalizing x y i with
  | nil => simp at hi
  | cons t ts ih =>
    intro j hj
    cases i with
    | zero =>
      have h0 : alignUp x t.align = alignUp y t.align := by simpa [offsetsFrom] using h
      rw [offsetsFrom_sync x y t ts h0]
    | succ i =>
      cases j with
      | zero => omega
      | succ j =>
        simp only [offsetsFrom, List.getElem?_cons_succ] at h ⊢
        exact ih _ _ i h (by simp at hi; omega) j (by omega)

/-- Offsets of `pre ++ rest` from `e`: the prefix, then `rest` from the prefix end. -/
theorem offsetsFrom_append (e : Nat) (pre rest : List FType) :
    offsetsFrom e (pre ++ rest) = offsetsFrom e pre ++ offsetsFrom (endFrom e pre) rest := by
  induction pre generalizing e with
  | nil => rfl
  | cons t ts ih => simp [offsetsFrom, endFrom, ih]

theorem offsetsFrom_length (e : Nat) (ts : List FType) : (offsetsFrom e ts).length = ts.length := by
  induction ts generalizing e with
  | nil => rfl
  | cons t ts ih => simp [offsetsFrom, ih]

/-- Old and new offsets of the suffix (old fields `pos, pos+1, …`) are the
same list placed from two different starts. -/
theorem suffix_offsets (c : Case) (hc : c.pos ≤ c.base.length) (j : Nat) (hj : c.pos ≤ j) :
    (oldOffsets c)[j]? = (offsetsFrom (endFrom 0 ((oldTys c).take c.pos)) ((oldTys c).drop c.pos))[j - c.pos]? ∧
    (newOffsets c)[newIdx c j]? =
      (offsetsFrom (endFrom (endFrom 0 ((oldTys c).take c.pos)) [c.ins.ft]) ((oldTys c).drop c.pos))[j - c.pos]? := by
  have hlen : ((oldTys c).take c.pos).length = c.pos := by
    simp [oldTys, List.length_take]; omega
  have hplen := offsetsFrom_length 0 ((oldTys c).take c.pos)
  constructor
  · unfold oldOffsets offsets
    conv => lhs; rw [← List.take_append_drop c.pos (oldTys c)]
    rw [offsetsFrom_append, List.getElem?_append_right (by omega)]
    congr 1; omega
  · unfold newOffsets offsets newTys insertAt newIdx
    rw [if_neg (by omega), offsetsFrom_append, List.getElem?_append_right (by omega)]
    have : (c.ins.ft :: List.drop c.pos (oldTys c)) = [c.ins.ft] ++ List.drop c.pos (oldTys c) := rfl
    rw [this, offsetsFrom_append, List.getElem?_append_right (by simp [offsetsFrom]; omega)]
    congr 1; simp [offsetsFrom]; omega

/-- Fields before the insertion point never move. -/
theorem prefix_not_moved (c : Case) (hc : c.pos ≤ c.base.length) (j : Nat) (hj : j < c.pos) :
    moved c j = false := by
  have h := insert_prefix_unchanged c.pos c.ins.ft (oldTys c) 0 (by simp [oldTys]; omega)
  have h2 := congrArg (·[j]?) h
  simp only [List.getElem?_take, if_pos hj] at h2
  simp [moved, newIdx, hj, oldOffsets, newOffsets, offsets, newTys, h2]

/-- **Contiguity.** The moved fields are an interval starting at the insertion
point: fields before it never move (`prefix_not_moved`), and if old field
`j ≥ pos` did not move, no later field moved. -/
theorem moved_contiguous (c : Case) (hc : c.pos ≤ c.base.length) (j j' : Nat)
    (hp : c.pos ≤ j) (hj : j < c.base.length) (hjj : j ≤ j') (h : moved c j = false) :
    moved c j' = false := by
  obtain ⟨o1, n1⟩ := suffix_offsets c hc j hp
  obtain ⟨o2, n2⟩ := suffix_offsets c hc j' (by omega)
  have hs : (offsetsFrom (endFrom 0 ((oldTys c).take c.pos)) ((oldTys c).drop c.pos))[j - c.pos]? =
      (offsetsFrom (endFrom (endFrom 0 ((oldTys c).take c.pos)) [c.ins.ft]) ((oldTys c).drop c.pos))[j - c.pos]? := by
    rw [← o1, ← n1]; simpa [moved] using h
  have := offsetsFrom_tail_sync _ _ _ (j - c.pos) hs (by simp [oldTys]; omega) (j' - c.pos) (by omega)
  simp [moved, o2, n2, this]

theorem moved_out_of_range (c : Case) (hc : c.pos ≤ c.base.length) (j : Nat)
    (hj : c.base.length ≤ j) : moved c j = false := by
  have h1 : (oldOffsets c)[j]? = none := by
    rw [List.getElem?_eq_none]; simp [oldOffsets, offsets, offsetsFrom_length, oldTys]; omega
  have h2 : (newOffsets c)[newIdx c j]? = none := by
    rw [List.getElem?_eq_none]
    simp only [newOffsets, offsets, offsetsFrom_length, newTys, insertAt, oldTys, newIdx]
    simp [List.length_take, List.length_drop]; split <;> omega
  simp [moved, h1, h2]

/-- A Boolean predicate that is false below `pos` and, from `pos` on, stays
false once false, selects an interval `[pos, pos+L)` from `range n`. -/
theorem filter_range_interval (p : Nat → Bool) (pos : Nat)
    (hpre : ∀ j, j < pos → p j = false)
    (hclo : ∀ j j', pos ≤ j → j ≤ j' → p j = false → p j' = false) (n : Nat) :
    (List.range n).filter p = List.range' pos ((List.range n).filter p).length := by
  induction n with
  | zero => simp
  | succ n ih =>
    rw [List.range_succ, List.filter_append]
    cases hpn : p n
    · simp only [List.filter_cons, hpn, List.filter_nil, List.append_nil]
      simpa using ih
    · simp only [List.filter_cons, hpn, List.filter_nil, if_true, List.length_append,
        List.length_singleton]
      have hmem : ∀ x, x ∈ (List.range n).filter p ↔ pos ≤ x ∧ x < pos + ((List.range n).filter p).length := by
        intro x; rw [← List.mem_range'_1, ← ih]
      have hge : pos ≤ n := Nat.le_of_not_lt fun hlt => by have := hpre n hlt; simp_all
      generalize hL : ((List.range n).filter p).length = L at ih hmem
      have hn : n = pos + L := by
        rcases Nat.lt_trichotomy n (pos + L) with hlt | heq | hgt
        · have := (hmem n).2 ⟨hge, hlt⟩
          simp [List.mem_filter, List.mem_range] at this
        · exact heq
        · have hm : p (pos + L) = false := by
            cases hq : p (pos + L)
            · rfl
            · have := (hmem (pos + L)).1 (by simp [List.mem_filter, List.mem_range, hq, hgt])
              omega
          have := hclo _ n (by omega) (by omega) hm
          simp_all
      rw [ih, List.range'_concat, hn]; simp

/-- The moved fields, as a list, are exactly the interval
`[pos, pos + L)` with `L` their number. -/
theorem movedFields_interval (c : Case) (hc : c.pos ≤ c.base.length) :
    movedFields c = List.range' c.pos (movedFields c).length := by
  have hclo : ∀ j j', c.pos ≤ j → j ≤ j' → moved c j = false → moved c j' = false := by
    intro j j' hp hjj h
    by_cases hj : j < c.base.length
    · exact moved_contiguous c hc j j' hp hj hjj h
    · exact moved_out_of_range c hc j' (by omega)
  exact filter_range_interval (moved c) c.pos (prefix_not_moved c hc) hclo _

/-! ### The class key and the equivalence theorem -/

/-- The class key: which old fields moved, whether sizeof changed, whether the
alignment changed. By `movedFields_interval` the first component is an
interval `[pos, pos+L)` (or empty), so there are at most 11 · 2 · 2 = 44 keys
for `maxFields = 4`. -/
structure Key where
  moved : List Nat
  size  : Bool
  align : Bool
  deriving DecidableEq, Repr

def key (c : Case) : Key := ⟨movedFields c, sizeChanged c, alignChanged c⟩

/-- Offset-change members named in a finding list. -/
def offsetMembers : List Finding → List Nat
  | [] => []
  | ⟨.typeFieldOffsetChanged, .member (.old j)⟩ :: fs => j :: offsetMembers fs
  | _ :: fs => offsetMembers fs

def hasKind (k : Kind) (fs : List Finding) : Bool := fs.any (·.kind == k)

theorem offsetMembers_append (a b : List Finding) :
    offsetMembers (a ++ b) = offsetMembers a ++ offsetMembers b := by
  induction a with
  | nil => rfl
  | cons f fs ih =>
    have hcons : ∀ l, offsetMembers (f :: l) = offsetMembers [f] ++ offsetMembers l := by
      intro l; rcases f with ⟨k, s⟩
      cases k <;> rcases s with ((j | _) | _) <;> rfl
    rw [List.cons_append, hcons (fs ++ b), hcons fs, ih, List.append_assoc]

theorem offsetMembers_findings (c : Case) : offsetMembers (findings c) = movedFields c := by
  have hm : ∀ l : List Nat, offsetMembers (l.map fun j => ⟨.typeFieldOffsetChanged, .member (.old j)⟩) = l := by
    intro l; induction l with
    | nil => rfl
    | cons j js ih => simp [offsetMembers, ih]
  unfold findings
  rw [List.cons_append, List.cons_append]
  simp only [offsetMembers, List.append_assoc, offsetMembers_append, hm]
  split <;> split <;> simp [offsetMembers]

theorem hasKind_size (c : Case) : hasKind .typeSizeChanged (findings c) = sizeChanged c := by
  unfold findings hasKind
  split <;> split <;> simp_all [List.any_map]

theorem hasKind_align (c : Case) : hasKind .typeAlignmentChanged (findings c) = alignChanged c := by
  unfold findings hasKind
  split <;> split <;> simp_all [List.any_map]

/-- **Equivalence condition (proved).** Two cases have the same observable
result — the same findings (kind and subject) and the same verdict — iff they
have the same key. With `movedFields_interval`, the key is determined by four
small values: the first moved field, the number of moved fields, and two bits. -/
theorem obs_eq_iff_key (c₁ c₂ : Case) : obs c₁ = obs c₂ ↔ key c₁ = key c₂ := by
  constructor
  · intro h
    have hf : findings c₁ = findings c₂ := congrArg Obs.findings h
    have h1 := offsetMembers_findings c₁
    have h2 := offsetMembers_findings c₂
    have h3 := hasKind_size c₁; have h4 := hasKind_size c₂
    have h5 := hasKind_align c₁; have h6 := hasKind_align c₂
    rw [hf] at h1 h3 h5
    simp only [key, Key.mk.injEq]
    exact ⟨h1.symm.trans h2, h3.symm.trans h4, h5.symm.trans h6⟩
  · intro h
    simp only [key, Key.mk.injEq] at h
    obtain ⟨hm, hs, ha⟩ := h
    simp [obs, findings, verdict, hm, hs, ha]

/-! ### The operation alone does not decide the class -/

/-- **Merging by operation is unsound.** Same base length, same insertion
position, same inserted type — different observable results, because padding
absorbs the insertion in one layout and not in the other (cf. the repository's
padding operators). -/
theorem opName_not_enough :
    let c₁ : Case := ⟨[.s1, .s4], 1, .s1⟩   -- {char f0; int f1} → insert char: nothing moves
    let c₂ : Case := ⟨[.s4, .s4], 1, .s1⟩   -- {int f0; int f1}  → insert char: f1 moves
    c₁.base.length = c₂.base.length ∧ c₁.pos = c₂.pos ∧ c₁.ins = c₂.ins ∧
    verdict c₁ = .compatible ∧ verdict c₂ = .breaking ∧ obs c₁ ≠ obs c₂ := by
  decide

/-- Same for append: `{int; char}` + char keeps sizeof, `{int; int}` + char grows it. -/
theorem append_not_enough :
    let c₁ : Case := ⟨[.s4, .s1], 2, .s1⟩
    let c₂ : Case := ⟨[.s4, .s4], 2, .s1⟩
    verdict c₁ = .compatible ∧ verdict c₂ = .breaking := by
  decide

end AbiLean.Fragment
