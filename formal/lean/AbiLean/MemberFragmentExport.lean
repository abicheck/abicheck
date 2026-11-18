/- Export the static ↔ non-static transition table (AbiLean.MemberFragment) as data:
     lake env lean AbiLean/MemberFragmentExport.lean   (writes member_fragment_cases.json)
   One row per transition of `transitions`, in the execution oracle's order
   (formal/experiments/member_fragment.py `mixed_cases`):
     [old kind, new kind, declared parameter types, call compatible]
   the same row format as formal/member_fragment_observed.json, which
   tests/test_formal_member_fragment.py compares with this file case by case.
   Not part of the library build. -/
import AbiLean.MemberFragment
open AbiLean.MemberFragment
open AbiLean.FuncFragment (Ty callCompatible)

def jStr (s : String) : String := "\"" ++ s ++ "\""
def jList (xs : List String) : String := "[" ++ ",".intercalate xs ++ "]"
def jBool (b : Bool) : String := if b then "true" else "false"

def tyC : Ty → String
  | .int => "int" | .long => "long" | .dbl => "double" | .ptr => "void *"

def kindC : MKind → String | .static => "static" | .inst => "instance"

def row (t : MKind × MKind × List Ty) : String :=
  jList [jStr (kindC t.1), jStr (kindC t.2.1), jList (t.2.2.map fun u => jStr (tyC u)),
    jBool (callCompatible (transition t.1 t.2.1 t.2.2))]

#eval IO.FS.writeFile "member_fragment_cases.json"
  ("{\"_source\": \"formal/lean/AbiLean/MemberFragmentExport.lean (AbiLean.MemberFragment: transitions, transition, FuncFragment.callCompatible)\",\n" ++
   " \"row_fields\": [\"old_kind\", \"new_kind\", \"params\", \"call_compatible\"],\n" ++
   " \"rows\": [\n" ++ ",\n".intercalate (transitions.map row) ++ "\n]}\n")

#print axioms AbiLean.MemberFragment.shiftLoc_inj
#print axioms AbiLean.MemberFragment.locAt_this
#print axioms AbiLean.MemberFragment.argLoc_this
#print axioms AbiLean.MemberFragment.this_loc
#print axioms AbiLean.MemberFragment.this_after_sret
#print axioms AbiLean.MemberFragment.member_call_compatible
#print axioms AbiLean.MemberFragment.member_argLoc_eq_iff
#print axioms AbiLean.MemberFragment.this_intact_iff
#print axioms AbiLean.MemberFragment.member_args_intact_iff
#print axioms AbiLean.MemberFragment.member_ret_call_compatible
#print axioms AbiLean.MemberFragment.static_to_instance_incompatible
#print axioms AbiLean.MemberFragment.instance_to_static_compatible_iff
#print axioms AbiLean.MemberFragment.transitions_length
#print axioms AbiLean.MemberFragment.compatible_transitions
