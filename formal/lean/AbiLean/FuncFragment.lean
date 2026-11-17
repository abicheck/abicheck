/-
  A closed function-signature fragment: "one edit to the parameter list of a
  C function with 1–3 parameters of type `int`, `long`, `double` or `void *`",
  C11, x86-64 System V.

  The same five parts as the layout and enum fragments (claims in
  formal/FUNC_FRAGMENT.md), with two oracles instead of one:

  1. `Case`, `InSpace`, `space`   – the case space; `mem_space`,
     `space_nodup`, `space_length`.
  2. `gen`, `decode`              – the abstract generator and its inverse;
     `decode_gen`, `gen_decode`, `generated_in_exactly_one`.
  3. `findings`, `verdict`        – the report contract: parameters are
     matched by position (in C a parameter's identity is its position).
     `callCompatible`             – the calling-convention model: SysV
     assigns each scalar argument the next register of its class (INTEGER:
     rdi, rsi, …; SSE: xmm0, …); an old caller still calls the new function
     correctly iff every parameter the new function reads is where the old
     caller put it, in the same representation, and nothing was inserted.
  4. `rem_compatible_iff`, `set_compatible_iff`, `ins_never_compatible`,
     `obs_eq_iff_key`; `breaking_but_call_compatible` is a case the report
     contract calls breaking although the binary call interface is unchanged.
  5. The reduced set is exported by FuncFragmentExport.lean.
-/
import AbiLean.Fragment

namespace AbiLean.FuncFragment
open AbiLean.Fragment (nodup_flatMap nodup_map_of_inj length_flatMap_const filter_range_interval)

/-! ## 1. The case space -/

/-- A parameter type: `int`, `long`, `double`, `void *`. -/
inductive Ty | int | long | dbl | ptr
  deriving DecidableEq, Repr

def Ty.all : List Ty := [.int, .long, .dbl, .ptr]
theorem Ty.mem_all (i : Ty) : i ∈ Ty.all := by cases i <;> decide
theorem Ty.all_nodup : Ty.all.Nodup := by decide

/-- Upper bound on the number of parameters of the old function. -/
def maxParams : Nat := 3

/-- One edit. Positions index the old list (`ins p`: the new parameter goes
before old parameter `p`; `p = length` appends). -/
inductive Op
  | ins (pos : Nat) (i : Ty)   -- insert parameter `ins` of type `i`
  | rem (pos : Nat)              -- remove old parameter `pos`
  | set (pos : Nat) (i : Ty)   -- change old parameter `pos`'s type to `i`
  deriving DecidableEq, Repr

/-- One case: old parameter types `base` (parameter `j` is named `p<j>`) and one edit. -/
structure Case where
  base : List Ty
  op   : Op
  deriving DecidableEq, Repr

/-- Which edits apply to a parameter list `b`. Removing the only parameter
is allowed (the function becomes `(void)`); setting a type to the one it has
is not an edit. -/
def OpOk (b : List Ty) : Op → Prop
  | .ins p _ => p ≤ b.length
  | .rem p   => 1 ≤ b.length ∧ p < b.length
  | .set p i => p < b.length ∧ b[p]? ≠ some i

instance (b : List Ty) (o : Op) : Decidable (OpOk b o) := by
  cases o <;> unfold OpOk <;> infer_instance

/-- **The space**, as a predicate. Nothing here mentions the generator. -/
def InSpace (c : Case) : Prop :=
  1 ≤ c.base.length ∧ c.base.length ≤ maxParams ∧ OpOk c.base c.op

instance (c : Case) : Decidable (InSpace c) := by unfold InSpace; infer_instance

/-- All words of length `n` over `Ty`. -/
def words : Nat → List (List Ty)
  | 0 => [[]]
  | n + 1 => Ty.all.flatMap fun s => (words n).map (s :: ·)

/-- Every edit of a parameter list `b`, by kind then position. -/
def opsFor (b : List Ty) : List Op :=
  (List.range (b.length + 1)).flatMap (fun p => Ty.all.map (.ins p)) ++
  (if 1 ≤ b.length then (List.range b.length).map .rem else []) ++
  (List.range b.length).flatMap (fun p => (Ty.all.filter (fun i => b[p]? != some i)).map (.set p))

def casesOfLen (n : Nat) : List Case :=
  (words n).flatMap fun b => (opsFor b).map (⟨b, ·⟩)

/-- **The explicit enumeration** of the space. -/
def space : List Case := (List.range maxParams).flatMap fun i => casesOfLen (i + 1)

theorem mem_words {n : Nat} {b : List Ty} : b ∈ words n ↔ b.length = n := by
  induction n generalizing b with
  | zero => cases b <;> simp [words]
  | succ n ih =>
    cases b with
    | nil => simp [words]
    | cons s bs => simp [words, ih, Ty.mem_all]

theorem mem_opsFor {b : List Ty} {o : Op} : o ∈ opsFor b ↔ OpOk b o := by
  cases o with
  | ins p i =>
    simp only [opsFor, List.mem_append, List.mem_flatMap, List.mem_range, List.mem_map, OpOk]
    constructor
    · rintro ((⟨p', hp, i', -, h⟩ | h) | ⟨p', -, i', -, h⟩)
      · cases h; omega
      · split at h <;> simp_all
      · cases h
    · intro h; exact Or.inl (Or.inl ⟨p, by omega, i, Ty.mem_all i, rfl⟩)
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
      exact Or.inr ⟨p, hp, i, ⟨Ty.mem_all i, by simpa using hne⟩, rfl⟩

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
  simp only [space, List.mem_flatMap, List.mem_range, mem_casesOfLen, InSpace, maxParams]
  constructor
  · rintro ⟨i, hi, hl, ho⟩; exact ⟨by omega, by omega, ho⟩
  · rintro ⟨h1, h2, ho⟩; exact ⟨c.base.length - 1, by omega, by omega, ho⟩

/-! ### No duplicates -/

theorem words_nodup (n : Nat) : (words n).Nodup := by
  induction n with
  | zero => simp [words]
  | succ n ih =>
    apply nodup_flatMap Ty.all_nodup
    · intro s _; exact nodup_map_of_inj (fun a b h => List.cons.inj h |>.2) ih
    · intro s₁ s₂ x h1 h2
      simp only [List.mem_map] at h1 h2
      obtain ⟨_, _, rfl⟩ := h1
      obtain ⟨_, _, h⟩ := h2
      exact (List.cons.inj h).1.symm

theorem opsFor_nodup (b : List Ty) : (opsFor b).Nodup := by
  unfold opsFor
  have hins : ((List.range (b.length + 1)).flatMap (fun p => Ty.all.map (Op.ins p))).Nodup := by
    apply nodup_flatMap (List.nodup_range _)
    · intro p _; exact nodup_map_of_inj (fun a b h => by cases h; rfl) Ty.all_nodup
    · intro p₁ p₂ x h1 h2
      simp only [List.mem_map] at h1 h2
      obtain ⟨_, _, rfl⟩ := h1; obtain ⟨_, _, h⟩ := h2; cases h; rfl
  have hrem : (if 1 ≤ b.length then (List.range b.length).map Op.rem else []).Nodup := by
    split
    · exact nodup_map_of_inj (fun a b h => by cases h; rfl) (List.nodup_range _)
    · simp
  have hset : ((List.range b.length).flatMap
      (fun p => (Ty.all.filter (fun i => b[p]? != some i)).map (Op.set p))).Nodup := by
    apply nodup_flatMap (List.nodup_range _)
    · intro p _
      exact nodup_map_of_inj (fun a b h => by cases h; rfl) (Ty.all_nodup.filter _)
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
    · simp [Ty.all, Nat.pow_succ]; omega
    · intro s _; simp [ih]

theorem filter_ne_length (x : Option Ty) (hx : x.isSome) :
    (Ty.all.filter (fun i => x != some i)).length = 3 := by
  rcases x with _ | i
  · simp at hx
  · cases i <;> decide

/-- Every parameter list of length `n ≥ 1` has the same number of edits:
`4(n+1)` inserts, `n` removals, `3n` type changes. -/
theorem opsFor_length (b : List Ty) :
    (opsFor b).length = 4 * (b.length + 1) + (if 1 ≤ b.length then b.length else 0) + 3 * b.length := by
  unfold opsFor
  rw [List.length_append, List.length_append, length_flatMap_const (m := 4),
    length_flatMap_const (m := 3)]
  · simp only [List.length_range]; split <;> simp <;> omega
  · intro p hp
    rw [List.length_map, filter_ne_length]
    simp only [List.mem_range] at hp
    simp [List.getElem?_eq_getElem hp]
  · intro p _; simp [Ty.all]

theorem casesOfLen_length (n : Nat) :
    (casesOfLen n).length = 4 ^ n * (4 * (n + 1) + (if 1 ≤ n then n else 0) + 3 * n) := by
  unfold casesOfLen
  rw [length_flatMap_const (m := 4 * (n + 1) + (if 1 ≤ n then n else 0) + 3 * n), words_length]
  intro b hb
  rw [List.length_map, opsFor_length, mem_words.1 hb]

/-- The size of the space: Σ_{n=1..3} 4ⁿ·(4(n+1) + n + 3n) = 2160. -/
theorem space_length : space.length = 2160 := by
  simp only [space, maxParams]
  rw [show List.range 3 = [0, 1, 2] from rfl]
  simp only [List.flatMap_cons, List.flatMap_nil, List.length_append, casesOfLen_length,
    List.length_nil]
  decide

/-! ## 2. The abstract generator and its inverse -/

/-- Parameter names: `p<j>` for the old function's j-th parameter, `ins` for
the inserted one. -/
inductive PName | old (j : Nat) | ins
  deriving DecidableEq, Repr

structure Decl where
  name : PName
  ty   : Ty
  deriving DecidableEq, Repr

/-- A generated program pair, abstractly: the old and new parameter lists in
declaration order. The C text renders this as `int fN(<type> <name>, …)`. -/
structure Program where
  old : List Decl
  new : List Decl
  deriving DecidableEq, Repr

def declsFrom : Nat → List Ty → List Decl
  | _, [] => []
  | j, i :: is => ⟨.old j, i⟩ :: declsFrom (j + 1) is

def oldDecls (b : List Ty) : List Decl := declsFrom 0 b

def gen (c : Case) : Program :=
  let o := oldDecls c.base
  ⟨o, match c.op with
    | .ins p i => o.take p ++ ⟨.ins, i⟩ :: o.drop p
    | .rem p   => o.eraseIdx p
    | .set p i => o.set p ⟨.old p, i⟩⟩

theorem declsFrom_getElem? (j : Nat) (b : List Ty) (k : Nat) :
    (declsFrom j b)[k]? = b[k]?.map (fun i => ⟨.old (j + k), i⟩) := by
  induction b generalizing j k with
  | nil => simp [declsFrom]
  | cons x xs ih =>
    cases k with
    | zero => simp [declsFrom]
    | succ k =>
      simp only [declsFrom, List.getElem?_cons_succ, ih]
      rw [show j + 1 + k = j + (k + 1) by omega]

theorem oldDecls_getElem? (b : List Ty) (k : Nat) :
    (oldDecls b)[k]? = b[k]?.map (fun i => ⟨.old k, i⟩) := by
  simp [oldDecls, declsFrom_getElem?]

theorem declsFrom_length (j : Nat) (b : List Ty) : (declsFrom j b).length = b.length := by
  induction b generalizing j with
  | nil => rfl
  | cons x xs ih => simp [declsFrom, ih]

theorem oldDecls_length (b : List Ty) : (oldDecls b).length = b.length := declsFrom_length 0 b

theorem oldDecls_no_ins (b : List Ty) : ∀ d ∈ oldDecls b, d.name ≠ .ins := by
  intro d hd
  obtain ⟨k, hk, h⟩ := List.mem_iff_getElem.1 hd
  have e := oldDecls_getElem? b k
  rw [List.getElem?_eq_getElem hk, h] at e
  cases hb : b[k]? with
  | none => simp [hb] at e
  | some x => simp only [hb, Option.map_some', Option.some.injEq] at e; rw [e]; simp

/-- Read the old parameter list back: names must be `p0, p1, …` in order. -/
def decodeOld : Nat → List Decl → List Ty
  | _, [] => []
  | j, d :: ds => d.ty :: decodeOld (j + 1) ds

def wellNamed : Nat → List Decl → Bool
  | _, [] => true
  | j, d :: ds => d.name == .old j && wellNamed (j + 1) ds

theorem decodeOld_declsFrom (j : Nat) (b : List Ty) : decodeOld j (declsFrom j b) = b := by
  induction b generalizing j with
  | nil => rfl
  | cons x xs ih => simp [declsFrom, decodeOld, ih]

/-- Position of the inserted parameter. -/
def findIns : List Decl → Option Nat
  | [] => none
  | d :: ds => if d.name = .ins then some 0 else (findIns ds).map (· + 1)

theorem findIns_none (l : List Decl) (h : ∀ d ∈ l, d.name ≠ .ins) : findIns l = none := by
  induction l with
  | nil => rfl
  | cons d ds ih =>
    rw [findIns, if_neg (h d (by simp)), ih (fun x hx => h x (by simp [hx]))]; rfl

theorem findIns_ins (o r : List Decl) (t : Ty) (ho : ∀ d ∈ o, d.name ≠ .ins) :
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

/-- Where a type changed: the first position whose declaration differs. -/
def setAt (o n : List Decl) : Option Nat :=
  firstIdx (fun j => n[j]? != o[j]?) 0 o.length

/-- Decode a program pair to a case, or `none` if it is not a generated one. -/
def decodeOp (o n : List Decl) : Option Op :=
  match findIns n with
  | some k => (n[k]?).map fun d => .ins k d.ty
  | none =>
    if n.length + 1 = o.length then (remAt o n).map .rem
    else ((setAt o n).bind fun p => (n[p]?).map fun d => .set p d.ty)

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

theorem wellNamed_declsFrom (j : Nat) (b : List Ty) : wellNamed j (declsFrom j b) = true := by
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
    try dsimp only
    rw [hget]; rfl
  | rem p =>
    obtain ⟨h2, hp⟩ : 1 ≤ b.length ∧ p < b.length := ho
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
  try dsimp only
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
/-! ## 3. The two oracles -/

def newTys (c : Case) : List Ty :=
  match c.op with
  | .ins p i => c.base.take p ++ i :: c.base.drop p
  | .rem p   => c.base.eraseIdx p
  | .set p i => c.base.set p i

def added (c : Case) : Bool := match c.op with | .ins _ _ => true | _ => false

/-- Position of old parameter `j` in the new list (`none`: removed). -/
def newIdx (c : Case) (j : Nat) : Option Nat :=
  match c.op with
  | .ins p _ => some (if j < p then j else j + 1)
  | .rem p   => if j < p then some j else if j = p then none else some (j - 1)
  | .set _ _ => some j

/-! ### The report contract -/

def Ty.isPtr : Ty → Bool | .ptr => true | _ => false

/-- abicheck ChangeKinds this fragment can produce (catalog names). -/
inductive Kind
  | paramsChanged      -- func_params_changed,         BREAKING
  | ptrLevelChanged    -- param_pointer_level_changed, BREAKING
  | renamed            -- param_renamed,               RISK
  deriving DecidableEq, Repr

/-- A finding: its kind and the parameter position it is about (`none`: the
function as a whole). -/
structure Finding where
  kind : Kind
  pos  : Option Nat
  deriving DecidableEq, Repr

/-- Parameters are matched **by position**: in C a parameter's identity is its
position (callers never use names). At each position both lists have, a
change of pointer level is reported; otherwise the same type under a
different name is a rename. -/
def posFindings : List Decl → List Decl → Nat → List Finding
  | a :: as, b :: bs, k =>
    (if a.ty.isPtr != b.ty.isPtr then [⟨.ptrLevelChanged, some k⟩]
     else if a.ty = b.ty ∧ a.name ≠ b.name then [⟨.renamed, some k⟩] else []) ++
    posFindings as bs (k + 1)
  | _, _, _ => []

/-- **Expected findings** (the report contract): the parameter list changed,
plus the positional findings. -/
def findings (c : Case) : List Finding :=
  ⟨.paramsChanged, none⟩ :: posFindings (gen c).old (gen c).new 0

inductive Verdict | noChange | breaking
  deriving DecidableEq, Repr

/-- `func_params_changed` is breaking by the catalog default, and it fires
whenever the parameter type list changed. -/
def verdict (c : Case) : Verdict := if newTys c = c.base then .noChange else .breaking

/-! ### The calling-convention model (x86-64 System V, scalar arguments) -/

inductive Cls | integer | sse
  deriving DecidableEq, Repr

def Ty.cls : Ty → Cls | .dbl => .sse | _ => .integer

/-- How the value sits in its register: 32-bit integer, 64-bit integer
(`long` and `void *` alike), 64-bit float. -/
inductive Rep | i32 | i64 | f64
  deriving DecidableEq, Repr

def Ty.rep : Ty → Rep | .int => .i32 | .long => .i64 | .ptr => .i64 | .dbl => .f64

theorem Ty.cls_of_rep {a b : Ty} (h : a.rep = b.rep) : a.cls = b.cls := by
  cases a <;> cases b <;> simp_all [Ty.rep, Ty.cls]

def Ty.sameCls (c : Cls) (u : Ty) : Bool := u.cls == c

/-- SysV: the `k`-th argument goes in the next register of its class, i.e. the
register numbered by how many earlier arguments have the same class. (With at
most 4 arguments no class runs out of registers: 6 INTEGER, 8 SSE.) -/
def locAt (l : List Ty) (k : Nat) : Option (Cls × Nat) :=
  l[k]?.map fun u => (u.cls, (l.take k).countP (Ty.sameCls u.cls))

/-- **Call compatibility.** An old caller still calls the new function
correctly iff nothing was inserted (an inserted parameter is read from a
register the old caller never set) and every old parameter the new function
still has is read from the register the old caller wrote it to, in the same
representation. -/
def callCompatible (c : Case) : Bool :=
  !added c && (List.range c.base.length).all fun j =>
    match newIdx c j with
    | none => true
    | some k => locAt c.base j == locAt (newTys c) k &&
                (c.base[j]?.map Ty.rep) == ((newTys c)[k]?.map Ty.rep)

/-! ## 4. What the model says -/

theorem locAt_left (pre r : List Ty) (k : Nat) (hk : k < pre.length) :
    locAt (pre ++ r) k = locAt pre k := by
  simp [locAt, List.getElem?_append_left hk, List.take_append_eq_append_take,
    show k - pre.length = 0 by omega]

/-- The argument after `pre` and one more argument `y`. -/
theorem locAt_mid (pre suf : List Ty) (y : Ty) (i : Nat) :
    locAt (pre ++ y :: suf) (pre.length + 1 + i) =
      suf[i]?.map fun u => (u.cls, pre.countP (Ty.sameCls u.cls) +
        (if Ty.sameCls u.cls y then 1 else 0) + (suf.take i).countP (Ty.sameCls u.cls)) := by
  unfold locAt
  rw [List.getElem?_append_right (by omega), show pre.length + 1 + i - pre.length = i + 1 by omega,
    List.getElem?_cons_succ, List.take_append_eq_append_take,
    show pre.length + 1 + i - pre.length = i + 1 by omega, List.take_of_length_le (by omega)]
  cases suf[i]? <;> simp [List.countP_append, List.take_succ_cons, List.countP_cons]; omega

theorem locAt_skip (pre suf : List Ty) (i : Nat) :
    locAt (pre ++ suf) (pre.length + i) =
      suf[i]?.map fun u => (u.cls, pre.countP (Ty.sameCls u.cls) + (suf.take i).countP (Ty.sameCls u.cls)) := by
  unfold locAt
  rw [List.getElem?_append_right (by omega), show pre.length + i - pre.length = i by omega,
    List.take_append_eq_append_take, show pre.length + i - pre.length = i by omega,
    List.take_of_length_le (by omega)]
  cases suf[i]? <;> simp [List.countP_append]

theorem get_mid (pre suf : List Ty) (y : Ty) : (pre ++ y :: suf)[pre.length]? = some y := by
  rw [List.getElem?_append_right (Nat.le_refl _), Nat.sub_self, List.getElem?_cons_zero]

theorem locAt_here (pre suf : List Ty) (y : Ty) :
    locAt (pre ++ y :: suf) pre.length = some (y.cls, pre.countP (Ty.sameCls y.cls)) := by
  unfold locAt
  rw [get_mid, List.take_append_eq_append_take, Nat.sub_self, List.take_zero, List.append_nil,
    List.take_of_length_le (Nat.le_refl _)]
  rfl

theorem set_mid (pre suf : List Ty) (x t : Ty) :
    (pre ++ x :: suf).set pre.length t = pre ++ t :: suf := by
  rw [List.set_eq_take_append_cons_drop, if_pos (by simp)]; simp

theorem erase_mid (pre suf : List Ty) (x : Ty) :
    (pre ++ x :: suf).eraseIdx pre.length = pre ++ suf := by
  rw [List.eraseIdx_eq_take_drop_succ]; simp

theorem all_range_iff (n : Nat) (f : Nat → Bool) :
    (List.range n).all f = true ↔ ∀ j, j < n → f j = true := by
  simp [List.all_eq_true]

/-- **An insertion never keeps the call interface**: the new parameter is read
from a register the old caller never set. (This is by the definition of
`callCompatible`; the C run checks it on every insertion case.) -/
theorem ins_never_compatible (b : List Ty) (p : Nat) (t : Ty) :
    callCompatible ⟨b, .ins p t⟩ = false := by
  simp [callCompatible, added]

/-- **Changing one parameter's type keeps the call interface iff the
representation is the same** (`long` ↔ `void *`): a class change moves every
later argument of both classes, and a width change (`int` → `long`) leaves the
caller's upper bits undefined. -/
theorem set_compatible_iff (pre suf : List Ty) (x t : Ty) :
    callCompatible ⟨pre ++ x :: suf, .set pre.length t⟩ = true ↔ x.rep = t.rep := by
  have hn : newTys ⟨pre ++ x :: suf, .set pre.length t⟩ = pre ++ t :: suf := set_mid pre suf x t
  simp only [callCompatible, added, Bool.not_false, Bool.true_and, newIdx, hn, all_range_iff]
  constructor
  · intro h
    have := h pre.length (by simp)
    try dsimp only at this
    rw [locAt_here, locAt_here, get_mid, get_mid] at this
    simp at this
    exact this.2
  · intro hr j hj
    have hc := Ty.cls_of_rep hr
    simp only [List.length_append, List.length_cons] at hj
    rcases Nat.lt_trichotomy j pre.length with hlt | heq | hgt
    · try dsimp only
      rw [locAt_left _ _ _ hlt, locAt_left _ _ _ hlt, List.getElem?_append_left hlt,
        List.getElem?_append_left hlt]
      simp
    · subst heq; try dsimp only
      rw [locAt_here, locAt_here, get_mid, get_mid]; simp [hc, hr]
    · obtain ⟨i, rfl⟩ : ∃ i, j = pre.length + 1 + i := ⟨j - pre.length - 1, by omega⟩
      try dsimp only
      rw [locAt_mid, locAt_mid, List.getElem?_append_right (by omega), List.getElem?_append_right (by omega),
        show pre.length + 1 + i - pre.length = i + 1 by omega, List.getElem?_cons_succ, List.getElem?_cons_succ]
      cases suf[i]? <;> simp [Ty.sameCls, hc]

/-- **Removing a parameter keeps the call interface iff no later parameter has
its class**: removing an argument shifts every later argument of the same
class down one register; arguments of the other class stay put, and the old
caller's extra argument is simply ignored. In particular removing the last
parameter always keeps it (`trailing_removal_compatible`). -/
theorem rem_compatible_iff (pre suf : List Ty) (x : Ty) :
    callCompatible ⟨pre ++ x :: suf, .rem pre.length⟩ = true ↔ ∀ u ∈ suf, u.cls ≠ x.cls := by
  have hn : newTys ⟨pre ++ x :: suf, .rem pre.length⟩ = pre ++ suf := erase_mid pre suf x
  simp only [callCompatible, added, Bool.not_false, Bool.true_and, newIdx, hn, all_range_iff]
  constructor
  · intro h u hu
    obtain ⟨i, hi⟩ := List.mem_iff_getElem?.1 hu
    obtain ⟨hil, -⟩ := List.getElem?_eq_some_iff.1 hi
    have := h (pre.length + 1 + i) (by simp; omega)
    simp only [show ¬ (pre.length + 1 + i < pre.length) by omega,
      show ¬ (pre.length + 1 + i = pre.length) by omega, if_false] at this
    try dsimp only at this
    rw [show pre.length + 1 + i - 1 = pre.length + i by omega, locAt_mid, locAt_skip, hi] at this
    simp only [Option.map_some', beq_iff_eq, Option.some.injEq, Prod.mk.injEq, true_and,
      Bool.and_eq_true] at this
    intro hux
    have h1 := this.1
    simp [Ty.sameCls, hux] at h1
  · intro h j hj
    simp only [List.length_append, List.length_cons] at hj
    rcases Nat.lt_trichotomy j pre.length with hlt | heq | hgt
    · simp only [if_pos hlt]
      rw [locAt_left _ _ _ hlt, locAt_left _ _ _ hlt, List.getElem?_append_left hlt,
        List.getElem?_append_left hlt]
      simp
    · simp [heq]
    · obtain ⟨i, rfl⟩ : ∃ i, j = pre.length + 1 + i := ⟨j - pre.length - 1, by omega⟩
      simp only [show ¬ (pre.length + 1 + i < pre.length) by omega,
        show ¬ (pre.length + 1 + i = pre.length) by omega, if_false]
      rw [show pre.length + 1 + i - 1 = pre.length + i by omega, locAt_mid, locAt_skip, List.getElem?_append_right (by omega), List.getElem?_append_right (by omega),
        show pre.length + 1 + i - pre.length = i + 1 by omega, show pre.length + i - pre.length = i by omega,
        List.getElem?_cons_succ]
      cases hs : suf[i]? with
      | none => simp
      | some u =>
        have hu := h u (List.mem_of_getElem? hs)
        have : Ty.sameCls u.cls x = false := by simp [Ty.sameCls]; exact fun e => hu e.symm
        simp [this]

theorem trailing_removal_compatible (pre : List Ty) (x : Ty) :
    callCompatible ⟨pre ++ [x], .rem pre.length⟩ = true :=
  (rem_compatible_iff pre [] x).2 (by simp)

/-- Every case of the space changes the parameter type list, so the report
contract's verdict is always breaking. -/
theorem every_case_breaking (c : Case) (hc : InSpace c) : verdict c = .breaking := by
  rcases c with ⟨b, o⟩
  obtain ⟨-, -, ho⟩ := hc
  unfold verdict
  rw [if_neg]
  intro h
  cases o with
  | ins p t =>
    have := congrArg List.length h
    simp [newTys] at this; omega
  | rem p =>
    obtain ⟨-, hp⟩ : 1 ≤ b.length ∧ p < b.length := ho
    have := congrArg List.length h
    simp [newTys, List.length_eraseIdx, hp] at this; omega
  | set p t =>
    obtain ⟨hp, hne⟩ : p < b.length ∧ b[p]? ≠ some t := ho
    have := congrArg (·[p]?) h
    simp [newTys, List.getElem?_set, hp] at this
    exact hne (by rw [List.getElem?_eq_getElem hp, this])

/-- **The report contract and the call interface disagree.** Removing the
`int` before a `double` is breaking under the contract, yet an old binary
still calls the new function correctly: the `double` stays in `xmm0`, and the
old caller's `edi` is ignored. In C the edit still breaks every caller's
source, so the accurate verdict is a source break, not an ABI break
(formal/FUNC_FRAGMENT.md, "Findings"). -/
theorem breaking_but_call_compatible :
    verdict ⟨[.int, .dbl], .rem 0⟩ = .breaking ∧ callCompatible ⟨[.int, .dbl], .rem 0⟩ = true := by
  decide

/-! ### The class key and the equivalence theorem -/

structure Obs where
  findings : List Finding
  verdict  : Verdict
  call     : Bool
  deriving DecidableEq, Repr

def obs (c : Case) : Obs := ⟨findings c, verdict c, callCompatible c⟩

/-- The class key: the positional findings and the call compatibility. -/
structure Key where
  positional : List Finding
  call       : Bool
  deriving DecidableEq, Repr

def key (c : Case) : Key := ⟨posFindings (gen c).old (gen c).new 0, callCompatible c⟩

/-- **Equivalence condition.** On the space, two cases have the same observable
result (report findings, verdict, call compatibility) iff they have the same
key: the verdict is constant (`every_case_breaking`), so it carries no class
information. -/
theorem obs_eq_iff_key (c₁ c₂ : Case) (h₁ : InSpace c₁) (h₂ : InSpace c₂) :
    obs c₁ = obs c₂ ↔ key c₁ = key c₂ := by
  simp only [obs, key, findings, Obs.mk.injEq, Key.mk.injEq, List.cons.injEq, true_and,
    every_case_breaking c₁ h₁, every_case_breaking c₂ h₂, and_true]

end AbiLean.FuncFragment
