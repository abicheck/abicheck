/- Export the record-edit fragment (AbiLean.RecordEdit) as data:
     lake env lean --tstack=4000000 AbiLean/RecordEditFragmentExport.lean
   (writes record_edit_fragment_cases.json). One compact row per case of
   `space`, in `space` order:
     [base type indices, [op, a, b], [old offsets, old sizeof, old align],
      [new offsets, new sizeof, new align], class]
   op: 0 remove k (a = k), 1 swap i j, 2 replace k t (b = type index).
   One row per class: the oracle's findings (kind:member, record-level kind:)
   and verdict, and its representative (the first case of `space` in the
   class). `gen_fnv1a64` checksums the rendering of `gen c` for every case, so
   the C generator is checked against Lean's `gen` on all 9252 cases.
   Not part of the library build. -/
import AbiLean.RecordEditFragment
open AbiLean.RecordEdit

def jStr (s : String) : String := "\"" ++ s ++ "\""
def jList (xs : List String) : String := "[" ++ ",".intercalate xs ++ "]"
def jNats (xs : List Nat) : String := jList (xs.map toString)

namespace AbiLean.RecordEdit
def body (ds : List Decl) : String :=
  " ".intercalate (ds.map fun d => d.ty.cty ++ " f" ++ toString d.name ++ ";")

def Kind.c : Kind → String
  | .typeFieldRemoved => "type_field_removed"
  | .typeFieldTypeChanged => "type_field_type_changed"
  | .typeFieldOffsetChanged => "type_field_offset_changed"
  | .typeSizeChanged => "type_size_changed"
  | .typeAlignmentChanged => "type_alignment_changed"

def Finding.c (f : Finding) : String :=
  f.kind.c ++ ":" ++ match f.subj with | .member j => "f" ++ toString j | .record => ""

def Verdict.c : Verdict → String | .compatible => "compatible" | .breaking => "breaking"

def Edit.row : Edit → List Nat
  | .remove k => [0, k, 0]
  | .swap i j => [1, i, j]
  | .replace k t => [2, k, t.idx]
end AbiLean.RecordEdit

def obsKey (c : Case) : List String := (findings c).map Finding.c

def reps : List Case :=
  space.foldl (fun acc c => if acc.any (fun r => obsKey r == obsKey c) then acc else acc ++ [c]) []

def classIdx (c : Case) : Nat := (reps.findIdx? (fun r => obsKey r == obsKey c)).getD 0

def row (c : Case) : String :=
  jList [jNats (c.base.map Ty.idx), jNats c.edit.row,
    jList [jNats (oldOffsets c), toString (oldSize c), toString (oldAlign c)],
    jList [jNats (newOffsets c), toString (newSize c), toString (newAlign c)],
    toString (classIdx c)]

def rendered (c : Case) : String := body (gen c).old ++ "\n" ++ body (gen c).new ++ "\n"

def fnv1a64 (h : UInt64) (s : String) : UInt64 :=
  s.toUTF8.foldl (fun h b => (h ^^^ b.toUInt64) * 1099511628211) h

def genChecksum : UInt64 := space.foldl (fun h c => fnv1a64 h (rendered c)) 14695981039346656037

def classRow (i : Nat) (c : Case) : String :=
  "{" ++ ",".intercalate [
    "\"class\":" ++ toString i,
    "\"findings\":" ++ jList ((obsKey c).map jStr),
    "\"verdict\":" ++ jStr (verdict c).c,
    "\"rep\":" ++ toString ((space.findIdx? (· == c)).getD 0)] ++ "}"

#eval IO.FS.writeFile "record_edit_fragment_cases.json"
  ("{\"_source\": \"formal/lean/AbiLean/RecordEditFragmentExport.lean (AbiLean.RecordEdit: space, gen, oracle)\",\n" ++
   " \"space_size\": " ++ toString space.length ++ ",\n" ++
   " \"types\": [\"char\",\"short\",\"int\",\"double\",\"unsigned int\",\"float\"],\n" ++
   " \"row_fields\": [\"base\", \"edit\", \"old\", \"new\", \"class\"],\n" ++
   " \"gen_fnv1a64\": \"" ++ toString genChecksum ++ "\",\n" ++
   " \"classes\": [\n" ++ ",\n".intercalate ((reps.enum).map fun (i, c) => classRow i c) ++ "\n],\n" ++
   " \"rows\": [\n" ++ ",\n".intercalate (space.map row) ++ "\n]}\n")

#print axioms AbiLean.RecordEdit.mem_space
#print axioms AbiLean.RecordEdit.space_nodup
#print axioms AbiLean.RecordEdit.space_length
#print axioms AbiLean.RecordEdit.op_sizes
#print axioms AbiLean.RecordEdit.decode_gen
#print axioms AbiLean.RecordEdit.generated_in_exactly_one
#print axioms AbiLean.RecordEdit.every_case_generated
#print axioms AbiLean.RecordEdit.remove_prefix_not_moved
#print axioms AbiLean.RecordEdit.replace_prefix_not_moved
#print axioms AbiLean.RecordEdit.swap_align_unchanged
#print axioms AbiLean.RecordEdit.every_case_breaking
#print axioms AbiLean.RecordEdit.swap_moves_one
#print axioms AbiLean.RecordEdit.same_layout_retype_exists
