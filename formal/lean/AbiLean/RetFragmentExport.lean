/- Export the closed return-type fragment (AbiLean.RetFragment) as data:
     lake env lean AbiLean/RetFragmentExport.lean   (writes ret_fragment_cases.json)
   One compact row per case of `space`, in `space` order:
     [parameter types, old return type, new return type, call compatible,
      arguments intact, class]
   one row per class with the key, the expected findings and verdict, call
   compatibility, argument placement, its representative and that case's
   rendered prototypes; and `gen_fnv1a64`, an FNV-1a-64 checksum of the
   rendering of `gen c` for every case in `space` order
   (tests/test_formal_ret_fragment.py). Not part of the library build. -/
import AbiLean.RetFragment
open AbiLean.RetFragment
open AbiLean.FuncFragment (Ty Decl PName)

def jStr (s : String) : String := "\"" ++ s ++ "\""
def jList (xs : List String) : String := "[" ++ ",".intercalate xs ++ "]"
def jBool (b : Bool) : String := if b then "true" else "false"

namespace AbiLean.RetFragment
def tyC : Ty → String
  | .int => "int" | .long => "long" | .dbl => "double" | .ptr => "void *"

def RTy.c : RTy → String
  | .void => "void" | .int => "int" | .long => "long" | .dbl => "double" | .ptr => "void *"
  | .s2l => "struct s2l" | .s2d => "struct s2d" | .s3l => "struct s3l"

def nameC : PName → String
  | .old j => "p" ++ toString j
  | .ins => "ins"

def declC (d : Decl) : String :=
  match d.ty with
  | .ptr => "void *" ++ nameC d.name
  | t => tyC t ++ " " ++ nameC d.name

/-- The C prototype without its name: `long (int p0, void *p1)`, `(void)` when
there are no parameters. -/
def Proto.c (p : Proto) : String :=
  p.ret.c ++ " (" ++ (if p.params.isEmpty then "void" else ", ".intercalate (p.params.map declC)) ++ ")"

def Kind.c : Kind → String
  | .returnChanged => "func_return_changed"
  | .retPtrLevelChanged => "return_pointer_level_changed"
  | .structRetConvChanged => "struct_return_convention_changed"

def Verdict.c : Verdict → String | .noChange => "no_change" | .breaking => "breaking"

def Key.c (k : Key) : String := jList [jBool k.ptrFlip, jBool k.convFlip, jBool k.call, jBool k.args]
end AbiLean.RetFragment

def reps : List Case :=
  space.foldl (fun acc c => if acc.any (fun r => key r == key c) then acc else acc ++ [c]) []

def classIdx (c : Case) : Nat := (reps.findIdx? (fun r => key r == key c)).getD 0

def row (c : Case) : String :=
  jList [jList (c.params.map fun t => jStr (tyC t)), jStr c.old.c, jStr c.new.c,
    jBool (callCompatible c), jBool (argsIntact c), toString (classIdx c)]

def rendered (c : Case) : String := (gen c).old.c ++ "\n" ++ (gen c).new.c ++ "\n"

def fnv1a64 (h : UInt64) (s : String) : UInt64 :=
  s.toUTF8.foldl (fun h b => (h ^^^ b.toUInt64) * 1099511628211) h

def genChecksum : UInt64 := space.foldl (fun h c => fnv1a64 h (rendered c)) 14695981039346656037

def classRow (i : Nat) (c : Case) : String :=
  "{" ++ ",".intercalate [
    "\"class\":" ++ toString i,
    "\"key\":" ++ (key c).c,
    "\"findings\":" ++ jList ((findings c).map fun k => jStr k.c),
    "\"verdict\":" ++ jStr (verdict c).c,
    "\"call_compatible\":" ++ jBool (callCompatible c),
    "\"args_intact\":" ++ jBool (argsIntact c),
    "\"rep\":" ++ toString ((space.findIdx? (· == c)).getD 0),
    "\"rep_old\":" ++ jStr (gen c).old.c,
    "\"rep_new\":" ++ jStr (gen c).new.c] ++ "}"

#eval IO.FS.writeFile "ret_fragment_cases.json"
  ("{\"_source\": \"formal/lean/AbiLean/RetFragmentExport.lean (AbiLean.RetFragment: space, gen, report contract, call model, key)\",\n" ++
   " \"space_size\": " ++ toString space.length ++ ",\n" ++
   " \"row_fields\": [\"params\", \"old\", \"new\", \"call_compatible\", \"args_intact\", \"class\"],\n" ++
   " \"gen_fnv1a64\": \"" ++ toString genChecksum ++ "\",\n" ++
   " \"classes\": [\n" ++ ",\n".intercalate ((reps.enum).map fun (i, c) => classRow i c) ++ "\n],\n" ++
   " \"rows\": [\n" ++ ",\n".intercalate (space.map row) ++ "\n]}\n")

#print axioms AbiLean.RetFragment.mem_space
#print axioms AbiLean.RetFragment.space_nodup
#print axioms AbiLean.RetFragment.space_length
#print axioms AbiLean.RetFragment.decode_gen
#print axioms AbiLean.RetFragment.generated_in_exactly_one
#print axioms AbiLean.RetFragment.every_case_generated
#print axioms AbiLean.RetFragment.every_case_breaking
#print axioms AbiLean.RetFragment.args_intact_iff
#print axioms AbiLean.RetFragment.call_compatible_iff
#print axioms AbiLean.RetFragment.params_irrelevant
#print axioms AbiLean.RetFragment.void_result_compatible_iff
#print axioms AbiLean.RetFragment.breaking_but_call_compatible
#print axioms AbiLean.RetFragment.scalar_to_sret_moves_args
#print axioms AbiLean.RetFragment.obs_eq_iff_key
