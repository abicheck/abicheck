/-
  The implicit-`this` extension of the function-signature (E13) and
  return-type (E14) fragments: C++ non-static member functions, x86-64 System
  V with the Itanium C++ ABI (claims in formal/MEMBER_FRAGMENT.md, E15).

  A non-static member `R K::f(P)` is called exactly like the free function
  `R f(K *this, P)`: `this` is a prepended INTEGER (pointer) argument. When
  `R` is returned in memory the hidden sret pointer comes first (`rdi`) and
  `this` second (`rsi`); that is `RetFragment.argLoc` applied to `.ptr :: P`
  (`this_loc`, `this_after_sret`).

  1. `locAt_this`, `argLoc_this`  – prepending `this` shifts every INTEGER
     register index by one and leaves SSE indices alone (`shiftLoc`, which is
     injective), so two slots share a location after the prepend iff they did
     before it.
  2. `member_call_compatible`     – FuncFragment's call model: an edit of the
     member's declared parameters (positions shifted past `this`) keeps the
     call interface iff the same edit of the free function does, for every
     parameter list and edit.
     `member_argLoc_eq_iff`, `member_args_intact_iff`,
     `member_ret_call_compatible` – RetFragment's model, sret included: each
     declared argument is placed alike iff it was in the free function; `this`
     itself moves (`rdi` ↔ `rsi`) exactly when the sret status changes.
  3. `static_to_instance_incompatible`, `instance_to_static_compatible_iff` –
     a static ↔ non-static change is E13's insertion / removal of a `void *`
     at position 0: never compatible one way, compatible the other way iff no
     declared parameter is INTEGER.
  4. The transition table is exported by MemberFragmentExport.lean and
     compared with the execution oracle's member_fragment_observed.json.
-/
import AbiLean.FuncFragment
import AbiLean.RetFragment

namespace AbiLean.MemberFragment
open AbiLean.FuncFragment (Ty Cls Ty.cls Ty.rep Ty.sameCls locAt Op Case callCompatible
  newTys newIdx added rem_compatible_iff ins_never_compatible words)
open AbiLean.RetFragment (argLoc argsIntact RTy params_irrelevant args_intact_iff)

/-! ## 1. The `this` shift -/

/-- The parameter list a member function is called with: `this` first. -/
def withThis (l : List Ty) : List Ty := .ptr :: l

/-- What prepending one INTEGER argument does to a location. -/
def shiftLoc (p : Cls × Nat) : Cls × Nat := (p.1, if p.1 = .integer then p.2 + 1 else p.2)

theorem shiftLoc_inj {p q : Cls × Nat} : shiftLoc p = shiftLoc q ↔ p = q := by
  rcases p with ⟨a, i⟩; rcases q with ⟨b, j⟩
  cases a <;> cases b <;> simp [shiftLoc]

theorem map_shiftLoc_inj {p q : Option (Cls × Nat)} : p.map shiftLoc = q.map shiftLoc ↔ p = q := by
  cases p <;> cases q <;> simp [shiftLoc_inj]

/-- **The shift lemma** for `locAt`: declared argument `k` of a member sits at
its free-function location, one INTEGER register up. -/
theorem locAt_this (l : List Ty) (k : Nat) :
    locAt (withThis l) (k + 1) = (locAt l k).map shiftLoc := by
  unfold locAt withThis
  rw [List.getElem?_cons_succ, List.take_succ_cons]
  cases l[k]? with
  | none => rfl
  | some u => cases u <;> simp [shiftLoc, Ty.sameCls, Ty.cls, List.countP_cons]

/-- `this` is the first INTEGER argument. -/
theorem locAt_this_zero (l : List Ty) : locAt (withThis l) 0 = some (.integer, 0) := by
  simp [locAt, withThis, Ty.cls]

/-- The shift lemma for `argLoc`, with or without a hidden sret pointer. -/
theorem argLoc_this (s : Bool) (l : List Ty) (k : Nat) :
    argLoc s (withThis l) (k + 1) = (argLoc s l k).map shiftLoc := by
  unfold argLoc
  rw [locAt_this]
  cases locAt l k with
  | none => rfl
  | some p => rcases p with ⟨c, i⟩; cases c <;> cases s <;> simp [shiftLoc]

/-- **Where `this` goes** (the modelling choice of E15, checked in g++ and
clang++ assembly): `rdi` without sret, `rsi` (INTEGER register 1) after the
sret pointer in `rdi`. -/
theorem this_loc (s : Bool) (l : List Ty) :
    argLoc s (withThis l) 0 = some (.integer, if s then 1 else 0) := by
  cases s <;> simp [argLoc, locAt, withThis, Ty.cls]

theorem this_after_sret (l : List Ty) : argLoc true (withThis l) 0 = some (.integer, 1) := by
  simp [this_loc]

/-! ## 2. Member to member: the free function's answer -/

/-- An edit of the declared parameters, at the member's argument positions
(past `this`). -/
def shiftOp : Op → Op
  | .ins p i => .ins (p + 1) i
  | .rem p => .rem (p + 1)
  | .set p i => .set (p + 1) i

/-- The member version of an E13 case: `this` prepended to the old list, the
edit moved past it. -/
def member (c : Case) : Case := ⟨withThis c.base, shiftOp c.op⟩

/-- One old slot of `callCompatible`. -/
def slotOk (c : Case) (j : Nat) : Bool :=
  match newIdx c j with
  | none => true
  | some k => locAt c.base j == locAt (newTys c) k &&
              (c.base[j]?.map Ty.rep) == ((newTys c)[k]?.map Ty.rep)

theorem callCompatible_eq (c : Case) :
    callCompatible c = (!added c && (List.range c.base.length).all (slotOk c)) := rfl

theorem added_member (c : Case) : added (member c) = added c := by
  rcases c with ⟨b, o⟩; cases o <;> rfl

theorem newTys_member (c : Case) : newTys (member c) = withThis (newTys c) := by
  rcases c with ⟨b, o⟩
  cases o <;> simp [member, shiftOp, newTys, withThis, List.take_succ_cons,
    List.drop_succ_cons, List.eraseIdx_cons_succ, List.set_cons_succ]

theorem newIdx_member_zero (c : Case) : newIdx (member c) 0 = some 0 := by
  rcases c with ⟨b, o⟩; cases o <;> simp [member, shiftOp, newIdx]

theorem newIdx_member_succ (c : Case) (j : Nat) :
    newIdx (member c) (j + 1) = (newIdx c j).map (· + 1) := by
  rcases c with ⟨b, o⟩
  cases o with
  | ins p i => simp only [member, shiftOp, newIdx]; split <;> split <;> simp_all <;> omega
  | rem p =>
    simp only [member, shiftOp, newIdx]
    by_cases h1 : j < p
    · simp [h1]
    · by_cases h2 : j = p
      · simp [h2]
      · simp [h1, h2]; omega
  | set p i => rfl

theorem slotOk_member_zero (c : Case) : slotOk (member c) 0 = true := by
  unfold slotOk
  rw [newIdx_member_zero, newTys_member]
  simp [member, withThis, locAt, Ty.cls]

theorem slotOk_member_succ (c : Case) (j : Nat) : slotOk (member c) (j + 1) = slotOk c j := by
  unfold slotOk
  rw [newIdx_member_succ]
  cases newIdx c j with
  | none => rfl
  | some k =>
    simp only [Option.map_some']
    rw [newTys_member]
    simp only [member, locAt_this]
    have hb : (withThis c.base)[j + 1]? = c.base[j]? := by simp [withThis]
    have hn : (withThis (newTys c))[k + 1]? = (newTys c)[k]? := by simp [withThis]
    rw [hb, hn]
    cases locAt c.base j <;> cases locAt (newTys c) k <;> simp [shiftLoc_inj]
    rename_i p q
    have : (shiftLoc p == shiftLoc q) = (p == q) := by
      rw [Bool.eq_iff_iff, beq_iff_eq, beq_iff_eq, shiftLoc_inj]
    rw [this]

/-- **The shift lemma for the E13 call model.** For every parameter list and
every edit, the member version keeps the call interface iff the free version
does: `this` is at `rdi` on both sides, and every declared argument moves by
the same `shiftLoc` on both sides. -/
theorem member_call_compatible (c : Case) : callCompatible (member c) = callCompatible c := by
  rw [callCompatible_eq, callCompatible_eq, added_member]
  congr 1
  show (List.range (c.base.length + 1)).all (slotOk (member c)) = _
  rw [List.range_succ_eq_map, List.all_cons, slotOk_member_zero, Bool.true_and, List.all_map]
  congr 1
  funext j
  exact slotOk_member_succ c j

/-- **The shift lemma for the E14 placement**, sret included: a declared
argument is placed alike on both sides of a member iff it is placed alike on
both sides of the free function, whatever the two sret statuses. -/
theorem member_argLoc_eq_iff (a b : Bool) (l l' : List Ty) (j k : Nat) :
    argLoc a (withThis l) (j + 1) = argLoc b (withThis l') (k + 1) ↔ argLoc a l j = argLoc b l' k := by
  rw [argLoc_this, argLoc_this, map_shiftLoc_inj]

/-- `this` keeps its register iff the sret status is unchanged. -/
theorem this_intact_iff (a b : Bool) (l l' : List Ty) :
    argLoc a (withThis l) 0 = argLoc b (withThis l') 0 ↔ a = b := by
  rw [this_loc, this_loc]; cases a <;> cases b <;> simp

/-- A member's arguments (`this` included) survive a return-type change iff
the sret status is unchanged: unlike a free function with only `double`
parameters, a member always has the INTEGER `this`. -/
theorem member_args_intact_iff (ps : List Ty) (o n : RTy) :
    argsIntact ⟨withThis ps, o, n⟩ = true ↔ o.sret = n.sret := by
  rw [args_intact_iff]
  constructor
  · rintro (h | h)
    · exact h
    · exact absurd (h .ptr (List.mem_cons_self _ _)) (by decide)
  · exact Or.inl

/-- **The shift lemma for the E14 call model**: a member's return-type change
keeps the call interface iff the free function's does. -/
theorem member_ret_call_compatible (ps : List Ty) (o n : RTy) :
    RetFragment.callCompatible ⟨withThis ps, o, n⟩ = RetFragment.callCompatible ⟨ps, o, n⟩ := by
  rw [params_irrelevant, params_irrelevant ps]

/-! ## 3. Static ↔ non-static -/

inductive MKind | static | inst
  deriving DecidableEq, Repr

/-- A static ↔ non-static transition with declared parameters `l`, as an E13
case: static → instance inserts `this` at position 0, instance → static
removes it. -/
def transition (o n : MKind) (l : List Ty) : Case :=
  match o, n with
  | .static, .inst => ⟨l, .ins 0 .ptr⟩
  | .inst, .static => ⟨withThis l, .rem 0⟩
  | .static, .static => ⟨l, .set 0 .long⟩   -- not a transition; unused
  | .inst, .inst => ⟨withThis l, .set 0 .long⟩

/-- **Static → instance never keeps the call interface**: the new function
reads `this` from `rdi`, which the old caller set to its first INTEGER
argument or never set. -/
theorem static_to_instance_incompatible (l : List Ty) :
    callCompatible (transition .static .inst l) = false :=
  ins_never_compatible l 0 .ptr

/-- **Instance → static keeps the call interface iff no declared parameter is
INTEGER**: removing `this` moves every INTEGER argument down one register;
SSE arguments stay, and the stray `this` in `rdi` is ignored. -/
theorem instance_to_static_compatible_iff (l : List Ty) :
    callCompatible (transition .inst .static l) = true ↔ ∀ u ∈ l, u.cls = .sse := by
  have := rem_compatible_iff [] l .ptr
  simp only [List.nil_append, List.length_nil] at this
  show callCompatible ⟨.ptr :: l, .rem 0⟩ = true ↔ _
  rw [this]
  constructor
  · intro h u hu; have := h u hu; cases hc : u.cls <;> simp_all [Ty.cls]
  · intro h u hu; rw [h u hu]; decide

/-- The transitions the execution oracle ran: both directions, every list of
0–3 parameters of `int`, `long`, `double`, `void *`. -/
def transitions : List (MKind × MKind × List Ty) :=
  [(MKind.static, MKind.inst), (MKind.inst, MKind.static)].flatMap fun (o, n) =>
    (List.range 4).flatMap fun k => (words k).map fun l => (o, n, l)

set_option maxRecDepth 100000 in
theorem transitions_length : transitions.length = 170 := by decide

set_option maxRecDepth 100000 in
/-- Exactly four transitions keep the call interface: instance → static with
no parameter or only `double`s. -/
theorem compatible_transitions :
    transitions.filter (fun t => callCompatible (transition t.1 t.2.1 t.2.2)) =
      [(.inst, .static, []), (.inst, .static, [.dbl]), (.inst, .static, [.dbl, .dbl]),
       (.inst, .static, [.dbl, .dbl, .dbl])] := by
  decide

end AbiLean.MemberFragment
