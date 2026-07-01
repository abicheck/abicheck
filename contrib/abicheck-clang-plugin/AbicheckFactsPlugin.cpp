// Copyright 2026 Nikolay Petrov
//
// Licensed under the Apache License, Version 2.0 (the "License").
//
// abicheck Clang facts plugin (ADR-035 D5, ADR-038 Flow C) — REFERENCE
// IMPLEMENTATION.
//
// During a normal compile this plugin emits abicheck's normalized Flow-2 source
// facts (source_facts/*.jsonl, schema =
// abicheck.buildsource.source_abi.SourceAbiTu) straight from the AST Clang
// already built, so no second front-end pass is needed (the cost the
// `abicheck-cc` wrapper otherwise pays). The output is the same
// `abicheck_inputs/` protocol abicheck ingests via `merge`.
//
// Reference recipe (ADR-038 C.2): because the plugin reads the *clang* AST its
// reference is `abicheck/buildsource/source_extractors/clang.py`
// (`source_abi_from_clang_ast`) — NOT `base.py`, which is the castxml recipe.
// Field/hash construction here mirrors clang.py so the emitted records are
// entity-equivalent to the clang backend the plugin substitutes (the C.6
// differential-conformance gate).
//
// Coverage (ADR-038 C.7). Declaration-level fields — `id`, `qualified_name`,
// `mangled_name` (with the mangled-name rule), `signature_hash`, default-arg
// `value` for literal defaults, typedef `type_hash`/`value`, `visibility`,
// `api_relevant`, and macros (via in-compile PPCallbacks) — are implemented and
// match clang.py. The AST-*subtree* hashes — `type_hash` for records/enums and
// `body_hash` for inline/template bodies — depend on reproducing clang.py's
// alpha-renamed, commutative-normalized, build-root-stripped canonical form of
// clang's JSON AST; that is the plugin's genuine engineering risk, so those
// entities are emitted WITHOUT the subtree hash (partial, never wrong) and a
// diagnostic is recorded, exactly as C.7 sanctions. The clang wrapper/full-scan
// path stays the reference for those fields until parity is proven by C.6.
//
// This plugin links against the *loading* clang's LLVM/Clang libraries and is
// therefore ABI-locked to its LLVM major (C.5). It is `contrib/` reference and
// is never built or gated in abicheck's own CI.

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
#include "clang/AST/RecursiveASTVisitor.h"
#include "clang/Basic/LangStandard.h"
#include "clang/Basic/SourceManager.h"
#include "clang/Frontend/CompilerInstance.h"
#include "clang/Frontend/FrontendPluginRegistry.h"
#include "clang/Lex/MacroInfo.h"
#include "clang/Lex/PPCallbacks.h"
#include "clang/Lex/Preprocessor.h"

#include "llvm/ADT/ArrayRef.h"
#include "llvm/ADT/SmallString.h"
#include "llvm/ADT/StringExtras.h"
#include "llvm/ADT/StringRef.h"
#include "llvm/Support/FileSystem.h"
#include "llvm/Support/Path.h"
#include "llvm/Support/SHA256.h"
#include "llvm/Support/raw_ostream.h"

#include <cctype>
#include <cstdio>
#include <ctime>
#include <fstream>
#include <initializer_list>
#include <map>
#include <memory>
#include <set>
#include <string>
#include <vector>

using namespace clang;

namespace {

// Producer id, recorded in the manifest's `created_by` and the TU `extractor`
// field. Bump on any change to the emitted-record recipe.
constexpr const char *kPluginVersion = "0.1";

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
// Minimal JSON emission with a fixed key order, so the pack is diff-stable
// (ADR-038 C.2 "Determinism") and matches SourceEntity.to_dict/SourceAbiTu
// .to_dict field order.
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

// A normalized SourceEntity (ADR-030 D4). Empty hash fields are the sanctioned
// partial state (ADR-038 C.7); to_json emits them as "" like SourceEntity
// .to_dict does for an unset hash.
struct Entity {
  std::string id;
  std::string kind;
  std::string qualified_name;
  std::string mangled_name;
  std::string signature_hash;
  std::string body_hash;
  std::string type_hash;
  std::string value;
  std::string loc_path;
  int loc_line = 0;
  std::string loc_origin = "UNKNOWN";
  std::string visibility = "unknown";
  bool api_relevant = true;

  std::string to_json() const {
    std::string loc = "{\"path\":" + jsonStr(loc_path) +
                      ",\"line\":" + std::to_string(loc_line) +
                      ",\"origin\":" + jsonStr(loc_origin) + "}";
    // Field order mirrors SourceEntity.to_dict(); confidence is always "high"
    // (directly observed from the AST), matching clang.py's LayerConfidence.HIGH.
    return "{\"id\":" + jsonStr(id) + ",\"kind\":" + jsonStr(kind) +
           ",\"qualified_name\":" + jsonStr(qualified_name) +
           ",\"mangled_name\":" + jsonStr(mangled_name) +
           ",\"signature_hash\":" + jsonStr(signature_hash) +
           ",\"body_hash\":" + jsonStr(body_hash) +
           ",\"type_hash\":" + jsonStr(type_hash) +
           ",\"value\":" + jsonStr(value) + ",\"source_location\":" + loc +
           ",\"visibility\":" + jsonStr(visibility) +
           ",\"api_relevant\":" + (api_relevant ? "true" : "false") +
           ",\"confidence\":\"high\"}";
  }
};

// ---------------------------------------------------------------------------
// Path helpers for public-surface classification (ADR-038 C.2 visibility).
// A pragmatic reference classifier: a file is on the public surface when a
// public-root's path segments appear as a contiguous subsequence of the file's
// path segments (so `public-roots=include` matches `/proj/include/api/foo.h`,
// and an exact-file root matches its own tail). clang.py uses a richer
// include-spelling-equivalence model; matching it byte-for-byte is not required
// for identity, only visibility, which C.7 lists as decl-level/straightforward.
// ---------------------------------------------------------------------------
std::vector<std::string> pathSegments(llvm::StringRef p) {
  std::vector<std::string> segs;
  std::string cur;
  for (char c : p) {
    if (c == '/' || c == '\\') {
      if (!cur.empty() && cur != ".") {
        segs.push_back(cur);
      }
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
    for (size_t j = 0; j < needle.size(); ++j) {
      if (hay[i + j] != needle[j]) {
        ok = false;
        break;
      }
    }
    if (ok)
      return true;
  }
  return false;
}

std::string collapseWhitespace(llvm::StringRef s) {
  // Mirrors clang.py's re.sub(r"\s+", " ", ...).strip() over macro text.
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
  // foo.h -> FOO_H (mirrors clang.py::_is_include_guard stem derivation).
  llvm::StringRef base = llvm::sys::path::filename(file);
  std::string stem;
  for (char c : base) {
    if (std::isalnum(static_cast<unsigned char>(c))) {
      stem.push_back(static_cast<char>(std::toupper(static_cast<unsigned char>(c))));
    } else {
      stem.push_back('_');
    }
  }
  // Collapse runs of '_' then strip leading/trailing '_'.
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

// Strip paren/cast/ConstantExpr wrappers, matching the intent of clang.py's
// _unwrap_expr, so a lone literal under implicit casts reads as that literal.
const Expr *unwrapExpr(const Expr *e) {
  if (!e)
    return nullptr;
  const Expr *cur = e->IgnoreParenCasts();
  if (const auto *ce = dyn_cast<ConstantExpr>(cur))
    cur = ce->getSubExpr()->IgnoreParenCasts();
  return cur;
}

// A value string for a lone literal, matching clang.py's use of clang's JSON
// `value` for IntegerLiteral / CXXBoolLiteralExpr. Returns false for any other
// (compound) expression — those need the subtree hash, deferred per C.7.
bool literalValue(const Expr *e, std::string &out) {
  const Expr *core = unwrapExpr(e);
  if (!core)
    return false;
  if (const auto *il = dyn_cast<IntegerLiteral>(core)) {
    bool isSigned = core->getType()->isSignedIntegerOrEnumerationType();
    llvm::SmallString<32> s;
    il->getValue().toString(s, 10, isSigned);
    out = std::string(s.str());
    return true;
  }
  if (const auto *bl = dyn_cast<CXXBoolLiteralExpr>(core)) {
    out = bl->getValue() ? "true" : "false";
    return true;
  }
  return false;
}

// ---------------------------------------------------------------------------
// Macro capture via in-compile PPCallbacks (ADR-038 C.2 / C.7). No second
// `-E -dD` pass — that would reintroduce the extra front end Flow C exists to
// avoid. Values are token-reconstructed and whitespace-collapsed to match
// clang.py::macros_from_preprocessor's "{params} {body}" normalization. Because
// D0 requires the old and new baselines of a comparison to be produced the same
// way, this token normalization is self-consistent within the producer; the
// only place operator-adjacent spacing could differ from the textual `-E -dD`
// backend is the cross-producer C.6 gate, which finalizes macro parity.
// ---------------------------------------------------------------------------
struct MacroRecord {
  std::string value;
  std::string file;
};

class MacroCollector : public PPCallbacks {
public:
  MacroCollector(Preprocessor &pp, std::map<std::string, MacroRecord> &out)
      : PP(pp), SM(pp.getSourceManager()), Defs(out) {}

  void MacroDefined(const Token &nameTok, const MacroDirective *md) override {
    const MacroInfo *mi = md ? md->getMacroInfo() : nullptr;
    if (!mi || mi->isBuiltinMacro())
      return;
    const IdentifierInfo *ii = nameTok.getIdentifierInfo();
    if (!ii)
      return;
    SourceLocation loc = mi->getDefinitionLoc();
    if (loc.isInvalid())
      return;
    PresumedLoc pl = SM.getPresumedLoc(loc);
    if (pl.isInvalid())
      return;
    llvm::StringRef file = pl.getFilename();
    // <built-in>, <command line>, <scratch space> are not real files.
    if (file.empty() || file.starts_with("<"))
      return;
    Defs[ii->getName().str()] = MacroRecord{macroValue(mi), file.str()};
  }

  void MacroUndefined(const Token &nameTok, const MacroDefinition &,
                      const MacroDirective *) override {
    if (const IdentifierInfo *ii = nameTok.getIdentifierInfo())
      Defs.erase(ii->getName().str());
  }

private:
  std::string macroValue(const MacroInfo *mi) {
    std::string params;
    if (mi->isFunctionLike()) {
      params += "(";
      bool first = true;
      for (const IdentifierInfo *pi : mi->params()) {
        if (!first)
          params += ",";
        first = false;
        if (pi->getName() == "__VA_ARGS__")
          params += "...";
        else
          params += pi->getName().str();
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
  std::map<std::string, MacroRecord> &Defs;
};

// ---------------------------------------------------------------------------
// The AST visitor: maps public Decls -> SourceEntity records (ADR-038 C.2).
// ---------------------------------------------------------------------------
class FactsVisitor : public RecursiveASTVisitor<FactsVisitor> {
public:
  FactsVisitor(ASTContext &ctx, const std::vector<std::string> &roots,
               std::vector<Entity> &functions, std::vector<Entity> &types,
               std::vector<Entity> &templates, std::vector<Entity> &inlineBodies,
               std::vector<Entity> &constexprValues, std::set<std::string> &diags)
      : Ctx(ctx), SM(ctx.getSourceManager()), PP(ctx.getPrintingPolicy()),
        Roots(roots), Functions(functions), Types(types), Templates(templates),
        InlineBodies(inlineBodies), ConstexprValues(constexprValues),
        Diags(diags), Mangle(ctx.createMangleContext()) {}

  // We want deterministic AST-order emission (C.2 Determinism); the default
  // traversal is pre-order, which is deterministic for one TU.
  bool shouldVisitTemplateInstantiations() const { return false; }
  bool shouldVisitImplicitCode() const { return false; }

  bool VisitFunctionDecl(FunctionDecl *fd) {
    if (fd->isImplicit() || fd->getDescribedFunctionTemplate())
      return true; // the pattern is emitted by the template visitor
    if (fd->getTemplatedKind() == FunctionDecl::TK_FunctionTemplateSpecialization)
      return true; // an instantiation — noise, not a source declaration
    if (!isAccessible(fd) || fd->getNameAsString().empty())
      return true;
    std::string file, origin, visibility;
    if (!classify(fd, file, origin, visibility))
      return true;

    std::string name = fd->getQualifiedNameAsString();
    std::string sig = fd->getType().getAsString(PP);
    std::string mangled = mangledName(fd);
    std::string key = mangled.empty() ? name : mangled;
    int line = presumedLine(fd);

    std::string value;
    if (!defaultArgRepr(fd, value)) {
      value.clear();
      Diags.insert("default-argument value with a non-literal expression omitted "
                   "(subtree-hash parity pending, ADR-038 C.7)");
    }

    Entity e;
    e.id = H({"function", key, sig});
    e.kind = "function";
    e.qualified_name = name;
    e.mangled_name = mangled;
    e.signature_hash = H({"sig", sig});
    e.value = value;
    e.loc_path = file;
    e.loc_line = line;
    e.loc_origin = origin;
    e.visibility = visibility;
    Functions.push_back(e);

    // A function/method defined in a public header ships its body to consumers;
    // clang.py fingerprints it (inline_body_changed). The body_hash is a subtree
    // hash, deferred per C.7 — emit the inline entity without it so presence is
    // tracked, and record the diagnostic once.
    if (fd->doesThisDeclarationHaveABody() && fd->hasBody()) {
      Entity ib;
      ib.id = H({"inline", key, sig});
      ib.kind = "inline";
      ib.qualified_name = name;
      ib.mangled_name = mangled;
      ib.signature_hash = H({"sig", sig});
      ib.loc_path = file;
      ib.loc_line = line;
      ib.loc_origin = origin;
      ib.visibility = visibility;
      InlineBodies.push_back(ib);
      Diags.insert("inline body_hash omitted (AST-subtree hash parity pending, "
                   "ADR-038 C.7)");
    }
    return true;
  }

  bool VisitCXXRecordDecl(CXXRecordDecl *rd) {
    if (rd->isImplicit() || rd->getDescribedClassTemplate())
      return true; // template pattern handled by the template visitor
    if (isa<ClassTemplateSpecializationDecl>(rd))
      return true; // an instantiation
    if (!rd->isThisDeclarationADefinition())
      return true; // forward decls carry no meaningful type hash (clang.py)
    emitType(rd, rd->getQualifiedNameAsString(), "record");
    return true;
  }

  bool VisitEnumDecl(EnumDecl *ed) {
    if (ed->isImplicit() || !ed->isThisDeclarationADefinition())
      return true;
    emitType(ed, ed->getQualifiedNameAsString(), "enum");
    return true;
  }

  bool VisitTypedefNameDecl(TypedefNameDecl *td) {
    if (td->isImplicit())
      return true;
    if (const auto *alias = dyn_cast<TypeAliasDecl>(td))
      if (alias->getDescribedAliasTemplate())
        return true; // alias-template pattern — clang.py does not emit these
    if (!isAccessible(td))
      return true;
    std::string file, origin, visibility;
    if (!classify(td, file, origin, visibility))
      return true;
    std::string underlying = td->getUnderlyingType().getAsString(PP);
    if (underlying.empty())
      return true;
    std::string name = td->getQualifiedNameAsString();
    // Fully reproducible: typedef entities need no subtree hash (clang.py uses
    // _hash("typedef-target", underlying)).
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
    Types.push_back(e);
    return true;
  }

  bool VisitVarDecl(VarDecl *vd) {
    if (vd->isImplicit() || isa<ParmVarDecl>(vd) || !vd->isConstexpr())
      return true;
    if (vd->getParentFunctionOrMethod())
      return true; // a function-local constexpr is not part of the surface
    if (!isAccessible(vd) || vd->getNameAsString().empty())
      return true;
    std::string file, origin, visibility;
    if (!classify(vd, file, origin, visibility))
      return true;
    std::string value;
    if (!literalValue(vd->getInit(), value)) {
      // A compound constexpr initializer needs the subtree hash (which also
      // feeds this entity's id); rather than emit a divergent id, skip it and
      // record the partial state (clang.py stays the reference — C.7).
      Diags.insert("constexpr with a non-literal initializer skipped "
                   "(value/id subtree-hash parity pending, ADR-038 C.7)");
      return true;
    }
    std::string name = vd->getQualifiedNameAsString();
    Entity e;
    e.id = H({"constexpr", name, value});
    e.kind = "constexpr";
    e.qualified_name = name;
    e.mangled_name = mangledName(vd);
    e.value = value;
    e.loc_path = file;
    e.loc_line = presumedLine(vd);
    e.loc_origin = origin;
    e.visibility = visibility;
    ConstexprValues.push_back(e);
    return true;
  }

  bool VisitFunctionTemplateDecl(FunctionTemplateDecl *td) {
    emitTemplate(td);
    return true;
  }

  bool VisitClassTemplateDecl(ClassTemplateDecl *td) {
    emitTemplate(td);
    return true;
  }

private:
  bool isAccessible(const Decl *d) const {
    AccessSpecifier as = d->getAccess();
    return as != AS_private && as != AS_protected;
  }

  int presumedLine(const Decl *d) const {
    PresumedLoc pl = SM.getPresumedLoc(SM.getExpansionLoc(d->getLocation()));
    return pl.isValid() ? static_cast<int>(pl.getLine()) : 0;
  }

  // Classify a decl's declaring file into the public surface. Returns false
  // (drop) for system headers and anything not under a public root — mirroring
  // clang.py, which drops UNKNOWN/private decls from the L4 surface.
  bool classify(const Decl *d, std::string &file, std::string &origin,
                std::string &visibility) const {
    SourceLocation loc = SM.getExpansionLoc(d->getLocation());
    if (loc.isInvalid())
      return false;
    if (SM.isInSystemHeader(loc))
      return false;
    PresumedLoc pl = SM.getPresumedLoc(loc);
    if (pl.isInvalid())
      return false;
    file = pl.getFilename();
    std::vector<std::string> fileSegs = pathSegments(file);
    for (const std::string &root : Roots) {
      if (isContiguousSubsequence(fileSegs, pathSegments(root))) {
        origin = "PUBLIC_HEADER";
        visibility = "public_header";
        return true;
      }
    }
    return false;
  }

  std::string mangledName(const NamedDecl *nd) {
    // clang.py::_mangled: take clang's mangledName; if it equals the plain name
    // (extern "C", some ctors) leave it empty so identity() falls back to
    // qualified_name#signature_hash and keeps unmangled overloads distinct.
    if (!Mangle || !Mangle->shouldMangleDeclName(nd))
      return "";
    std::string out;
    llvm::raw_string_ostream os(out);
    if (const auto *cd = dyn_cast<CXXConstructorDecl>(nd))
      Mangle->mangleName(GlobalDecl(cd, Ctor_Complete), os);
    else if (const auto *dd = dyn_cast<CXXDestructorDecl>(nd))
      Mangle->mangleName(GlobalDecl(dd, Dtor_Complete), os);
    else if (const auto *fd = dyn_cast<FunctionDecl>(nd))
      Mangle->mangleName(GlobalDecl(fd), os);
    else if (const auto *vd = dyn_cast<VarDecl>(nd))
      Mangle->mangleName(GlobalDecl(vd), os);
    else
      return "";
    os.flush();
    if (out == nd->getNameAsString())
      return "";
    return out;
  }

  // Build clang.py::_default_arg_repr: p<pos>=<literal-or-...> per defaulted
  // parameter. Returns false when any defaulted parameter has a non-literal
  // default (needs the deferred subtree hash) — the caller then omits `value`.
  bool defaultArgRepr(const FunctionDecl *fd, std::string &out) {
    out.clear();
    int pos = -1;
    bool first = true;
    for (const ParmVarDecl *p : fd->parameters()) {
      ++pos;
      if (!p->hasDefaultArg())
        continue;
      if (p->hasUninstantiatedDefaultArg())
        return false;
      std::string rep;
      if (!literalValue(p->getDefaultArg(), rep))
        return false;
      if (!first)
        out += ",";
      first = false;
      out += "p" + std::to_string(pos) + "=" + rep;
    }
    return true;
  }

  void emitType(const TagDecl *td, const std::string &name,
                const std::string &kind) {
    if (!isAccessible(td))
      return;
    std::string file, origin, visibility;
    if (!classify(td, file, origin, visibility))
      return;
    // type_hash is a subtree hash, deferred per C.7 — emit the type so its
    // presence/removal is tracked and record the diagnostic once.
    Entity e;
    e.id = H({"type", name});
    e.kind = kind;
    e.qualified_name = name;
    e.loc_path = file;
    e.loc_line = presumedLine(td);
    e.loc_origin = origin;
    e.visibility = visibility;
    Types.push_back(e);
    Diags.insert("record/enum type_hash omitted (AST-subtree hash parity "
                 "pending, ADR-038 C.7)");
  }

  void emitTemplate(const TemplateDecl *td) {
    if (td->isImplicit() || !isAccessible(td) ||
        td->getNameAsString().empty())
      return;
    std::string file, origin, visibility;
    if (!classify(td, file, origin, visibility))
      return;
    std::string name = td->getQualifiedNameAsString();
    Entity e;
    e.id = H({"template", name});
    e.kind = "template";
    e.qualified_name = name;
    e.loc_path = file;
    e.loc_line = presumedLine(td);
    e.loc_origin = origin;
    e.visibility = visibility;
    Templates.push_back(e);
    Diags.insert("template body_hash omitted (AST-subtree hash parity pending, "
                 "ADR-038 C.7)");
  }

  ASTContext &Ctx;
  SourceManager &SM;
  PrintingPolicy PP;
  const std::vector<std::string> &Roots;
  std::vector<Entity> &Functions;
  std::vector<Entity> &Types;
  std::vector<Entity> &Templates;
  std::vector<Entity> &InlineBodies;
  std::vector<Entity> &ConstexprValues;
  std::set<std::string> &Diags;
  std::unique_ptr<MangleContext> Mangle;
};

// ---------------------------------------------------------------------------
// The consumer: run the visitor after the real codegen, assemble one SourceAbiTu
// per TU, and append it to a per-TU JSONL file (ADR-038 C.1/C.4).
// ---------------------------------------------------------------------------
class FactsConsumer : public ASTConsumer {
public:
  FactsConsumer(std::string outDir, std::vector<std::string> roots,
                std::string library, std::string version,
                std::map<std::string, MacroRecord> &macros)
      : OutDir(std::move(outDir)), Roots(std::move(roots)),
        Library(std::move(library)), Version(std::move(version)),
        Macros(macros) {}

  void HandleTranslationUnit(ASTContext &ctx) override {
    SourceManager &sm = ctx.getSourceManager();

    std::vector<Entity> functions, types, templates, inlineBodies, constexprValues;
    std::set<std::string> diags;
    FactsVisitor visitor(ctx, Roots, functions, types, templates, inlineBodies,
                         constexprValues, diags);
    visitor.TraverseDecl(ctx.getTranslationUnitDecl());

    std::vector<Entity> macros = collectMacros(diags);

    // Provenance for this TU.
    std::string source;
    if (const auto *fe = sm.getFileEntryForID(sm.getMainFileID()))
      source = fe->getName().str();
    std::string standard;
    if (ctx.getLangOpts().LangStd != LangStandard::lang_unspecified)
      standard =
          LangStandard::getLangStandardForKind(ctx.getLangOpts().LangStd)
              .getName();
    std::string triple = ctx.getTargetInfo().getTriple().str();
    std::string ctxHash = H({"ctx", standard, triple});
    std::string cfg = ctxHash.substr(std::string("sha256:").size(), 12);
    std::string tuId = "cu://" + source + "#cfg:" + cfg;
    std::string targetId = Library.empty() ? "" : "target://" + Library;

    if (!writeTu(source, tuId, targetId, ctxHash, functions, types, templates,
                 inlineBodies, constexprValues, macros, diags)) {
      // Best-effort: a fact-emission failure must never abort codegen
      // (ADR-038 C.3). AddAfterMainAction already ran, so the object is safe;
      // we only warn.
      llvm::errs() << "abicheck-facts: could not write source facts to "
                   << OutDir << "\n";
    }
  }

private:
  std::vector<Entity> collectMacros(std::set<std::string> &diags) {
    std::vector<Entity> out;
    bool any = false;
    for (const auto &kv : Macros) { // std::map iterates sorted by name (C.2)
      const std::string &name = kv.first;
      const std::string &value = kv.second.value;
      const std::string &file = kv.second.file;
      // Drop include guards (empty value whose name is the file's guard token),
      // mirroring clang.py::_is_include_guard.
      if (value.empty()) {
        std::string up = name;
        for (char &c : up)
          c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
        // strip surrounding underscores
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
    std::vector<std::string> fileSegs = pathSegments(file);
    for (const std::string &root : Roots) {
      if (isContiguousSubsequence(fileSegs, pathSegments(root))) {
        origin = "PUBLIC_HEADER";
        visibility = "public_header";
        return true;
      }
    }
    return false;
  }

  // Assemble the SourceAbiTu JSON (field order mirrors SourceAbiTu.to_dict) and
  // append it as one line to the per-TU facts file. Also ensures the pack's
  // source_facts dir and manifest.json exist (C.4), matching init_inputs_pack.
  bool writeTu(const std::string &source, const std::string &tuId,
               const std::string &targetId, const std::string &ctxHash,
               const std::vector<Entity> &functions,
               const std::vector<Entity> &types,
               const std::vector<Entity> &templates,
               const std::vector<Entity> &inlineBodies,
               const std::vector<Entity> &constexprValues,
               const std::vector<Entity> &macros,
               const std::set<std::string> &diags) {
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

    std::string extractor = "{\"name\":\"abicheck-clang-plugin\",\"version\":" +
                            jsonStr(kPluginVersion) + "}";
    std::vector<std::string> diagVec(diags.begin(), diags.end());

    std::string tu =
        "{\"schema_version\":1,\"tu_id\":" + jsonStr(tuId) +
        ",\"target_id\":" + jsonStr(targetId) + ",\"extractor\":" + extractor +
        ",\"compile_context_hash\":" + jsonStr(ctxHash) +
        ",\"source\":" + jsonStr(source) + ",\"public_header_roots\":" +
        jsonStrArray(Roots) + ",\"declarations\":[]" +
        ",\"types\":" + arr(types) + ",\"functions\":" + arr(functions) +
        ",\"variables\":[]" + ",\"macros\":" + arr(macros) +
        ",\"templates\":" + arr(templates) +
        ",\"inline_bodies\":" + arr(inlineBodies) +
        ",\"constexpr_values\":" + arr(constexprValues) +
        ",\"source_edges\":[],\"diagnostics\":" + jsonStrArray(diagVec) +
        ",\"read_files\":[]}";

    // Per-TU, race-free filename: <stem>.<sha256(source)[:12]>.jsonl, mirroring
    // inputs_emit.facts_filename so parallel -j compiles never share a file.
    llvm::StringRef stem = llvm::sys::path::filename(source);
    std::string factsFile = factsDir + "/" +
                            (stem.empty() ? std::string("tu") : stem.str()) +
                            "." + sha256Hex(source).substr(0, 12) + ".jsonl";
    std::ofstream out(factsFile, std::ios::app);
    if (!out)
      return false;
    out << tu << "\n";
    return true;
  }

  // Idempotent, atomic manifest write (matches inputs_emit.init_inputs_pack /
  // _write_manifest). Only the first TU to observe a missing manifest writes it;
  // concurrent writers each produce identical content, so the atomic rename is
  // safe regardless of which one wins.
  void ensureManifest() {
    std::string manifestPath = OutDir + "/manifest.json";
    if (llvm::sys::fs::exists(manifestPath))
      return;
    std::string createdBy =
        std::string("abicheck-clang-plugin ") + kPluginVersion;
    std::string manifest =
        "{\n  \"abicheck_inputs_version\": 1,\n  \"binary\": \"\",\n"
        "  \"compile_db\": \"\",\n  \"created_at\": " +
        jsonStr(nowIso8601Utc()) + ",\n  \"created_by\": " + jsonStr(createdBy) +
        ",\n  \"exported_symbols\": [],\n  \"headers\": [],\n"
        "  \"kind\": \"abicheck_inputs\",\n  \"library\": " + jsonStr(Library) +
        ",\n  \"source_facts\": [],\n  \"version\": " + jsonStr(Version) +
        "\n}\n";

    llvm::SmallString<128> tmp;
    int fd = -1;
    if (llvm::sys::fs::createUniqueFile(
            llvm::Twine(OutDir) + "/.manifest.%%%%%%.tmp", fd, tmp))
      return;
    {
      llvm::raw_fd_ostream os(fd, /*shouldClose=*/true);
      os << manifest;
    }
    // Atomic publish; if another writer beat us, the rename still yields a valid
    // manifest (identical content).
    if (llvm::sys::fs::rename(tmp, manifestPath))
      llvm::sys::fs::remove(tmp);
  }

  std::string OutDir;
  std::vector<std::string> Roots;
  std::string Library;
  std::string Version;
  std::map<std::string, MacroRecord> &Macros;
};

// ---------------------------------------------------------------------------
// The plugin action. AddAfterMainAction runs after the real codegen, so fact
// emission never perturbs the object output (ADR-038 C.1).
// ---------------------------------------------------------------------------
class FactsAction : public PluginASTAction {
public:
  std::unique_ptr<ASTConsumer> CreateASTConsumer(CompilerInstance &ci,
                                                  llvm::StringRef) override {
    // Register the macro collector on the live preprocessor so macros are
    // captured in-compile, with no second -E pass (ADR-038 C.2).
    Preprocessor &pp = ci.getPreprocessor();
    pp.addPPCallbacks(std::make_unique<MacroCollector>(pp, Macros));
    return std::make_unique<FactsConsumer>(OutDir, Roots, Library, Version,
                                           Macros);
  }

  bool ParseArgs(const CompilerInstance &,
                 const std::vector<std::string> &args) override {
    // Invoke with the unambiguous cc1 form (ADR-038 Flow C):
    //   -Xclang -plugin-arg-abicheck-facts -Xclang out=abicheck_inputs
    //   -Xclang -plugin-arg-abicheck-facts -Xclang public-roots=include
    // The -fplugin-arg-abicheck-facts-<arg> shorthand mis-parses a *hyphenated*
    // plugin name, so it is not the documented form.
    for (const std::string &arg : args) {
      if (consumePrefix(arg, "out=", OutDir))
        continue;
      std::string root;
      if (consumePrefix(arg, "public-roots=", root)) {
        if (!root.empty())
          Roots.push_back(root); // repeatable
        continue;
      }
      if (consumePrefix(arg, "library=", Library))
        continue;
      if (consumePrefix(arg, "version=", Version))
        continue;
    }
    return true;
  }

  // Emit facts by default during a normal compile (no explicit -plugin needed
  // beyond -fplugin); the action attaches after the main codegen action.
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
  std::map<std::string, MacroRecord> Macros;
};

} // namespace

static FrontendPluginRegistry::Add<FactsAction>
    X("abicheck-facts", "emit abicheck Flow-C source facts during compile");
