/- Export the layout class table for E3/E5/E6 (not part of the library build):
     lake env lean AbiLean/LayoutExport.lean   (writes layout_table.json) -/
import AbiLean.Layout
open AbiLean.Layout

def types : List FType := [tChar, tShort, tInt, tDouble]
def bases : List (List FType) :=
  (types.map fun a => [a]) ++
  (types.flatMap fun a => types.map fun b => [a, b]) ++
  (types.flatMap fun a => types.flatMap fun b => types.map fun c => [a, b, c])

def jList (xs : List String) : String := "[" ++ ", ".intercalate xs ++ "]"
def jStr (s : String) : String := "\"" ++ s ++ "\""
def jNats (xs : List Nat) : String := jList (xs.map toString)

def row (b : List FType) (k : Nat) (t : FType) : String :=
  let n := insertAt k t b
  "{" ++ ", ".intercalate [
    "\"base\": " ++ jList (b.map (jStr ·.name)),
    "\"insert_at\": " ++ toString k,
    "\"inserted\": " ++ jStr t.name,
    "\"old_offsets\": " ++ jNats (offsets b),
    "\"new_offsets\": " ++ jNats (offsets n),
    "\"old_size\": " ++ toString (AbiLean.Layout.sizeOf b),
    "\"new_size\": " ++ toString (AbiLean.Layout.sizeOf n)] ++ "}"

def rows : List String :=
  bases.flatMap fun b => (List.range (b.length + 1)).flatMap fun k => types.map fun t => row b k t

#eval IO.FS.writeFile "layout_table.json"
  ("{\"_source\": \"formal/lean/AbiLean/LayoutExport.lean (AbiLean.Layout.offsets/sizeOf)\",\n \"rows\": [\n  "
   ++ ",\n  ".intercalate rows ++ "\n]}\n")
