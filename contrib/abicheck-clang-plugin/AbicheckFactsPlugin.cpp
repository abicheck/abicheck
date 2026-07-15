// Copyright 2026 Nikolay Petrov
//
// Licensed under the Apache License, Version 2.0 (the "License").
//
// abicheck Clang facts plugin (ADR-035 D5, ADR-038 Plugin injection) — REFERENCE
// IMPLEMENTATION.
//
// During a normal compile this plugin emits abicheck's normalized Flow-2 source
// facts (source_facts/*.jsonl, schema =
// abicheck.buildsource.source_abi.SourceAbiTu) straight from the AST Clang
// already built, so no second front-end pass is needed. The output is the same
// `abicheck_inputs/` protocol abicheck ingests via `merge`.
//
// Reference recipe (ADR-038 C.2). Because the plugin reads the *clang* AST its
// reference is `abicheck/buildsource/source_extractors/clang.py`
// (`source_abi_from_clang_ast`) — NOT `base.py`, which is the castxml recipe.
//
// How subtree-hash parity is achieved (ADR-038 C.7 → now implemented). The
// hard part of clang.py is `_subtree_hash`: it hashes an alpha-renamed,
// commutative-normalized, build-root-stripped canonical form of clang's *JSON*
// AST. Rather than hand-reproduce clang's JSON (which drifts across LLVM majors
// and is enormous to mirror node-by-node), this plugin serializes the relevant
// subtree with clang's OWN JSON dumper in-process — `Decl::dump(os, false,
// ADOF_JSON)`, the exact code path `-ast-dump=json` uses — and then ports
// clang.py's `_alpha_rename_map` / `_canonical` / `_subtree_hash` (and
// `_expr_value` / `_default_arg_repr`) onto that JSON. Because the wrapper's
// clang backend consumes the *same* clang JSON, the hashes match by
// construction for a given clang version. Cross-version drift is caught by the
// C.6 differential-conformance gate, which now runs as a CI matrix over several
// clang versions (see `.github/workflows/clang-plugin.yml`). Producing both
// baselines of a comparison the same way (D0) keeps it correct in real use even
// where a floating-point literal's textual value is only reproduced
// best-effort (the one documented residual — see `pyFloat`).
//
// This plugin links against the *loading* clang's LLVM/Clang libraries and is
// therefore ABI-locked to its LLVM major (C.5). It stays `contrib/` reference
// and is not a required gate in abicheck's own CI.

#include "clang/AST/ASTConsumer.h"
#include "clang/AST/ASTContext.h"
#include "clang/AST/Decl.h"
#include "clang/AST/DeclCXX.h"
#include "clang/AST/DeclTemplate.h"
#include "clang/AST/Expr.h"
#include "clang/AST/ExprCXX.h"
#include "clang/AST/GlobalDecl.h"
#include "clang/AST/Mangle.h"
#include "clang/AST/PrettyPrinter.h"
#include "clang/AST/ASTDumperUtils.h"
#include "clang/AST/RecursiveASTVisitor.h"
#include "clang/Basic/FileManager.h"
#include "clang/Basic/LangStandard.h"
#include "clang/Basic/SourceManager.h"
#include "clang/Basic/TargetInfo.h"
#include "clang/Basic/TargetOptions.h"
#include "clang/Basic/Version.h"
#include "clang/Frontend/CompilerInstance.h"
#include "clang/Frontend/FrontendPluginRegistry.h"
#include "clang/Index/USRGeneration.h"
#include "clang/Lex/HeaderSearchOptions.h"
#include "clang/Lex/MacroInfo.h"
#include "clang/Lex/PPCallbacks.h"
#include "clang/Lex/Preprocessor.h"
#include "clang/Lex/PreprocessorOptions.h"

#include "llvm/ADT/ArrayRef.h"
#include "llvm/ADT/SmallString.h"
#include "llvm/ADT/StringExtras.h"
#include "llvm/ADT/StringMap.h"
#include "llvm/ADT/StringRef.h"
#include "llvm/Support/Error.h"
#include "llvm/Support/FileSystem.h"
#include "llvm/Support/Format.h"
#include "llvm/Support/JSON.h"
#include "llvm/Support/MemoryBuffer.h"
#include "llvm/Support/Path.h"
#include "llvm/Support/SHA256.h"
#include "llvm/Support/raw_ostream.h"

#include <algorithm>
#include <cctype>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <fstream>
#include <functional>
#include <initializer_list>
#include <map>
#include <memory>
#include <optional>
#include <regex>
#include <set>
#include <string>
#include <tuple>
#include <vector>

using namespace clang;

namespace {

// Producer id, recorded in the manifest's `created_by` and the TU `extractor`
// field. Bump on any change to the emitted-record recipe. 0.5: populate
// read_files (P1 #15-16) and source_edges (P1 #17-18) during the existing AST
// walk instead of reporting them `unsupported`.
constexpr const char *kPluginVersion = "0.5";

// ADR-038 C.8: the canonical fact-set identity every SourceAbiTu producer
// stamps (abicheck.buildsource.source_abi.SOURCE_ABI_FACT_SET_NAME/VERSION —
// keep these two literals in sync with that module). There is exactly one
// fact-set version this plugin can emit: it always collects the complete
// mandatory family list for that version. A build that wants LESS evidence
// simply does not load this plugin (ADR-038's own "optional, not modal"
// deployment story) — there is no in-plugin flag that narrows collection.
constexpr const char *kFactSetName = "abicheck-clang-canonical";
constexpr int kFactSetVersion = 1;

// ---------------------------------------------------------------------------
// Optional profiling (ABICHECK_PLUGIN_PROFILE=1): attribute subtree-hash cost
// to its phases (clang JSON dump / json::parse / canonicalize) so the hot loop
// can be optimized on evidence. Zero cost when disabled.
//
// P1 #24: by default the per-TU summary line goes to stderr (llvm::errs()),
// which for a build with many parallel compiles interleaves unreadably and
// gets swallowed by build systems that only surface stderr on failure. When
// ABICHECK_PLUGIN_PROFILE_LOG=<path> is also set, the line is appended to
// that file instead (one process-wide sink shared across the parallel TUs
// via O_APPEND, same "many writers, one file" pattern as the facts pack
// itself) — never written to *both*, so nothing about the emitted
// source_facts/*.jsonl content changes either way (P1 #25 invariance: the
// choice of profiling sink is execution policy, not decoded-fact content).
// ---------------------------------------------------------------------------
struct ProfileCounters {
  bool enabled = false;
  uint64_t dumpNs = 0, parseNs = 0, canonNs = 0;
  uint64_t dumpCalls = 0, canonCalls = 0;
  uint64_t dumpBytes = 0;
};
inline ProfileCounters &prof() {
  static ProfileCounters p = [] {
    ProfileCounters c;
    const char *e = std::getenv("ABICHECK_PLUGIN_PROFILE");
    c.enabled = e && *e && std::strcmp(e, "0") != 0;
    return c;
  }();
  return p;
}
inline const std::string &profileLogPath() {
  static std::string path = [] {
    const char *e = std::getenv("ABICHECK_PLUGIN_PROFILE_LOG");
    return e ? std::string(e) : std::string();
  }();
  return path;
}
// Append *line* (already newline-terminated) to the configured profiling
// channel: the log file when ABICHECK_PLUGIN_PROFILE_LOG is set, else stderr.
inline void emitProfileLine(const std::string &line) {
  const std::string &path = profileLogPath();
  if (path.empty()) {
    llvm::errs() << line;
    return;
  }
  std::ofstream log(path, std::ios::app);
  if (log)
    log << line;
  else
    llvm::errs() << line; // fall back rather than silently drop telemetry
}
using ProfClock = std::chrono::steady_clock;
inline uint64_t nowNs() {
  return std::chrono::duration_cast<std::chrono::nanoseconds>(
             ProfClock::now().time_since_epoch())
      .count();
}

// ---------------------------------------------------------------------------
// Hashing — mirrors clang.py::_hash exactly:
//   _hash(*parts) = "sha256:" + hex(sha256(parts joined by "\x00")).
// Any deviation changes identity()/*_hash and fails the C.6 gate (ADR-038 C.2).
// ---------------------------------------------------------------------------
std::string sha256Hex(llvm::StringRef data) {
  auto digest = llvm::SHA256::hash(
      llvm::ArrayRef<uint8_t>(reinterpret_cast<const uint8_t *>(data.data()),
                              data.size()));
  return llvm::toHex(llvm::ArrayRef<uint8_t>(digest), /*LowerCase=*/true);
}

std::string H(std::initializer_list<std::string> parts) {
  std::string blob;
  bool first = true;
  for (const std::string &p : parts) {
    if (!first)
      blob.push_back('\0');
    first = false;
    blob += p;
  }
  return "sha256:" + sha256Hex(blob);
}

// ---------------------------------------------------------------------------
// Minimal JSON emission with a fixed key order, for the emitted SourceEntity /
// SourceAbiTu records (matches SourceEntity.to_dict / SourceAbiTu.to_dict field
// order). Distinct from the *canonical* serializer below, which reproduces
// Python's json.dumps for hashing.
// ---------------------------------------------------------------------------
std::string jsonEscape(llvm::StringRef s) {
  std::string out;
  out.reserve(s.size() + 2);
  for (char c : s) {
    switch (c) {
    case '"':
      out += "\\\"";
      break;
    case '\\':
      out += "\\\\";
      break;
    case '\n':
      out += "\\n";
      break;
    case '\r':
      out += "\\r";
      break;
    case '\t':
      out += "\\t";
      break;
    case '\b':
      out += "\\b";
      break;
    case '\f':
      out += "\\f";
      break;
    default:
      if (static_cast<unsigned char>(c) < 0x20) {
        char buf[8];
        std::snprintf(buf, sizeof(buf), "\\u%04x", c & 0xff);
        out += buf;
      } else {
        out.push_back(c);
      }
    }
  }
  return out;
}

std::string jsonStr(llvm::StringRef s) { return "\"" + jsonEscape(s) + "\""; }

std::string jsonStrArray(const std::vector<std::string> &items) {
  std::string out = "[";
  for (size_t i = 0; i < items.size(); ++i) {
    if (i)
      out += ",";
    out += jsonStr(items[i]);
  }
  out += "]";
  return out;
}

std::string jsonRawArray(const std::vector<std::string> &items) {
  std::string out = "[";
  for (size_t i = 0; i < items.size(); ++i) {
    if (i)
      out += ",";
    out += items[i];
  }
  out += "]";
  return out;
}

std::string jsonStrMap(const std::map<std::string, std::string> &items) {
  std::string out = "{";
  bool first = true;
  for (const auto &kv : items) {
    if (!first)
      out += ",";
    first = false;
    out += jsonStr(kv.first) + ":" + jsonStr(kv.second);
  }
  out += "}";
  return out;
}

// ---------------------------------------------------------------------------
// ADR-038 C.8 coverage: one of complete/empty-confirmed/partial/unsupported/
// failed per fact family, mirroring
// abicheck.buildsource.source_abi.coverage_state_for_family()'s decision
// table exactly so every producer reports coverage the same way. This is
// NOT a user-selectable mode: every family is always attempted; the state
// only records what happened.
std::string familyCoverageState(bool entitiesPresent, bool diagnosticsSeen,
                                 bool unsupported = false) {
  if (unsupported)
    return "unsupported";
  if (diagnosticsSeen)
    return entitiesPresent ? "partial" : "failed";
  return entitiesPresent ? "complete" : "empty-confirmed";
}

// Whether any diagnostic in `diags` contains one of `substrs` — the plugin's
// existing per-family "JSON dump failed" diagnostics (emitted where an
// individual declaration's dump/canonicalization failed, see
// VisitFunctionDecl/emitConstexprVar/emitDataVariable/emitType/
// VisitTypedefNameDecl/emitTemplate/emitClassTemplateMemberPatterns) double
// as the coverage signal, so no new state has to be threaded through the
// visitor. Every post-classification early return that can drop an
// otherwise-accessible entity should insert one of these -- a return with no
// diagnostic is invisible to family coverage (PR4 audit).
bool anyDiagContains(const std::set<std::string> &diags,
                     std::initializer_list<const char *> substrs) {
  for (const std::string &d : diags)
    for (const char *s : substrs)
      if (d.find(s) != std::string::npos)
        return true;
  return false;
}

// ===========================================================================
// Canonical serializer + subtree hashing — a faithful C++ port of clang.py's
// `_hash("clang-ast", json.dumps(_canonical(node, amap), sort_keys=True))`.
// Operates on `llvm::json::Value` parsed from clang's own JSON dumper, so the
// input is byte-identical to what clang.py consumes for the same clang version.
// ===========================================================================
using llvm::json::Array;
using llvm::json::Object;
using llvm::json::Value;

// Append the \uXXXX escape (lowercase hex) for a single BMP code unit, matching
// the CPython json encoder's ensure_ascii output.
void appendJsonU(std::string &out, uint32_t cp) {
  char b[8];
  std::snprintf(b, sizeof(b), "\\u%04x", cp);
  out += b;
}

// json.dumps escaping of one ASCII byte (c < 0x80): the six short escapes, a
// \uXXXX for the remaining control chars, else the literal byte. Returns false
// for a non-ASCII lead byte (c >= 0x80), which the caller decodes as UTF-8.
bool appendPyStrAscii(std::string &out, unsigned char c) {
  switch (c) {
  case '"': out += "\\\""; return true;
  case '\\': out += "\\\\"; return true;
  case '\n': out += "\\n"; return true;
  case '\r': out += "\\r"; return true;
  case '\t': out += "\\t"; return true;
  case '\b': out += "\\b"; return true;
  case '\f': out += "\\f"; return true;
  default: break;
  }
  if (c < 0x20) { appendJsonU(out, c); return true; }
  if (c < 0x80) { out.push_back(static_cast<char>(c)); return true; }
  return false;
}

// Decode the UTF-8 sequence starting at s[i]; on success set cp (the code point)
// and len (bytes consumed) and return true. Returns false for an invalid lead
// byte, a truncated sequence, or a bad continuation byte, leaving the caller to
// emit the raw byte as a \uXX escape.
bool decodeUtf8(llvm::StringRef s, size_t i, uint32_t &cp, int &len) {
  unsigned char c = static_cast<unsigned char>(s[i]);
  int extra = 0;
  if ((c & 0xE0) == 0xC0) { cp = c & 0x1F; extra = 1; }
  else if ((c & 0xF0) == 0xE0) { cp = c & 0x0F; extra = 2; }
  else if ((c & 0xF8) == 0xF0) { cp = c & 0x07; extra = 3; }
  else return false;
  if (i + extra >= s.size()) return false;
  for (int k = 1; k <= extra; k++) {
    unsigned char cc = static_cast<unsigned char>(s[i + k]);
    if ((cc & 0xC0) != 0x80) return false;
    cp = (cp << 6) | (cc & 0x3F);
  }
  len = extra + 1;
  return true;
}

// Emit a decoded code point as ensure_ascii \uXXXX escapes, using a UTF-16
// surrogate pair for astral (> 0xFFFF) code points, matching CPython's encoder.
void appendPyStrCodepoint(std::string &out, uint32_t cp) {
  if (cp <= 0xFFFF) {
    appendJsonU(out, cp);
  } else {
    cp -= 0x10000;
    appendJsonU(out, 0xD800 + (cp >> 10));
    appendJsonU(out, 0xDC00 + (cp & 0x3FF));
  }
}

// json.dumps of a Python str: quoted, ensure_ascii=True (non-ASCII → \uXXXX,
// with surrogate pairs for astral code points), lowercase hex — matching the
// CPython json encoder defaults clang.py relies on.
std::string pyStr(llvm::StringRef s) {
  std::string out = "\"";
  size_t i = 0, n = s.size();
  while (i < n) {
    unsigned char c = static_cast<unsigned char>(s[i]);
    if (appendPyStrAscii(out, c)) { i++; continue; }
    // Decode a UTF-8 sequence to a code point and emit \uXXXX (ensure_ascii).
    uint32_t cp = 0;
    int len = 0;
    if (!decodeUtf8(s, i, cp, len)) { appendJsonU(out, c); i++; continue; }
    appendPyStrCodepoint(out, cp);
    i += len;
  }
  out += "\"";
  return out;
}

// Best-effort Python repr() of a float. Exact byte-parity with CPython's
// shortest-round-trip formatting is the one documented residual (ADR-038 C.7):
// a floating-point literal appearing *inside* a hashed subtree may serialize
// differently here. It is still self-consistent within this producer, so under
// D0 (both baselines produced the same way) it never yields a false finding;
// only the cross-producer C.6 gate can surface it.
std::string pyFloat(double d) {
  char buf[64];
  for (int prec = 1; prec <= 17; ++prec) {
    std::snprintf(buf, sizeof(buf), "%.*g", prec, d);
    if (std::strtod(buf, nullptr) == d)
      break;
  }
  std::string s = buf;
  if (s.find('.') == std::string::npos && s.find('e') == std::string::npos &&
      s.find('n') == std::string::npos && s.find('i') == std::string::npos)
    s += ".0";
  return s;
}

std::string pyDumps(const Value &v); // recursive; used by the helpers below

// Serialize an already-serialized {key: value} map as json.dumps would with
// sort_keys=True and the default (', ', ': ') separators. The map is std::map,
// so its keys are already in sorted (ASCII) order. Shared by pyDumps's object
// case and the canonical serializer.
std::string joinSortedObject(const std::map<std::string, std::string> &kv) {
  std::string out = "{";
  bool first = true;
  for (auto &e : kv) {
    if (!first) out += ", ";
    first = false;
    out += pyStr(e.first) + ": " + e.second;
  }
  return out + "}";
}

// json.dumps of a JSON number: an int verbatim, else a uint64 above
// int64_t::max verbatim (getAsInteger rejects it), else a float via pyFloat.
std::string pyDumpsNumber(const Value &v) {
  if (auto i = v.getAsInteger())
    return std::to_string(*i);
  if (auto u = v.getAsUINT64())
    return std::to_string(*u);
  return pyFloat(*v.getAsNumber());
}

std::string pyDumpsArray(const Array &a) {
  std::string out = "[";
  bool first = true;
  for (const Value &e : a) {
    if (!first) out += ", ";
    first = false;
    out += pyDumps(e);
  }
  return out + "]";
}

std::string pyDumpsObject(const Object &obj) {
  std::map<std::string, std::string> kv;
  for (const auto &kvp : obj)
    kv[llvm::StringRef(kvp.first).str()] = pyDumps(kvp.second);
  return joinSortedObject(kv);
}

// json.dumps of an arbitrary JSON value with sort_keys=True and the default
// (', ', ': ') separators — used to copy a scalar `value` verbatim inside the
// canonical form.
std::string pyDumps(const Value &v) {
  switch (v.kind()) {
  case Value::Null:
    return "null";
  case Value::Boolean:
    return *v.getAsBoolean() ? "true" : "false";
  case Value::Number:
    return pyDumpsNumber(v);
  case Value::String:
    return pyStr(*v.getAsString());
  case Value::Array:
    return pyDumpsArray(*v.getAsArray());
  case Value::Object:
    return pyDumpsObject(*v.getAsObject());
  }
  return "null";
}

// Python str(x) semantics for a JSON `value` used by _expr_value: a string is
// its raw characters (no quotes), a bool is "True"/"False", an int its digits.
std::string pyStrOfValue(const Value &v) {
  switch (v.kind()) {
  case Value::Null:
    return "None";
  case Value::Boolean:
    return *v.getAsBoolean() ? "True" : "False";
  case Value::Number:
    if (auto i = v.getAsInteger())
      return std::to_string(*i);
    if (auto u = v.getAsUINT64())
      return std::to_string(*u);
    return pyFloat(*v.getAsNumber());
  case Value::String:
    return v.getAsString()->str();
  default:
    return pyDumps(v);
  }
}

bool isRenameableLocal(const Object &o) {
  auto kind = o.getString("kind");
  if (!kind)
    return false;
  if (*kind == "ParmVarDecl" || *kind == "BindingDecl" ||
      *kind == "DecompositionDecl")
    return true;
  if (*kind == "VarDecl") {
    if (auto sc = o.getString("storageClass"))
      if (*sc == "static" || *sc == "extern")
        return false;
    return true;
  }
  return false;
}

// Port of clang.py::_alpha_rename_map: map each local-binding clang id to a
// positional placeholder ($0, $1, …) by first occurrence (params first).
llvm::StringMap<std::string> alphaRenameMap(const Value &node,
                                            llvm::ArrayRef<std::string> paramIds) {
  std::set<std::string> localIds;
  for (const std::string &p : paramIds)
    if (!p.empty())
      localIds.insert(p);

  std::function<void(const Value &)> collect = [&](const Value &n) {
    const Object *o = n.getAsObject();
    if (!o)
      return;
    if (auto id = o->getString("id"))
      if (isRenameableLocal(*o))
        localIds.insert(id->str());
    if (const Array *inner = o->getArray("inner"))
      for (const Value &c : *inner)
        collect(c);
  };
  collect(node);
  llvm::StringMap<std::string> amap;
  if (localIds.empty())
    return amap;

  std::vector<std::string> order;
  std::set<std::string> seen;
  for (const std::string &p : paramIds)
    if (localIds.count(p) && !seen.count(p)) {
      seen.insert(p);
      order.push_back(p);
    }
  std::function<void(const Value &)> walk = [&](const Value &n) {
    const Object *o = n.getAsObject();
    if (!o)
      return;
    if (auto id = o->getString("id"))
      if (localIds.count(id->str()) && !seen.count(id->str())) {
        seen.insert(id->str());
        order.push_back(id->str());
      }
    if (const Object *ref = o->getObject("referencedDecl"))
      if (auto rid = ref->getString("id"))
        if (localIds.count(rid->str()) && !seen.count(rid->str())) {
          seen.insert(rid->str());
          order.push_back(rid->str());
        }
    if (const Array *inner = o->getArray("inner"))
      for (const Value &c : *inner)
        walk(c);
  };
  walk(node);
  for (size_t i = 0; i < order.size(); ++i)
    amap[order[i]] = "$" + std::to_string(i);
  return amap;
}

const std::set<std::string> &commutativeOps() {
  static const std::set<std::string> ops = {"+", "*", "==", "!=", "&", "|", "^"};
  return ops;
}

std::string canonical(const Value &node,
                      const llvm::StringMap<std::string> &amap); // recursive

// The node's own alpha-rename placeholder ($0, $1, …), if its `id` is a renamed
// local binding; std::nullopt otherwise.
std::optional<std::string>
canonicalPlaceholder(const Object &o, const llvm::StringMap<std::string> &amap) {
  if (auto id = o.getString("id")) {
    auto it = amap.find(*id);
    if (it != amap.end())
      return it->second;
  }
  return std::nullopt;
}

// The scalar/copy-through keys of a canonical node — kind/name/value/opcode/
// castKind (with `name` replaced by the node's rename placeholder when it is a
// renamed local) plus the flattened type.qualType.
void canonicalScalarKeys(const Object &o,
                         const std::optional<std::string> &placeholder,
                         std::map<std::string, std::string> &kv) {
  for (const char *key : {"kind", "name", "value", "opcode", "castKind"}) {
    if (const Value *v = o.get(key)) {
      if (std::strcmp(key, "name") == 0 && placeholder)
        kv[key] = pyStr(*placeholder);
      else
        kv[key] = pyDumps(*v);
    }
  }
  if (const Object *t = o.getObject("type"))
    if (auto q = t->getString("qualType"))
      kv["type"] = pyStr(*q);
}

// The already-serialized "ref" value for a node's referencedDecl: its
// alpha-rename placeholder when the referenced id is a renamed local, else the
// referenced decl's own (non-empty) name. std::nullopt when there is no ref.
std::optional<std::string>
canonicalRef(const Object &o, const llvm::StringMap<std::string> &amap) {
  const Object *ref = o.getObject("referencedDecl");
  if (!ref)
    return std::nullopt;
  if (auto rid = ref->getString("id")) {
    auto it = amap.find(*rid);
    if (it != amap.end())
      return pyStr(it->second);
  }
  if (auto rn = ref->getString("name"); rn && !rn->empty())
    return pyStr(*rn);
  return std::nullopt;
}

// The serialized `inner` array of a canonical node: each child canonicalized,
// with the two operands of a commutative binary operator sorted by their
// canonical serialization so operand order does not affect the hash.
std::string canonicalInner(const Object &o, const Array &inner,
                           const llvm::StringMap<std::string> &amap) {
  std::vector<std::string> children;
  children.reserve(inner.size());
  for (const Value &c : inner)
    children.push_back(canonical(c, amap));
  auto kind = o.getString("kind");
  auto op = o.getString("opcode");
  if (kind && *kind == "BinaryOperator" && op &&
      commutativeOps().count(op->str()) && children.size() == 2)
    std::sort(children.begin(), children.end());
  std::string arr = "[";
  for (size_t i = 0; i < children.size(); ++i) {
    if (i) arr += ", ";
    arr += children[i];
  }
  arr += "]";
  return arr;
}

// Port of clang.py::_canonical → json.dumps(..., sort_keys=True) as one string.
std::string canonical(const Value &node,
                      const llvm::StringMap<std::string> &amap) {
  const Object *o = node.getAsObject();
  if (!o)
    return pyDumps(node);

  std::map<std::string, std::string> kv; // std::map keeps keys sorted (ASCII)
  std::optional<std::string> placeholder = canonicalPlaceholder(*o, amap);
  canonicalScalarKeys(*o, placeholder, kv);
  if (std::optional<std::string> ref = canonicalRef(*o, amap))
    kv["ref"] = *ref;
  if (const Array *inner = o->getArray("inner"))
    kv["inner"] = canonicalInner(*o, *inner, amap);
  return joinSortedObject(kv);
}

std::string subtreeHash(const Value &node, llvm::ArrayRef<std::string> paramIds) {
  const bool p = prof().enabled;
  uint64_t t0 = p ? nowNs() : 0;
  auto amap = alphaRenameMap(node, paramIds);
  std::string c = canonical(node, amap);
  if (p) {
    prof().canonNs += nowNs() - t0;
    prof().canonCalls++;
  }
  return H({"clang-ast", c});
}

// clang.py::_WRAPPER_EXPR_KINDS — single-child wrappers descended through when
// deciding whether an initializer is a lone literal.
bool isWrapperExpr(llvm::StringRef k) {
  return k == "ImplicitCastExpr" || k == "CStyleCastExpr" ||
         k == "CXXStaticCastExpr" || k == "ConstantExpr" ||
         k == "ExprWithCleanups" || k == "ParenExpr" ||
         k == "CXXFunctionalCastExpr" || k == "MaterializeTemporaryExpr";
}

bool isLiteralKind(llvm::StringRef k) {
  return k == "IntegerLiteral" || k == "FloatingLiteral" ||
         k == "CharacterLiteral" || k == "StringLiteral" ||
         k == "CXXBoolLiteralExpr" || k == "FixedPointLiteral";
}

bool declLikeKind(llvm::StringRef k) {
  return k.ends_with("Decl") || k.ends_with("Attr") || k.ends_with("Comment");
}

// Port of clang.py::_unwrap_expr.
const Value *unwrapExprJson(const Value *node) {
  const Value *cur = node;
  while (cur) {
    const Object *o = cur->getAsObject();
    if (!o)
      break;
    auto kind = o->getString("kind");
    if (!kind || !isWrapperExpr(*kind))
      break;
    const Array *inner = o->getArray("inner");
    if (!inner)
      break;
    std::vector<const Value *> dicts;
    for (const Value &c : *inner)
      if (c.getAsObject())
        dicts.push_back(&c);
    if (dicts.size() != 1)
      break;
    cur = dicts[0];
  }
  return cur;
}

// Port of clang.py::_init_expr: the last child that is not a decl/attr/comment.
const Value *initExprJson(const Object &node) {
  const Array *inner = node.getArray("inner");
  if (!inner)
    return nullptr;
  const Value *last = nullptr;
  for (const Value &c : *inner) {
    const Object *co = c.getAsObject();
    if (!co)
      continue;
    auto kind = co->getString("kind");
    if (kind && declLikeKind(*kind))
      continue;
    last = &c;
  }
  return last;
}

// Port of clang.py::_expr_value.
std::string exprValueJson(const Value &node) {
  const Value *core = unwrapExprJson(&node);
  if (core) {
    const Object *o = core->getAsObject();
    if (o) {
      auto kind = o->getString("kind");
      const Value *val = o->get("value");
      if (kind && isLiteralKind(*kind) && val)
        return pyStrOfValue(*val);
    }
  }
  return subtreeHash(node, {});
}

// Port of clang.py::_default_arg_repr over the FunctionDecl JSON.
std::string defaultArgReprJson(const Object &fnNode) {
  const Array *inner = fnNode.getArray("inner");
  if (!inner)
    return "";
  std::string out;
  bool first = true;
  int position = -1;
  for (const Value &c : *inner) {
    const Object *co = c.getAsObject();
    if (!co)
      continue;
    auto kind = co->getString("kind");
    if (!kind || *kind != "ParmVarDecl")
      continue;
    ++position;
    const Value *init = initExprJson(*co);
    // clang.py: `if not child.get("init") and init is None: continue`. clang's
    // JSON marks a defaulted parameter with a truthy "init" string (e.g. "c").
    bool hasInitFlag = false;
    if (const Value *f = co->get("init")) {
      if (auto s = f->getAsString())
        hasInitFlag = !s->empty();
      else if (auto b = f->getAsBoolean())
        hasInitFlag = *b;
      else
        hasInitFlag = f->kind() != Value::Null;
    }
    if (!hasInitFlag && init == nullptr)
      continue;
    std::string rep = init ? exprValueJson(*init) : "default";
    if (!first)
      out += ",";
    first = false;
    out += "p" + std::to_string(position) + "=" + rep;
  }
  return out;
}

// Port of clang.py::_param_ids: the FunctionDecl's ParmVarDecl child ids.
std::vector<std::string> paramIdsJson(const Object &fnNode) {
  std::vector<std::string> ids;
  if (const Array *inner = fnNode.getArray("inner"))
    for (const Value &c : *inner) {
      const Object *co = c.getAsObject();
      if (!co)
        continue;
      auto kind = co->getString("kind");
      if (kind && *kind == "ParmVarDecl")
        if (auto id = co->getString("id"))
          ids.push_back(id->str());
    }
  return ids;
}

// The CompoundStmt body child of a FunctionDecl JSON node, or nullptr.
const Value *bodyStmtJson(const Object &fnNode) {
  if (const Array *inner = fnNode.getArray("inner"))
    for (const Value &c : *inner) {
      const Object *co = c.getAsObject();
      if (co)
        if (auto kind = co->getString("kind"); kind && *kind == "CompoundStmt")
          return &c;
    }
  return nullptr;
}

// clang.py::_mangled over JSON: take the node's mangledName; if it equals the
// plain (unqualified) name — extern "C", some ctors — leave it empty so
// identity() falls back to qualified_name#signature_hash.
std::string mangledFromJson(const Object &o) {
  auto m = o.getString("mangledName");
  auto n = o.getString("name");
  if (m && !m->empty() && (!n || *m != *n))
    return m->str();
  return "";
}

// Skip Itanium CV-qualifier / ref-qualifier prefixes (`r`/`V`/`K`/`O`) that may
// follow a nested-name `N`, returning the index of the first non-qualifier char.
size_t skipItaniumCvQualifiers(const std::string &m, size_t i, size_t n) {
  while (i < n && (m[i] == 'r' || m[i] == 'V' || m[i] == 'K' || m[i] == 'O'))
    ++i;
  return i;
}

// Advance past a `<length><source-name>` component whose first digit is at `i`,
// returning the index just past the name, or `std::string::npos` when the length
// prefix overruns the remaining string (an exotic/truncated production → bail).
size_t advancePastLengthComponent(const std::string &m, size_t i, size_t n) {
  size_t j = i, length = 0;
  while (j < n && m[j] >= '0' && m[j] <= '9') {
    length = length * 10 + static_cast<size_t>(m[j] - '0');
    ++j;
    if (length > n - j)
      return std::string::npos;
  }
  return j + length;
}

// Port of clang.py::_mangled_has_internal_linkage: true when an Itanium mangled
// name marks internal linkage (its own <source-name> is prefixed with the
// GCC/clang seniority marker `L`, e.g. `_ZN2nsL7g_constE`, `_ZL1xE`). Parses by
// length prefixes so an `L` inside a source name is never miscounted, and bails
// to false (external, keep) on any exotic production. Must stay byte-identical to
// the Python port so both producers drop the same set (C.6 gate).
bool mangledHasInternalLinkage(const std::string &m) {
  if (m.rfind("_Z", 0) != 0)
    return false;
  // Anonymous namespace (`namespace { ... }` -> `_ZN12_GLOBAL__N_1...`): internal
  // linkage with no `L` marker. The component is compiler-reserved, so a plain
  // substring test is unambiguous (Codex review).
  if (m.find("_GLOBAL__N_") != std::string::npos)
    return true;
  size_t i = 2, n = m.size();
  if (i < n && m[i] == 'N') {
    ++i;
    i = skipItaniumCvQualifiers(m, i, n);
  }
  while (i < n) {
    char c = m[i];
    if (c == 'E')
      break;
    if (c == 'L')
      return i + 1 < n && m[i + 1] >= '0' && m[i + 1] <= '9';
    if (c >= '0' && c <= '9') {
      size_t next = advancePastLengthComponent(m, i, n);
      if (next == std::string::npos)
        return false;
      i = next;
      continue;
    }
    return false;
  }
  return false;
}

// Port of clang.py::_is_top_level_const: true when a type spelling is
// const-qualified at the top level (`const int`, `int *const`, `ns::Foo const`),
// which at namespace scope without `extern` gives internal linkage in C++.
// Pointer/reference-to-const (`const char *`) is NOT top-level const.
bool isTopLevelConst(const std::string &qual) {
  auto trim = [](std::string s) {
    size_t b = s.find_first_not_of(" \t");
    size_t e = s.find_last_not_of(" \t");
    return (b == std::string::npos) ? std::string() : s.substr(b, e - b + 1);
  };
  std::string q = trim(qual);
  while (!q.empty() && q.back() == '&')
    q.pop_back();
  q = trim(q);
  // Array: const iff the element type (spelled before the first `[`) is const.
  size_t bracket = q.find('[');
  if (bracket != std::string::npos)
    q = trim(q.substr(0, bracket));
  const std::string kConst = "const";
  // Trailing `const` must be a standalone token, not the tail of an identifier
  // (a type named `almost_const` is not const-qualified): the char before it is
  // a non-identifier char (space / `*`), or the string is exactly `const`.
  if (q.size() >= kConst.size() &&
      q.compare(q.size() - kConst.size(), kConst.size(), kConst) == 0) {
    if (q.size() == kConst.size())
      return true;
    char prev = q[q.size() - kConst.size() - 1];
    if (!(std::isalnum(static_cast<unsigned char>(prev)) || prev == '_'))
      return true;
  }
  return q.rfind("const ", 0) == 0 && q.find('*') == std::string::npos;
}

// clang.py::_signature over JSON: the node's type.qualType.
std::string qualTypeFromJson(const Object &o) {
  if (const Object *t = o.getObject("type"))
    if (auto q = t->getString("qualType"))
      return q->str();
  return "";
}

// clang.py::_signature_desugared over JSON: the node's type.desugaredQualType
// (the alias-resolved spelling, e.g. `const int` for `using CI = const int`).
std::string desugaredQualTypeFromJson(const Object &o) {
  if (const Object *t = o.getObject("type"))
    if (auto q = t->getString("desugaredQualType"))
      return q->str();
  return "";
}

// ---------------------------------------------------------------------------
// Pruned JSON parse (perf, C.2-preserving).
//
// clang's `-ast-dump=json` emits full location/range/type/flag detail for every
// node, but the only keys ANY reader in this file touches are a fixed, small set
// (verified exhaustively): the recursive `inner` array; the scalars `kind`, `id`,
// `name`, `storageClass`, `opcode`, `mangledName`, `castKind`, `value`, `init`;
// and the *shallow* objects `type` (→ `qualType`/`desugaredQualType`) and
// `referencedDecl` (→ `id`/`name`). Everything else is parsed and then discarded
// by `canonical()` — pure waste that dominated the profile (json::parse ≈ 68% of
// subtree-hash time, on ~200 MB of dumped text per TU).
//
// This parser walks only the STRUCTURE (objects/arrays) and delegates every kept
// LEAF token to `llvm::json::parse`, so scalar/escape/number semantics are
// byte-identical to a full parse — the produced Value is indistinguishable to
// every reader, and therefore every emitted hash is unchanged (validated by
// output-identity against the full-parse build). Skipped values are scanned but
// never materialized, so the discarded ~90% costs only a linear character skip.
// ---------------------------------------------------------------------------
class PrunedJsonParser {
public:
  explicit PrunedJsonParser(llvm::StringRef s) : S(s), I(0) {}

  std::optional<Value> parse() {
    skipWs();
    if (I >= S.size() || S[I] != '{')
      return std::nullopt; // a Decl dump root is always an object
    std::optional<Value> v = parseNode();
    if (!v)
      return std::nullopt;
    return v;
  }

private:
  llvm::StringRef S;
  size_t I;
  bool Failed = false;

  void skipWs() {
    while (I < S.size()) {
      char c = S[I];
      if (c == ' ' || c == '\t' || c == '\n' || c == '\r')
        ++I;
      else
        break;
    }
  }

  // Scan (and consume) exactly one JSON value, returning its raw [start,end)
  // slice. Handles nested objects/arrays and string escapes. On malformation it
  // sets Failed and returns an empty ref.
  llvm::StringRef captureValue() {
    skipWs();
    size_t start = I;
    if (I >= S.size()) {
      Failed = true;
      return {};
    }
    char c = S[I];
    if (c == '"') {
      scanString();
    } else if (c == '{' || c == '[') {
      scanBalanced();
    } else {
      scanScalar();
    }
    return S.substr(start, I - start);
  }

  // At '{' or '[': consume through the matching close, honoring string escapes so
  // a brace/bracket inside a string never shifts the nesting depth.
  void scanBalanced() {
    char open = S[I], close = (open == '{') ? '}' : ']';
    int depth = 0;
    while (I < S.size()) {
      char d = S[I];
      if (d == '"') {
        scanString();
        continue;
      }
      if (d == open)
        ++depth;
      else if (d == close) {
        --depth;
        if (depth == 0) {
          ++I;
          break;
        }
      }
      ++I;
    }
  }

  // Scalar: number / true / false / null — consumed up to a delimiter.
  void scanScalar() {
    while (I < S.size()) {
      char d = S[I];
      if (d == ',' || d == '}' || d == ']' || d == ' ' || d == '\t' ||
          d == '\n' || d == '\r')
        break;
      ++I;
    }
  }

  void scanString() {
    // assumes S[I] == '"'; consumes through the closing quote
    ++I;
    while (I < S.size()) {
      char c = S[I++];
      if (c == '\\') {
        if (I < S.size())
          ++I; // skip escaped char (\uXXXX's trailing hex are skipped by loop)
      } else if (c == '"') {
        return;
      }
    }
    Failed = true;
  }

  // Parse a leaf token slice into a Value with full json semantics.
  std::optional<Value> leaf(llvm::StringRef raw) {
    auto parsed = llvm::json::parse(raw);
    if (!parsed) {
      llvm::consumeError(parsed.takeError());
      return std::nullopt;
    }
    return std::move(*parsed);
  }

  // At '{': build an Object keeping only the pruned key set; `recurseInner`
  // controls whether `inner` children are parsed as full nodes (true for AST
  // nodes) or skipped (for the shallow `type`/`referencedDecl` objects, which no
  // reader recurses into).
  std::optional<Value> parseObject(bool recurseInner,
                                   bool shallowScalarsOnly) {
    Object out;
    ++I; // consume '{'
    skipWs();
    if (I < S.size() && S[I] == '}') {
      ++I;
      return Value(std::move(out));
    }
    while (I < S.size()) {
      skipWs();
      if (S[I] != '"') {
        Failed = true;
        return std::nullopt;
      }
      llvm::StringRef keyRaw = captureValueString();
      auto keyV = leaf(keyRaw);
      if (Failed || !keyV || !keyV->getAsString()) {
        Failed = true;
        return std::nullopt;
      }
      std::string key = keyV->getAsString()->str();
      skipWs();
      if (I >= S.size() || S[I] != ':') {
        Failed = true;
        return std::nullopt;
      }
      ++I; // consume ':'
      storeMember(out, key, recurseInner, shallowScalarsOnly);
      if (Failed)
        return std::nullopt;
      skipWs();
      if (I < S.size() && S[I] == ',') {
        ++I;
        continue;
      }
      if (I < S.size() && S[I] == '}') {
        ++I;
        break;
      }
      Failed = true;
      return std::nullopt;
    }
    return Value(std::move(out));
  }

  llvm::StringRef captureValueString() {
    skipWs();
    size_t start = I;
    scanString();
    return S.substr(start, I - start);
  }

  // The `inner` child array of an AST node: parse each object element as a full
  // node (keeping the pruned key set), skipping non-object elements. `recurseInner`
  // false (shallow `type`/`referencedDecl` objects) skips the whole value.
  void storeInnerMember(Object &out, bool recurseInner) {
    if (!recurseInner) {
      captureValue(); // shallow objects never recurse into inner
      return;
    }
    skipWs();
    if (I >= S.size() || S[I] != '[') {
      captureValue();
      return;
    }
    Array arr;
    ++I; // consume '['
    skipWs();
    if (I < S.size() && S[I] == ']') {
      ++I;
      out["inner"] = Value(std::move(arr));
      return;
    }
    while (I < S.size()) {
      skipWs();
      if (S[I] == '{') {
        if (auto node = parseNode())
          arr.push_back(std::move(*node));
        else if (Failed)
          return;
      } else {
        captureValue(); // non-object array element: irrelevant, skip
      }
      skipWs();
      if (I < S.size() && S[I] == ',') {
        ++I;
        continue;
      }
      if (I < S.size() && S[I] == ']') {
        ++I;
        break;
      }
      Failed = true;
      return;
    }
    out["inner"] = Value(std::move(arr));
  }

  void storeMember(Object &out, const std::string &key, bool recurseInner,
                   bool shallowScalarsOnly) {
    // Keys read as recursive structure.
    if (key == "inner") {
      storeInnerMember(out, recurseInner);
      return;
    }
    // Shallow objects: `type` and `referencedDecl` — keep only their scalars.
    if (!shallowScalarsOnly && (key == "type" || key == "referencedDecl")) {
      skipWs();
      if (I < S.size() && S[I] == '{') {
        if (auto obj = parseObject(/*recurseInner=*/false,
                                   /*shallowScalarsOnly=*/true))
          out[key] = std::move(*obj);
      } else {
        captureValue();
      }
      return;
    }
    // Kept scalar keys (both on AST nodes and inside shallow objects).
    static const std::set<std::string> kKeptScalars = {
        "kind",       "id",       "name",     "storageClass", "opcode",
        "mangledName", "castKind", "value",    "init",         "qualType",
        "desugaredQualType"};
    if (kKeptScalars.count(key)) {
      llvm::StringRef raw = captureValue();
      if (Failed)
        return;
      if (auto v = leaf(raw))
        out[key] = std::move(*v);
      return;
    }
    // Everything else: skip without materializing.
    captureValue();
  }

  std::optional<Value> parseNode() {
    return parseObject(/*recurseInner=*/true, /*shallowScalarsOnly=*/false);
  }
};

// Dump a Decl to clang's JSON (identical to -ast-dump=json for that node) and
// parse it, KEEPING ONLY the keys any reader uses (see PrunedJsonParser).
// Returns nullopt on any dump/parse failure (best-effort, C.3).
std::optional<Value> dumpDeclJson(const Decl *d) {
  std::string buf;
  llvm::raw_string_ostream os(buf);
  const bool p = prof().enabled;
  uint64_t t0 = p ? nowNs() : 0;
  d->dump(os, /*Deserialize=*/false, ADOF_JSON);
  os.flush();
  if (p) {
    uint64_t t1 = nowNs();
    prof().dumpNs += t1 - t0;
    prof().dumpCalls++;
    prof().dumpBytes += buf.size();
  }
  uint64_t t1 = p ? nowNs() : 0;
  std::optional<Value> parsed = PrunedJsonParser(buf).parse();
  if (p)
    prof().parseNs += nowNs() - t1;
  return parsed;
}

// clang.py::_emit_type skips a type node whose JSON dump carries no `inner`
// array — a forward declaration, or an empty enum with no enumerators. (An
// empty struct still has implicit members in the JSON, so it is kept, mirroring
// the backend.) Returns true when the node should be emitted. An absent dump
// (json == nullopt) is treated as emittable — best-effort: type_hash then falls
// back to empty and the caller records a diagnostic.
bool jsonTypeHasMembers(const std::optional<Value> &json) {
  if (!json)
    return true;
  const Object *o = json->getAsObject();
  const Array *inner = o ? o->getArray("inner") : nullptr;
  return inner && !inner->empty();
}

// ---------------------------------------------------------------------------
// Emitted SourceEntity (ADR-030 D4).
// ---------------------------------------------------------------------------
struct Entity {
  std::string id;
  std::string kind;
  std::string qualified_name;
  std::string mangled_name;
  std::string signature_hash;
  std::string body_hash;
  std::string type_hash;
  std::string value;
  std::map<std::string, std::string> names;
  std::map<std::string, std::string> relations;
  std::map<std::string, std::string> ownership;
  std::string loc_path;
  int loc_line = 0;
  std::string loc_origin = "UNKNOWN";
  std::string visibility = "unknown";
  bool api_relevant = true;

  std::string to_json() const {
    std::string loc = "{\"path\":" + jsonStr(loc_path) +
                      ",\"line\":" + std::to_string(loc_line) +
                      ",\"origin\":" + jsonStr(loc_origin) + "}";
    return "{\"id\":" + jsonStr(id) + ",\"kind\":" + jsonStr(kind) +
           ",\"qualified_name\":" + jsonStr(qualified_name) +
           ",\"mangled_name\":" + jsonStr(mangled_name) +
           ",\"signature_hash\":" + jsonStr(signature_hash) +
           ",\"body_hash\":" + jsonStr(body_hash) +
           ",\"type_hash\":" + jsonStr(type_hash) +
           ",\"value\":" + jsonStr(value) +
           ",\"names\":" + jsonStrMap(names) +
           ",\"relations\":" + jsonStrMap(relations) +
           ",\"ownership\":" + jsonStrMap(ownership) +
           ",\"source_location\":" + loc +
           ",\"visibility\":" + jsonStr(visibility) +
           ",\"api_relevant\":" + (api_relevant ? "true" : "false") +
           ",\"confidence\":\"high\"}";
  }
};

// ---------------------------------------------------------------------------
// Emitted source-graph edge (ADR-031 D2, P1 #17-18). Shape mirrors
// buildsource.source_graph.GraphEdge.to_dict() (edge/src/dst/provenance/
// confidence/attrs) so a future fold step can ingest these directly. Captured
// during the SAME AST walk FactsVisitor already runs for entities — no second
// frontend pass.
// ---------------------------------------------------------------------------
struct SourceEdge {
  std::string kind; // one of DECL_CALLS_DECL/DECL_REFERENCES_DECL/
                    // DECL_HAS_TYPE/TYPE_HAS_FIELD_TYPE/TYPE_INHERITS
  std::string src;
  std::string dst;
  std::string confidence = "high";
  std::map<std::string, std::string> attrs;

  std::string to_json() const {
    return "{\"edge\":" + jsonStr(kind) + ",\"src\":" + jsonStr(src) +
           ",\"dst\":" + jsonStr(dst) +
           ",\"provenance\":\"clang-plugin-inline\",\"confidence\":" +
           jsonStr(confidence) + ",\"attrs\":" + jsonStrMap(attrs) + "}";
  }
};

// Build the qualified name the way clang.py does: join only the *named*
// enclosing namespace and record/tag scopes (its _SCOPE_NODE_KINDS, which
// does NOT include functions), then the decl's own simple name. This
// deliberately differs from getQualifiedNameAsString(), which prepends
// function scopes ("n::f()::Local") for a body-local type and
// "(anonymous namespace)::" for an unnamed-namespace decl — spellings
// clang.py's scope stack never produces. Since qualified_name feeds the
// entity id (types/typedefs/constexpr/templates) and identity(), matching
// clang.py here keeps ids equal to the clang backend for those cases rather
// than reading as simultaneous add+remove (Codex review).
//
// A free function (not a FactsVisitor member) because it touches no visitor
// state — CallRefVisitor's per-function-body edge sub-walk reuses it too
// (P1 #17-18), so the two visitors stay on one qualified-name convention
// instead of drifting apart (Codex review would otherwise flag the
// duplicate).
std::string scopedName(const NamedDecl *d) {
  std::vector<std::string> scopes;
  // Walk the LEXICAL context chain, not the semantic one: clang.py builds the
  // scope from JSON AST nesting (where the decl is written), so an out-of-line
  // qualified definition (`int n::f(){}` written at TU scope, esp. extern "C"
  // where the mangled name is suppressed and identity falls back to the
  // qualified name) is named `f`, not `n::f`. getLexicalDeclContext mirrors
  // that; for the common in-place declaration lexical == semantic (Codex review).
  for (const DeclContext *dc = d->getLexicalDeclContext(); dc;
       dc = dc->getLexicalParent()) {
    if (const auto *ns = dyn_cast<NamespaceDecl>(dc)) {
      if (!ns->isAnonymousNamespace() && !ns->getName().empty())
        scopes.push_back(ns->getNameAsString());
    } else if (const auto *rd = dyn_cast<RecordDecl>(dc)) {
      // clang.py's JSON kind for an explicit/partial class-template
      // specialization is ClassTemplateSpecializationDecl /
      // ClassTemplatePartialSpecializationDecl, which are NOT in
      // _SCOPE_NODE_KINDS, so it never adds them as a scope segment. They
      // derive from CXXRecordDecl, so exclude them explicitly — otherwise a
      // type nested in `template<> struct S<int>{ struct N{}; }` would be
      // named `ns::S::N` here but `ns::N` by the backend (Codex/review).
      if (!isa<ClassTemplateSpecializationDecl>(rd) && !rd->getName().empty())
        scopes.push_back(rd->getNameAsString());
    }
    // FunctionDecl / LinkageSpecDecl / the TU contribute nothing to the
    // name, exactly like clang.py's _child_scope.
  }
  std::string out;
  for (auto it = scopes.rbegin(); it != scopes.rend(); ++it)
    out += *it + "::";
  out += d->getNameAsString();
  return out;
}

// scopedName() alone collapses distinct overloads (and any same-named decl in
// the same scope that differs only by signature) onto one string; used as a
// source_edges endpoint identity, that lets the (kind, src, dst) dedup in
// addEdge() conflate e.g. `foo(int)` and `foo(double)` into a single node, so
// an edge belonging to one overload can read as belonging to the other. This
// mirrors the mangled-or-scoped-name `key` scheme entity ids already use
// (emitFunctionFacts's `key = mangled.empty() ? name : mangled`) — same
// identity scheme this decl's own `key` would get if FactsVisitor visited it.
//
// This is NOT full parity with the Python clang.py/call_graph.py extractor's
// `_identity()` (also mangled-name-or-name): that extractor resolves a call's
// callee from `-ast-dump=json`'s compact `referencedDecl` stub, which clang
// never populates with `mangledName` (verified empirically — only a
// fully-dumped decl node carries it), so Flow B's call edges stay name-only
// for an overloaded callee regardless of this fix. Operating on the live
// `FunctionDecl*` instead of clang's JSON text, this plugin (Flow C) can do
// better here than Flow B currently does; a future fold step that merges
// call-graph nodes across both producers by identity string would need to
// account for that asymmetry (out of scope for this pass).
//
// Constructors/destructors are excluded and fall back to scopedName():
// clang::GlobalDecl's single-FunctionDecl constructor asserts on a bare
// ctor/dtor (it requires an explicit CXXCtorType/CXXDtorType, and a single
// declaration mangles to multiple ABI symbols — complete/base/deleting
// object), so disambiguating those would need call-site-specific variant
// selection this pass does not attempt; overload collisions among a class's
// own constructors remain a known imprecision (CodeRabbit review, P2).
std::string mangledOrScopedName(MangleContext *mc, const NamedDecl *d) {
  if (mc) {
    if (const auto *fd = dyn_cast_or_null<FunctionDecl>(d)) {
      if (!isa<CXXConstructorDecl>(fd) && !isa<CXXDestructorDecl>(fd) &&
          mc->shouldMangleDeclName(fd)) {
        std::string buf;
        llvm::raw_string_ostream os(buf);
        mc->mangleName(GlobalDecl(fd), os);
        os.flush();
        if (!buf.empty())
          return buf;
      }
    }
  }
  return scopedName(d);
}

// ---------------------------------------------------------------------------
// Small per-function-body sub-walk collecting DECL_CALLS_DECL/
// DECL_REFERENCES_DECL edge targets (P1 #17-18). Scoped to one FunctionDecl's
// body so it needs no "current enclosing function" state threaded through the
// main FactsVisitor traversal — FactsVisitor invokes one of these per public
// function body it already visits, so this is still the SAME AST walk, not a
// second frontend pass.
// ---------------------------------------------------------------------------
class CallRefVisitor : public RecursiveASTVisitor<CallRefVisitor> {
public:
  // (target decl, resolution is "virtual-overapprox" when true). Keeps the
  // raw decl rather than a pre-computed scopedName() string so the caller can
  // resolve it through mangledOrScopedName() — this visitor has no
  // MangleContext of its own (it is instantiated fresh per function body with
  // no ASTContext threaded in), and the target AST outlives this sub-walk.
  std::vector<std::pair<const FunctionDecl *, bool>> Calls;
  std::vector<std::string> References;

  bool shouldVisitImplicitCode() const { return false; }
  bool shouldVisitTemplateInstantiations() const { return false; }

  bool VisitCallExpr(CallExpr *ce) {
    const FunctionDecl *callee = ce->getDirectCallee();
    if (!callee || callee->getNameAsString().empty())
      return true; // function-pointer/unresolved call: unknown static target
    bool isVirtual = false;
    if (isa<CXXMemberCallExpr>(ce)) {
      if (const auto *method = dyn_cast<CXXMethodDecl>(callee))
        isVirtual = method->isVirtual();
    }
    Calls.emplace_back(callee, isVirtual);
    return true;
  }

  bool VisitDeclRefExpr(DeclRefExpr *dre) {
    const ValueDecl *vd = dre->getDecl();
    if (const auto *varD = dyn_cast<VarDecl>(vd)) {
      // Locals/parameters are not ABI-relevant dependency targets; keep only
      // references to namespace/file-scope or static-member data, mirroring
      // the plugin's own public-variable scope (emitDataVariable).
      if (varD->isLocalVarDeclOrParm() || varD->getNameAsString().empty())
        return true;
      References.push_back(scopedName(varD));
    } else if (const auto *ecd = dyn_cast<EnumConstantDecl>(vd)) {
      if (!ecd->getNameAsString().empty())
        References.push_back(scopedName(ecd));
    }
    return true;
  }
};

std::string usrForDecl(const Decl *d) {
  if (!d)
    return "";
  llvm::SmallString<128> usr;
  if (clang::index::generateUSRForDecl(d, usr))
    return "";
  return std::string(usr.str());
}

std::string ownershipRole(llvm::StringRef visibility) {
  if (visibility == "public_header")
    return "own_api_candidate";
  if (visibility == "generated")
    return "generated_api_candidate";
  if (visibility == "system_header")
    return "dependency_candidate";
  if (visibility == "private_header")
    return "internal_candidate";
  return "unknown";
}

void stampDeclEvidence(Entity &e, const NamedDecl *d) {
  e.names["source_qualified"] = e.qualified_name;
  if (!e.mangled_name.empty())
    e.names["mangled"] = e.mangled_name;
  std::string usr = usrForDecl(d);
  if (!usr.empty())
    e.names["usr"] = usr;
  std::string canonical = usrForDecl(d ? d->getCanonicalDecl() : nullptr);
  if (!canonical.empty())
    e.names["canonical_usr"] = canonical;
  e.ownership["visibility"] = e.visibility;
  e.ownership["origin"] = e.loc_origin;
  e.ownership["role"] = ownershipRole(e.visibility);
}

// ---------------------------------------------------------------------------
// Path helpers for public-surface classification (ADR-038 C.2 visibility).
// ---------------------------------------------------------------------------
std::vector<std::string> pathSegments(llvm::StringRef p) {
  std::vector<std::string> segs;
  std::string cur;
  for (char c : p) {
    if (c == '/' || c == '\\') {
      if (!cur.empty() && cur != ".")
        segs.push_back(cur);
      cur.clear();
    } else {
      cur.push_back(c);
    }
  }
  if (!cur.empty() && cur != ".")
    segs.push_back(cur);
  return segs;
}

bool isContiguousSubsequence(const std::vector<std::string> &hay,
                             const std::vector<std::string> &needle) {
  if (needle.empty() || needle.size() > hay.size())
    return false;
  for (size_t i = 0; i + needle.size() <= hay.size(); ++i) {
    bool ok = true;
    for (size_t j = 0; j < needle.size(); ++j)
      if (hay[i + j] != needle[j]) {
        ok = false;
        break;
      }
    if (ok)
      return true;
  }
  return false;
}

// Absolute-normalized path segments: resolve `p` against the compile's CWD (the
// same base a relative `-I` resolves against) before segmenting.
std::vector<std::string> absSegments(llvm::StringRef p) {
  llvm::SmallString<256> abs(p);
  llvm::sys::fs::make_absolute(abs);
  // Collapse `.`/`..` lexically: an out-of-source build compiling with
  // `-I../include` reports headers as `../include/foo.hpp`, which make_absolute
  // turns into `/repo/build/../include/foo.hpp`; without this the retained `..`
  // segment stops the `/repo/include` root from matching (Codex review).
  llvm::sys::path::remove_dots(abs, /*remove_dot_dot=*/true);
  return pathSegments(abs);
}

// Whether `file` sits under any of `roots`, matching either the raw spellings
// OR their absolute-normalized forms. Without the absolute fallback an absolute
// public-root (e.g. `$PWD/include`) never matches a relative header spelling
// (`include/foo.hpp`) — the root's segments are longer — so every decl from that
// root would be silently dropped and the pack look empty (Codex review).
bool pathUnderAnyRoot(llvm::StringRef file, const std::vector<std::string> &roots) {
  std::vector<std::string> fileSegs = pathSegments(file);
  std::vector<std::string> fileAbs;
  bool haveAbs = false;
  for (const std::string &root : roots) {
    if (isContiguousSubsequence(fileSegs, pathSegments(root)))
      return true;
    if (!haveAbs) {
      fileAbs = absSegments(file);
      haveAbs = true;
    }
    if (isContiguousSubsequence(fileAbs, absSegments(root)))
      return true;
  }
  return false;
}

// Auto-derive public roots from the compile's user include search paths when the
// operator passed no explicit `public-roots=` (ADR-038 Plugin injection, Caveat A). The
// `-I` (Angled) and `-iquote` (Quoted) directories are exactly where a project's
// own public headers resolve, so treating them as roots turns the common "forgot
// public-roots → silently empty pack" trap into a populated (if slightly broad)
// public surface. System / compiler-builtin entries (`-isystem`, the resource
// dir, the sysroot) are excluded so libstdc++ / SDK headers do not flood the
// surface. Returns absolute-normalized directories, de-duplicated, in search
// order. The operator can always pass an explicit `public-roots=` to scope the
// surface precisely; inference only runs when they passed none.
// Whether `path` is at or below directory `prefix` (both absolute-normalized),
// checked at a path-component boundary so `/home/proj` does not "contain"
// `/home/project2`.
bool pathIsUnder(llvm::StringRef path, llvm::StringRef prefix) {
  if (prefix.empty() || !path.starts_with(prefix))
    return false;
  return path.size() == prefix.size() ||
         llvm::sys::path::is_separator(path[prefix.size()]);
}

std::vector<std::string> deriveRootsFromIncludes(const HeaderSearchOptions &hso) {
  std::vector<std::string> roots;
  std::set<std::string> seen;
  // Restrict inference to PROJECT-LOCAL include dirs: an absolute `-I` outside the
  // compile's working directory (`/opt/boost/include`, `/usr/include/eigen3`) is a
  // third-party dependency whose headers must not flood the public surface, so
  // only dirs at/below the build cwd are inferred (Codex review). In-tree includes
  // (`-Iinclude`, `./gen`, an absolute path under the build/source tree) are kept.
  // An out-of-source layout that puts headers elsewhere infers nothing here and
  // gets the "pass public-roots=" diagnostic instead of a Boost-flooded pack.
  llvm::SmallString<256> cwd;
  bool haveCwd = !llvm::sys::fs::current_path(cwd);
  if (haveCwd)
    llvm::sys::path::remove_dots(cwd, /*remove_dot_dot=*/true);
  for (const auto &e : hso.UserEntries) {
    if (e.IsFramework)
      continue;
    if (e.Group != frontend::Angled && e.Group != frontend::Quoted)
      continue;
    if (e.Path.empty())
      continue;
    llvm::SmallString<256> abs(e.Path);
    llvm::sys::fs::make_absolute(abs);
    llvm::sys::path::remove_dots(abs, /*remove_dot_dot=*/true);
    if (haveCwd && !pathIsUnder(abs, cwd))
      continue;
    std::string s(abs.str());
    if (seen.insert(s).second)
      roots.push_back(s);
  }
  return roots;
}

// Whether `file` names a C/C++ translation-unit source (not a header). Used to
// tell an ordinary internal .cpp decl (expected to be non-public) from a decl in
// a *header* that fell outside the public roots (the public-roots-misconfigured
// signal, ADR-038 Plugin injection, Caveat A).
bool isSourceFileName(llvm::StringRef file) {
  llvm::StringRef ext = llvm::sys::path::extension(file);
  return ext.equals_insensitive(".c") || ext.equals_insensitive(".cc") ||
         ext.equals_insensitive(".cpp") || ext.equals_insensitive(".cxx") ||
         ext.equals_insensitive(".c++") || ext.equals_insensitive(".cp") ||
         ext == ".C";
}

// Whether a header path looks machine-generated — a faithful port of
// provenance._is_generated_header (a `generated`/`gen`/… directory segment, or
// a moc_/ui_/qrc_/protobuf/flatbuffers/gRPC basename). The clang backend keeps
// such a public header on the surface but marks it GENERATED/generated
// (ADR-030 generated_header_changed); the plugin must mirror that for C.6.
bool isGeneratedHeaderPath(llvm::StringRef file) {
  std::vector<std::string> segs = pathSegments(file);
  if (segs.empty())
    return false;
  static const std::set<std::string> genDirs = {
      "generated", "_generated", ".generated", "gen", "autogen"};
  for (size_t i = 0; i + 1 < segs.size(); ++i)
    if (genDirs.count(segs[i]))
      return true;
  static const std::regex genBase(
      R"(^moc_.*\.(h|hpp|cpp|cc)$|^ui_.*\.h$|^qrc_.*\.(cpp|cc)$|.*\.pb\.(h|cc)$|.*_generated\.h$|.*\.grpc\.pb\.(h|cc)$)");
  return std::regex_match(segs.back(), genBase);
}

// The (origin, visibility) labels for a file already known to be on the public
// surface: GENERATED for a generated public header, else PUBLIC_HEADER — matching
// clang.py::_ClassifyContext.classify.
void publicSurfaceLabels(llvm::StringRef file, std::string &origin,
                         std::string &visibility) {
  if (isGeneratedHeaderPath(file)) {
    origin = "GENERATED";
    visibility = "generated";
  } else {
    origin = "PUBLIC_HEADER";
    visibility = "public_header";
  }
}

std::string collapseWhitespace(llvm::StringRef s) {
  std::string out;
  bool inWs = false;
  for (char c : s) {
    if (c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\f' ||
        c == '\v') {
      inWs = true;
      continue;
    }
    if (inWs && !out.empty())
      out.push_back(' ');
    inWs = false;
    out.push_back(c);
  }
  return out;
}

std::string upperStrippedStem(llvm::StringRef file) {
  llvm::StringRef base = llvm::sys::path::filename(file);
  std::string stem;
  for (char c : base) {
    if (std::isalnum(static_cast<unsigned char>(c)))
      stem.push_back(
          static_cast<char>(std::toupper(static_cast<unsigned char>(c))));
    else
      stem.push_back('_');
  }
  std::string collapsed;
  bool underscore = false;
  for (char c : stem) {
    if (c == '_') {
      underscore = true;
      continue;
    }
    if (underscore && !collapsed.empty())
      collapsed.push_back('_');
    underscore = false;
    collapsed.push_back(c);
  }
  return collapsed;
}

std::string macroGuardToken(llvm::StringRef text) {
  std::string raw;
  for (char c : text) {
    if (std::isalnum(static_cast<unsigned char>(c)))
      raw.push_back(
          static_cast<char>(std::toupper(static_cast<unsigned char>(c))));
    else
      raw.push_back('_');
  }
  std::string out;
  bool underscore = true;
  for (char c : raw) {
    if (c == '_') {
      if (!underscore)
        out.push_back('_');
      underscore = true;
      continue;
    }
    out.push_back(c);
    underscore = false;
  }
  while (!out.empty() && out.back() == '_')
    out.pop_back();
  return out;
}

bool looksLikeIncludeGuard(llvm::StringRef name, llvm::StringRef file) {
  std::string macro = macroGuardToken(name);
  std::string stem = macroGuardToken(llvm::sys::path::stem(file));
  if (macro.empty() || stem.empty())
    return false;
  if (macro == stem)
    return true;
  size_t pos = macro.rfind(stem);
  if (pos == std::string::npos)
    return false;
  llvm::StringRef tail(macro.data() + pos + stem.size(),
                       macro.size() - pos - stem.size());
  return tail == "_H" || tail == "_HH" || tail == "_HPP" ||
         tail == "_HXX";
}

// Strips `/* ... */` block and `//` line comments so a leading file-header
// comment (near-universal above a guard) doesn't masquerade as "code before
// the guard" to isStructuralIncludeGuard's scan. Best-effort, not a real
// lexer: doesn't account for string/char literals containing comment-like
// text, which essentially never appears ahead of a file's own include guard.
std::string stripCComments(llvm::StringRef text) {
  std::string out;
  out.reserve(text.size());
  for (size_t i = 0; i < text.size();) {
    if (text[i] == '/' && i + 1 < text.size() && text[i + 1] == '*') {
      size_t end = text.find("*/", i + 2);
      i = (end == llvm::StringRef::npos) ? text.size() : end + 2;
      continue;
    }
    if (text[i] == '/' && i + 1 < text.size() && text[i + 1] == '/') {
      size_t end = text.find('\n', i + 2);
      i = (end == llvm::StringRef::npos) ? text.size() : end; // keep the '\n'
      continue;
    }
    out.push_back(text[i]);
    ++i;
  }
  return out;
}

// Structural fallback for a project-prefixed guard (e.g. `MYLIB_FOO_H`,
// `CASE47_V1_HPP`) that doesn't derive from the filename at all, so
// looksLikeIncludeGuard's spelling-only heuristic misses it. Mirrors
// clang.py's `_include_guard_macro` / `_is_include_guard` structural
// fallback: read the file directly and check whether `name` is genuinely
// its leading `#ifndef`/`#define` pair — the classic whole-file guard idiom
// — regardless of naming convention. A leading `#pragma once` is neutral and
// skipped. Best-effort: any read failure or non-matching shape returns false
// (never turns a real macro into a false suppression).
bool isStructuralIncludeGuard(llvm::StringRef name, llvm::StringRef file) {
  if (file.empty())
    return false;
  auto bufOrErr = llvm::MemoryBuffer::getFile(file);
  if (!bufOrErr)
    return false;
  std::string stripped = stripCComments((*bufOrErr)->getBuffer());
  llvm::StringRef text(stripped);
  llvm::SmallVector<llvm::StringRef, 8> lines;
  text.split(lines, '\n');
  std::string guard;
  bool sawIfndef = false;
  for (llvm::StringRef raw : lines) {
    llvm::StringRef line = raw.trim();
    if (line.empty())
      continue;
    if (!line.starts_with("#"))
      return false; // code before any directive → not a whole-file guard
    if (!sawIfndef) {
      if (line == "#pragma once")
        continue; // neutral — keep probing for a classic guard after it
      if (!line.consume_front("#ifndef"))
        return false;
      guard = line.trim().str();
      if (guard.empty())
        return false;
      sawIfndef = true;
      continue;
    }
    if (!line.consume_front("#define"))
      return false;
    return line.trim().str() == guard && guard == name.str();
  }
  return false;
}

std::string joinStrings(const std::vector<std::string> &v, char sep) {
  std::string out;
  for (size_t i = 0; i < v.size(); ++i) {
    if (i)
      out.push_back(sep);
    out += v[i];
  }
  return out;
}

std::string templateParamName(const NamedDecl *param, unsigned index) {
  if (!param->getName().empty())
    return param->getNameAsString();
  if (isa<NonTypeTemplateParmDecl>(param))
    return "N" + std::to_string(index);
  return "T" + std::to_string(index);
}

std::vector<std::string> templateParamNames(const TemplateParameterList *params) {
  std::vector<std::string> out;
  if (!params)
    return out;
  for (unsigned i = 0; i < params->size(); ++i)
    out.push_back(templateParamName(params->getParam(i), i));
  return out;
}

std::string nowIso8601Utc() {
  std::time_t t = std::time(nullptr);
  std::tm tm{};
#if defined(_WIN32)
  gmtime_s(&tm, &t);
#else
  gmtime_r(&t, &tm);
#endif
  char buf[32];
  std::strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%S+00:00", &tm);
  return buf;
}

// ---------------------------------------------------------------------------
// Macro capture via in-compile PPCallbacks (ADR-038 C.2). No second `-E -dD`
// pass. Values are token-reconstructed and whitespace-collapsed to match
// clang.py::macros_from_preprocessor's "{params} {body}" normalization.
// ---------------------------------------------------------------------------
struct MacroRecord {
  std::string value;
  std::string file;
};

class MacroCollector : public PPCallbacks {
public:
  MacroCollector(Preprocessor &pp,
                 std::shared_ptr<std::map<std::string, MacroRecord>> out)
      : PP(pp), SM(pp.getSourceManager()), Defs(std::move(out)) {}

  void MacroDefined(const Token &nameTok, const MacroDirective *md) override {
    const MacroInfo *mi = md ? md->getMacroInfo() : nullptr;
    const IdentifierInfo *ii = nameTok.getIdentifierInfo();
    if (!mi || !ii)
      return;
    const std::string name = ii->getName().str();
    // Determine whether this — the current (final so far) definition — is on the
    // library's public surface. A non-public (re)definition must ERASE any prior
    // public entry, not merely be skipped: clang's `-E -dD` backend tracks the
    // final definition and then filters by its defining file, so a system header
    // redefining a public macro (without an intervening #undef) drops it. Leaving
    // the stale public value here would emit a phantom public macro.
    // (stdlib/toolchain headers are never public; the path-only isPublicFile
    // check downstream would otherwise accept /usr/include/... via its `include`
    // segment, so the SourceManager::isInSystemHeader guard belongs here.)
    SourceLocation loc = mi->getDefinitionLoc();
    bool nonPublic = mi->isBuiltinMacro() || loc.isInvalid() ||
                     SM.isInSystemHeader(loc);
    std::string file;
    if (!nonPublic) {
      // Physical file, ignoring `#line` (UseLineDirectives=false), to match the
      // decl classifier and the clang backend (Codex review).
      PresumedLoc pl = SM.getPresumedLoc(loc, /*UseLineDirectives=*/false);
      if (pl.isInvalid() || llvm::StringRef(pl.getFilename()).empty() ||
          llvm::StringRef(pl.getFilename()).starts_with("<"))
        nonPublic = true;
      else
        file = pl.getFilename();
    }
    if (nonPublic) {
      Defs->erase(name);
      return;
    }
    (*Defs)[name] = MacroRecord{macroValue(mi), file};
  }

  void MacroUndefined(const Token &nameTok, const MacroDefinition &,
                      const MacroDirective *) override {
    if (const IdentifierInfo *ii = nameTok.getIdentifierInfo())
      Defs->erase(ii->getName().str());
  }

private:
  std::string macroValue(const MacroInfo *mi) {
    std::string params;
    if (mi->isFunctionLike()) {
      params += "(";
      auto pl = mi->params();
      for (unsigned i = 0; i < pl.size(); ++i) {
        if (i)
          params += ",";
        const IdentifierInfo *pi = pl[i];
        if (pi->getName() == "__VA_ARGS__") {
          params += "..."; // C99 variadic
        } else {
          params += pi->getName().str();
          // GNU named variadic (`#define LOG(fmt, args...)`): the ellipsis rides
          // the last parameter, so append it to keep the call contract distinct.
          if (mi->isGNUVarargs() && i + 1 == pl.size())
            params += "...";
        }
      }
      params += ")";
    }
    std::string body;
    for (const Token &t : mi->tokens()) {
      if (!body.empty())
        body += " ";
      body += PP.getSpelling(t);
    }
    return collapseWhitespace(params + " " + body);
  }

  Preprocessor &PP;
  SourceManager &SM;
  std::shared_ptr<std::map<std::string, MacroRecord>> Defs;
};

// ---------------------------------------------------------------------------
// The AST visitor: maps public Decls -> SourceEntity records (ADR-038 C.2).
// ---------------------------------------------------------------------------
class FactsVisitor : public RecursiveASTVisitor<FactsVisitor> {
public:
  FactsVisitor(ASTContext &ctx, const std::vector<std::string> &roots,
               std::vector<Entity> &functions, std::vector<Entity> &variables,
               std::vector<Entity> &types, std::vector<Entity> &templates,
               std::vector<Entity> &inlineBodies,
               std::vector<Entity> &constexprValues, std::set<std::string> &diags,
               std::vector<SourceEdge> &edges, bool inferredRoots = false)
      : SM(ctx.getSourceManager()), PP(ctx.getPrintingPolicy()), Roots(roots),
        Functions(functions), Variables(variables), Types(types),
        Templates(templates), InlineBodies(inlineBodies),
        ConstexprValues(constexprValues), Diags(diags), Edges(edges),
        InferredRoots(inferredRoots), Mangler(ctx.createMangleContext()) {}

  bool shouldVisitTemplateInstantiations() const { return false; }
  bool shouldVisitImplicitCode() const { return false; }

  bool VisitFunctionDecl(FunctionDecl *fd) {
    if (fd->isImplicit() || fd->getDescribedFunctionTemplate())
      return true;
    // Explicit specializations (`template<> int id<int>(int)`) are real callable
    // decls the clang backend emits (it stops only at the FunctionTemplateDecl
    // pattern), so do NOT skip TK_FunctionTemplateSpecialization; implicit
    // instantiations are already excluded by shouldVisitTemplateInstantiations().
    if (!isAccessible(fd) || fd->getNameAsString().empty())
      return true;
    std::string file, origin, visibility;
    if (!classify(fd, file, origin, visibility))
      return true;

    // Read signature + mangled name from clang's own JSON (as clang.py does),
    // so id/signature_hash match the wrapper's clang backend exactly.
    std::optional<Value> json = dumpDeclJson(fd);
    if (!json || !json->getAsObject()) {
      Diags.insert("function facts unavailable (JSON dump failed)");
      return true;
    }
    const Object &o = *json->getAsObject();
    std::string name = scopedName(fd);
    std::string sig = qualTypeFromJson(o);
    std::string mangled = mangledFromJson(o);
    std::string key = mangled.empty() ? name : mangled;
    int line = presumedLine(fd);

    Entity e;
    e.id = H({"function", key, sig});
    e.kind = "function";
    e.qualified_name = name;
    e.mangled_name = mangled;
    e.signature_hash = H({"sig", sig});
    e.value = defaultArgReprJson(o);
    e.loc_path = file;
    e.loc_line = line;
    e.loc_origin = origin;
    e.visibility = visibility;
    stampDeclEvidence(e, fd);
    Functions.push_back(e);

    // P1 #17-18: source-graph edges captured in the SAME AST walk, no second
    // frontend pass. DECL_HAS_TYPE for the return type and each parameter;
    // DECL_CALLS_DECL/DECL_REFERENCES_DECL from a bounded sub-walk of this
    // function's own body (CallRefVisitor). Edges are keyed by `key`
    // (mangled-or-scoped-name), not the bare `name`/scopedName() — two
    // overloads share one `name`, and using it as the edge src would let the
    // (kind, src, dst) dedup conflate `foo(int)`'s edges with `foo(double)`'s
    // (CodeRabbit review, P2; see mangledOrScopedName()).
    addEdge("DECL_HAS_TYPE", key, fd->getReturnType().getAsString(PP), "high",
            {{"role", "return"}});
    for (const ParmVarDecl *p : fd->parameters()) {
      std::string ptype = p->getType().getAsString(PP);
      if (!ptype.empty())
        addEdge("DECL_HAS_TYPE", key, ptype, "high", {{"role", "param"}});
    }
    if (Stmt *body = fd->getBody()) {
      CallRefVisitor crv;
      crv.TraverseStmt(body);
      for (const auto &call : crv.Calls)
        addEdge("DECL_CALLS_DECL", key,
                mangledOrScopedName(Mangler.get(), call.first),
                call.second ? "reduced" : "high",
                {{"call_kind", call.second ? "virtual" : "direct"},
                 {"resolution", call.second ? "overapprox" : "exact"}});
      for (const std::string &ref : crv.References)
        addEdge("DECL_REFERENCES_DECL", key, ref, "reduced", {{"role", "ref"}});
    }

    // Gate the whole inline entity on a CompoundStmt being present in the
    // dumped JSON (clang.py::_has_body), NOT on the AST body predicate. Because
    // this runs post-codegen (AddAfterMainAction), an implicit/defaulted
    // special member that codegen defined reports hasBody()==true while its
    // JSON dump carries no ordinary CompoundStmt; keying on the predicate would
    // emit an `inline` entity (with an empty body_hash) that the wrapper's
    // -fsyntax-only pass never produces (Codex/review).
    if (const Value *body = bodyStmtJson(o)) {
      Entity ib;
      ib.id = H({"inline", key, sig});
      ib.kind = "inline";
      ib.qualified_name = name;
      ib.mangled_name = mangled;
      ib.signature_hash = H({"sig", sig});
      ib.body_hash = subtreeHash(*body, paramIdsJson(o));
      ib.loc_path = file;
      ib.loc_line = line;
      ib.loc_origin = origin;
      ib.visibility = visibility;
      stampDeclEvidence(ib, fd);
      InlineBodies.push_back(ib);
    }
    return true;
  }

  bool VisitCXXRecordDecl(CXXRecordDecl *rd) {
    if (rd->isImplicit() || rd->getDescribedClassTemplate())
      return true;
    if (isa<ClassTemplateSpecializationDecl>(rd))
      return true;
    if (!rd->isThisDeclarationADefinition())
      return true;
    std::string name = scopedName(rd);
    if (!emitType(rd, name, "record"))
      return true;
    // P1 #17-18: TYPE_INHERITS / TYPE_HAS_FIELD_TYPE, same AST walk. Only for
    // a record whose type entity was itself just emitted (public, non-empty
    // JSON dump) — bounds edge capture to exactly the public surface
    // FactsVisitor already walks.
    for (const CXXBaseSpecifier &base : rd->bases()) {
      std::string baseName = base.getType().getAsString(PP);
      if (!baseName.empty())
        addEdge("TYPE_INHERITS", name, baseName, "high", {{"role", "base"}});
    }
    for (const FieldDecl *field : rd->fields()) {
      std::string ftype = field->getType().getAsString(PP);
      if (!ftype.empty())
        addEdge("TYPE_HAS_FIELD_TYPE", name, ftype, "high", {{"role", "field"}});
    }
    return true;
  }

  bool VisitEnumDecl(EnumDecl *ed) {
    if (ed->isImplicit() || !ed->isThisDeclarationADefinition())
      return true;
    emitType(ed, scopedName(ed), "enum");
    return true;
  }

  bool VisitTypedefNameDecl(TypedefNameDecl *td) {
    if (td->isImplicit())
      return true;
    // Alias templates (`template<class T> using Ptr = T*;`) are a
    // TypeAliasTemplateDecl wrapping a TypeAliasDecl; the clang backend does not
    // treat TypeAliasTemplateDecl as a template node, so it descends and emits a
    // typedef for the alias. Emit it too (do not skip the described-alias child).
    if (!isAccessible(td))
      return true;
    std::string file, origin, visibility;
    if (!classify(td, file, origin, visibility))
      return true;
    // Read the aliased spelling from clang's own JSON `type.qualType` (falling
    // back to `desugaredQualType`), exactly as clang.py::_typedef_underlying
    // does — NOT from getUnderlyingType().getAsString(PP). The pretty-printer
    // and the JSON qualType can spell the same type differently (`_Bool` vs
    // `bool`, elaborated `struct X` vs `X`, sugared aliases), which would make
    // `value`/`type_hash`/`id` diverge from the clang backend (Codex review).
    std::optional<Value> tjson = dumpDeclJson(td);
    const Object *to = tjson ? tjson->getAsObject() : nullptr;
    std::string underlying;
    if (to)
      if (const Object *t = to->getObject("type")) {
        if (auto q = t->getString("qualType"))
          underlying = q->str();
        if (underlying.empty())
          if (auto dq = t->getString("desugaredQualType"))
            underlying = dq->str();
      }
    if (underlying.empty()) {
      // A publicly-visible typedef whose JSON dump was absent/non-object, or
      // lacked both qualType and desugaredQualType, is silently dropped here
      // -- no diagnostic, and "types" coverage only watches for "record/enum
      // type_hash unavailable" (below), so a batch of failed typedefs
      // alongside one successfully-collected record/enum still reported
      // types as "complete", hiding the missing typedefs entirely
      // (latest-main Clang plugin review, PR4). Record a diagnostic and fold
      // it into the same family so a comparison sees partial/failed
      // coverage instead of a silent gap.
      Diags.insert("typedef facts unavailable (JSON dump/type spelling failed)");
      return true;
    }
    std::string name = scopedName(td);
    Entity e;
    e.id = H({"typedef", name, underlying});
    e.kind = "typedef";
    e.qualified_name = name;
    e.type_hash = H({"typedef-target", underlying});
    e.value = underlying;
    e.loc_path = file;
    e.loc_line = presumedLine(td);
    e.loc_origin = origin;
    e.visibility = visibility;
    stampDeclEvidence(e, td);
    Types.push_back(e);
    return true;
  }

  // A block-scope `constexpr` inside a public inline/header function body IS
  // emitted (not skipped on getParentFunctionOrMethod): the clang backend descends
  // accessible function bodies and emits such locals as `constexpr` entities just
  // like it emits body-local types, so the plugin matches it (Codex review).
  // scopedName() omits the function scope, so a local `k` in `demo::f()` is named
  // `demo::k` exactly as the backend names it. Local constexpr are syntactic
  // (present in the AST regardless of codegen), so there is no capture-point
  // asymmetry here. Returns the VisitVarDecl continue-traversal verdict (true).
  bool emitConstexprVar(VarDecl *vd) {
    std::string file, origin, visibility;
    if (!classify(vd, file, origin, visibility))
      return true;
    std::optional<Value> json = dumpDeclJson(vd);
    if (!json || !json->getAsObject()) {
      Diags.insert("constexpr value unavailable (JSON dump failed)");
      return true;
    }
    const Object &o = *json->getAsObject();
    const Value *init = initExprJson(o);
    std::string value = init ? exprValueJson(*init) : subtreeHash(*json, {});
    std::string name = scopedName(vd);
    Entity e;
    e.id = H({"constexpr", name, value});
    e.kind = "constexpr";
    e.qualified_name = name;
    e.mangled_name = mangledFromJson(o);
    e.value = value;
    e.loc_path = file;
    e.loc_line = presumedLine(vd);
    e.loc_origin = origin;
    e.visibility = visibility;
    stampDeclEvidence(e, vd);
    ConstexprValues.push_back(e);
    return true;
  }

  // Internal-linkage data variables that must NOT be emitted as exportable decls:
  //  - clang's own linkage verdict, encoded as the Itanium `L` seniority marker /
  //    `_GLOBAL__N_` component (header `const`/file-`static`/anon-namespace var);
  //  - a C / extern "C" file-scope static, which carries no mangled name for the
  //    marker, so filter on storageClass (a static data member — lexical parent a
  //    record — is external, so kept);
  //  - MSVC / clang-cl mangling (`?name@...`) has no Itanium marker, so a
  //    namespace-scope top-level `const` without `extern` (internal in C++) is
  //    caught by the type-based `_is_top_level_const` rule (Codex review). A
  //    C++17 `inline const` at namespace scope is externally linked (the
  //    `inline` keyword overrides const's internal linkage), so it is exempt from
  //    the top-level-const drop and kept (Codex review).
  bool isDroppedInternalVariable(VarDecl *vd, const std::string &mangled,
                                 const std::string &sig, const Object &o) {
    if (mangledHasInternalLinkage(mangled))
      return true;
    if (vd->getStorageClass() == SC_Static &&
        !isa<CXXRecordDecl>(vd->getLexicalDeclContext()))
      return true;
    if (mangled.rfind("?", 0) == 0 && vd->getStorageClass() != SC_Extern &&
        !vd->isInline() &&
        !isa<CXXRecordDecl>(vd->getLexicalDeclContext()) &&
        (isTopLevelConst(sig) ||
         isTopLevelConst(desugaredQualTypeFromJson(o))))
      return true;
    return false;
  }

  // Non-constexpr external-linkage data variables / static data members — these
  // become exported OBJECT symbols so capturing them lets a binary data export map
  // to a source decl (ADR-030 D4). Mirrors clang.py's `_is_variable_node`/
  // `_emit_variable`: skip function-local vars and variable templates (the clang
  // backend never walks into those); internal-linkage variables are dropped by
  // isDroppedInternalVariable. Returns the continue-traversal verdict (true).
  bool emitDataVariable(VarDecl *vd) {
    if (vd->isLocalVarDecl() || vd->getDescribedVarTemplate() ||
        isa<VarTemplateSpecializationDecl>(vd))
      return true;
    std::string file, origin, visibility;
    if (!classify(vd, file, origin, visibility))
      return true;
    std::optional<Value> json = dumpDeclJson(vd);
    if (!json || !json->getAsObject()) {
      Diags.insert("variable facts unavailable (JSON dump failed)");
      return true;
    }
    const Object &o = *json->getAsObject();
    std::string name = scopedName(vd);
    std::string sig = qualTypeFromJson(o);
    std::string mangled = mangledFromJson(o);
    if (isDroppedInternalVariable(vd, mangled, sig, o))
      return true;
    std::string key = mangled.empty() ? name : mangled;
    Entity e;
    e.id = H({"variable", key, sig});
    e.kind = "variable";
    e.qualified_name = name;
    e.mangled_name = mangled;
    e.type_hash = H({"type", sig});
    e.loc_path = file;
    e.loc_line = presumedLine(vd);
    e.loc_origin = origin;
    e.visibility = visibility;
    stampDeclEvidence(e, vd);
    Variables.push_back(e);
    return true;
  }

  bool VisitVarDecl(VarDecl *vd) {
    if (vd->isImplicit() || isa<ParmVarDecl>(vd))
      return true;
    if (!isAccessible(vd) || vd->getNameAsString().empty())
      return true;
    if (vd->isConstexpr())
      return emitConstexprVar(vd);
    return emitDataVariable(vd);
  }

  // Emit the template entity and STOP descending — matching clang.py, which
  // fingerprints a template whole and does not descend into the templated
  // pattern (a class template's member functions are not themselves template
  // patterns, so a Visit*+return-true would leak them in as ordinary functions).
  bool TraverseFunctionTemplateDecl(FunctionTemplateDecl *td) {
    emitTemplate(td);
    return true;
  }

  bool TraverseClassTemplateDecl(ClassTemplateDecl *td) {
    emitTemplate(td);
    emitClassTemplateMemberPatterns(td);
    return true;
  }

  // Prune the whole subtree of an inaccessible (private/protected) function.
  // isAccessible() only climbs enclosing CXXRecordDecl contexts, so a type or
  // typedef declared *inside a private method body* has a FunctionDecl for its
  // DeclContext and would otherwise be classified public and leaked into the
  // surface. clang.py keeps a non-accessible decl's whole subtree hidden
  // (running_access is preserved wholesale), so match it: for a hidden
  // function, emit nothing and do not descend. Accessible function bodies are
  // still traversed (clang.py emits local types of a public inline function),
  // and template patterns keep their dedicated Traverse* overrides above
  // (FunctionTemplateDecl is not a FunctionDecl, so it falls through here).
  bool TraverseDecl(Decl *d) {
    if (const auto *fd = dyn_cast_or_null<FunctionDecl>(d))
      if (!isAccessible(fd))
        return true;
    return RecursiveASTVisitor<FactsVisitor>::TraverseDecl(d);
  }

private:
  bool isAccessible(const Decl *d) const {
    const Decl *cur = d;
    while (cur) {
      AccessSpecifier as = cur->getAccess();
      if (as == AS_private || as == AS_protected)
        return false;
      const DeclContext *dc = cur->getDeclContext();
      if (!dc || !isa<CXXRecordDecl>(dc))
        break;
      cur = Decl::castFromDeclContext(dc);
    }
    return true;
  }

  int presumedLine(const Decl *d) const {
    // UseLineDirectives=false → the physical line, ignoring `#line`, matching
    // clang's JSON dumper (and clang.py, which reads that JSON loc).
    PresumedLoc pl = SM.getPresumedLoc(SM.getExpansionLoc(d->getLocation()),
                                       /*UseLineDirectives=*/false);
    return pl.isValid() ? static_cast<int>(pl.getLine()) : 0;
  }

  bool classify(const Decl *d, std::string &file, std::string &origin,
                std::string &visibility) const {
    SourceLocation loc = SM.getExpansionLoc(d->getLocation());
    if (loc.isInvalid())
      return false;
    if (SM.isInSystemHeader(loc))
      return false;
    // Classify from the PHYSICAL file, ignoring `#line` (UseLineDirectives=false):
    // clang's JSON dumper reports the physical spelling file in loc.file, so the
    // clang backend classifies by it. A `#line` directive in a generated or
    // amalgamated public header would otherwise remap the presumed name out of
    // the public roots and drop that decl AND every following one (Codex review).
    PresumedLoc pl = SM.getPresumedLoc(loc, /*UseLineDirectives=*/false);
    if (pl.isInvalid())
      return false;
    file = pl.getFilename();
    if (pathUnderAnyRoot(file, Roots)) {
      // With INFERRED roots (no explicit public-roots=), a root can be an ancestor
      // of the translation-unit sources — e.g. `-I.`/`-I$repo` — so a decl in an
      // implementation `.cpp` under the repo would be pulled onto the public
      // surface, polluting L4/L5 with private API and possibly masking
      // exported-not-public leaks. Public API lives in headers, so a source-file
      // decl is never public when roots were inferred (Codex review). Explicit
      // roots keep exact wrapper/C.6 parity (no extension filter).
      if (InferredRoots && isSourceFileName(file))
        return false;
      publicSurfaceLabels(file, origin, visibility);
      return true;
    }
    // Misconfiguration signal (ADR-038 Plugin injection, Caveat A): a decl in a real,
    // non-system *header* that is not under any public root. Ordinary internal
    // code lives in the .cpp source, so counting only header-declared rejections
    // distinguishes "public-roots does not match how headers resolve" (every
    // public header rejected → an empty pack) from a legitimately internal TU.
    if (!isSourceFileName(file)) {
      ++RejectedHeaderDecls;
      if (ExampleRejectedHeader.empty())
        ExampleRejectedHeader = file;
    }
    return false;
  }

public:
  //: Header-declared decls rejected because no public root matched them, plus one
  //: example path — feeds the "0 public entities" diagnostic (Caveat A).
  size_t rejectedHeaderDecls() const { return RejectedHeaderDecls; }
  const std::string &exampleRejectedHeader() const {
    return ExampleRejectedHeader;
  }

private:
  // Returns whether a type Entity was actually emitted, so a caller (P1
  // #17-18's edge capture) can bound its own emission to exactly the public
  // surface this function accepted.
  bool emitType(const TagDecl *td, const std::string &name,
                const std::string &kind) {
    // Anonymous records/enums have an empty qualified name; clang.py only treats
    // named type nodes as entities (bool(name)), so skip them — otherwise every
    // anonymous tag collides on H({"type", ""}).
    if (name.empty() || !isAccessible(td))
      return false;
    std::string file, origin, visibility;
    if (!classify(td, file, origin, visibility))
      return false;
    std::optional<Value> json = dumpDeclJson(td);
    if (!jsonTypeHasMembers(json))
      return false;
    Entity e;
    e.id = H({"type", name});
    e.kind = kind;
    e.qualified_name = name;
    if (json)
      e.type_hash = subtreeHash(*json, {});
    if (e.type_hash.empty())
      Diags.insert("record/enum type_hash unavailable (JSON dump failed)");
    e.loc_path = file;
    e.loc_line = presumedLine(td);
    e.loc_origin = origin;
    e.visibility = visibility;
    stampDeclEvidence(e, td);
    Types.push_back(e);
    return true;
  }

  void emitTemplate(const TemplateDecl *td) {
    if (td->isImplicit() || !isAccessible(td) || td->getNameAsString().empty())
      return;
    std::string file, origin, visibility;
    if (!classify(td, file, origin, visibility))
      return;
    std::string name = scopedName(td);
    Entity e;
    e.id = H({"template", name});
    e.kind = "template";
    e.qualified_name = name;
    if (std::optional<Value> json = dumpDeclJson(td))
      e.body_hash = subtreeHash(*json, {});
    if (e.body_hash.empty())
      Diags.insert("template body_hash unavailable (JSON dump failed)");
    e.loc_path = file;
    e.loc_line = presumedLine(td);
    e.loc_origin = origin;
    e.visibility = visibility;
    e.relations["template_kind"] = std::string(td->getDeclKindName());
    if (const TemplateParameterList *params = td->getTemplateParameters())
      e.relations["template_parameters"] =
          joinStrings(templateParamNames(params), ',');
    stampDeclEvidence(e, td);
    Templates.push_back(e);
  }

  std::string classTemplatePatternName(const ClassTemplateDecl *td) const {
    std::string name = scopedName(td);
    std::vector<std::string> params = templateParamNames(td->getTemplateParameters());
    if (!params.empty())
      name += "<" + joinStrings(params, ',') + ">";
    return name;
  }

  void emitClassTemplateMemberPatterns(const ClassTemplateDecl *td) {
    if (td->isImplicit() || !isAccessible(td) || td->getNameAsString().empty())
      return;
    CXXRecordDecl *rd = td->getTemplatedDecl();
    if (!rd)
      return;
    std::string owner = classTemplatePatternName(td);
    for (Decl *member : rd->decls()) {
      auto *fd = dyn_cast<FunctionDecl>(member);
      if (!fd || fd->isImplicit() || fd->getNameAsString().empty() ||
          !isAccessible(fd))
        continue;
      std::string file, origin, visibility;
      if (!classify(fd, file, origin, visibility))
        continue;
      std::optional<Value> json = dumpDeclJson(fd);
      if (!json || !json->getAsObject()) {
        Diags.insert("class-template member facts unavailable (JSON dump failed)");
        continue;
      }
      const Object &o = *json->getAsObject();
      std::string sig = qualTypeFromJson(o);
      std::string name = owner + "::" + fd->getNameAsString();
      Entity e;
      e.id = H({"function", name, sig});
      e.kind = "function";
      e.qualified_name = name;
      e.signature_hash = H({"sig", sig});
      e.value = defaultArgReprJson(o);
      e.loc_path = file;
      e.loc_line = presumedLine(fd);
      e.loc_origin = origin;
      e.visibility = visibility;
      e.relations["template_owner"] = owner;
      e.relations["template_parameters"] =
          joinStrings(templateParamNames(td->getTemplateParameters()), ',');
      e.relations["declaration_role"] = "class_template_member_pattern";
      stampDeclEvidence(e, fd);
      Functions.push_back(e);

      // P1 #17-18 parity with VisitFunctionDecl: without this, a call/
      // reference inside a class-template member body was silently absent
      // from source_edges with no diagnostic at all (dumpDeclJson succeeds
      // normally here), so source_edges coverage could read complete/
      // empty-confirmed while calls from every class-template member body
      // in the TU were unconditionally missing (Codex review, P2). No
      // mangled name applies to an uninstantiated template member pattern
      // (unlike VisitFunctionDecl's `key`, which prefers one), so edges are
      // keyed by the same qualified `name` used for this entity's own
      // identity above.
      addEdge("DECL_HAS_TYPE", name, fd->getReturnType().getAsString(PP),
              "high", {{"role", "return"}});
      for (const ParmVarDecl *p : fd->parameters()) {
        std::string ptype = p->getType().getAsString(PP);
        if (!ptype.empty())
          addEdge("DECL_HAS_TYPE", name, ptype, "high", {{"role", "param"}});
      }
      if (Stmt *body = fd->getBody()) {
        CallRefVisitor crv;
        crv.TraverseStmt(body);
        for (const auto &call : crv.Calls)
          addEdge("DECL_CALLS_DECL", name,
                  mangledOrScopedName(Mangler.get(), call.first),
                  call.second ? "reduced" : "high",
                  {{"call_kind", call.second ? "virtual" : "direct"},
                   {"resolution", call.second ? "overapprox" : "exact"}});
        for (const std::string &ref : crv.References)
          addEdge("DECL_REFERENCES_DECL", name, ref, "reduced", {{"role", "ref"}});
      }
    }
  }

  SourceManager &SM;
  PrintingPolicy PP;
  const std::vector<std::string> &Roots;
  std::vector<Entity> &Functions;
  std::vector<Entity> &Variables;
  std::vector<Entity> &Types;
  std::vector<Entity> &Templates;
  std::vector<Entity> &InlineBodies;
  std::vector<Entity> &ConstexprValues;
  std::set<std::string> &Diags;
  std::vector<SourceEdge> &Edges;
  // Per-TU edge dedup key: (kind, src, dst) — "Deduplicate identical edges
  // per TU" (P1 #18). Not a reference: purely this visitor's own bookkeeping.
  std::set<std::tuple<std::string, std::string, std::string>> SeenEdgeKeys;
  //: True when Roots were auto-derived (no explicit public-roots=); gates the
  //: source-file exclusion so an inferred `-I.` root does not pull `.cpp` decls
  //: onto the public surface.
  bool InferredRoots = false;
  // Mutable: updated from the const `classify` gate while walking the AST.
  mutable size_t RejectedHeaderDecls = 0;
  mutable std::string ExampleRejectedHeader;
  // Owning; ASTContext::createMangleContext() returns a raw pointer the
  // caller must free. Built once per TU and reused for every
  // mangledOrScopedName() call (edge endpoints only — entity `key`s keep
  // reading their mangled name from clang's own JSON dump, unchanged) rather
  // than re-mangling on every call expression.
  std::unique_ptr<MangleContext> Mangler;

  // Deterministic edge identity (P1 #18): kind + source entity identity +
  // target entity identity. Silently drops a self-edge or an edge with an
  // unresolved (empty) endpoint rather than emitting a useless/noisy one.
  void addEdge(const std::string &kind, const std::string &src,
               const std::string &dst, const std::string &confidence,
               std::map<std::string, std::string> attrs = {}) {
    if (src.empty() || dst.empty() || src == dst)
      return;
    if (!SeenEdgeKeys.insert(std::make_tuple(kind, src, dst)).second)
      return;
    SourceEdge e;
    e.kind = kind;
    e.src = src;
    e.dst = dst;
    e.confidence = confidence;
    e.attrs = std::move(attrs);
    Edges.push_back(std::move(e));
  }
};

// ---------------------------------------------------------------------------
// The consumer: run the visitor after the real codegen and append one
// SourceAbiTu per TU to a per-TU JSONL file (ADR-038 C.1/C.4).
// ---------------------------------------------------------------------------
class FactsConsumer : public ASTConsumer {
public:
  FactsConsumer(std::string outDir, std::vector<std::string> roots,
                std::string library, std::string version,
                std::string ctxHash,
                std::shared_ptr<std::map<std::string, MacroRecord>> macros,
                size_t inferredRootCount = 0)
      : OutDir(std::move(outDir)), Roots(std::move(roots)),
        Library(std::move(library)), Version(std::move(version)),
        CtxHash(std::move(ctxHash)), Macros(std::move(macros)),
        InferredRootCount(inferredRootCount) {}

  void HandleTranslationUnit(ASTContext &ctx) override {
    SourceManager &sm = ctx.getSourceManager();

    std::vector<Entity> functions, variables, types, templates, inlineBodies,
        constexprValues;
    std::vector<SourceEdge> edges;
    std::set<std::string> diags;
    FactsVisitor visitor(ctx, Roots, functions, variables, types, templates,
                         inlineBodies, constexprValues, diags, edges,
                         InferredRootCount > 0);
    visitor.TraverseDecl(ctx.getTranslationUnitDecl());

    std::vector<Entity> macros = collectMacros(diags);

    emitInferenceNote(diags);

    size_t publicCount = functions.size() + variables.size() + types.size() +
                         templates.size() + inlineBodies.size() +
                         constexprValues.size() + macros.size();
    emitEmptyPackDiagnostics(visitor, publicCount, diags);

    std::string source = resolveMainSource(sm);
    const std::string &ctxHash = CtxHash;
    std::string cfg = ctxHash.substr(std::string("sha256:").size(), 12);
    std::string targetId = Library.empty() ? "" : "target://" + Library;
    // Fold the target/library identity into tu_id (not just the separate
    // target_id field) so two different libraries compiling the *same*
    // source+compile-context never collide on one tu_id — the exact
    // same-source/two-library ambiguity a shared tu_id would otherwise leave
    // undetectable downstream (latest-main Clang plugin review, PR3).
    std::string tuId = Library.empty()
                            ? "cu://" + source + "#cfg:" + cfg
                            : "cu://" + source + "#cfg:" + cfg + "#target:" + Library;

    // P1 #15-16: every file the preprocessor actually opened for this TU —
    // captured from the SourceManager it already built, not a second pass.
    std::vector<std::string> readFiles = collectReadFiles(sm);

    if (!writeTu(source, tuId, targetId, ctxHash, functions, variables, types,
                 templates, inlineBodies, constexprValues, macros, diags,
                 edges, readFiles))
      llvm::errs() << "abicheck-facts: could not write source facts to " << OutDir
                   << "\n";
  }

private:
  // Public-roots inference note (ADR-038 Plugin injection, Caveat A): when no explicit
  // `public-roots=` was given, the roots were auto-derived from the compile's
  // -I/-iquote include dirs. Record that per-TU (forensic) and tell the operator
  // once — the inferred surface can be slightly broad, so an explicit
  // public-roots= is the precise scoping knob. Not an error: a populated (broad)
  // surface beats the silent empty pack the missing flag used to produce.
  void emitInferenceNote(std::set<std::string> &diags) {
    if (InferredRootCount == 0)
      return;
    std::string roots;
    for (const std::string &r : Roots)
      roots += (roots.empty() ? "" : ", ") + r;
    std::string msg =
        "abicheck-facts: no public-roots given; inferred " +
        std::to_string(InferredRootCount) +
        " public root(s) from the compile's -I/-iquote include dirs [" + roots +
        "]. Pass public-roots=<dir> to scope the public surface precisely.";
    diags.insert(msg);
    if (claimFirstInferenceNote())
      llvm::errs() << msg << "\n";
  }

  // Caveat A (ADR-038 Plugin injection): fail loud, not silent. If public-roots is set
  // but this TU emitted zero public entities *while* header-declared decls were
  // rejected for not being under any root, public-roots may not match how the
  // compiler resolves the public headers (e.g. it points at the installed
  // `include/` while `-I..` makes `<pvxs/x.h>` resolve to `src/pvxs/`).
  // Previously this produced an empty pack with exit 0 and no message.
  //
  // This signal is necessarily per-TU: an internal-implementation-only TU that
  // includes a private header and defines nothing public trips the same
  // condition even with correct roots — the plugin cannot see whether *other*
  // TUs produced public facts (it runs once per compile). To keep the "fail
  // loud" signal credible without crying wolf on every internal TU under `-j`,
  // the human-facing stderr line is emitted **once per output pack** (a
  // deterministic misconfiguration shows it on the first TU); the accurate
  // per-TU note is still recorded in this TU's pack `diagnostics` as forensic
  // data. A whole build whose pack ends up empty is the real confirmation.
  void emitEmptyPackDiagnostics(const FactsVisitor &visitor, size_t publicCount,
                                std::set<std::string> &diags) {
    bool emptyWithRejections =
        publicCount == 0 && visitor.rejectedHeaderDecls() > 0;
    if (emptyWithRejections && Roots.empty()) {
      // NO roots at all — neither an explicit public-roots= nor any project-local
      // -I/-iquote to infer from — yet this TU saw header decls. Previously this
      // fell through silently (the `!Roots.empty()` guard), reproducing the exact
      // empty-pack trap the diagnostic exists to kill (Codex review). Fail loud.
      std::string msg =
          "abicheck-facts: no public-roots given and no project-local include "
          "dirs to infer from; this TU produced 0 public entities though " +
          std::to_string(visitor.rejectedHeaderDecls()) +
          " header decl(s) were seen (e.g. " + visitor.exampleRejectedHeader() +
          "). Pass public-roots=<dir> so the public headers are recognized.";
      diags.insert(msg);
      if (claimFirstRootsWarning())
        llvm::errs() << msg << "\n";
    } else if (emptyWithRejections && InferredRootCount == 0) {
      // Explicit public-roots= that matched nothing (roots were operator-set, not
      // inferred): the classic Caveat-A misconfiguration message.
      std::string roots;
      for (const std::string &r : Roots)
        roots += (roots.empty() ? "" : ", ") + r;
      std::string msg =
          "abicheck-facts: public-roots matched 0 declarations for this TU (" +
          std::to_string(visitor.rejectedHeaderDecls()) +
          " header decl(s) were seen outside the root(s) [" + roots +
          "], e.g. " + visitor.exampleRejectedHeader() +
          "). If this TU defines public API, public-roots likely does not match "
          "how the compiler resolves the public headers (verify with `clang -H`) "
          "— it must be the resolved directory, not necessarily the installed "
          "include dir. (Harmless for an internal-only TU.)";
      diags.insert(msg);
      if (claimFirstRootsWarning())
        llvm::errs() << msg << "\n";
    }
    // When roots were INFERRED (InferredRootCount > 0) the inference note above
    // already told the operator; a per-TU "matched 0" here would (a) misadvise
    // them to fix a public-roots= they never set and (b) cry wolf on every
    // internal-only TU (Codex review). The authoritative empty-pack signal for the
    // inferred case is the project-level `merge` warning over the whole surface.
  }

  std::string resolveMainSource(SourceManager &sm) const {
    std::string source;
    if (auto fe = sm.getFileEntryRefForID(sm.getMainFileID())) {
      // Resolve to an absolute path: a relative main-file spelling (e.g.
      // `foo.cpp`) would make two same-spelled sources built from different
      // directories into one pack collide on the tu_id / facts-filename hash,
      // and the truncating write would drop one. Absolute paths are distinct.
      llvm::SmallString<256> abs(fe->getName());
      llvm::sys::fs::make_absolute(abs);
      source = std::string(abs.str());
    }
    return source;
  }

  // P1 #15-16: every file the preprocessor actually opened while building
  // this TU — the main source plus every transitively included header
  // (public, private, generated, forced), straight from the SourceManager
  // this compile already populated. Deduplicated and sorted deterministically
  // (recommendation #16). Absolute paths, matching resolveMainSource() so a
  // relative -I doesn't make two same-spelled builds collide.
  //
  // SourceManager::fileinfo_begin()/end() iterate a map whose *key* type
  // changed across the LLVM majors this plugin supports (C.5): `FileEntryRef`
  // from LLVM 18, `const FileEntry *` on LLVM 16/17 (confirmed by the C.6 CI
  // matrix — a `FileEntryRef fe = it->first;` here fails to compile on 17).
  // The two overloads below dispatch on whichever key type the installed
  // clang headers actually declare, so this file builds unmodified against
  // every matrix leg without an `#if CLANG_VERSION_MAJOR` guard.
  static llvm::StringRef fileEntryKeyName(const FileEntry *fe) {
    return fe->getName();
  }
  static llvm::StringRef fileEntryKeyName(FileEntryRef fe) {
    return fe.getName();
  }
  std::vector<std::string> collectReadFiles(SourceManager &sm) const {
    std::vector<std::string> files;
    for (auto it = sm.fileinfo_begin(), e = sm.fileinfo_end(); it != e; ++it) {
      llvm::SmallString<256> abs(fileEntryKeyName(it->first));
      llvm::sys::fs::make_absolute(abs);
      files.push_back(std::string(abs.str()));
    }
    std::sort(files.begin(), files.end());
    files.erase(std::unique(files.begin(), files.end()), files.end());
    return files;
  }

  std::vector<Entity> collectMacros(std::set<std::string> &diags) {
    std::vector<Entity> out;
    bool any = false;
    for (const auto &kv : *Macros) { // std::map iterates sorted by name (C.2)
      const std::string &name = kv.first;
      const std::string &value = kv.second.value;
      const std::string &file = kv.second.file;
      if ((looksLikeIncludeGuard(name, file) ||
           isStructuralIncludeGuard(name, file)) &&
          (value.empty() || value == "1"))
        continue;
      if (value.empty()) {
        std::string up = name;
        for (char &c : up)
          c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
        size_t b = up.find_first_not_of('_');
        size_t e = up.find_last_not_of('_');
        std::string core =
            (b == std::string::npos) ? "" : up.substr(b, e - b + 1);
        if (core == upperStrippedStem(file))
          continue;
      }
      std::string origin, visibility;
      if (!isPublicFile(file, origin, visibility))
        continue;
      Entity m;
      m.id = H({"macro", name, value});
      m.kind = "macro";
      m.qualified_name = name;
      m.value = value;
      m.loc_path = file;
      m.loc_line = 0;
      m.loc_origin = origin;
      m.visibility = visibility;
      m.names["source_qualified"] = name;
      m.ownership["visibility"] = visibility;
      m.ownership["origin"] = origin;
      m.ownership["role"] = ownershipRole(visibility);
      out.push_back(m);
      any = true;
    }
    if (any)
      diags.insert("macro values are token-reconstructed in-compile "
                   "(PPCallbacks); operator-adjacent spacing may differ from the "
                   "clang -E -dD backend until the C.6 gate (ADR-038 C.7)");
    return out;
  }

  bool isPublicFile(const std::string &file, std::string &origin,
                    std::string &visibility) const {
    if (pathUnderAnyRoot(file, Roots)) {
      // Same source-file exclusion as FactsVisitor::classify for inferred roots:
      // a macro defined in an implementation `.cpp` under an inferred `-I.` root
      // is not public API (Codex review).
      if (InferredRootCount > 0 && isSourceFileName(file))
        return false;
      publicSurfaceLabels(file, origin, visibility);
      return true;
    }
    return false;
  }

  bool writeTu(const std::string &source, const std::string &tuId,
               const std::string &targetId, const std::string &ctxHash,
               const std::vector<Entity> &functions,
               const std::vector<Entity> &variables,
               const std::vector<Entity> &types,
               const std::vector<Entity> &templates,
               const std::vector<Entity> &inlineBodies,
               const std::vector<Entity> &constexprValues,
               const std::vector<Entity> &macros,
               const std::set<std::string> &diags,
               const std::vector<SourceEdge> &edges,
               const std::vector<std::string> &readFiles) {
    std::string factsDir = OutDir + "/source_facts";
    if (llvm::sys::fs::create_directories(factsDir))
      return false;
    ensureManifest();

    auto arr = [](const std::vector<Entity> &v) {
      std::vector<std::string> items;
      items.reserve(v.size());
      for (const Entity &e : v)
        items.push_back(e.to_json());
      return jsonRawArray(items);
    };
    auto edgeArr = [](const std::vector<SourceEdge> &v) {
      std::vector<std::string> items;
      items.reserve(v.size());
      for (const SourceEdge &e : v)
        items.push_back(e.to_json());
      return jsonRawArray(items);
    };

    std::string extractor = "{\"name\":\"abicheck-clang-plugin\",\"version\":" +
                            jsonStr(kPluginVersion) + "}";
    std::vector<std::string> diagVec(diags.begin(), diags.end());

    // ADR-038 C.8: fact-set identity + per-family coverage, always computed —
    // never behind a flag. See familyCoverageState()/anyDiagContains() above.
    std::string factSet =
        "{\"name\":" + jsonStr(kFactSetName) +
        ",\"version\":" + std::to_string(kFactSetVersion) +
        ",\"producer\":\"abicheck-clang-plugin\",\"producer_version\":" +
        jsonStr(kPluginVersion) +
        ",\"compiler_family\":\"clang\",\"compiler_version\":" +
        jsonStr(CLANG_VERSION_STRING) + "}";

    std::map<std::string, std::string> coverageMap = {
        // Class-template member patterns (emitClassTemplateMemberPatterns)
        // also push into `functions`, so a JSON-dump failure there must
        // count toward this family's diagnostic check too, or an all-failed
        // batch of template members with no other free function in the TU
        // reports empty-confirmed instead of failed (CodeRabbit review, P2).
        {"functions",
         familyCoverageState(
             !functions.empty(),
             anyDiagContains(diags, {"function facts unavailable",
                                     "class-template member facts unavailable"}))},
        {"variables", familyCoverageState(
                          !variables.empty(),
                          anyDiagContains(diags, {"variable facts unavailable"}))},
        // "typedef facts unavailable" (PR4) included alongside the
        // record/enum diagnostic: VisitTypedefNameDecl() also pushes into
        // `types`, so a JSON-dump failure there must count toward this
        // family's diagnostic check too, the same reasoning already applied
        // to "functions" above for class-template member patterns.
        {"types", familyCoverageState(
                      !types.empty(),
                      anyDiagContains(diags, {"record/enum type_hash unavailable",
                                               "typedef facts unavailable"}))},
        {"macros", familyCoverageState(!macros.empty(), /*diagnosticsSeen=*/false)},
        // "class-template member facts unavailable" deliberately excluded
        // here: emitClassTemplateMemberPatterns() pushes a failed member's
        // entity into `functions` (it IS a function), never `templates` —
        // counting it here would mark templates coverage partial/failed even
        // when every actual Template entity in the TU was captured cleanly
        // (review finding).
        {"templates",
         familyCoverageState(
             !templates.empty(),
             anyDiagContains(diags, {"template body_hash unavailable"}))},
        {"inline_bodies",
         familyCoverageState(
             !inlineBodies.empty(),
             anyDiagContains(diags, {"function facts unavailable"}))},
        {"constexpr_values",
         familyCoverageState(
             !constexprValues.empty(),
             anyDiagContains(diags, {"constexpr value unavailable"}))},
        // P1 #15-18: always attempted now (this producer's SourceManager walk
        // and per-function-body sub-walk), so "found nothing" is a real
        // empty-confirmed result, not an unsupported family -- EXCEPT when a
        // function's dumpDeclJson() failed: VisitFunctionDecl returns before
        // ever running the CallRefVisitor sub-walk that adds this function's
        // DECL_CALLS_DECL/DECL_REFERENCES_DECL edges, so every edge from that
        // function's body was silently skipped, not "confirmed absent". A
        // hardcoded diagnosticsSeen=false let source_edges report
        // complete/empty-confirmed in that failure mode, so `inputs validate`
        // and comparisons could trust an absence of source-edge findings that
        // was actually just missing evidence (Codex review, P2).
        {"source_edges",
         familyCoverageState(
             !edges.empty(),
             anyDiagContains(diags, {"function facts unavailable",
                                     "class-template member facts unavailable"}))},
        {"read_files", familyCoverageState(!readFiles.empty(), /*diagnosticsSeen=*/false)},
    };
    std::string coverage = jsonStrMap(coverageMap);

    std::string tu =
        "{\"schema_version\":1,\"tu_id\":" + jsonStr(tuId) +
        ",\"target_id\":" + jsonStr(targetId) + ",\"extractor\":" + extractor +
        ",\"compile_context_hash\":" + jsonStr(ctxHash) +
        ",\"source\":" + jsonStr(source) + ",\"public_header_roots\":" +
        jsonStrArray(Roots) + ",\"declarations\":[]" +
        ",\"types\":" + arr(types) + ",\"functions\":" + arr(functions) +
        ",\"variables\":" + arr(variables) + ",\"macros\":" + arr(macros) +
        ",\"templates\":" + arr(templates) +
        ",\"inline_bodies\":" + arr(inlineBodies) +
        ",\"constexpr_values\":" + arr(constexprValues) +
        ",\"source_edges\":" + edgeArr(edges) +
        ",\"diagnostics\":" + jsonStrArray(diagVec) +
        ",\"read_files\":" + jsonStrArray(readFiles) + ",\"fact_set\":" + factSet +
        ",\"coverage\":" + coverage + "}";

    // Per-TU, deterministic filename keyed by source path, compile context,
    // AND target/library identity. Including the context hash keeps distinct
    // ABI-relevant compile variants of the same source (e.g. SIMD/feature
    // builds) in separate files — one is not erased by another — while a
    // rebuild of the *same* variant overwrites its own file (truncate), so no
    // stale/duplicate records accumulate. Including the library identity
    // additionally prevents two different targets that happen to compile the
    // *same* source file with the *same* compile context (a common object
    // shared into two libraries) from clobbering each other when both point
    // `out=` at one shared directory (latest-main Clang plugin review, PR3) —
    // previously the filename hashed only the source path, so the later
    // compile's facts silently overwrote the earlier target's. Ingest globs
    // `source_facts/*.jsonl`, so the filename shape is free.
    std::string cfg = (ctxHash.rfind("sha256:", 0) == 0)
                          ? ctxHash.substr(std::string("sha256:").size(), 12)
                          : ctxHash.substr(0, 12);
    llvm::StringRef stem = llvm::sys::path::filename(source);
    std::string pathAndLibraryHash =
        sha256Hex(Library.empty() ? source : (Library + "\x1f" + source))
            .substr(0, 12);
    std::string factsFile = factsDir + "/" +
                            (stem.empty() ? std::string("tu") : stem.str()) +
                            "." + pathAndLibraryHash + "." + cfg + ".jsonl";
    std::ofstream out(factsFile, std::ios::trunc);
    if (!out)
      return false;
    out << tu << "\n";
    if (prof().enabled) {
      auto &p = prof();
      auto ms = [](uint64_t ns) { return ns / 1e6; };
      std::string line;
      llvm::raw_string_ostream os(line);
      os << "abicheck-facts PROFILE " << stem << ": dump="
         << llvm::format("%.1f", ms(p.dumpNs)) << "ms parse="
         << llvm::format("%.1f", ms(p.parseNs)) << "ms canonicalize="
         << llvm::format("%.1f", ms(p.canonNs)) << "ms  (dumps="
         << p.dumpCalls << " canon=" << p.canonCalls << " jsonMB="
         << llvm::format("%.1f", p.dumpBytes / 1e6) << ")\n";
      os.flush();
      emitProfileLine(line);
    }
    return true;
  }

  // Return true for exactly the first caller across all parallel compiles that
  // share this OutDir, by atomically creating a sentinel file (CD_CreateNew
  // fails if it already exists). Used to emit the public-roots stderr warning
  // once per pack instead of once per TU (Caveat A / CodeRabbit review). A
  // failure to create for any *other* reason falls back to emitting (fail loud
  // rather than swallow the signal).
  bool claimFirstRootsWarning() {
    // The out dir is otherwise created lazily by writeTu (which runs after this
    // check); create it up front so the sentinel has a home on the very first TU.
    llvm::sys::fs::create_directories(OutDir);
    int fd = -1;
    std::error_code ec = llvm::sys::fs::openFile(
        llvm::Twine(OutDir) + "/.abicheck-roots-warning", fd,
        llvm::sys::fs::CD_CreateNew, llvm::sys::fs::FA_Write,
        llvm::sys::fs::OF_None);
    if (ec == std::errc::file_exists)
      return false;
    if (ec)
      return true; // unexpected error: prefer emitting over silence
    llvm::raw_fd_ostream os(fd, /*shouldClose=*/true); // create + close
    return true;
  }

  // Same once-per-pack claim as claimFirstRootsWarning, for the distinct
  // public-roots-inference note (a different sentinel so the two notes do not
  // suppress each other). Emitted at most once across all parallel compiles that
  // share this OutDir.
  bool claimFirstInferenceNote() {
    llvm::sys::fs::create_directories(OutDir);
    int fd = -1;
    std::error_code ec = llvm::sys::fs::openFile(
        llvm::Twine(OutDir) + "/.abicheck-roots-inferred", fd,
        llvm::sys::fs::CD_CreateNew, llvm::sys::fs::FA_Write,
        llvm::sys::fs::OF_None);
    if (ec == std::errc::file_exists)
      return false;
    if (ec)
      return true; // unexpected error: prefer emitting over silence
    llvm::raw_fd_ostream os(fd, /*shouldClose=*/true);
    return true;
  }

  // Loudly flag (never abort the compile over) an existing manifest naming a
  // different non-empty library/version than this invocation — the exact
  // same-source/two-library collision a silent first-writer-wins manifest
  // allowed (latest-main Clang plugin review, PR3): the manifest would keep
  // describing whichever target/version compiled first while later targets'
  // facts (now isolated per-target by writeTu's filename fix) accumulate
  // underneath it, describing a library manifest.json never claims to be.
  // A stderr warning, not a hard error, matches every other best-effort
  // diagnostic in this plugin (ADR-028 D7: source-fact collection never
  // aborts the build it is instrumenting) — the fix is an operational one
  // (one out= directory per target/configuration/architecture), not
  // something this TU's compile can itself repair.
  void checkManifestTargetAgreement(const std::string &manifestPath) {
    auto bufOrErr = llvm::MemoryBuffer::getFile(manifestPath);
    if (!bufOrErr)
      return;
    auto parsed = llvm::json::parse((*bufOrErr)->getBuffer());
    if (!parsed) {
      llvm::consumeError(parsed.takeError());
      return;
    }
    const Object *obj = parsed->getAsObject();
    if (!obj)
      return;
    if (!Library.empty()) {
      if (auto existing = obj->getString("library"))
        if (!existing->empty() && *existing != Library)
          llvm::errs()
              << "abicheck-facts: WARNING: " << manifestPath
              << " already names library " << *existing
              << "; this compile names " << Library
              << ". Two different targets must not share one "
                 "abicheck_inputs pack directory -- use a separate out= "
                 "directory per target/configuration/architecture, or "
                 "downstream `abicheck inputs validate` will reject this "
                 "pack.\n";
    }
    if (!Version.empty()) {
      if (auto existing = obj->getString("version"))
        if (!existing->empty() && *existing != Version)
          llvm::errs()
              << "abicheck-facts: WARNING: " << manifestPath
              << " already names version " << *existing
              << "; this compile names " << Version
              << ". Two different versions must not share one "
                 "abicheck_inputs pack directory -- use a fresh out= "
                 "directory per build.\n";
    }
  }

  void ensureManifest() {
    std::string manifestPath = OutDir + "/manifest.json";
    llvm::sys::fs::create_directories(OutDir);
    std::string createdBy =
        std::string("abicheck-clang-plugin ") + kPluginVersion;
    // ADR-038 C.8: the pack-level fact_set mirrors every TU record's — one
    // canonical fact set per pack, never per-TU variance (this plugin instance
    // only ever emits one fact-set version for its whole run).
    std::string factSet =
        "{\"name\":" + jsonStr(kFactSetName) +
        ",\"version\":" + std::to_string(kFactSetVersion) +
        ",\"producer\":\"abicheck-clang-plugin\",\"producer_version\":" +
        jsonStr(kPluginVersion) +
        ",\"compiler_family\":\"clang\",\"compiler_version\":" +
        jsonStr(CLANG_VERSION_STRING) + "}";
    std::string manifest =
        "{\n  \"abicheck_inputs_version\": 1,\n  \"binary\": \"\",\n"
        "  \"compile_db\": \"\",\n  \"created_at\": " +
        jsonStr(nowIso8601Utc()) + ",\n  \"created_by\": " + jsonStr(createdBy) +
        ",\n  \"exported_symbols\": [],\n  \"fact_set\": " + factSet +
        ",\n  \"headers\": [],\n"
        "  \"kind\": \"abicheck_inputs\",\n  \"library\": " + jsonStr(Library) +
        ",\n  \"source_facts\": [\"source_facts\"],\n  \"version\": " + jsonStr(Version) +
        "\n}\n";

    // Write the full manifest to a private temp file first, then publish it
    // via create_hard_link() -- an all-or-nothing claim of manifest.json
    // itself, exactly like os.link() in the Python inputs_emit wrapper's
    // equivalent fix. An earlier revision of this fix claimed manifestPath
    // directly via openFile(..., CD_CreateNew, ...) and wrote the content
    // into it afterward: that leaves a window where the file exists but is
    // still empty/partial, so a losing concurrent compile's file_exists
    // check could immediately call checkManifestTargetAgreement() on a
    // torn read, fail json::parse, and silently skip the cross-target
    // warning it exists to give (Codex review) -- the very race this fix
    // was meant to close, just moved one step later. create_hard_link()
    // only ever leaves manifestPath as "absent" or "fully written," never
    // partial, so a loser's read is always complete.
    llvm::SmallString<128> tmp;
    int fd = -1;
    if (llvm::sys::fs::createUniqueFile(
            llvm::Twine(OutDir) + "/.manifest.%%%%%%.tmp", fd, tmp))
      return; // unexpected error: best-effort, never abort the compile
    {
      llvm::raw_fd_ostream os(fd, /*shouldClose=*/true);
      os << manifest;
    }
    std::error_code ec = llvm::sys::fs::create_hard_link(tmp, manifestPath);
    llvm::sys::fs::remove(tmp);
    if (ec == std::errc::file_exists)
      checkManifestTargetAgreement(manifestPath);
    // Any other error (including success) needs no further action here:
    // success published the manifest; an unexpected error is best-effort
    // and never aborts the compile.
  }

  std::string OutDir;
  std::vector<std::string> Roots;
  std::string Library;
  std::string Version;
  std::string CtxHash;
  std::shared_ptr<std::map<std::string, MacroRecord>> Macros;
  //: >0 when Roots were auto-derived from the compile's -I/-iquote include dirs
  //: (no explicit public-roots= given) — drives the one-time inference note.
  size_t InferredRootCount = 0;
};

// ---------------------------------------------------------------------------
// The plugin action. AddAfterMainAction runs after the real codegen, so fact
// emission never perturbs the object output (ADR-038 C.1).
// ---------------------------------------------------------------------------
class FactsAction : public PluginASTAction {
public:
  std::unique_ptr<ASTConsumer> CreateASTConsumer(CompilerInstance &ci,
                                                  llvm::StringRef) override {
    // The PluginASTAction is destroyed right after this returns, while the
    // consumer it produces (and the PPCallbacks) run later — so the macro map
    // must be owned by something that outlives the action. Share it (shared_ptr)
    // between the collector and the consumer rather than referencing an action
    // member (which would dangle).
    auto macros = std::make_shared<std::map<std::string, MacroRecord>>();
    Preprocessor &pp = ci.getPreprocessor();
    pp.addPPCallbacks(std::make_unique<MacroCollector>(pp, macros));
    // Auto-derive public roots from the -I/-iquote include dirs when the operator
    // gave none (ADR-038 Plugin injection, Caveat A): a populated (broad) surface beats the
    // silent empty pack a missing public-roots= used to produce.
    std::vector<std::string> roots = Roots;
    size_t inferredRootCount = 0;
    if (roots.empty()) {
      roots = deriveRootsFromIncludes(ci.getHeaderSearchOpts());
      inferredRootCount = roots.size();
    }
    return std::make_unique<FactsConsumer>(OutDir, std::move(roots), Library,
                                           Version, computeContextHash(ci),
                                           macros, inferredRootCount);
  }

  // The compile-context hash — the per-TU cache key (ADR-030 D8) and the `cfg`
  // segment of the facts filename. Fold in every ABI-relevant compile input the
  // clang extractor's context uses (standard/triple/sysroot/defines/includes/
  // target-features), so distinct variants of one source get distinct hashes
  // (and distinct facts files) rather than colliding. Not compared in C.6 (only
  // entities are) — it only needs to be deterministic and discriminating.
  static std::string computeContextHash(const CompilerInstance &ci) {
    std::string standard;
    const LangOptions &lo = ci.getLangOpts();
    if (lo.LangStd != LangStandard::lang_unspecified)
      standard = LangStandard::getLangStandardForKind(lo.LangStd).getName();
    const TargetOptions &to = ci.getTargetOpts();
    // Preserve command-line ORDER of -D/-U (do not sort): `-D FOO -U FOO` and
    // `-U FOO -D FOO` yield different final macro state, so ordering is
    // ABI-relevant and must change the hash.
    std::vector<std::string> defs;
    for (const auto &m : ci.getPreprocessorOpts().Macros)
      defs.push_back((m.second ? "U:" : "D:") + m.first);
    std::vector<std::string> incs;
    const HeaderSearchOptions &hso = ci.getHeaderSearchOpts();
    // Resolve include dirs to absolute paths and fold in the search-kind
    // (Group): two build dirs both passing a relative `-Igenerated` see
    // different physical headers, and `-I include` vs `-isystem include` yield a
    // different public surface (via isInSystemHeader) — both must change the
    // hash, so they cannot collide on the facts filename.
    auto absStr = [](llvm::StringRef s) {
      llvm::SmallString<256> p(s);
      llvm::sys::fs::make_absolute(p);
      return std::string(p.str());
    };
    // Fold the search-mode flags (IsFramework: `-F` vs `-I`; IgnoreSysRoot:
    // `-isystem` vs `-iwithsysroot`) alongside Group + absolute path: the same
    // directory reached through a different search mode can expose different
    // headers, so those variants must not collide on the facts filename (Codex
    // review).
    for (const auto &e : hso.UserEntries)
      incs.push_back(std::to_string(static_cast<int>(e.Group)) +
                     (e.IsFramework ? ":F" : ":I") +
                     (e.IgnoreSysRoot ? ":r" : ":s") + ":" + absStr(e.Path));
    // Forced preincludes (-include) and macro-includes (-imacros) also change the
    // TU's ABI-relevant context; keep their order (significant) and root them to
    // absolute paths so a relative `-include ./config.hpp` from two build dirs
    // does not collide.
    std::vector<std::string> preinc;
    for (const auto &f : ci.getPreprocessorOpts().Includes)
      preinc.push_back("i:" + absStr(f));
    for (const auto &f : ci.getPreprocessorOpts().MacroIncludes)
      preinc.push_back("m:" + absStr(f));
    // A precompiled header (`-include-pch`) is included implicitly and can inject
    // macros/declarations that change the public-header AST, so fold it in too
    // (rooted like the other path inputs) — else two source variants differing
    // only by their PCH collide on the facts filename (Codex review).
    if (!ci.getPreprocessorOpts().ImplicitPCHInclude.empty())
      preinc.push_back("p:" + absStr(ci.getPreprocessorOpts().ImplicitPCHInclude));
    // `-ffile-prefix-map`/`-fmacro-prefix-map` rewrite the strings clang emits
    // for __FILE__/__builtin_FILE(), so a public constexpr/default arg using
    // those builtins parses to different facts under otherwise-identical flags.
    // Fold the (ordered) prefix-map pairs in so the variants get distinct facts
    // files (Codex review). LangOptions::MacroPrefixMap is an ordered map.
    for (const auto &kv : lo.MacroPrefixMap)
      preinc.push_back("x:" + kv.first + "=" + kv.second);
    // VFS overlays (`-ivfsoverlay`) remap the virtual filesystem, so two builds
    // of one source with different overlays can parse different header content
    // under otherwise-identical flags. Fold the overlay files in (ordered,
    // rooted to absolute like the other path inputs), or those variants collide
    // on the facts filename and the later write truncates the earlier (Codex
    // review). Predefines cannot capture this — it is a content change, not a
    // macro change.
    for (const auto &f : hso.VFSOverlayFiles)
      preinc.push_back("v:" + absStr(f));
    // Root a relative sysroot too (but leave an unset one empty, so the cfg is
    // not made cwd-dependent when no sysroot is in play).
    std::string sysroot = hso.Sysroot.empty() ? std::string() : absStr(hso.Sysroot);
    // The Clang resource directory (`-resource-dir`) holds the compiler's
    // builtin headers (stddef.h, stdint.h, intrinsics …); two variants pointing
    // at different resource dirs can parse different builtins under otherwise
    // identical flags, so fold it in (rooted) to keep their facts files distinct
    // (Codex review).
    std::string resourceDir =
        hso.ResourceDir.empty() ? std::string() : absStr(hso.ResourceDir);
    // Fold in the compiler's full predefined-macro buffer. Every language ABI
    // mode that changes the *declared* public surface manifests as a predefined
    // macro (-fexceptions → __EXCEPTIONS, -frtti → __GXX_RTTI, -fshort-wchar →
    // __WCHAR_WIDTH__/__WCHAR_TYPE__, -fno-char8_t → __cpp_char8_t, fast-math →
    // __FAST_MATH__, …), so hashing the whole buffer discriminates every such
    // variant at once instead of chasing a hand-maintained flag allow-list one
    // Codex review at a time — the wrapper's compile_unit_id hashes the full
    // argv for the same reason. The buffer is deterministic for a given
    // invocation (it excludes __DATE__/__TIME__), so it never spuriously
    // differs; distinct clang majors legitimately differ, and the context hash
    // is not compared in C.6 (Codex review, P2).
    const std::string &predefines = ci.getPreprocessor().getPredefines();
    return H({"ctx", standard, to.Triple, sysroot, resourceDir,
              joinStrings(defs, ','), joinStrings(incs, ','),
              joinStrings(to.Features, ','), joinStrings(preinc, ','),
              predefines});
  }

  bool ParseArgs(const CompilerInstance &,
                 const std::vector<std::string> &args) override {
    // Invoke with the unambiguous cc1 form (ADR-038 Plugin injection):
    //   -Xclang -plugin-arg-abicheck-facts -Xclang out=abicheck_inputs
    //   -Xclang -plugin-arg-abicheck-facts -Xclang public-roots=include
    for (const std::string &arg : args) {
      if (consumePrefix(arg, "out=", OutDir))
        continue;
      std::string root;
      if (consumePrefix(arg, "public-roots=", root)) {
        if (!root.empty())
          Roots.push_back(root);
        continue;
      }
      if (consumePrefix(arg, "library=", Library))
        continue;
      if (consumePrefix(arg, "version=", Version))
        continue;
    }
    return true;
  }

  ActionType getActionType() override { return AddAfterMainAction; }

private:
  static bool consumePrefix(const std::string &arg, const char *prefix,
                            std::string &out) {
    std::string p(prefix);
    if (arg.rfind(p, 0) == 0) {
      out = arg.substr(p.size());
      return true;
    }
    return false;
  }

  std::string OutDir = "abicheck_inputs";
  std::vector<std::string> Roots;
  std::string Library;
  std::string Version;
};

} // namespace

static FrontendPluginRegistry::Add<FactsAction>
    X("abicheck-facts", "emit abicheck Plugin injection source facts during compile");
