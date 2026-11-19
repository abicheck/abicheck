/-
  Same-class struct returns (E14b): change the declared members of a C struct
  returned by value from a function with no parameters, x86-64 System V.
  Claims in formal/RET_FRAGMENT.md ("Struct member layouts").

  1. `Ty`, `layout`, `size`, `shape`  – member types `int`/`long`/`double`,
     natural layout (byte offsets), and the psABI return classification
     (INT1/INT2/SSE1/SSE2 in registers, MEMORY over 16 bytes, `none` for a
     mixed INTEGER/SSE struct or an all-padding eightbyte).
  2. `loc`, `intact`                  – the oracle. The old caller reads each
     member it knows (type and offset) from where the OLD classification puts
     it (register of the struct's class for its eightbyte and the byte inside
     it, or the caller's buffer); the result is intact iff the new function
     leaves that same member in that same place, and a new function that
     writes through a hidden pointer is called by a caller that passed one,
     with a buffer at least as large.
     `image`, `checkIntact`           – abicheck's comparison
     (`compare/call_interface._result_broken` over `model.call_interface.ret_image`):
     every (place, size, class) piece of the old image is in the new image,
     and a result in caller memory did not grow.
  3. General theorems over any member list (no enumeration):
     `checkIntact_iff_intact` (abicheck = oracle), `intact_iff_sublayout`
     (intact iff the old member layout is a sub-layout of the new one, both or
     neither is in memory, and memory did not grow), `reg_member_cls` (every
     member of a register-returned struct has the struct's class).
  4. The fragment: `structs` (35 structs) and `space` (1190 ordered pairs);
     `mem_space`, `space_nodup`, `space_length`; the generator `gen`/`decode`
     (`decode_gen`, `gen_injective`, `generated_in_exactly_one`); and
     `intact_pairs`, the 12 intact pairs, which the execution oracle observed
     (formal/struct_ret_fragment_observed.json).
  5. StructRetFragmentExport.lean exports the table.

  Modelling assumption (checked by execution, not provable here): a read of a
  member returns the value the new function stored iff the new function
  stored the member of the same type at the same place.
-/
import AbiLean.Fragment

namespace AbiLean.StructRetFragment
open AbiLean.Fragment (nodup_flatMap nodup_map_of_inj)

/-! ## 1. Types, layout, classification -/

inductive Ty | i | l | d
  deriving DecidableEq, Repr

inductive Cls | int | sse
  deriving DecidableEq, Repr

/-- Size (= natural alignment) in bytes. -/
def Ty.size : Ty → Nat | .i => 4 | .l => 8 | .d => 8
def Ty.cls : Ty → Cls | .i => .int | .l => .int | .d => .sse

def Ty.all : List Ty := [.i, .l, .d]
theorem Ty.mem_all (t : Ty) : t ∈ Ty.all := by cases t <;> decide

/-- A member: its type and byte offset (the experiment's member name `l8`). -/
structure Mem where
  ty  : Ty
  off : Nat
  deriving DecidableEq, Repr

def alignUp (x a : Nat) : Nat := (x + a - 1) / a * a

theorem alignUp_ge (t : Ty) (x : Nat) : x ≤ alignUp x t.size := by
  cases t <;> simp only [alignUp, Ty.size] <;> omega

theorem alignUp_mod (t : Ty) (x : Nat) : alignUp x t.size % t.size = 0 := by
  cases t <;> simp only [alignUp, Ty.size] <;> omega

def layoutFrom : Nat → List Ty → List Mem
  | _, [] => []
  | off, t :: ts => ⟨t, alignUp off t.size⟩ :: layoutFrom (alignUp off t.size + t.size) ts

def endFrom : Nat → List Ty → Nat
  | off, [] => off
  | off, t :: ts => endFrom (alignUp off t.size + t.size) ts

def structAlign (s : List Ty) : Nat := if s.any (fun t => t.size == 8) then 8 else 4

def layout (s : List Ty) : List Mem := layoutFrom 0 s
/-- Size in bytes: the end of the last member rounded up to the alignment. -/
def size (s : List Ty) : Nat := alignUp (endFrom 0 s) (structAlign s)
def nEb (s : List Ty) : Nat := (size s + 7) / 8

/-- The class of eightbyte `k`: INTEGER if any member in it is, SSE if it
holds only SSE members, `none` if it is all padding. -/
def ebClass (s : List Ty) (k : Nat) : Option Cls :=
  let ms := (layout s).filter (fun m => m.off / 8 == k)
  if ms.any (fun m => m.ty.cls == .int) then some .int
  else if ms.isEmpty then none else some .sse

inductive Shape | int1 | int2 | sse1 | sse2 | memory
  deriving DecidableEq, Repr

def shape (s : List Ty) : Option Shape :=
  if 16 < size s then some .memory
  else if nEb s = 1 then
    if ebClass s 0 = some .int then some .int1
    else if ebClass s 0 = some .sse then some .sse1 else none
  else if nEb s = 2 then
    if ebClass s 0 = some .int ∧ ebClass s 1 = some .int then some .int2
    else if ebClass s 0 = some .sse ∧ ebClass s 1 = some .sse then some .sse2 else none
  else none

def Shape.regCls : Shape → Option Cls
  | .int1 | .int2 => some .int
  | .sse1 | .sse2 => some .sse
  | .memory => none

/-- The register class a struct comes back in, `none` for caller memory (a
struct outside the classification, `shape = none`, is outside the fragment;
it falls here too, which no theorem about the fragment relies on). -/
def regC (s : List Ty) : Option Cls := (shape s).bind Shape.regCls

def isMem (s : List Ty) : Prop := regC s = none
instance (s : List Ty) : Decidable (isMem s) := by unfold isMem; infer_instance

/-! ## 2. The oracle and abicheck's comparison -/

/-- A place: the caller's buffer, or the `k`-th register of a class (`rax`,
`rdx`; `xmm0`, `xmm1`). -/
inductive Loc | mem | reg (c : Cls) (k : Nat)
  deriving DecidableEq, Repr

/-- Where the struct `s` puts member `m`: the place and the byte inside it. In a
register struct eightbyte `k` takes the `k`-th register of the struct's class. -/
def loc (s : List Ty) (m : Mem) : Loc × Nat :=
  match regC s with
  | none => (.mem, m.off)
  | some c => (.reg c (m.off / 8), m.off % 8)

/-- Every member the old caller reads is the new function's member of the same
type at the same place. -/
def readsOk (o n : List Ty) : Prop := ∀ m ∈ layout o, m ∈ layout n ∧ loc o m = loc n m

/-- A new function returning in memory writes through the hidden pointer: the
old caller must have passed one, to a buffer at least as large. -/
def writeSafe (o n : List Ty) : Prop := isMem n → isMem o ∧ size n ≤ size o

def intact (o n : List Ty) : Prop := readsOk o n ∧ writeSafe o n

instance (o n : List Ty) : Decidable (intact o n) := by
  unfold intact readsOk writeSafe; infer_instance

/-- One piece of abicheck's `ret_image`: place, byte inside it, size, class. -/
structure Part where
  loc  : Loc × Nat
  size : Nat
  cls  : Cls
  deriving DecidableEq, Repr

def part (s : List Ty) (m : Mem) : Part := ⟨loc s m, m.ty.size, m.ty.cls⟩
def image (s : List Ty) : List Part := (layout s).map (part s)

/-- abicheck: no piece of the old image is lost, and a memory result did not grow. -/
def checkIntact (o n : List Ty) : Prop :=
  (∀ p ∈ image o, p ∈ image n) ∧ (isMem o → size n ≤ size o)

instance (o n : List Ty) : Decidable (checkIntact o n) := by
  unfold checkIntact; infer_instance

/-! ## 3. General theorems -/

theorem ty_of_size_cls {a b : Ty} (h1 : a.size = b.size) (h2 : a.cls = b.cls) : a = b := by
  cases a <;> cases b <;> simp_all [Ty.size, Ty.cls]

theorem ty_size_pos (t : Ty) : 4 ≤ t.size := by cases t <;> simp [Ty.size]

theorem loc_inj {o n : List Ty} {m m' : Mem} (h : loc o m = loc n m') : m.off = m'.off := by
  unfold loc at h
  split at h <;> split at h <;> simp_all [Prod.mk.injEq] <;> omega

theorem loc_mem_iff {o n : List Ty} {m m' : Mem} (h : loc o m = loc n m') :
    (isMem o ↔ isMem n) := by
  unfold loc at h; unfold isMem
  split at h <;> split at h <;> simp_all

theorem part_inj {o n : List Ty} {m m' : Mem} (h : part o m = part n m') :
    m = m' ∧ loc o m = loc n m' := by
  simp only [part, Part.mk.injEq] at h
  obtain ⟨hl, hs, hc⟩ := h
  have ht := ty_of_size_cls hs hc
  have ho := loc_inj hl
  cases m; cases m'; simp_all

theorem layout_ne_nil {s : List Ty} (h : s ≠ []) : ∃ m, m ∈ layout s := by
  cases s with
  | nil => exact absurd rfl h
  | cons t ts => exact ⟨_, List.mem_cons_self _ _⟩

/-- **abicheck's comparison is the oracle**, for any nonempty member list. -/
theorem checkIntact_iff_intact (o n : List Ty) (ho : o ≠ []) :
    checkIntact o n ↔ intact o n := by
  unfold checkIntact intact readsOk writeSafe image
  constructor
  · rintro ⟨hsub, hsz⟩
    have hr : ∀ m ∈ layout o, m ∈ layout n ∧ loc o m = loc n m := by
      intro m hm
      obtain ⟨m', hm', he⟩ := List.mem_map.1 (hsub _ (List.mem_map_of_mem _ hm))
      obtain ⟨rfl, hl⟩ := part_inj he
      exact ⟨hm', hl.symm⟩
    refine ⟨hr, fun hn => ?_⟩
    obtain ⟨m, hm⟩ := layout_ne_nil ho
    have hio := loc_mem_iff (hr m hm).2
    have : isMem o := hio.2 hn
    exact ⟨this, hsz this⟩
  · rintro ⟨hr, hw⟩
    refine ⟨fun p hp => ?_, fun hom => ?_⟩
    · obtain ⟨m, hm, rfl⟩ := List.mem_map.1 hp
      obtain ⟨hmn, hl⟩ := hr m hm
      exact List.mem_map.2 ⟨m, hmn, by simp [part, hl]⟩
    · obtain ⟨m, hm⟩ := layout_ne_nil ho
      exact (hw ((loc_mem_iff (hr m hm).2).1 hom)).2

/-! ### Layout facts -/

theorem ge_of_mem {off : Nat} {s : List Ty} {m : Mem} (h : m ∈ layoutFrom off s) :
    off ≤ m.off := by
  induction s generalizing off with
  | nil => simp [layoutFrom] at h
  | cons t ts ih =>
    simp only [layoutFrom, List.mem_cons] at h
    have := alignUp_ge t off
    rcases h with rfl | h
    · exact this
    · have := ih h; omega

theorem end_ge (off : Nat) (s : List Ty) : off ≤ endFrom off s := by
  induction s generalizing off with
  | nil => simp [endFrom]
  | cons t ts ih =>
    simp only [endFrom]; have := ih (alignUp off t.size + t.size); have := alignUp_ge t off; omega

theorem end_le {off : Nat} {s : List Ty} {m : Mem} (h : m ∈ layoutFrom off s) :
    m.off + m.ty.size ≤ endFrom off s := by
  induction s generalizing off with
  | nil => simp [layoutFrom] at h
  | cons t ts ih =>
    simp only [layoutFrom, List.mem_cons] at h
    simp only [endFrom]
    rcases h with rfl | h
    · exact end_ge _ _
    · exact ih h

theorem aligned {off : Nat} {s : List Ty} {m : Mem} (h : m ∈ layoutFrom off s) :
    m.off % m.ty.size = 0 := by
  induction s generalizing off with
  | nil => simp [layoutFrom] at h
  | cons t ts ih =>
    simp only [layoutFrom, List.mem_cons] at h
    rcases h with rfl | h
    · exact alignUp_mod t off
    · exact ih h

theorem disjoint {off : Nat} {s : List Ty} {m m' : Mem} (h : m ∈ layoutFrom off s)
    (h' : m' ∈ layoutFrom off s) (hne : m ≠ m') :
    m.off + m.ty.size ≤ m'.off ∨ m'.off + m'.ty.size ≤ m.off := by
  induction s generalizing off with
  | nil => simp [layoutFrom] at h
  | cons t ts ih =>
    simp only [layoutFrom, List.mem_cons] at h h'
    rcases h with rfl | h <;> rcases h' with rfl | h'
    · exact absurd rfl hne
    · exact Or.inl (ge_of_mem h')
    · exact Or.inr (ge_of_mem h)
    · exact ih h h'

theorem size_ge_end (s : List Ty) : endFrom 0 s ≤ size s := by
  unfold size structAlign alignUp; split <;> omega

theorem mem_end_le_size {s : List Ty} {m : Mem} (h : m ∈ layout s) :
    m.off + m.ty.size ≤ size s := Nat.le_trans (end_le h) (size_ge_end s)

/-- The eightbyte holding member `m` has `m`'s class (a `double` fills its
eightbyte alone). -/
theorem ebClass_mem {s : List Ty} {m : Mem} (h : m ∈ layout s) :
    ebClass s (m.off / 8) = some m.ty.cls := by
  have hin : m ∈ (layout s).filter (fun x => x.off / 8 == m.off / 8) :=
    List.mem_filter.2 ⟨h, by simp⟩
  cases ht : m.ty with
  | i | l =>
    have : ((layout s).filter (fun x => x.off / 8 == m.off / 8)).any
        (fun x => x.ty.cls == .int) = true :=
      List.any_eq_true.2 ⟨m, hin, by rw [ht]; rfl⟩
    simp only [ebClass, this, if_true]; rfl
  | d =>
    have hno : ((layout s).filter (fun x => x.off / 8 == m.off / 8)).any
        (fun x => x.ty.cls == .int) = false := by
      apply Bool.eq_false_iff.2
      intro hany
      obtain ⟨x, hx, hc⟩ := List.any_eq_true.1 hany
      obtain ⟨hxl, hxe⟩ := List.mem_filter.1 hx
      have hxe : x.off / 8 = m.off / 8 := by simpa using hxe
      have hxm : x ≠ m := by
        intro e; subst e; rw [ht] at hc; exact absurd hc (by decide)
      have hal := aligned h
      have hdis := disjoint hxl h hxm
      have hpos := ty_size_pos x.ty
      have hm8 : m.ty.size = 8 := by rw [ht]; rfl
      rw [hm8] at hal hdis
      omega
    have hne : ((layout s).filter (fun x => x.off / 8 == m.off / 8)).isEmpty = false := by
      cases hl : (layout s).filter (fun x => x.off / 8 == m.off / 8) with
      | nil => rw [hl] at hin; simp at hin
      | cons _ _ => rfl
    simp only [ebClass, hno, hne, Bool.false_eq_true, if_false]; rfl

/-- A register-returned struct has one class in every eightbyte. -/
theorem reg_shape {s : List Ty} {c : Cls} (hc : regC s = some c) :
    ∀ k, k < nEb s → ebClass s k = some c := by
  unfold regC shape at hc
  intro k hk
  by_cases h16 : 16 < size s
  · rw [if_pos h16] at hc; simp [Option.bind, Shape.regCls] at hc
  rw [if_neg h16] at hc
  by_cases h1 : nEb s = 1
  · rw [if_pos h1] at hc
    have : k = 0 := by omega
    subst this
    by_cases a : ebClass s 0 = some .int
    · rw [if_pos a] at hc; simp only [Option.bind, Shape.regCls, Option.some.injEq] at hc
      subst hc; exact a
    · rw [if_neg a] at hc
      by_cases b : ebClass s 0 = some .sse
      · rw [if_pos b] at hc; simp only [Option.bind, Shape.regCls, Option.some.injEq] at hc
        subst hc; exact b
      · rw [if_neg b] at hc; simp [Option.bind] at hc
  · rw [if_neg h1] at hc
    by_cases h2 : nEb s = 2
    · rw [if_pos h2] at hc
      have hk01 : k = 0 ∨ k = 1 := by omega
      by_cases a : ebClass s 0 = some .int ∧ ebClass s 1 = some .int
      · rw [if_pos a] at hc; simp only [Option.bind, Shape.regCls, Option.some.injEq] at hc
        subst hc; rcases hk01 with rfl | rfl
        · exact a.1
        · exact a.2
      · rw [if_neg a] at hc
        by_cases b : ebClass s 0 = some .sse ∧ ebClass s 1 = some .sse
        · rw [if_pos b] at hc; simp only [Option.bind, Shape.regCls, Option.some.injEq] at hc
          subst hc; rcases hk01 with rfl | rfl
          · exact b.1
          · exact b.2
        · rw [if_neg b] at hc; simp [Option.bind] at hc
    · rw [if_neg h2] at hc; simp [Option.bind] at hc

/-- **Every member of a register-returned struct has the struct's class.** -/
theorem reg_member_cls {s : List Ty} {c : Cls} (hc : regC s = some c) {m : Mem}
    (hm : m ∈ layout s) : m.ty.cls = c := by
  have hle := mem_end_le_size hm
  have hpos := ty_size_pos m.ty
  have hk : m.off / 8 < nEb s := by unfold nEb; omega
  exact Option.some.inj ((ebClass_mem hm).symm.trans (reg_shape hc _ hk))

/-- **Characterisation** (any nonempty member list): an old caller reads the
new struct intact iff every old member (type and offset) is a member of the
new struct, both or neither come back in memory, and a memory result did not
grow. Same shape is not required: `{int}` → `{int; long}` (INT1 → INT2) is
intact, the int stays in the low 32 bits of `rax`. -/
theorem intact_iff_sublayout (o n : List Ty) (ho : o ≠ []) :
    intact o n ↔ (∀ m ∈ layout o, m ∈ layout n) ∧ (isMem o ↔ isMem n) ∧
      (isMem o → size n ≤ size o) := by
  unfold intact readsOk writeSafe
  constructor
  · rintro ⟨hr, hw⟩
    obtain ⟨m, hm⟩ := layout_ne_nil ho
    have hio := loc_mem_iff (hr m hm).2
    exact ⟨fun x hx => (hr x hx).1, hio, fun h => (hw (hio.1 h)).2⟩
  · rintro ⟨hsub, hio, hsz⟩
    refine ⟨fun m hm => ⟨hsub m hm, ?_⟩, fun hn => ⟨hio.2 hn, hsz (hio.2 hn)⟩⟩
    unfold loc
    unfold isMem at hio
    cases hco : regC o with
    | none =>
      have : regC n = none := hio.1 hco
      simp [this]
    | some c =>
      cases hcn : regC n with
      | none => rw [hcn] at hio; simp [hio.2 rfl] at hco
      | some c' =>
        have e1 := reg_member_cls hco hm
        have e2 := reg_member_cls hcn (hsub m hm)
        simp [← e1, ← e2]

/-! ## 4. The fragment -/

def words : Nat → List (List Ty)
  | 0 => [[]]
  | n + 1 => Ty.all.flatMap fun t => (words n).map (t :: ·)

theorem mem_words (n : Nat) (w : List Ty) : w ∈ words n ↔ w.length = n := by
  induction n generalizing w with
  | zero => cases w <;> simp [words]
  | succ n ih =>
    cases w with
    | nil => simp [words]
    | cons t ts =>
      simp only [words, List.mem_flatMap, List.mem_map, List.length_cons, Nat.add_right_cancel_iff]
      constructor
      · rintro ⟨t', -, w', hw', e⟩
        simp only [List.cons.injEq] at e
        exact e.2 ▸ (ih w').1 hw'
      · intro h; exact ⟨t, Ty.mem_all t, ts, (ih ts).2 h, rfl⟩

/-- The two 32-byte (MEMORY) structs: `long ×4` and `long, long, double, double`. -/
def larger : List (List Ty) := [[.l, .l, .l, .l], [.l, .l, .d, .d]]

/-- A struct of the fragment, as a predicate. -/
def InStruct (s : List Ty) : Prop :=
  (1 ≤ s.length ∧ s.length ≤ 3 ∧ shape s ≠ none) ∨ s ∈ larger

instance (s : List Ty) : Decidable (InStruct s) := by unfold InStruct; infer_instance

/-- The fragment's structs, shortest first (the experiment's `structs()`). -/
def structs : List (List Ty) :=
  ([1, 2, 3].flatMap fun n => (words n).filter fun s => shape s != none) ++ larger

theorem mem_structs (s : List Ty) : s ∈ structs ↔ InStruct s := by
  unfold structs InStruct
  simp only [List.mem_append, List.mem_flatMap, List.mem_filter, mem_words, bne_iff_ne, ne_eq,
    List.mem_cons, List.mem_singleton, List.not_mem_nil, or_false]
  constructor
  · rintro (⟨n, hn, hl, hs⟩ | h)
    · left; rcases hn with rfl | rfl | rfl <;> simp_all
    · right; exact h
  · rintro (⟨h1, h3, hs⟩ | h)
    · left; refine ⟨s.length, by omega, rfl, hs⟩
    · right; exact h

theorem structs_nodup : structs.Nodup := by decide
theorem structs_length : structs.length = 35 := by decide

structure Case where
  old : List Ty
  new : List Ty
  deriving DecidableEq, Repr

def InSpace (c : Case) : Prop := InStruct c.old ∧ InStruct c.new ∧ c.old ≠ c.new

instance (c : Case) : Decidable (InSpace c) := by unfold InSpace; infer_instance

/-- Every ordered pair of distinct structs, old-major (the experiment's `space()`). -/
def space : List Case :=
  structs.flatMap fun o => (structs.filter (· != o)).map fun n => ⟨o, n⟩

theorem mem_space (c : Case) : c ∈ space ↔ InSpace c := by
  unfold space InSpace
  simp only [List.mem_flatMap, List.mem_map, List.mem_filter, bne_iff_ne, ne_eq, ← mem_structs]
  constructor
  · rintro ⟨o, ho, n, ⟨hn, hne⟩, rfl⟩; exact ⟨ho, hn, fun e => hne e.symm⟩
  · rintro ⟨ho, hn, hne⟩; exact ⟨c.old, ho, c.new, ⟨hn, fun e => hne e.symm⟩, rfl⟩

theorem space_nodup : space.Nodup := by
  unfold space
  apply nodup_flatMap structs_nodup
  · intro o _
    exact nodup_map_of_inj (fun a b h => by simpa using h) (List.Nodup.sublist (List.filter_sublist _) structs_nodup)
  · intro a₁ a₂ x h₁ h₂
    obtain ⟨_, -, rfl⟩ := List.mem_map.1 h₁
    obtain ⟨_, -, h⟩ := List.mem_map.1 h₂
    simp only [Case.mk.injEq] at h; exact h.1.symm

set_option maxRecDepth 100000 in
theorem space_length : space.length = 1190 := by decide

/-! ### The generator: the experiment's spelling of a struct (`"il"`) -/

def Ty.ch : Ty → Char | .i => 'i' | .l => 'l' | .d => 'd'
def Ty.ofCh : Char → Option Ty
  | 'i' => some .i | 'l' => some .l | 'd' => some .d | _ => none

theorem Ty.ofCh_ch (t : Ty) : Ty.ofCh t.ch = some t := by cases t <;> rfl

def spell (s : List Ty) : String := String.mk (s.map Ty.ch)
def parse (str : String) : Option (List Ty) := str.toList.mapM Ty.ofCh

theorem parse_spell (s : List Ty) : parse (spell s) = some s := by
  unfold parse spell
  simp only [String.toList]
  induction s with
  | nil => rfl
  | cons t ts ih => simp [List.mapM_cons, Ty.ofCh_ch, ih]

structure Program where
  old : String
  new : String
  deriving DecidableEq, Repr

def gen (c : Case) : Program := ⟨spell c.old, spell c.new⟩

/-- Decode a program pair to a case, or `none` if it is not a generated one. -/
def decode (p : Program) : Option Case :=
  match parse p.old, parse p.new with
  | some o, some n => if InSpace ⟨o, n⟩ ∧ gen ⟨o, n⟩ = p then some ⟨o, n⟩ else none
  | _, _ => none

theorem decode_gen (c : Case) (hc : InSpace c) : decode (gen c) = some c := by
  simp [decode, gen, parse_spell, hc]

theorem gen_decode {p : Program} {c : Case} (h : decode p = some c) :
    InSpace c ∧ gen c = p := by
  unfold decode at h
  split at h
  · split at h
    · rename_i hc; cases h; exact hc
    · contradiction
  · contradiction

theorem gen_injective {c₁ c₂ : Case} (h₁ : InSpace c₁) (h₂ : InSpace c₂)
    (h : gen c₁ = gen c₂) : c₁ = c₂ := by
  have := decode_gen c₁ h₁; rw [h, decode_gen c₂ h₂] at this; exact (Option.some.inj this).symm

/-- Every generated program pair belongs to exactly one case of the space. -/
theorem generated_in_exactly_one (p : Program) (hp : p ∈ space.map gen) :
    ∃ c, c ∈ space ∧ gen c = p ∧ ∀ c', c' ∈ space → gen c' = p → c' = c := by
  obtain ⟨c, hc, rfl⟩ := List.mem_map.1 hp
  exact ⟨c, hc, rfl, fun c' hc' h =>
    gen_injective ((mem_space c').1 hc') ((mem_space c).1 hc) h⟩

/-! ### The verdicts on the fragment -/

/-- The 12 pairs an old caller reads intact -- exactly the pairs the execution
oracle observed intact on gcc and clang (struct_ret_fragment_observed.json). -/
def intactPairs : List Case :=
  [⟨[.i], [.i, .i]⟩, ⟨[.i], [.i, .l]⟩, ⟨[.i], [.i, .i, .i]⟩, ⟨[.i], [.i, .i, .l]⟩,
   ⟨[.l], [.l, .i]⟩, ⟨[.l], [.l, .l]⟩, ⟨[.l], [.l, .i, .i]⟩, ⟨[.d], [.d, .d]⟩,
   ⟨[.i, .i], [.i, .i, .i]⟩, ⟨[.i, .i], [.i, .i, .l]⟩, ⟨[.i, .l], [.i, .i, .l]⟩,
   ⟨[.l, .i], [.l, .i, .i]⟩]

set_option maxRecDepth 100000 in
theorem intact_pairs : space.filter (fun c => decide (intact c.old c.new)) = intactPairs := by
  decide

/-- On the fragment abicheck's comparison is the oracle (from the general theorem). -/
theorem check_eq_oracle (c : Case) (hc : c ∈ space) :
    checkIntact c.old c.new ↔ intact c.old c.new := by
  apply checkIntact_iff_intact
  have := ((mem_space c).1 hc).1
  unfold InStruct larger at this
  intro e; rw [e] at this; simp at this

/-- abicheck's comparison picks out the same 12 pairs. -/
theorem check_pairs :
    space.filter (fun c => decide (checkIntact c.old c.new)) = intactPairs := by
  rw [← intact_pairs]
  apply List.filter_congr
  intro c hc
  simp only [decide_eq_decide]
  exact check_eq_oracle c hc

end AbiLean.StructRetFragment
