/-
  A closed return-type fragment: "change the return type of a C function with
  0–2 parameters of type `int`, `long`, `double` or `void *`, among `void`,
  `int`, `long`, `double`, `void *` and three structs", C11, x86-64 System V.

  The same five parts as the earlier fragments (claims in
  formal/RET_FRAGMENT.md), with two oracles, as in the function-signature
  fragment (FuncFragment.lean, whose parameter types and register model this
  module reuses):

  1. `Case`, `InSpace`, `space`   – the case space; `mem_space`,
     `space_nodup`, `space_length`.
  2. `gen`, `decode`              – the abstract generator and its inverse;
     `decode_gen`, `gen_decode`, `generated_in_exactly_one`.
  3. `findings`, `verdict`        – the report contract.
     `callCompatible`             – the calling-convention model. SysV returns
     a value in a register of its class (`eax`, `rax`, `xmm0`, `rax:rdx`,
     `xmm0:xmm1`) or, for a struct over 16 bytes, in caller memory whose
     address the caller passes as a hidden first INTEGER argument in `rdi`
     ("sret"). An old caller still calls the new function correctly iff every
     argument is where the new function reads it (`argsIntact`) and the
     result is where, and in the form, the old caller reads it
     (`resultIntact`).
  4. `args_intact_iff`, `call_compatible_iff`, `params_irrelevant`,
     `void_result_compatible_iff`, `every_case_breaking`, `obs_eq_iff_key`;
     `breaking_but_call_compatible` is a case the report contract calls
     breaking although the binary call interface is unchanged.
  5. The reduced set is exported by RetFragmentExport.lean.
-/
import AbiLean.FuncFragment

namespace AbiLean.RetFragment
open AbiLean.Fragment (nodup_flatMap nodup_map_of_inj length_flatMap_const)
open AbiLean.FuncFragment (Ty Ty.all Ty.mem_all Ty.all_nodup Cls Ty.cls words mem_words
  words_nodup words_length locAt all_range_iff Decl declsFrom oldDecls decodeOld wellNamed
  decodeOld_declsFrom wellNamed_declsFrom)

/-! ## 1. The case space -/

/-- A return type: `void`, the four scalar parameter types, and three structs:
`struct s2l {long a, b;}` (16 bytes, INTEGER:INTEGER), `struct s2d {double a,
b;}` (16 bytes, SSE:SSE) and `struct s3l {long a, b, c;}` (24 bytes, MEMORY). -/
inductive RTy | void | int | long | dbl | ptr | s2l | s2d | s3l
  deriving DecidableEq, Repr

def RTy.all : List RTy := [.void, .int, .long, .dbl, .ptr, .s2l, .s2d, .s3l]
theorem RTy.mem_all (t : RTy) : t ∈ RTy.all := by cases t <;> decide
theorem RTy.all_nodup : RTy.all.Nodup := by decide

/-- Upper bound on the number of parameters. -/
def maxParams : Nat := 2

/-- One case: the parameter types (parameter `j` is named `p<j>`, unchanged by
the edit) and the old and new return types. -/
structure Case where
  params : List Ty
  old    : RTy
  new    : RTy
  deriving DecidableEq, Repr

/-- **The space**, as a predicate. Nothing here mentions the generator. -/
def InSpace (c : Case) : Prop := c.params.length ≤ maxParams ∧ c.old ≠ c.new

instance (c : Case) : Decidable (InSpace c) := by unfold InSpace; infer_instance

/-- Every return-type change: old type, then new type. -/
def pairs : List (RTy × RTy) :=
  RTy.all.flatMap fun o => (RTy.all.filter (· != o)).map (o, ·)

/-- **The explicit enumeration** of the space. -/
def space : List Case :=
  (List.range (maxParams + 1)).flatMap fun n =>
    (words n).flatMap fun ps => pairs.map fun on => ⟨ps, on.1, on.2⟩

theorem mem_pairs {o n : RTy} : (o, n) ∈ pairs ↔ o ≠ n := by
  simp only [pairs, List.mem_flatMap, List.mem_map, List.mem_filter, Prod.mk.injEq]
  constructor
  · rintro ⟨o', -, n', ⟨-, h⟩, rfl, rfl⟩; intro e; subst e; simp at h
  · intro h
    refine ⟨o, RTy.mem_all o, n, ⟨RTy.mem_all n, ?_⟩, rfl, rfl⟩
    simp only [bne_iff_ne, ne_eq]; exact fun e => h e.symm

theorem mem_space (c : Case) : c ∈ space ↔ InSpace c := by
  rcases c with ⟨ps, o, n⟩
  simp only [space, maxParams, InSpace, List.mem_flatMap, List.mem_range, List.mem_map, mem_words]
  constructor
  · rintro ⟨k, hk, ps', hps, ⟨o', n'⟩, hp, h⟩
    simp only [Case.mk.injEq] at h; obtain ⟨rfl, rfl, rfl⟩ := h
    exact ⟨by omega, mem_pairs.1 hp⟩
  · rintro ⟨hl, hne⟩
    exact ⟨ps.length, by omega, ps, rfl, (o, n), mem_pairs.2 hne, rfl⟩

theorem pairs_nodup : pairs.Nodup := by decide

theorem space_nodup : space.Nodup := by
  apply nodup_flatMap (List.nodup_range _)
  · intro k _
    apply nodup_flatMap (words_nodup k)
    · intro ps _
      exact nodup_map_of_inj (fun a b h => by
        simp only [Case.mk.injEq] at h; exact Prod.ext h.2.1 h.2.2) pairs_nodup
    · intro p₁ p₂ x h1 h2
      simp only [List.mem_map] at h1 h2
      obtain ⟨_, _, rfl⟩ := h1; obtain ⟨_, _, h⟩ := h2
      simp only [Case.mk.injEq] at h; exact h.1.symm
  · intro k₁ k₂ x h1 h2
    simp only [List.mem_flatMap, List.mem_map, mem_words] at h1 h2
    obtain ⟨_, rfl, _, _, rfl⟩ := h1; obtain ⟨_, h, _, _, he⟩ := h2
    simp only [Case.mk.injEq] at he; rw [← h, he.1]

theorem pairs_length : pairs.length = 56 := by decide

/-- The size of the space: (1 + 4 + 16) parameter lists × 8·7 changes = 1176. -/
theorem space_length : space.length = 1176 := by
  simp only [space, maxParams]
  rw [show List.range (2 + 1) = [0, 1, 2] from rfl]
  simp only [List.flatMap_cons, List.flatMap_nil, List.length_append, List.length_nil]
  rw [length_flatMap_const (m := 56), length_flatMap_const (m := 56),
    length_flatMap_const (m := 56)]
  · simp [words_length]
  all_goals intro _ _; rw [List.length_map, pairs_length]

/-! ## 2. The abstract generator and its inverse -/

/-- One prototype: return type and parameter declarations. -/
structure Proto where
  ret    : RTy
  params : List Decl
  deriving DecidableEq, Repr

/-- A generated program pair: the old and the new prototype. The C text
renders this as `<ret> fN(<type> p0, …)`. -/
structure Program where
  old : Proto
  new : Proto
  deriving DecidableEq, Repr

def gen (c : Case) : Program := ⟨⟨c.old, oldDecls c.params⟩, ⟨c.new, oldDecls c.params⟩⟩

/-- Decode a program pair to a case, or `none` if it is not a generated one. -/
def decode (p : Program) : Option Case :=
  let c : Case := ⟨decodeOld 0 p.old.params, p.old.ret, p.new.ret⟩
  if wellNamed 0 p.old.params ∧ InSpace c ∧ gen c = p then some c else none

theorem gen_decode {p : Program} {c : Case} (h : decode p = some c) : InSpace c ∧ gen c = p := by
  simp only [decode] at h
  split at h
  · rename_i hc; cases h; exact ⟨hc.2.1, hc.2.2⟩
  · contradiction

theorem decode_gen (c : Case) (hc : InSpace c) : decode (gen c) = some c := by
  rcases c with ⟨ps, o, n⟩
  simp [decode, gen, oldDecls, decodeOld_declsFrom, wellNamed_declsFrom, hc]

theorem gen_injective {c₁ c₂ : Case} (h₁ : InSpace c₁) (h₂ : InSpace c₂)
    (h : gen c₁ = gen c₂) : c₁ = c₂ := by
  have := decode_gen c₁ h₁; rw [h, decode_gen c₂ h₂] at this; exact (Option.some.inj this).symm

/-- Every generated program pair belongs to exactly one case of the space. -/
theorem generated_in_exactly_one (p : Program) (hp : p ∈ space.map gen) :
    ∃ c, c ∈ space ∧ gen c = p ∧ ∀ c', c' ∈ space → gen c' = p → c' = c := by
  obtain ⟨c, hc, rfl⟩ := List.mem_map.1 hp
  exact ⟨c, hc, rfl, fun c' hc' h =>
    gen_injective ((mem_space c').1 hc') ((mem_space c).1 hc) h⟩

theorem every_case_generated (c : Case) (hc : InSpace c) :
    decode (gen c) = some c ∧ gen c ∈ space.map gen :=
  ⟨decode_gen c hc, List.mem_map.2 ⟨c, (mem_space c).2 hc, rfl⟩⟩

/-! ## 3. The two oracles -/

/-! ### The report contract -/

def RTy.isPtr : RTy → Bool | .ptr => true | _ => false
def RTy.isStruct : RTy → Bool | .s2l => true | .s2d => true | .s3l => true | _ => false

/-- Returned through caller memory (SysV class MEMORY: over 16 bytes). -/
def RTy.sret : RTy → Bool | .s3l => true | _ => false

inductive Kind
  | returnChanged          -- `func_return_changed` (breaking)
  | retPtrLevelChanged     -- `return_pointer_level_changed` (breaking)
  | structRetConvChanged   -- `struct_return_convention_changed` (breaking)
  deriving DecidableEq, Repr

/-- Whether pointer-ness of the return type changed. -/
def ptrFlip (c : Case) : Bool := c.old.isPtr != c.new.isPtr

/-- Whether a struct return changed between registers and memory. abicheck
reports the convention change only when both sides return a struct; a scalar
↔ sret change is covered by `func_return_changed` alone. -/
def convFlip (c : Case) : Bool := c.old.isStruct && c.new.isStruct && c.old.sret != c.new.sret

def findingsOf (p s : Bool) : List Kind :=
  .returnChanged :: ((if p then [.retPtrLevelChanged] else []) ++
    (if s then [.structRetConvChanged] else []))

/-- **The report contract**: `func_return_changed` always, plus the pointer-level
and struct-return-convention findings when they apply. -/
def findings (c : Case) : List Kind := findingsOf (ptrFlip c) (convFlip c)

inductive Verdict | noChange | breaking
  deriving DecidableEq, Repr

/-- Every kind of the contract is breaking by catalog default. -/
def verdict (c : Case) : Verdict := if findings c = [] then .noChange else .breaking

theorem every_case_breaking (c : Case) : verdict c = .breaking := by
  simp [verdict, findings, findingsOf]

/-! ### The calling-convention model (x86-64 System V) -/

/-- Where and in what form the result comes back. Each location holds exactly
one representation in this space: `eax` a 32-bit integer, `rax` a 64-bit
integer (`long` and `void *` alike), `xmm0` a double, `rax:rdx` two longs,
`xmm0:xmm1` two doubles, `mem` three longs in caller memory. -/
inductive RLoc | none | eax | rax | xmm0 | raxRdx | xmm01 | mem
  deriving DecidableEq, Repr

def RTy.ret : RTy → RLoc
  | .void => .none | .int => .eax | .long => .rax | .ptr => .rax | .dbl => .xmm0
  | .s2l => .raxRdx | .s2d => .xmm01 | .s3l => .mem

theorem RTy.sret_of_ret {a b : RTy} (h : a.ret = b.ret) : a.sret = b.sret := by
  cases a <;> cases b <;> simp_all [RTy.ret, RTy.sret]

/-- Argument `k`'s register: SysV's register assignment (`locAt`), with every
INTEGER argument moved up one register when a hidden sret pointer takes
`rdi`. -/
def argLoc (sret : Bool) (l : List Ty) (k : Nat) : Option (Cls × Nat) :=
  (locAt l k).map fun (c, i) => (c, if sret && c == .integer then i + 1 else i)

/-- Every argument the old caller passed is where the new function reads it. -/
def argsIntact (c : Case) : Bool :=
  (List.range c.params.length).all fun k => argLoc c.old.sret c.params k == argLoc c.new.sret c.params k

/-- The old caller reads the result where, and in the form, the new function
leaves it. A `void` caller reads nothing, but a new sret function still writes
through `rdi`, which that caller never set to a buffer. -/
def resultIntact (c : Case) : Bool :=
  if c.old = .void then !c.new.sret else c.old.ret == c.new.ret

/-- **Call compatibility**: arguments and result both intact. -/
def callCompatible (c : Case) : Bool := argsIntact c && resultIntact c

/-! ## 4. What the model says -/

theorem argLoc_eq (a b : Bool) (l : List Ty) (k : Nat) :
    argLoc a l k = argLoc b l k ↔ (a = b ∨ ∀ u, l[k]? = some u → u.cls = .sse) := by
  unfold argLoc locAt
  cases h : l[k]? with
  | none => simp
  | some u =>
    cases hu : u.cls <;> cases a <;> cases b <;> simp [hu]

/-- **Arguments survive a return-type change iff the sret status is unchanged
or no parameter is INTEGER**: a hidden pointer appearing or disappearing in
`rdi` moves every INTEGER argument by one register, and leaves SSE arguments
where they were. Stated for parameter lists of any length. -/
theorem args_intact_iff (c : Case) :
    argsIntact c = true ↔ (c.old.sret = c.new.sret ∨ ∀ u ∈ c.params, u.cls = .sse) := by
  unfold argsIntact
  rw [all_range_iff]
  simp only [beq_iff_eq, argLoc_eq]
  constructor
  · intro h
    by_cases hs : c.old.sret = c.new.sret
    · exact Or.inl hs
    · right; intro u hu
      obtain ⟨k, hk, he⟩ := List.mem_iff_getElem.1 hu
      rcases h k hk with h' | h'
      · exact absurd h' hs
      · exact h' u (by rw [List.getElem?_eq_getElem hk, he])
  · rintro (hs | hs) k _
    · exact Or.inl hs
    · right; intro u hu; exact hs u (List.mem_iff_getElem?.2 ⟨k, hu⟩)

/-- **When a return-type change keeps the call interface**, for any
parameters: the old return type was `void` and the new one is not returned
through memory, or both are returned in the same place and form (`long` ↔
`void *`). -/
theorem call_compatible_iff (c : Case) :
    callCompatible c = true ↔
      (c.old = .void ∧ c.new ≠ .s3l) ∨ (c.old ≠ .void ∧ c.old.ret = c.new.ret) := by
  unfold callCompatible resultIntact
  rw [Bool.and_eq_true, args_intact_iff]
  rcases c with ⟨ps, o, n⟩
  dsimp only
  cases o <;> cases n <;> simp [RTy.sret, RTy.ret]

/-- The parameters never decide call compatibility: they matter only when the
sret status changes, and then the result is broken anyway. -/
theorem params_irrelevant (ps : List Ty) (o n : RTy) :
    callCompatible ⟨ps, o, n⟩ = callCompatible ⟨[], o, n⟩ := by
  apply Bool.eq_iff_iff.2
  rw [call_compatible_iff, call_compatible_iff]

/-- **A `void` function may start returning a value**, unless that value comes
back through memory. -/
theorem void_result_compatible_iff (ps : List Ty) (n : RTy) :
    callCompatible ⟨ps, .void, n⟩ = true ↔ n ≠ .s3l := by
  rw [call_compatible_iff]; simp

/-- `void f(int)` → `int f(int)`: breaking under the contract, and an old binary
still calls it correctly (it never reads `eax`). -/
theorem breaking_but_call_compatible :
    verdict ⟨[.int], .void, .int⟩ = .breaking ∧ callCompatible ⟨[.int], .void, .int⟩ = true := by
  decide

/-- A scalar → sret change moves the INTEGER arguments, and abicheck reports it
only as `func_return_changed`: the struct-return-convention finding needs a
struct on both sides. -/
theorem scalar_to_sret_moves_args :
    argsIntact ⟨[.long], .long, .s3l⟩ = false ∧ findings ⟨[.long], .long, .s3l⟩ = [.returnChanged] := by
  decide

/-! ### The class key and the equivalence theorem -/

structure Obs where
  findings : List Kind
  verdict  : Verdict
  call     : Bool
  args     : Bool
  deriving DecidableEq, Repr

def obs (c : Case) : Obs := ⟨findings c, verdict c, callCompatible c, argsIntact c⟩

/-- The class key: which extra findings apply, call compatibility, and whether
the arguments survive. -/
structure Key where
  ptrFlip  : Bool
  convFlip : Bool
  call     : Bool
  args     : Bool
  deriving DecidableEq, Repr

def key (c : Case) : Key := ⟨ptrFlip c, convFlip c, callCompatible c, argsIntact c⟩

theorem findingsOf_inj {a b a' b' : Bool} : findingsOf a b = findingsOf a' b' ↔ a = a' ∧ b = b' := by
  cases a <;> cases b <;> cases a' <;> cases b' <;> decide

/-- **Equivalence condition.** Two cases have the same observable result
(findings, verdict, call compatibility, argument placement) iff they have the
same key. -/
theorem obs_eq_iff_key (c₁ c₂ : Case) : obs c₁ = obs c₂ ↔ key c₁ = key c₂ := by
  simp only [obs, key, findings, Obs.mk.injEq, Key.mk.injEq, every_case_breaking, true_and,
    findingsOf_inj, and_assoc]

end AbiLean.RetFragment
