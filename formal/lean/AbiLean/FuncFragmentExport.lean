/- Export the closed function-signature fragment (AbiLean.FuncFragment) as data:
     lake env lean AbiLean/FuncFragmentExport.lean   (writes func_fragment_cases.json)
   One compact row per case of `space`, in `space` order:
     [base types, op, pos, type (or ""), call compatible, class]
   one row per class with the key, the expected findings and verdict, call
   compatibility, its representative and that case's rendered parameter
   lists; and `gen_fnv1a64`, an FNV-1a-64 checksum of the rendering of `gen c`
   for every case in `space` order (tests/test_formal_func_fragment.py).
   Not part of the library build. -/
import AbiLean.FuncFragment
open AbiLean.FuncFragment

def jStr (s : String) : String := "\"" ++ s ++ "\""
def jList (xs : List String) : String := "[" ++ ",".intercalate xs ++ "]"
def jBool (b : Bool) : String := if b then "true" else "false"

namespace AbiLean.FuncFragment
def Ty.c : Ty → String
  | .int => "int" | .long => "long" | .dbl => "double" | .ptr => "void *"

def PName.c : PName → String
  | .old j => "p" ++ toString j
  | .ins => "ins"

def Decl.c (d : Decl) : String :=
  match d.ty with
  | .ptr => "void *" ++ d.name.c
  | t => t.c ++ " " ++ d.name.c

/-- The C parameter list: `int p0, void *p1`, or `void` when empty. -/
def body (ds : List Decl) : String := if ds.isEmpty then "void" else ", ".intercalate (ds.map Decl.c)

def Kind.c : Kind → String
  | .paramsChanged => "func_params_changed"
  | .ptrLevelChanged => "param_pointer_level_changed"
  | .renamed => "param_renamed"

def Finding.c (f : Finding) : String :=
  jStr (f.kind.c ++ ":" ++ match f.pos with | some k => toString k | none => "")

def Verdict.c : Verdict → String | .noChange => "no_change" | .breaking => "breaking"

def Key.c (k : Key) : String := jList [jList (k.positional.map Finding.c), jBool k.call]

def Op.cols : Op → List String
  | .ins p t => [jStr "ins", toString p, jStr t.c]
  | .rem p   => [jStr "rem", toString p, jStr ""]
  | .set p t => [jStr "set", toString p, jStr t.c]
end AbiLean.FuncFragment

def reps : List Case :=
  space.foldl (fun acc c => if acc.any (fun r => key r == key c) then acc else acc ++ [c]) []

def classIdx (c : Case) : Nat := (reps.findIdx? (fun r => key r == key c)).getD 0

def row (c : Case) : String :=
  jList ([jList (c.base.map fun t => jStr t.c)] ++ c.op.cols ++
    [jBool (callCompatible c), toString (classIdx c)])

def rendered (c : Case) : String := body (gen c).old ++ "\n" ++ body (gen c).new ++ "\n"

def fnv1a64 (h : UInt64) (s : String) : UInt64 :=
  s.toUTF8.foldl (fun h b => (h ^^^ b.toUInt64) * 1099511628211) h

def genChecksum : UInt64 := space.foldl (fun h c => fnv1a64 h (rendered c)) 14695981039346656037

def classRow (i : Nat) (c : Case) : String :=
  "{" ++ ",".intercalate [
    "\"class\":" ++ toString i,
    "\"key\":" ++ (key c).c,
    "\"findings\":" ++ jList ((findings c).map Finding.c),
    "\"verdict\":" ++ jStr (verdict c).c,
    "\"call_compatible\":" ++ jBool (callCompatible c),
    "\"rep\":" ++ toString ((space.findIdx? (· == c)).getD 0),
    "\"rep_old_body\":" ++ jStr (body (gen c).old),
    "\"rep_new_body\":" ++ jStr (body (gen c).new)] ++ "}"

#eval IO.FS.writeFile "func_fragment_cases.json"
  ("{\"_source\": \"formal/lean/AbiLean/FuncFragmentExport.lean (AbiLean.FuncFragment: space, gen, report contract, call model, key)\",\n" ++
   " \"space_size\": " ++ toString space.length ++ ",\n" ++
   " \"row_fields\": [\"base\", \"op\", \"pos\", \"type\", \"call_compatible\", \"class\"],\n" ++
   " \"gen_fnv1a64\": \"" ++ toString genChecksum ++ "\",\n" ++
   " \"classes\": [\n" ++ ",\n".intercalate ((reps.enum).map fun (i, c) => classRow i c) ++ "\n],\n" ++
   " \"rows\": [\n" ++ ",\n".intercalate (space.map row) ++ "\n]}\n")

#print axioms AbiLean.FuncFragment.mem_space
#print axioms AbiLean.FuncFragment.space_nodup
#print axioms AbiLean.FuncFragment.space_length
#print axioms AbiLean.FuncFragment.decode_gen
#print axioms AbiLean.FuncFragment.generated_in_exactly_one
#print axioms AbiLean.FuncFragment.every_case_generated
#print axioms AbiLean.FuncFragment.ins_never_compatible
#print axioms AbiLean.FuncFragment.set_compatible_iff
#print axioms AbiLean.FuncFragment.rem_compatible_iff
#print axioms AbiLean.FuncFragment.trailing_removal_compatible
#print axioms AbiLean.FuncFragment.every_case_breaking
#print axioms AbiLean.FuncFragment.breaking_but_call_compatible
#print axioms AbiLean.FuncFragment.obs_eq_iff_key
