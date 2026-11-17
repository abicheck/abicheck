/-
  A closed enum fragment: "one edit to a C enum of 1–4 enumerators whose
  initializers are implicit, `= 0`, `= 2` or `= -1`", C11, LP64 SysV.

  The same five parts as the layout fragment (AbiLean.Fragment; the claims are
  stated in formal/ENUM_FRAGMENT.md):

  1. `Case`, `InSpace`, `space`   – the case space, defined without the
     generator; `mem_space`, `space_nodup`, `space_length`.
  2. `gen`, `decode`              – the abstract generator (a case ↦ the old
     and new enumerator lists) and its inverse; `decode_gen`, `gen_decode`,
     `generated_in_exactly_one`, `every_case_generated`.
  3. `values`, `findings`, `verdict`, `obs` – the oracle: C11 6.7.2.2's value
     rule (an enumerator without `=` is the previous one plus 1, the first is
     0) and the product contract for added/removed/revalued enumerators.
  4. `changed_interval`, `explicit_never_shifted`, `obs_eq_iff_key` – the
     revalued old enumerators are an interval starting at the edit; two cases
     have the same observable result iff they have the same key;
     `op_not_enough` shows the edit alone does not decide the class.
  5. The reduced set is exported by EnumFragmentExport.lean; that it hits
     every class is checked by tests/test_formal_enum_fragment.py.
-/
import AbiLean.Fragment

namespace AbiLean.EnumFragment
open AbiLean.Fragment (nodup_flatMap nodup_map_of_inj length_flatMap_const filter_range_interval)

/-! ## 1. The case space -/

/-- An enumerator's initializer: none (`imp`), `= 0`, `= 2` or `= -1`. -/
inductive Init | imp | z | two | neg
  deriving DecidableEq, Repr

def Init.all : List Init := [.imp, .z, .two, .neg]
theorem Init.mem_all (i : Init) : i ∈ Init.all := by cases i <;> decide
theorem Init.all_nodup : Init.all.Nodup := by decide

/-- Upper bound on the number of enumerators of the old enum. -/
def maxEnumerators : Nat := 4

/-- One edit. Positions index the old enum (`ins p`: the new enumerator goes
before old enumerator `p`; `p = length` appends). -/
inductive Op
  | ins (pos : Nat) (i : Init)   -- insert enumerator `ins` with initializer `i`
  | rem (pos : Nat)              -- remove old enumerator `pos`
  | set (pos : Nat) (i : Init)   -- change old enumerator `pos`'s initializer to `i`
  deriving DecidableEq, Repr

/-- One case: old enum `base` (enumerator `j` is named `e<j>`) and one edit. -/
structure Case where
  base : List Init
  op   : Op
  deriving DecidableEq, Repr

/-- Which edits apply to an enum with initializers `b`. Removing the only
enumerator is excluded by a language rule (C11 6.7.2.2: an enumerator list is
not empty); setting an initializer to the one it has is not an edit. -/
def OpOk (b : List Init) : Op → Prop
  | .ins p _ => p ≤ b.length
  | .rem p   => 2 ≤ b.length ∧ p < b.length
  | .set p i => p < b.length ∧ b[p]? ≠ some i

instance (b : List Init) (o : Op) : Decidable (OpOk b o) := by
  cases o <;> unfold OpOk <;> infer_instance

/-- **The space**, as a predicate. Nothing here mentions the generator. -/
def InSpace (c : Case) : Prop :=
  1 ≤ c.base.length ∧ c.base.length ≤ maxEnumerators ∧ OpOk c.base c.op

instance (c : Case) : Decidable (InSpace c) := by unfold InSpace; infer_instance

/-- All words of length `n` over `Init`. -/
def words : Nat → List (List Init)
  | 0 => [[]]
  | n + 1 => Init.all.flatMap fun s => (words n).map (s :: ·)

/-- Every edit of an enum with initializers `b`, by kind then position. -/
def opsFor (b : List Init) : List Op :=
  (List.range (b.length + 1)).flatMap (fun p => Init.all.map (.ins p)) ++
  (if 2 ≤ b.length then (List.range b.length).map .rem else []) ++
  (List.range b.length).flatMap (fun p => (Init.all.filter (fun i => b[p]? != some i)).map (.set p))

def casesOfLen (n : Nat) : List Case :=
  (words n).flatMap fun b => (opsFor b).map (⟨b, ·⟩)

/-- **The explicit enumeration** of the space. -/
def space : List Case := (List.range maxEnumerators).flatMap fun i => casesOfLen (i + 1)

theorem mem_words {n : Nat} {b : List Init} : b ∈ words n ↔ b.length = n := by
  induction n generalizing b with
  | zero => cases b <;> simp [words]
  | succ n ih =>
    cases b with
    | nil => simp [words]
    | cons s bs => simp [words, ih, Init.mem_all]

theorem mem_opsFor {b : List Init} {o : Op} : o ∈ opsFor b ↔ OpOk b o := by
  cases o with
  | ins p i =>
    simp only [opsFor, List.mem_append, List.mem_flatMap, List.mem_range, List.mem_map, OpOk]
    constructor
    · rintro ((⟨p', hp, i', -, h⟩ | h) | ⟨p', -, i', -, h⟩)
      · cases h; omega
      · split at h <;> simp_all
      · cases h
    · intro h; exact Or.inl (Or.inl ⟨p, by omega, i, Init.mem_all i, rfl⟩)
  | rem p =>
    simp only [opsFor, List.mem_append, List.mem_flatMap, List.mem_range, List.mem_map, OpOk]
    constructor
    · rintro ((⟨p', -, i', -, h⟩ | h) | ⟨p', -, i', -, h⟩)
      · cases h
      · split at h
        · simp only [List.mem_map, List.mem_range] at h
          obtain ⟨p', hp, h⟩ := h; cases h; omega
        · simp at h
      · cases h
    · rintro ⟨h2, hp⟩
      refine Or.inl (Or.inr ?_)
      rw [if_pos h2]; exact List.mem_map.2 ⟨p, List.mem_range.2 hp, rfl⟩
  | set p i =>
    simp only [opsFor, List.mem_append, List.mem_flatMap, List.mem_range, List.mem_map,
      List.mem_filter, OpOk]
    constructor
    · rintro ((⟨p', -, i', -, h⟩ | h) | ⟨p', hp, i', ⟨-, hne⟩, h⟩)
      · cases h
      · split at h <;> simp_all
      · cases h; exact ⟨hp, by simpa using hne⟩
    · rintro ⟨hp, hne⟩
      exact Or.inr ⟨p, hp, i, ⟨Init.mem_all i, by simpa using hne⟩, rfl⟩

theorem mem_casesOfLen {n : Nat} {c : Case} :
    c ∈ casesOfLen n ↔ c.base.length = n ∧ OpOk c.base c.op := by
  rcases c with ⟨b, o⟩
  simp only [casesOfLen, List.mem_flatMap, List.mem_map, mem_words, mem_opsFor]
  constructor
  · rintro ⟨b', hb, o', ho, h⟩; cases h; exact ⟨hb, ho⟩
  · rintro ⟨hb, ho⟩; exact ⟨b, hb, o, ho, rfl⟩

/-- **Completeness and soundness of the enumeration**: a case is listed iff it
is in the space. -/
theorem mem_space (c : Case) : c ∈ space ↔ InSpace c := by
  simp only [space, List.mem_flatMap, List.mem_range, mem_casesOfLen, InSpace, maxEnumerators]
  constructor
  · rintro ⟨i, hi, hl, ho⟩; exact ⟨by omega, by omega, ho⟩
  · rintro ⟨h1, h2, ho⟩; exact ⟨c.base.length - 1, by omega, by omega, ho⟩

/-! ### No duplicates -/

theorem words_nodup (n : Nat) : (words n).Nodup := by
  induction n with
  | zero => simp [words]
  | succ n ih =>
    apply nodup_flatMap Init.all_nodup
    · intro s _; exact nodup_map_of_inj (fun a b h => List.cons.inj h |>.2) ih
    · intro s₁ s₂ x h1 h2
      simp only [List.mem_map] at h1 h2
      obtain ⟨_, _, rfl⟩ := h1
      obtain ⟨_, _, h⟩ := h2
      exact (List.cons.inj h).1.symm

theorem opsFor_nodup (b : List Init) : (opsFor b).Nodup := by
  unfold opsFor
  have hins : ((List.range (b.length + 1)).flatMap (fun p => Init.all.map (Op.ins p))).Nodup := by
    apply nodup_flatMap (List.nodup_range _)
    · intro p _; exact nodup_map_of_inj (fun a b h => by cases h; rfl) Init.all_nodup
    · intro p₁ p₂ x h1 h2
      simp only [List.mem_map] at h1 h2
      obtain ⟨_, _, rfl⟩ := h1; obtain ⟨_, _, h⟩ := h2; cases h; rfl
  have hrem : (if 2 ≤ b.length then (List.range b.length).map Op.rem else []).Nodup := by
    split
    · exact nodup_map_of_inj (fun a b h => by cases h; rfl) (List.nodup_range _)
    · simp
  have hset : ((List.range b.length).flatMap
      (fun p => (Init.all.filter (fun i => b[p]? != some i)).map (Op.set p))).Nodup := by
    apply nodup_flatMap (List.nodup_range _)
    · intro p _
      exact nodup_map_of_inj (fun a b h => by cases h; rfl) (Init.all_nodup.filter _)
    · intro p₁ p₂ x h1 h2
      simp only [List.mem_map] at h1 h2
      obtain ⟨_, _, rfl⟩ := h1; obtain ⟨_, _, h⟩ := h2; cases h; rfl
  unfold List.Nodup at *
  rw [List.pairwise_append, List.pairwise_append]
  refine ⟨⟨hins, hrem, ?_⟩, hset, ?_⟩
  · intro x hx y hy hxy; subst hxy
    simp only [List.mem_flatMap, List.mem_map] at hx
    obtain ⟨_, _, _, _, rfl⟩ := hx
    split at hy <;> simp_all
  · intro x hx y hy hxy; subst hxy
    simp only [List.mem_flatMap, List.mem_map, List.mem_filter] at hy
    obtain ⟨_, _, _, _, rfl⟩ := hy
    simp only [List.mem_append, List.mem_flatMap, List.mem_map] at hx
    rcases hx with ⟨_, _, _, _, h⟩ | hx
    · cases h
    · split at hx <;> simp_all

theorem casesOfLen_nodup (n : Nat) : (casesOfLen n).Nodup := by
  apply nodup_flatMap (words_nodup n)
  · intro b _; exact nodup_map_of_inj (fun a b h => by cases h; rfl) (opsFor_nodup b)
  · intro b₁ b₂ x h1 h2
    simp only [List.mem_map] at h1 h2
    obtain ⟨_, _, rfl⟩ := h1; obtain ⟨_, _, h⟩ := h2; cases h; rfl

theorem space_nodup : space.Nodup := by
  apply nodup_flatMap (List.nodup_range _)
  · intro i _; exact casesOfLen_nodup _
  · intro i₁ i₂ x h1 h2
    have := (mem_casesOfLen.1 h1).1; have := (mem_casesOfLen.1 h2).1; omega

/-! ### Size -/

theorem words_length (n : Nat) : (words n).length = 4 ^ n := by
  induction n with
  | zero => rfl
  | succ n ih =>
    simp only [words]
    rw [length_flatMap_const (m := 4 ^ n)]
    · simp [Init.all, Nat.pow_succ]; omega
    · intro s _; simp [ih]

theorem filter_ne_length (x : Option Init) (hx : x.isSome) :
    (Init.all.filter (fun i => x != some i)).length = 3 := by
  rcases x with _ | i
  · simp at hx
  · cases i <;> decide

/-- Every enum of length `n` has the same number of edits: `4(n+1)` inserts,
`n` removals when `n ≥ 2`, `3n` initializer changes. -/
theorem opsFor_length (b : List Init) :
    (opsFor b).length = 4 * (b.length + 1) + (if 2 ≤ b.length then b.length else 0) + 3 * b.length := by
  unfold opsFor
  rw [List.length_append, List.length_append, length_flatMap_const (m := 4),
    length_flatMap_const (m := 3)]
  · simp only [List.length_range]; split <;> simp <;> omega
  · intro p hp
    rw [List.length_map, filter_ne_length]
    simp only [List.mem_range] at hp
    simp [List.getElem?_eq_getElem hp]
  · intro p _; simp [Init.all]

theorem casesOfLen_length (n : Nat) :
    (casesOfLen n).length = 4 ^ n * (4 * (n + 1) + (if 2 ≤ n then n else 0) + 3 * n) := by
  unfold casesOfLen
  rw [length_flatMap_const (m := 4 * (n + 1) + (if 2 ≤ n then n else 0) + 3 * n), words_length]
  intro b hb
  rw [List.length_map, opsFor_length, mem_words.1 hb]

/-- The size of the space: Σ_{n=1..4} 4ⁿ·(4(n+1) + [n≥2]·n + 3n) = 11372. -/
theorem space_length : space.length = 11372 := by
  simp only [space, maxEnumerators]
  rw [show List.range 4 = [0, 1, 2, 3] from rfl]
  simp only [List.flatMap_cons, List.flatMap_nil, List.length_append, casesOfLen_length,
    List.length_nil]
  decide

/-! ## 2. The abstract generator and its inverse -/

/-- Enumerator names: `e<j>` for the old enum's j-th enumerator, `ins` for the
inserted one. (The C text prefixes each with the enum's own tag, because C
enumerators share one name space.) -/
inductive EName | old (j : Nat) | ins
  deriving DecidableEq, Repr

structure Decl where
  name : EName
  init : Init
  deriving DecidableEq, Repr

/-- A generated program pair, abstractly: the old and new enumerator lists in
declaration order. The C text renders this as `enum enN { … }`. -/
structure Program where
  old : List Decl
  new : List Decl
  deriving DecidableEq, Repr

def declsFrom : Nat → List Init → List Decl
  | _, [] => []
  | j, i :: is => ⟨.old j, i⟩ :: declsFrom (j + 1) is

def oldDecls (b : List Init) : List Decl := declsFrom 0 b

def gen (c : Case) : Program :=
  let o := oldDecls c.base
  ⟨o, match c.op with
    | .ins p i => o.take p ++ ⟨.ins, i⟩ :: o.drop p
    | .rem p   => o.eraseIdx p
    | .set p i => o.set p ⟨.old p, i⟩⟩

theorem declsFrom_getElem? (j : Nat) (b : List Init) (k : Nat) :
    (declsFrom j b)[k]? = b[k]?.map (fun i => ⟨.old (j + k), i⟩) := by
  induction b generalizing j k with
  | nil => simp [declsFrom]
  | cons x xs ih =>
    cases k with
    | zero => simp [declsFrom]
    | succ k =>
      simp only [declsFrom, List.getElem?_cons_succ, ih]
      rw [show j + 1 + k = j + (k + 1) by omega]

theorem oldDecls_getElem? (b : List Init) (k : Nat) :
    (oldDecls b)[k]? = b[k]?.map (fun i => ⟨.old k, i⟩) := by
  simp [oldDecls, declsFrom_getElem?]

theorem declsFrom_length (j : Nat) (b : List Init) : (declsFrom j b).length = b.length := by
  induction b generalizing j with
  | nil => rfl
  | cons x xs ih => simp [declsFrom, ih]

theorem oldDecls_length (b : List Init) : (oldDecls b).length = b.length := declsFrom_length 0 b

theorem oldDecls_no_ins (b : List Init) : ∀ d ∈ oldDecls b, d.name ≠ .ins := by
  intro d hd
  obtain ⟨k, hk, h⟩ := List.mem_iff_getElem.1 hd
  have e := oldDecls_getElem? b k
  rw [List.getElem?_eq_getElem hk, h] at e
  cases hb : b[k]? with
  | none => simp [hb] at e
  | some x => simp only [hb, Option.map_some', Option.some.injEq] at e; rw [e]; simp

/-- Read the old enum back: names must be `e0, e1, …` in order. -/
def decodeOld : Nat → List Decl → List Init
  | _, [] => []
  | j, d :: ds => d.init :: decodeOld (j + 1) ds

def wellNamed : Nat → List Decl → Bool
  | _, [] => true
  | j, d :: ds => d.name == .old j && wellNamed (j + 1) ds

theorem decodeOld_declsFrom (j : Nat) (b : List Init) : decodeOld j (declsFrom j b) = b := by
  induction b generalizing j with
  | nil => rfl
  | cons x xs ih => simp [declsFrom, decodeOld, ih]

/-- Position of the inserted enumerator. -/
def findIns : List Decl → Option Nat
  | [] => none
  | d :: ds => if d.name = .ins then some 0 else (findIns ds).map (· + 1)

theorem findIns_none (l : List Decl) (h : ∀ d ∈ l, d.name ≠ .ins) : findIns l = none := by
  induction l with
  | nil => rfl
  | cons d ds ih =>
    rw [findIns, if_neg (h d (by simp)), ih (fun x hx => h x (by simp [hx]))]; rfl

theorem findIns_ins (o r : List Decl) (t : Init) (ho : ∀ d ∈ o, d.name ≠ .ins) :
    findIns (o ++ ⟨.ins, t⟩ :: r) = some o.length := by
  induction o with
  | nil => simp [findIns]
  | cons d ds ih =>
    have hd := ho d (by simp)
    rw [List.cons_append, findIns, if_neg hd, ih (fun x hx => ho x (by simp [hx]))]
    rfl

/-- The first index `≥ j` (within `fuel` steps) where `P` holds. -/
def firstIdx (P : Nat → Bool) : Nat → Nat → Option Nat
  | _, 0 => none
  | j, fuel + 1 => if P j then some j else firstIdx P (j + 1) fuel

theorem firstIdx_eq (P : Nat → Bool) (p : Nat) :
    ∀ fuel j, j ≤ p → p < j + fuel → (∀ i, j ≤ i → i < p → P i = false) → P p = true →
      firstIdx P j fuel = some p := by
  intro fuel
  induction fuel with
  | zero => intro j hj hp; omega
  | succ fuel ih =>
    intro j hj hp hpre hP
    unfold firstIdx
    by_cases hjp : j = p
    · subst hjp; simp [hP]
    · rw [if_neg (by rw [hpre j (Nat.le_refl _) (by omega)]; simp)]
      exact ih (j + 1) (by omega) (by omega) (fun i h1 h2 => hpre i (by omega) h2) hP

/-- Where a removal happened: the first new position whose name is not `e<j>`. -/
def remAt (o n : List Decl) : Option Nat :=
  firstIdx (fun j => (n[j]?.map (·.name)) != some (.old j)) 0 o.length

/-- Where an initializer changed: the first position whose declaration differs. -/
def setAt (o n : List Decl) : Option Nat :=
  firstIdx (fun j => n[j]? != o[j]?) 0 o.length

/-- Decode a program pair to a case, or `none` if it is not a generated one. -/
def decodeOp (o n : List Decl) : Option Op :=
  match findIns n with
  | some k => (n[k]?).map fun d => .ins k d.init
  | none =>
    if n.length + 1 = o.length then (remAt o n).map .rem
    else ((setAt o n).bind fun p => (n[p]?).map fun d => .set p d.init)

def decode (p : Program) : Option Case :=
  if wellNamed 0 p.old then
    match decodeOp p.old p.new with
    | some op => if InSpace ⟨decodeOld 0 p.old, op⟩ ∧ gen ⟨decodeOld 0 p.old, op⟩ = p
                 then some ⟨decodeOld 0 p.old, op⟩ else none
    | none => none
  else none

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

theorem wellNamed_declsFrom (j : Nat) (b : List Init) : wellNamed j (declsFrom j b) = true := by
  induction b generalizing j with
  | nil => rfl
  | cons x xs ih => simp [declsFrom, wellNamed, ih]

theorem decodeOp_gen (c : Case) (hc : InSpace c) :
    decodeOp (oldDecls c.base) (gen c).new = some c.op := by
  rcases c with ⟨b, o⟩
  obtain ⟨-, -, ho⟩ := hc
  have hno := oldDecls_no_ins b
  cases o with
  | ins k t =>
    have hk : k ≤ b.length := ho
    have hlen : ((oldDecls b).take k).length = k := by
      rw [List.length_take, oldDecls_length]; omega
    have hget : ((oldDecls b).take k ++ ⟨.ins, t⟩ :: (oldDecls b).drop k)[k]? =
        some ⟨.ins, t⟩ := by
      rw [List.getElem?_append_right (by omega), hlen, Nat.sub_self]; simp
    show decodeOp (oldDecls b) ((oldDecls b).take k ++ ⟨.ins, t⟩ :: (oldDecls b).drop k) = some (.ins k t)
    unfold decodeOp
    rw [findIns_ins _ _ _ (fun d hd => hno d (List.mem_of_mem_take hd)), hlen]
    dsimp only
    rw [hget]; rfl
  | rem p =>
    obtain ⟨h2, hp⟩ : 2 ≤ b.length ∧ p < b.length := ho
    show decodeOp (oldDecls b) ((oldDecls b).eraseIdx p) = some (.rem p)
    have hnoins : ∀ d ∈ (oldDecls b).eraseIdx p, d.name ≠ .ins := by
      intro d hd
      obtain ⟨i, -, h⟩ := List.mem_eraseIdx_iff_getElem?.1 hd
      exact hno d (List.mem_of_getElem? h)
    have hlen : ((oldDecls b).eraseIdx p).length + 1 = (oldDecls b).length := by
      rw [List.length_eraseIdx, oldDecls_length, if_pos hp]; omega
    unfold decodeOp
    rw [findIns_none _ hnoins, if_pos hlen]
    unfold remAt
    rw [firstIdx_eq _ p _ 0 (by omega) (by rw [oldDecls_length]; omega)]
    · rfl
    · intro i _ hi
      rw [List.getElem?_eraseIdx, if_pos hi, oldDecls_getElem?]
      simp [List.getElem?_eq_getElem (show i < b.length by omega)]
    · rw [List.getElem?_eraseIdx, if_neg (by omega), oldDecls_getElem?]
      cases hb : b[p + 1]? <;> simp
  | set p t =>
    obtain ⟨hp, hne⟩ : p < b.length ∧ b[p]? ≠ some t := ho
    show decodeOp (oldDecls b) ((oldDecls b).set p ⟨.old p, t⟩) = some (.set p t)
    have hnoins : ∀ d ∈ (oldDecls b).set p ⟨.old p, t⟩, d.name ≠ .ins := by
      intro d hd
      obtain ⟨i, h⟩ := List.mem_iff_getElem?.1 hd
      rw [List.getElem?_set] at h
      split at h
      · split at h
        · cases h; simp
        · simp at h
      · exact hno d (List.mem_of_getElem? h)
    have hlen : ¬ ((oldDecls b).set p ⟨.old p, t⟩).length + 1 = (oldDecls b).length := by
      simp
    have hgetp : ((oldDecls b).set p ⟨.old p, t⟩)[p]? = some ⟨.old p, t⟩ := by
      rw [List.getElem?_set, if_pos rfl, if_pos (by rw [oldDecls_length]; exact hp)]
    unfold decodeOp
    rw [findIns_none _ hnoins, if_neg hlen]
    unfold setAt
    rw [firstIdx_eq _ p _ 0 (by omega) (by rw [oldDecls_length]; omega)]
    · simp [hgetp]
    · intro i _ hi
      rw [List.getElem?_set, if_neg (by omega)]; simp
    · rw [hgetp, oldDecls_getElem?, List.getElem?_eq_getElem hp]
      simp only [Option.map_some', bne_iff_ne, ne_eq, Option.some.injEq]
      intro h; cases h
      exact hne (List.getElem?_eq_getElem hp)

/-- **Totality and injectivity**: decoding a generated program returns exactly
the case it was generated from. -/
theorem decode_gen (c : Case) (hc : InSpace c) : decode (gen c) = some c := by
  unfold decode
  have hold : (gen c).old = oldDecls c.base := rfl
  have hw : wellNamed 0 (oldDecls c.base) = true := wellNamed_declsFrom 0 c.base
  have hb : decodeOld 0 (oldDecls c.base) = c.base := decodeOld_declsFrom 0 c.base
  rw [hold, if_pos hw, decodeOp_gen c hc]
  dsimp only
  rw [hb]
  exact if_pos ⟨hc, rfl⟩

theorem gen_injective {c₁ c₂ : Case} (h₁ : InSpace c₁) (h₂ : InSpace c₂)
    (h : gen c₁ = gen c₂) : c₁ = c₂ := by
  have := decode_gen c₁ h₁; rw [h, decode_gen c₂ h₂] at this; exact (Option.some.inj this).symm

/-- **Every generated program pair falls in exactly one case of the space.** -/
theorem generated_in_exactly_one (p : Program) (hp : p ∈ space.map gen) :
    ∃ c, (InSpace c ∧ gen c = p) ∧ ∀ c', InSpace c' ∧ gen c' = p → c' = c := by
  obtain ⟨c, hc, rfl⟩ := List.mem_map.1 hp
  have hc' := (mem_space c).1 hc
  exact ⟨c, ⟨hc', rfl⟩, fun c' ⟨h', hg⟩ => gen_injective h' hc' hg⟩

/-- …and every case of the space has a generated program. -/
theorem every_case_generated (c : Case) (hc : InSpace c) :
    ∃ p ∈ space.map gen, decode p = some c :=
  ⟨gen c, List.mem_map.2 ⟨c, (mem_space c).2 hc, rfl⟩, decode_gen c hc⟩

/-! ## 3. The oracle -/

/-- C11 6.7.2.2p3: an enumerator with `=` has that value; one without has the
previous enumerator's value plus 1, and the first one without has 0 (as if
the previous were -1). -/
def Init.next (prev : Int) : Init → Int
  | .imp => prev + 1
  | .z   => 0
  | .two => 2
  | .neg => -1

def valsFrom : Int → List Init → List Int
  | _, [] => []
  | a, i :: is => i.next a :: valsFrom (i.next a) is

/-- The enumerators' values, in declaration order. -/
def values (l : List Init) : List Int := valsFrom (-1) l

/-- The new enum's initializers. -/
def newInits (c : Case) : List Init :=
  match c.op with
  | .ins p i => c.base.take p ++ i :: c.base.drop p
  | .rem p   => c.base.eraseIdx p
  | .set p i => c.base.set p i

/-- Position of old enumerator `j` in the new enum (`none`: removed). -/
def newIdx (c : Case) (j : Nat) : Option Nat :=
  match c.op with
  | .ins p _ => some (if j < p then j else j + 1)
  | .rem p   => if j < p then some j else if j = p then none else some (j - 1)
  | .set _ _ => some j

def oldVal (c : Case) (j : Nat) : Option Int := (values c.base)[j]?
def newVal (c : Case) (j : Nat) : Option Int := (newIdx c j).bind fun k => (values (newInits c))[k]?

/-- Old enumerator `j` is still there and its value changed. -/
def changed (c : Case) (j : Nat) : Bool := (newIdx c j).isSome && oldVal c j != newVal c j
/-- Old enumerator `j` is gone. -/
def removed (c : Case) (j : Nat) : Bool := decide (j < c.base.length) && (newIdx c j).isNone
def added (c : Case) : Bool := match c.op with | .ins _ _ => true | _ => false

def changedList (c : Case) : List Nat := (List.range c.base.length).filter (changed c)
def removedList (c : Case) : List Nat := (List.range c.base.length).filter (removed c)

/-- abicheck ChangeKinds this fragment can produce (catalog names). -/
inductive Kind
  | memberAdded          -- enum_member_added,         COMPATIBLE
  | memberRemoved        -- enum_member_removed,       BREAKING
  | memberValueChanged   -- enum_member_value_changed, BREAKING
  deriving DecidableEq, Repr

structure Finding where
  kind : Kind
  subj : EName
  deriving DecidableEq, Repr

inductive Verdict | noChange | compatible | breaking
  deriving DecidableEq, Repr

/-- **Expected findings**, the product contract for an enum used in an exported
signature: an inserted enumerator is reported (compatible: old code never
sees its value); a removed one is reported (breaking: old code uses a
constant that no longer exists); every remaining old enumerator whose value
changed is reported (breaking: old binaries carry the old value). Spelling
an initializer differently with the same value is no change. The enum's
size is not an observable here: with every value in `[-1, 6]` it is 4 bytes
on both sides (C11 6.7.2.2p4 leaves the type to the implementation; LP64 SysV
compilers use `int`/`unsigned int`), which the C run asserts. -/
def findings (c : Case) : List Finding :=
  (if added c then [⟨.memberAdded, .ins⟩] else []) ++
  (removedList c).map (fun j => ⟨.memberRemoved, .old j⟩) ++
  (changedList c).map (fun j => ⟨.memberValueChanged, .old j⟩)

def verdict (c : Case) : Verdict :=
  if (removedList c).isEmpty && (changedList c).isEmpty then
    (if added c then .compatible else .noChange)
  else .breaking

structure Obs where
  findings : List Finding
  verdict  : Verdict
  deriving DecidableEq, Repr

def obs (c : Case) : Obs := ⟨findings c, verdict c⟩

/-! ### The oracle reads the generated program -/

theorem declsFrom_map_init (j : Nat) (b : List Init) : (declsFrom j b).map (·.init) = b := by
  induction b generalizing j with
  | nil => rfl
  | cons x xs ih => simp [declsFrom, ih]

theorem eraseIdx_map {α β} (f : α → β) (l : List α) (k : Nat) :
    (l.eraseIdx k).map f = (l.map f).eraseIdx k := by
  rw [List.eraseIdx_eq_take_drop_succ, List.eraseIdx_eq_take_drop_succ]; simp [List.map_take, List.map_drop]

/-- The oracle's new initializers are the generated new enum's. -/
theorem gen_new_inits (c : Case) : (gen c).new.map (·.init) = newInits c := by
  rcases c with ⟨b, o⟩
  have h := declsFrom_map_init 0 b
  cases o <;> simp [gen, newInits, oldDecls, List.map_take, List.map_drop, eraseIdx_map, List.map_set, h]

/-! ## 4. Equivalence: when do two cases have the same observable result? -/

def lastFrom : Int → List Init → Int
  | a, [] => a
  | a, i :: is => lastFrom (i.next a) is

theorem valsFrom_append (a : Int) (l₁ l₂ : List Init) :
    valsFrom a (l₁ ++ l₂) = valsFrom a l₁ ++ valsFrom (lastFrom a l₁) l₂ := by
  induction l₁ generalizing a with
  | nil => rfl
  | cons i is ih => simp [valsFrom, lastFrom, ih]

theorem valsFrom_length (a : Int) (l : List Init) : (valsFrom a l).length = l.length := by
  induction l generalizing a with
  | nil => rfl
  | cons i is ih => simp [valsFrom, ih]

/-- Values from two starts: once they agree at one position, they agree at
every later one (each value depends only on its predecessor). -/
theorem valsFrom_sync (x y : Int) (l : List Init) (i : Nat)
    (h : (valsFrom x l)[i]? = (valsFrom y l)[i]?) (hi : i < l.length) :
    ∀ j, i ≤ j → (valsFrom x l)[j]? = (valsFrom y l)[j]? := by
  induction l generalizing x y i with
  | nil => simp at hi
  | cons t ts ih =>
    intro j hj
    cases i with
    | zero =>
      have h0 : t.next x = t.next y := by simpa [valsFrom] using h
      simp [valsFrom, h0]
    | succ i =>
      cases j with
      | zero => omega
      | succ j =>
        simp only [valsFrom, List.getElem?_cons_succ] at h ⊢
        exact ih _ _ i h (by simp at hi; omega) j (by omega)

/-- An explicit initializer's value does not depend on its predecessor. -/
theorem valsFrom_explicit (x y : Int) (l : List Init) (k : Nat) (i : Init)
    (hk : l[k]? = some i) (hi : i ≠ .imp) : (valsFrom x l)[k]? = (valsFrom y l)[k]? := by
  induction l generalizing x y k with
  | nil => simp at hk
  | cons t ts ih =>
    cases k with
    | zero =>
      simp only [List.getElem?_cons_zero, Option.some.injEq] at hk; subst hk
      cases t <;> simp_all [valsFrom, Init.next]
    | succ k => simp only [valsFrom, List.getElem?_cons_succ] at hk ⊢; exact ih _ _ k hk

/-- Where an edit can first change a value: the edit position (insert, set),
or the enumerator after the removed one. -/
def start (c : Case) : Nat :=
  match c.op with
  | .ins p _ => p
  | .rem p   => p + 1
  | .set p _ => p

/-- **The suffix representation.** From `start c` on, the old and the new
values of the old enumerators are one initializer list `L` evaluated from two
different starts `x`, `y`. -/
theorem suffix_vals (c : Case) (hc : InSpace c) :
    ∃ x y : Int, ∃ L : List Init, start c ≤ c.base.length ∧ L.length = c.base.length - start c ∧
      ∀ j, start c ≤ j →
        oldVal c j = (valsFrom x L)[j - start c]? ∧ newVal c j = (valsFrom y L)[j - start c]? ∧
        (newIdx c j).isSome := by
  rcases c with ⟨b, o⟩
  obtain ⟨-, -, ho⟩ := hc
  have hsplit : ∀ p, b = b.take p ++ b.drop p := fun p => (List.take_append_drop p b).symm
  cases o with
  | ins p t =>
    have hp : p ≤ b.length := ho
    have hlen : (valsFrom (-1) (b.take p)).length = p := by
      rw [valsFrom_length, List.length_take]; omega
    refine ⟨lastFrom (-1) (b.take p), t.next (lastFrom (-1) (b.take p)), b.drop p,
      hp, by simp [start], fun j hj => ?_⟩
    simp only [start] at hj ⊢
    refine ⟨?_, ?_, by simp [newIdx]⟩
    · simp only [oldVal, values]
      conv => lhs; rw [hsplit p]
      rw [valsFrom_append, List.getElem?_append_right (by omega), hlen]
    · simp only [newVal, newIdx, if_neg (show ¬ j < p by omega), Option.some_bind, values, newInits]
      rw [valsFrom_append, List.getElem?_append_right (by omega), hlen]
      simp only [valsFrom]
      rw [show j + 1 - p = (j - p) + 1 by omega, List.getElem?_cons_succ]
  | rem p =>
    obtain ⟨-, hp⟩ : 2 ≤ b.length ∧ p < b.length := ho
    have hd := List.drop_eq_getElem_cons hp
    have hlen : (valsFrom (-1) (b.take p)).length = p := by
      rw [valsFrom_length, List.length_take]; omega
    refine ⟨b[p].next (lastFrom (-1) (b.take p)), lastFrom (-1) (b.take p), b.drop (p + 1),
      by simp only [start]; omega, by simp only [start, List.length_drop], fun j hj => ?_⟩
    simp only [start] at hj
    have hnot : ¬ j < p := by omega
    have hne : ¬ j = p := by omega
    refine ⟨?_, ?_, by simp [newIdx, hnot, hne]⟩
    · simp only [oldVal, values, start]
      conv => lhs; rw [hsplit p, hd]
      rw [valsFrom_append, List.getElem?_append_right (by omega), hlen]
      simp only [valsFrom]
      rw [show j - p = (j - (p + 1)) + 1 by omega, List.getElem?_cons_succ]
    · simp only [newVal, newIdx, hnot, hne, if_false, Option.some_bind, values, newInits, start]
      rw [List.eraseIdx_eq_take_drop_succ, valsFrom_append, List.getElem?_append_right (by omega),
        hlen]
      congr 1; omega
  | set p t =>
    obtain ⟨hp, -⟩ : p < b.length ∧ b[p]? ≠ some t := ho
    have hd := List.drop_eq_getElem_cons hp
    have hlen : (valsFrom (-1) (b.take p)).length = p := by
      rw [valsFrom_length, List.length_take]; omega
    -- `v :: valsFrom v L = valsFrom (v - 1) (imp :: L)`: one list, two starts.
    refine ⟨b[p].next (lastFrom (-1) (b.take p)) - 1, t.next (lastFrom (-1) (b.take p)) - 1,
      .imp :: b.drop (p + 1), by simp [start]; omega, by simp [start]; omega, fun j hj => ?_⟩
    simp only [start] at hj ⊢
    refine ⟨?_, ?_, by simp [newIdx]⟩
    · simp only [oldVal, values]
      conv => lhs; rw [hsplit p, hd]
      rw [valsFrom_append, List.getElem?_append_right (by omega), hlen]
      congr 1; simp only [valsFrom, Init.next, Int.sub_add_cancel]
    · simp only [newVal, newIdx, Option.some_bind, values, newInits]
      rw [List.set_eq_take_append_cons_drop, if_pos hp, valsFrom_append,
        List.getElem?_append_right (by omega), hlen]
      congr 1; simp only [valsFrom, Init.next, Int.sub_add_cancel]

/-- Before `start c`, no old enumerator changes value. -/
theorem prefix_unchanged (c : Case) (hc : InSpace c) (j : Nat) (hj : j < start c) :
    changed c j = false := by
  rcases c with ⟨b, o⟩
  obtain ⟨-, -, ho⟩ := hc
  have pre : ∀ (p : Nat) (r : List Init), j < p → p ≤ b.length →
      (values b)[j]? = (values (b.take p ++ r))[j]? := by
    intro p r hjp hp
    have hlen : (valsFrom (-1) (b.take p)).length = p := by
      rw [valsFrom_length, List.length_take]; omega
    simp only [values]
    conv => lhs; rw [← List.take_append_drop p b]
    rw [valsFrom_append, valsFrom_append, List.getElem?_append_left (by omega),
      List.getElem?_append_left (by omega)]
  cases o with
  | ins p t =>
    have hp : p ≤ b.length := ho
    simp only [start] at hj
    simp only [changed, oldVal, newVal, newIdx, if_pos hj, Option.some_bind, newInits]
    rw [pre p (t :: b.drop p) hj hp]; simp
  | rem p =>
    obtain ⟨-, hp⟩ : 2 ≤ b.length ∧ p < b.length := ho
    simp only [start] at hj
    by_cases hjp : j < p
    · simp only [changed, oldVal, newVal, newIdx, if_pos hjp, Option.some_bind, newInits,
        List.eraseIdx_eq_take_drop_succ]
      rw [pre p (b.drop (p + 1)) hjp (by omega)]; simp
    · have : j = p := by omega
      simp [changed, newIdx, this]
  | set p t =>
    obtain ⟨hp, -⟩ : p < b.length ∧ b[p]? ≠ some t := ho
    simp only [start] at hj
    simp only [changed, oldVal, newVal, newIdx, Option.some_bind, newInits,
      List.set_eq_take_append_cons_drop, if_pos hp]
    rw [pre p (t :: b.drop (p + 1)) hj (by omega)]; simp

/-- Old enumerators past the end never change. -/
theorem changed_out_of_range (c : Case) (hc : InSpace c) (j : Nat) (hj : c.base.length ≤ j) :
    changed c j = false := by
  obtain ⟨x, y, L, hs, hL, h⟩ := suffix_vals c hc
  obtain ⟨ho, hn, -⟩ := h j (by omega)
  have e1 : (valsFrom x L)[j - start c]? = none := by
    rw [List.getElem?_eq_none]; rw [valsFrom_length]; omega
  have e2 : (valsFrom y L)[j - start c]? = none := by
    rw [List.getElem?_eq_none]; rw [valsFrom_length]; omega
  simp [changed, ho, hn, e1, e2]

/-- **Contiguity.** From `start c` on, once an old enumerator keeps its value,
every later one does. -/
theorem changed_contiguous (c : Case) (hc : InSpace c) (j j' : Nat)
    (hs : start c ≤ j) (hjj : j ≤ j') (h : changed c j = false) : changed c j' = false := by
  by_cases hj : j < c.base.length
  · obtain ⟨x, y, L, hsl, hL, hv⟩ := suffix_vals c hc
    obtain ⟨o1, n1, s1⟩ := hv j hs
    obtain ⟨o2, n2, s2⟩ := hv j' (by omega)
    have heq : (valsFrom x L)[j - start c]? = (valsFrom y L)[j - start c]? := by
      rw [← o1, ← n1]; simpa [changed, s1] using h
    have := valsFrom_sync x y L (j - start c) heq (by omega) (j' - start c) (by omega)
    simp [changed, o2, n2, this]
  · exact changed_out_of_range c hc j' (by omega)

/-- **The revalued old enumerators are exactly the interval
`[start c, start c + L)`**, `L` their number. -/
theorem changed_interval (c : Case) (hc : InSpace c) :
    changedList c = List.range' (start c) (changedList c).length :=
  filter_range_interval (changed c) (start c) (prefix_unchanged c hc)
    (fun j j' hs hjj h => changed_contiguous c hc j j' hs hjj h) _

/-- An inserted or removed enumerator never changes an old enumerator that has
an explicit initializer: the shift runs only through implicit ones. -/
theorem explicit_never_shifted (c : Case) (hc : InSpace c)
    (hop : match c.op with | .set _ _ => False | _ => True)
    (j : Nat) (i : Init) (hj : c.base[j]? = some i) (hi : i ≠ .imp) : changed c j = false := by
  by_cases hs : j < start c
  · exact prefix_unchanged c hc j hs
  · rcases c with ⟨b, o⟩
    obtain ⟨-, -, ho⟩ := hc
    cases o with
    | set _ _ => exact hop.elim
    | ins p t =>
      have hp : p ≤ b.length := ho
      simp only [start] at hs
      have hlen : (valsFrom (-1) (b.take p)).length = p := by
        rw [valsFrom_length, List.length_take]; omega
      have hk : (b.drop p)[j - p]? = some i := by rw [List.getElem?_drop]; rw [← hj]; congr 1; omega
      have e := valsFrom_explicit (lastFrom (-1) (b.take p)) (t.next (lastFrom (-1) (b.take p)))
        (b.drop p) (j - p) i hk hi
      have o1 : oldVal ⟨b, .ins p t⟩ j = (valsFrom (lastFrom (-1) (b.take p)) (b.drop p))[j - p]? := by
        simp only [oldVal, values]
        conv => lhs; rw [← List.take_append_drop p b]
        rw [valsFrom_append, List.getElem?_append_right (by omega), hlen]
      have n1 : newVal ⟨b, .ins p t⟩ j = (valsFrom (t.next (lastFrom (-1) (b.take p))) (b.drop p))[j - p]? := by
        simp only [newVal, newIdx, if_neg (show ¬ j < p by omega), Option.some_bind, values, newInits]
        rw [valsFrom_append, List.getElem?_append_right (by omega), hlen]
        simp only [valsFrom]
        rw [show j + 1 - p = (j - p) + 1 by omega, List.getElem?_cons_succ]
      simp [changed, o1, n1, e]
    | rem p =>
      obtain ⟨-, hp⟩ : 2 ≤ b.length ∧ p < b.length := ho
      simp only [start] at hs
      have hd := List.drop_eq_getElem_cons hp
      have hlen : (valsFrom (-1) (b.take p)).length = p := by
        rw [valsFrom_length, List.length_take]; omega
      have hk : (b.drop (p + 1))[j - (p + 1)]? = some i := by
        rw [List.getElem?_drop]; rw [← hj]; congr 1; omega
      have e := valsFrom_explicit (b[p].next (lastFrom (-1) (b.take p))) (lastFrom (-1) (b.take p))
        (b.drop (p + 1)) (j - (p + 1)) i hk hi
      have o1 : oldVal ⟨b, .rem p⟩ j =
          (valsFrom (b[p].next (lastFrom (-1) (b.take p))) (b.drop (p + 1)))[j - (p + 1)]? := by
        simp only [oldVal, values]
        conv => lhs; rw [← List.take_append_drop p b, hd]
        rw [valsFrom_append, List.getElem?_append_right (by omega), hlen]
        simp only [valsFrom]
        rw [show j - p = (j - (p + 1)) + 1 by omega, List.getElem?_cons_succ]
      have n1 : newVal ⟨b, .rem p⟩ j = (valsFrom (lastFrom (-1) (b.take p)) (b.drop (p + 1)))[j - (p + 1)]? := by
        simp only [newVal, newIdx, if_neg (show ¬ j < p by omega), if_neg (show ¬ j = p by omega),
          Option.some_bind, values, newInits]
        rw [List.eraseIdx_eq_take_drop_succ, valsFrom_append, List.getElem?_append_right (by omega),
          hlen]
        congr 1; omega
      simp [changed, o1, n1, e]

/-- Appending an enumerator is never a break. -/
theorem append_compatible (c : Case) (hc : InSpace c) (t : Init)
    (h : c.op = .ins c.base.length t) : verdict c = .compatible := by
  have hch : changedList c = [] := by
    rw [changedList, List.filter_eq_nil_iff]
    intro j hj
    have := prefix_unchanged c hc j (by simp [start, h]; simpa using hj)
    simp [this]
  have hrm : removedList c = [] := by
    rw [removedList, List.filter_eq_nil_iff]
    intro j _; simp [removed, newIdx, h]
  simp [verdict, hch, hrm, added, h]

/-! ### The class key and the equivalence theorem -/

/-- The class key: whether an enumerator was added, which were removed,
which were revalued. By `changed_interval` the last is an interval. -/
structure Key where
  added   : Bool
  removed : List Nat
  changed : List Nat
  deriving DecidableEq, Repr

def key (c : Case) : Key := ⟨added c, removedList c, changedList c⟩

def membersOf (k : Kind) : List Finding → List Nat
  | [] => []
  | ⟨k', .old j⟩ :: fs => if k' = k then j :: membersOf k fs else membersOf k fs
  | _ :: fs => membersOf k fs

def hasAdded (fs : List Finding) : Bool := fs.any (·.kind == .memberAdded)

theorem membersOf_append (k : Kind) (a b : List Finding) :
    membersOf k (a ++ b) = membersOf k a ++ membersOf k b := by
  induction a with
  | nil => rfl
  | cons f fs ih =>
    rcases f with ⟨k', (j | _)⟩
    · rw [List.cons_append]; simp only [membersOf]; rw [ih]; split <;> rfl
    · rw [List.cons_append]; simp only [membersOf]; exact ih

theorem membersOf_map (k k' : Kind) (l : List Nat) :
    membersOf k (l.map fun j => ⟨k', .old j⟩) = if k' = k then l else [] := by
  induction l with
  | nil => simp [membersOf]
  | cons j js ih => simp only [List.map_cons, membersOf, ih]; split <;> simp

theorem membersOf_added (k : Kind) (c : Case) :
    membersOf k (if added c then [⟨.memberAdded, .ins⟩] else []) = [] := by
  split <;> rfl

theorem removed_of_findings (c : Case) : membersOf .memberRemoved (findings c) = removedList c := by
  simp [findings, membersOf_append, membersOf_map, membersOf_added]

theorem changed_of_findings (c : Case) :
    membersOf .memberValueChanged (findings c) = changedList c := by
  simp [findings, membersOf_append, membersOf_map, membersOf_added]

theorem added_of_findings (c : Case) : hasAdded (findings c) = added c := by
  unfold findings hasAdded
  cases added c <;> simp [List.any_map]

/-- **Equivalence condition (proved).** Two cases have the same observable
result — the same findings and the same verdict — iff they have the same key. -/
theorem obs_eq_iff_key (c₁ c₂ : Case) : obs c₁ = obs c₂ ↔ key c₁ = key c₂ := by
  constructor
  · intro h
    have hf : findings c₁ = findings c₂ := congrArg Obs.findings h
    have h1 := removed_of_findings c₁; have h2 := removed_of_findings c₂
    have h3 := changed_of_findings c₁; have h4 := changed_of_findings c₂
    have h5 := added_of_findings c₁; have h6 := added_of_findings c₂
    rw [hf] at h1 h3 h5
    simp only [key, Key.mk.injEq]
    exact ⟨h5.symm.trans h6, h1.symm.trans h2, h3.symm.trans h4⟩
  · intro h
    simp only [key, Key.mk.injEq] at h
    obtain ⟨ha, hr, hc⟩ := h
    simp [obs, findings, verdict, ha, hr, hc]

/-! ### The edit alone does not decide the class -/

/-- **Merging by edit is unsound.** Inserting an implicit enumerator first
shifts an implicit `e0` (0 → 1, breaking) but not an explicit `e0 = 0`. -/
theorem op_not_enough :
    let c₁ : Case := ⟨[.z], .ins 0 .imp⟩     -- enum { ins, e0 = 0 }: e0 stays 0
    let c₂ : Case := ⟨[.imp], .ins 0 .imp⟩   -- enum { ins, e0 }:     e0 becomes 1
    c₁.op = c₂.op ∧ verdict c₁ = .compatible ∧ verdict c₂ = .breaking ∧ obs c₁ ≠ obs c₂ := by
  decide

/-- A changed initializer with the same value is no change at all. -/
theorem respelling_is_no_change : verdict ⟨[.imp], .set 0 .z⟩ = .noChange := by decide

end AbiLean.EnumFragment
