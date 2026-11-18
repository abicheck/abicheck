/- Export the closed enum fragment (AbiLean.EnumFragment) as data:
     lake env lean AbiLean/EnumFragmentExport.lean   (writes enum_fragment_cases.json)
   One compact row per case of `space`, in `space` order:
     [base initializers, op, pos, initializer (or ""), old values, new values, class]
   one row per class with the key, the expected findings and verdict, its
   representative (the first case of `space` with that key) and that case's
   rendered enumerator lists; and `gen_fnv1a64`, an FNV-1a-64 checksum of the
   rendering of `gen c` for every case in `space` order, so the C generator
   is checked against Lean's `gen` on every case
   (tests/test_formal_enum_fragment.py). Not part of the library build. -/
import AbiLean.EnumFragment
open AbiLean.EnumFragment

def jStr (s : String) : String := "\"" ++ s ++ "\""
def jList (xs : List String) : String := "[" ++ ",".intercalate xs ++ "]"
def jInts (xs : List Int) : String := jList (xs.map toString)
def jNats (xs : List Nat) : String := jList (xs.map toString)
def jBool (b : Bool) : String := if b then "true" else "false"

namespace AbiLean.EnumFragment
/-- The initializer's C spelling after the enumerator name. -/
def Init.c : Init → String
  | .imp => "" | .z => " = 0" | .two => " = 2" | .neg => " = -1"

/-- The initializer's code in the export rows. -/
def Init.code : Init → String
  | .imp => "imp" | .z => "0" | .two => "2" | .neg => "-1"

def EName.c : EName → String
  | .old j => "e" ++ toString j
  | .ins => "ins"

def body (ds : List Decl) : String := ", ".intercalate (ds.map fun d => d.name.c ++ d.init.c)

def Kind.c : Kind → String
  | .memberAdded => "enum_member_added"
  | .memberRemoved => "enum_member_removed"
  | .memberValueChanged => "enum_member_value_changed"

def Finding.c (f : Finding) : String := jStr (f.kind.c ++ ":" ++ f.subj.c)

def Verdict.c : Verdict → String
  | .noChange => "no_change" | .compatible => "compatible" | .breaking => "breaking"

def Key.c (k : Key) : String := jList [jBool k.added, jNats k.removed, jNats k.changed]

def Op.cols : Op → List String
  | .ins p i => [jStr "ins", toString p, jStr i.code]
  | .rem p   => [jStr "rem", toString p, jStr ""]
  | .set p i => [jStr "set", toString p, jStr i.code]
end AbiLean.EnumFragment

/-- Class representatives: the first case of `space` for each key. -/
def reps : List Case :=
  space.foldl (fun acc c => if acc.any (fun r => key r == key c) then acc else acc ++ [c]) []

def classIdx (c : Case) : Nat := (reps.findIdx? (fun r => key r == key c)).getD 0

def row (c : Case) : String :=
  jList ([jList (c.base.map fun i => jStr i.code)] ++ c.op.cols ++
    [jInts (values c.base), jInts (values (newInits c)), toString (classIdx c)])

/-- The rendering of one generated program pair: old list, newline, new list, newline. -/
def rendered (c : Case) : String := body (gen c).old ++ "\n" ++ body (gen c).new ++ "\n"

/-- FNV-1a, 64-bit, over the UTF-8 bytes of `s`, continuing from `h`. -/
def fnv1a64 (h : UInt64) (s : String) : UInt64 :=
  s.toUTF8.foldl (fun h b => (h ^^^ b.toUInt64) * 1099511628211) h

def genChecksum : UInt64 := space.foldl (fun h c => fnv1a64 h (rendered c)) 14695981039346656037

def classRow (i : Nat) (c : Case) : String :=
  "{" ++ ",".intercalate [
    "\"class\":" ++ toString i,
    "\"key\":" ++ (key c).c,
    "\"findings\":" ++ jList ((findings c).map Finding.c),
    "\"verdict\":" ++ jStr (verdict c).c,
    "\"rep\":" ++ toString ((space.findIdx? (· == c)).getD 0),
    "\"rep_old_body\":" ++ jStr (body (gen c).old),
    "\"rep_new_body\":" ++ jStr (body (gen c).new)] ++ "}"

#eval IO.FS.writeFile "enum_fragment_cases.json"
  ("{\"_source\": \"formal/lean/AbiLean/EnumFragmentExport.lean (AbiLean.EnumFragment: space, gen, oracle, key)\",\n" ++
   " \"space_size\": " ++ toString space.length ++ ",\n" ++
   " \"row_fields\": [\"base\", \"op\", \"pos\", \"init\", \"old_values\", \"new_values\", \"class\"],\n" ++
   " \"gen_fnv1a64\": \"" ++ toString genChecksum ++ "\",\n" ++
   " \"classes\": [\n" ++ ",\n".intercalate ((reps.enum).map fun (i, c) => classRow i c) ++ "\n],\n" ++
   " \"rows\": [\n" ++ ",\n".intercalate (space.map row) ++ "\n]}\n")

#print axioms AbiLean.EnumFragment.mem_space
#print axioms AbiLean.EnumFragment.space_nodup
#print axioms AbiLean.EnumFragment.space_length
#print axioms AbiLean.EnumFragment.decode_gen
#print axioms AbiLean.EnumFragment.generated_in_exactly_one
#print axioms AbiLean.EnumFragment.every_case_generated
#print axioms AbiLean.EnumFragment.gen_new_inits
#print axioms AbiLean.EnumFragment.changed_contiguous
#print axioms AbiLean.EnumFragment.changed_interval
#print axioms AbiLean.EnumFragment.explicit_never_shifted
#print axioms AbiLean.EnumFragment.append_compatible
#print axioms AbiLean.EnumFragment.obs_eq_iff_key
#print axioms AbiLean.EnumFragment.op_not_enough
#print axioms AbiLean.EnumFragment.respelling_is_no_change
