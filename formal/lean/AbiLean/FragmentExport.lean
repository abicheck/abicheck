/- Export the closed layout fragment (AbiLean.Fragment) as data:
     lake env lean AbiLean/FragmentExport.lean   (writes fragment_cases.json)
   One row per case of `space`, in `space` order, with the abstract program
   pair from `gen`, the layout oracle (offsets, sizeof, alignment), its class
   and whether it is its class's representative (the first case of `space`
   with that key); one row per class with the key and the expected findings
   and verdict. Not part of the library build. -/
import AbiLean.Fragment
open AbiLean.Fragment

def jStr (s : String) : String := "\"" ++ s ++ "\""
def jList (xs : List String) : String := "[" ++ ",".intercalate xs ++ "]"
def jNats (xs : List Nat) : String := jList (xs.map toString)
def jBool (b : Bool) : String := if b then "true" else "false"

namespace AbiLean.Fragment
def FName.c : FName → String
  | .old j => "f" ++ toString j
  | .ins => "ins"

def body (ds : List Decl) : String := " ".intercalate (ds.map fun d => d.cty ++ " " ++ d.name.c ++ ";")

def Kind.c : Kind → String
  | .typeFieldAddedCompatible => "type_field_added_compatible"
  | .typeFieldOffsetChanged => "type_field_offset_changed"
  | .typeSizeChanged => "type_size_changed"
  | .typeAlignmentChanged => "type_alignment_changed"

def Finding.c (f : Finding) : String :=
  jStr (f.kind.c ++ ":" ++ match f.subj with | .member n => n.c | .record => "")

def Verdict.c : Verdict → String | .compatible => "compatible" | .breaking => "breaking"

def Key.c (k : Key) : String := jList [jNats k.moved, jBool k.size, jBool k.align]
end AbiLean.Fragment

/-- Class representatives: the first case of `space` for each key. -/
def reps : List Case :=
  space.foldl (fun acc c => if acc.any (fun r => key r == key c) then acc else acc ++ [c]) []

def classIdx (c : Case) : Nat := (reps.findIdx? (fun r => key r == key c)).getD 0

def row (i : Nat) (c : Case) : String :=
  let p := gen c
  "{" ++ ",".intercalate [
    "\"id\":" ++ toString i,
    "\"base\":" ++ jNats (c.base.map Sc.bytes),
    "\"pos\":" ++ toString c.pos,
    "\"ins\":" ++ toString c.ins.bytes,
    "\"old_body\":" ++ jStr (body p.old),
    "\"new_body\":" ++ jStr (body p.new),
    "\"old\":" ++ jList [jNats (oldOffsets c), toString (oldSize c), toString (oldAlign c)],
    "\"new\":" ++ jList [jNats (newOffsets c), toString (newSize c), toString (newAlign c)],
    "\"class\":" ++ toString (classIdx c),
    "\"rep\":" ++ jBool (reps.contains c)] ++ "}"

def rows : List String := (space.enum).map fun (i, c) => row i c

/-- One row per class: its key and the oracle's findings and verdict. By
`obs_eq_iff_key` these are the same for every case of the class, so they are
stored once here instead of once per case. -/
def classRow (i : Nat) (c : Case) : String :=
  "{" ++ ",".intercalate [
    "\"class\":" ++ toString i,
    "\"key\":" ++ (key c).c,
    "\"findings\":" ++ jList ((findings c).map Finding.c),
    "\"verdict\":" ++ jStr (verdict c).c,
    "\"rep\":" ++ toString ((space.findIdx? (· == c)).getD 0)] ++ "}"

#eval IO.FS.writeFile "fragment_cases.json"
  ("{\"_source\": \"formal/lean/AbiLean/FragmentExport.lean (AbiLean.Fragment: space, gen, oracle, key)\",\n" ++
   " \"space_size\": " ++ toString space.length ++ ",\n" ++
   " \"classes\": [\n" ++ ",\n".intercalate ((reps.enum).map fun (i, c) => classRow i c) ++ "\n],\n" ++
   " \"rows\": [\n" ++ ",\n".intercalate rows ++ "\n]}\n")

#print axioms AbiLean.Fragment.mem_space
#print axioms AbiLean.Fragment.space_nodup
#print axioms AbiLean.Fragment.space_length
#print axioms AbiLean.Fragment.decode_gen
#print axioms AbiLean.Fragment.generated_in_exactly_one
#print axioms AbiLean.Fragment.every_case_generated
#print axioms AbiLean.Fragment.moved_contiguous
#print axioms AbiLean.Fragment.movedFields_interval
#print axioms AbiLean.Fragment.obs_eq_iff_key
#print axioms AbiLean.Fragment.opName_not_enough
