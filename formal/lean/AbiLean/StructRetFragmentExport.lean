/- Export the struct-return fragment (AbiLean.StructRetFragment) as data:
     lake env lean AbiLean/StructRetFragmentExport.lean
   (writes struct_ret_fragment_cases.json)
   `structs`: one row per struct of `structs`, in order:
     [spelling, size in bytes, shape, [[type, byte offset], ...]]
   `rows`: one row per case of `space`, in `space` order:
     [old spelling, new spelling, intact]
   and `gen_fnv1a64`, an FNV-1a-64 checksum of `gen c` ("old\nnew\n") for
   every case in `space` order (tests/test_formal_struct_ret_fragment.py).
   Not part of the library build. -/
import AbiLean.StructRetFragment
open AbiLean.StructRetFragment

def jStr (s : String) : String := "\"" ++ s ++ "\""
def jList (xs : List String) : String := "[" ++ ",".intercalate xs ++ "]"
def jBool (b : Bool) : String := if b then "true" else "false"

def shapeC : Option Shape → String
  | some .int1 => "int1" | some .int2 => "int2" | some .sse1 => "sse1"
  | some .sse2 => "sse2" | some .memory => "memory" | none => "none"

def structRow (s : List Ty) : String :=
  jList [jStr (spell s), toString (size s), jStr (shapeC (shape s)),
    jList ((layout s).map fun m => jList [jStr (String.mk [m.ty.ch]), toString m.off])]

def row (c : Case) : String :=
  jList [jStr (spell c.old), jStr (spell c.new), jBool (decide (intact c.old c.new))]

def fnv1a64 (h : UInt64) (s : String) : UInt64 :=
  s.toUTF8.foldl (fun h b => (h ^^^ b.toUInt64) * 1099511628211) h

def genChecksum : UInt64 :=
  space.foldl (fun h c => fnv1a64 h ((gen c).old ++ "\n" ++ (gen c).new ++ "\n")) 14695981039346656037

#eval IO.FS.writeFile "struct_ret_fragment_cases.json"
  ("{\"_source\": \"formal/lean/AbiLean/StructRetFragmentExport.lean (AbiLean.StructRetFragment: structs, space, gen, intact)\",\n" ++
   " \"space_size\": " ++ toString space.length ++ ",\n" ++
   " \"struct_fields\": [\"spelling\", \"size\", \"shape\", \"layout\"],\n" ++
   " \"row_fields\": [\"old\", \"new\", \"intact\"],\n" ++
   " \"gen_fnv1a64\": \"" ++ toString genChecksum ++ "\",\n" ++
   " \"structs\": [\n" ++ ",\n".intercalate (structs.map structRow) ++ "\n],\n" ++
   " \"rows\": [\n" ++ ",\n".intercalate (space.map row) ++ "\n]}\n")

#print axioms AbiLean.StructRetFragment.checkIntact_iff_intact
#print axioms AbiLean.StructRetFragment.intact_iff_sublayout
#print axioms AbiLean.StructRetFragment.reg_member_cls
#print axioms AbiLean.StructRetFragment.mem_structs
#print axioms AbiLean.StructRetFragment.mem_space
#print axioms AbiLean.StructRetFragment.space_nodup
#print axioms AbiLean.StructRetFragment.space_length
#print axioms AbiLean.StructRetFragment.decode_gen
#print axioms AbiLean.StructRetFragment.gen_decode
#print axioms AbiLean.StructRetFragment.generated_in_exactly_one
#print axioms AbiLean.StructRetFragment.intact_pairs
#print axioms AbiLean.StructRetFragment.check_pairs
