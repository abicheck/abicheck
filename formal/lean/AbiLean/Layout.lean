/-
  Experiments E3/E4: a C struct layout model with alignment (SysV-style
  sequential layout, no bit-fields, no packing, no [[no_unique_address]]),
  the generator's layout edits as functions on it, and class-reduction
  theorems: when does an edit *class* determine the outcome, and when does
  only the concrete layout?

  offset_i = alignUp(end_{i-1}, align_i), sizeof = alignUp(end, maxAlign).
  Validated against gcc offsetof/sizeof by formal/experiments/layout_experiments.py (E6a).
-/
namespace AbiLean.Layout

structure FType where
  name  : String
  size  : Nat
  align : Nat
  deriving DecidableEq, Repr

def alignUp (n a : Nat) : Nat := (n + a - 1) / a * a

/-- Offsets of a field list placed from `start`. -/
def offsetsFrom : Nat → List FType → List Nat
  | _, [] => []
  | e, t :: ts => let o := alignUp e t.align; o :: offsetsFrom (o + t.size) ts

/-- End of the data (before tail padding). -/
def endFrom : Nat → List FType → Nat
  | e, [] => e
  | e, t :: ts => endFrom (alignUp e t.align + t.size) ts

def maxAlign (ts : List FType) : Nat := ts.foldl (fun m t => max m t.align) 1

def offsets (ts : List FType) : List Nat := offsetsFrom 0 ts
def sizeOf (ts : List FType) : Nat := alignUp (endFrom 0 ts) (maxAlign ts)

/-! ### Generator edits -/
def insertAt (k : Nat) (t : FType) (ts : List FType) : List FType :=
  ts.take k ++ t :: ts.drop k
def append (t : FType) (ts : List FType) : List FType := ts ++ [t]

/-! ### alignUp lemmas -/
theorem le_alignUp (n a : Nat) (ha : 0 < a) : n ≤ alignUp n a := by
  unfold alignUp
  have h1 := Nat.div_add_mod (n + a - 1) a
  have h2 := Nat.mod_lt (n + a - 1) ha
  rw [Nat.mul_comm] at h1
  omega

theorem alignUp_lt (n a : Nat) (ha : 0 < a) : alignUp n a < n + a := by
  unfold alignUp
  have h1 := Nat.div_add_mod (n + a - 1) a
  rw [Nat.mul_comm] at h1
  omega

theorem alignUp_mono {n m : Nat} (a : Nat) (h : n ≤ m) : alignUp n a ≤ alignUp m a := by
  unfold alignUp
  rcases Nat.eq_zero_or_pos a with rfl | ha
  · simp
  have h1 := Nat.div_add_mod (n + a - 1) a
  have h2 := Nat.div_add_mod (m + a - 1) a
  have h3 := Nat.mod_lt (m + a - 1) ha
  rw [Nat.mul_comm ((n + a - 1) / a), Nat.mul_comm ((m + a - 1) / a)]
  apply Nat.mul_le_mul_left
  apply Nat.le_of_not_lt
  intro hlt
  have h4 : a * ((m + a - 1) / a + 1) ≤ a * ((n + a - 1) / a) := Nat.mul_le_mul_left a hlt
  rw [Nat.mul_succ] at h4
  omega

/-! ### E4 theorems -/

/-- Every field before the insertion point keeps its offset (for all layouts). -/
theorem insert_prefix_unchanged (k : Nat) (t : FType) (ts : List FType) (e : Nat)
    (hk : k ≤ ts.length) :
    (offsetsFrom e (insertAt k t ts)).take k = (offsetsFrom e ts).take k := by
  induction ts generalizing k e with
  | nil => simp at hk; subst hk; simp
  | cons x xs ih =>
    cases k with
    | zero => simp
    | succ k =>
      simp only [insertAt, List.take_succ_cons, List.drop_succ_cons, List.cons_append,
        offsetsFrom, List.length_cons] at *
      exact congrArg _ (ih k _ (by omega))

/-- Offsets only grow when the start grows (monotone placement). -/
theorem offsetsFrom_mono {e e' : Nat} (h : e ≤ e') (ts : List FType) (i : Nat)
    (o o' : Nat) (ho : (offsetsFrom e ts)[i]? = some o)
    (ho' : (offsetsFrom e' ts)[i]? = some o') : o ≤ o' := by
  induction ts generalizing e e' i with
  | nil => simp [offsetsFrom] at ho
  | cons x xs ih =>
    have hal := alignUp_mono x.align h
    cases i with
    | zero =>
      simp [offsetsFrom] at ho ho'
      omega
    | succ i =>
      simp [offsetsFrom] at ho ho'
      exact ih (by omega) i ho ho'

/-- **Sufficient condition for breaking.** Inserting `t` immediately before a
field `f` whose alignment is at most `t.size` strictly moves `f` — so the
offset rule fires and every old reader of `f` is broken (FieldOffset.fires_sound). -/
theorem insert_moves_next (e : Nat) (t f : FType) (rest : List FType) (hf : 0 < f.align) (ht : 0 < t.align)
    (hs : f.align ≤ t.size) :
    (offsetsFrom e (f :: rest)).head? = some (alignUp e f.align) ∧
    (offsetsFrom e (t :: f :: rest))[1]? =
      some (alignUp (alignUp e t.align + t.size) f.align) ∧
    alignUp e f.align < alignUp (alignUp e t.align + t.size) f.align := by
  refine ⟨by simp [offsetsFrom], by simp [offsetsFrom], ?_⟩
  have h1 := alignUp_lt e f.align hf
  have h2 := le_alignUp e t.align ht
  have h3 := le_alignUp (alignUp e t.align + t.size) f.align hf
  omega

theorem foldl_max_ge (ts : List FType) (m : Nat) :
    m ≤ ts.foldl (fun m t => max m t.align) m := by
  induction ts generalizing m with
  | nil => simp
  | cons x xs ih => simp only [List.foldl_cons]; exact Nat.le_trans (Nat.le_max_left _ _) (ih _)

theorem endFrom_append (e : Nat) (ts : List FType) (t : FType) :
    endFrom e (append t ts) = alignUp (endFrom e ts) t.align + t.size := by
  induction ts generalizing e with
  | nil => rfl
  | cons x xs ih => simp only [append, List.cons_append, endFrom] at *; exact ih _

/-- **Sufficient condition for size growth on append.** Appending a field at
least as large as the old struct's alignment strictly grows `sizeof`. -/
theorem append_grows_size (ts : List FType) (t : FType)
    (ht : 0 < t.align) (hs : maxAlign ts ≤ t.size) :
    sizeOf ts < sizeOf (append t ts) := by
  have hm : 1 ≤ maxAlign ts := foldl_max_ge _ _
  have hlt := alignUp_lt (endFrom 0 ts) (maxAlign ts) (by omega)
  have hend : endFrom 0 (append t ts) = alignUp (endFrom 0 ts) t.align + t.size :=
    endFrom_append 0 ts t
  have hge := le_alignUp (endFrom 0 (append t ts)) (maxAlign (append t ts))
    (by have := foldl_max_ge (append t ts) 1; unfold maxAlign; omega)
  have hal := le_alignUp (endFrom 0 ts) t.align ht
  unfold sizeOf
  omega

end AbiLean.Layout

namespace AbiLean.Layout
/-! ### Concrete C types (x86-64 SysV) and class counterexamples -/
def tChar   : FType := ⟨"char", 1, 1⟩
def tShort  : FType := ⟨"short", 2, 2⟩
def tInt    : FType := ⟨"int", 4, 4⟩
def tDouble : FType := ⟨"double", 8, 8⟩

/-- E4: "insert a field before f" does NOT determine that f moves:
`{char a; int b}` → insert char at 0 → `b` stays at 4. -/
theorem insert_class_not_determined :
    (offsets [tChar, tInt])[1]? = some 4 ∧
    (offsets (insertAt 0 tChar [tChar, tInt]))[2]? = some 4 := by decide

/-- E4: "append a field" does NOT determine that sizeof grows:
`{int a; char b}` (8 bytes) → append char → still 8 bytes. -/
theorem append_class_not_determined :
    sizeOf [tInt, tChar] = 8 ∧ sizeOf (append tChar [tInt, tChar]) = 8 := by decide

/-- The generator's own seed instance (`struct rec { int id; double weight;
char tag[8]; }`, modelled with tag as 8 chars of one 8-byte/align-1 type)
does grow under `struct_append_field` (appends `int extra`). -/
def tChar8 : FType := ⟨"char[8]", 8, 1⟩
theorem seed_rec_append_grows :
    sizeOf [tInt, tDouble, tChar8] = 24 ∧ sizeOf (append tInt [tInt, tDouble, tChar8]) = 32 := by
  decide
end AbiLean.Layout

namespace AbiLean.Layout
/-! ### E2: the generator's C layout operators on their own seed (`c_core`) -/
def tLong : FType := ⟨"long", 8, 8⟩
def tChar16 : FType := ⟨"char[16]", 16, 1⟩
def tChar6 : FType := ⟨"char[6]", 6, 1⟩
def rec : List FType := [tInt, tDouble, tChar8]
def point : List FType := [tInt, tInt]

/-- struct_append_field (`long extra`): sizeof 24 → 32. -/
example : sizeOf rec = 24 ∧ sizeOf (append tLong rec) = 32 := by decide
/-- …and class-wide: appending `long` grows *every* struct whose max alignment
is ≤ 8 (all of x86-64 SysV except 16-aligned members), by `append_grows_size`. -/
theorem append_long_always_grows (ts : List FType) (h : maxAlign ts ≤ 8) :
    sizeOf ts < sizeOf (append tLong ts) :=
  append_grows_size ts tLong (by decide) h
/-- struct_remove_trailing_field: 24 → 16. -/
example : sizeOf (rec.take 2) = 16 := by decide
/-- …but the class is not determined: removing a trailing `char` from
`{double; int; char}` keeps 16. -/
example : sizeOf [tDouble, tInt, tChar] = 16 ∧ sizeOf [tDouble, tInt] = 16 := by decide
/-- struct_swap_fields: both fields move. -/
example : offsets point = [0, 4] := by decide
/-- struct_field_widen (`int x` → `long long x`): `y` moves 4 → 8. -/
example : offsets [tLong, tInt] = [0, 8] := by decide
/-- struct_array_grow (`tag[8]` → `tag[16]`): 24 → 32, offsets kept. -/
example : offsets [tInt, tDouble, tChar16] = offsets rec ∧ sizeOf [tInt, tDouble, tChar16] = 32 := by
  decide
/-- struct_array_shrink_in_padding (`tag[6]`): layout identical, which is why
the operator expects API_BREAK rather than BREAKING. -/
example : offsets [tInt, tDouble, tChar6] = offsets rec ∧ sizeOf [tInt, tDouble, tChar6] = 24 := by
  decide
end AbiLean.Layout
