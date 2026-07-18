// Copyright 2026 Nikolay Petrov
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
//
// abicheck-clang-layout-tool (G28 Phase 4): a small LibTooling program that
// walks every complete, non-dependent CXXRecordDecl in a translation unit and
// serializes the REAL compiled layout Clang's own Sema/CodeGen compute
// internally (clang::ASTRecordLayout) -- size, alignment, field offsets, base
// offsets, and the primary vtable pointer's offset -- as JSON on stdout.
//
// This is the one capability abicheck's direct-clang L2 backend
// (dumper_clang.py, `-ast-dump=json`) structurally cannot provide on its
// own: clang's plain JSON AST dump is syntactic only (declarations, types,
// signatures) and never computes a record's actual layout, which is exactly
// why CastXML -- which runs its own bundled Clang internally and exports the
// layout it already computed -- remains abicheck's sole layout source today.
// See docs/development/plans/g28-castxml-clang-l2-parity-hardening.md,
// "Phase 4 -- a Clang ASTRecordLayout plugin".
//
// Deliberately NOT attempted here (documented, not silently dropped):
// - Full vtable slot enumeration / thunk offsets: needs clang::VTableContext
//   (ItaniumVTableContext), a materially larger surface than record layout,
//   and the G28 plan scoped this phase to size/alignment/offsets/vptr
//   placement specifically.
// - Anonymous-aggregate-flattened fields: Python's own RecordType.fields
//   already flattens `struct Foo { union { int a; }; };`-style anonymous
//   members (dumper_clang.py); this tool emits only DIRECT FieldDecls, and
//   the Python-side merge (abicheck/clang_layout_tool.py) matches purely by
//   name, so a flattened field this tool never named is simply left alone
//   rather than mismatched.
// - Bitfield-specific reporting beyond the plain bit offset
//   (ASTRecordLayout::getFieldOffset already returns the correct bit offset
//   for a bitfield exactly as it does for an ordinary field).
//
// Output is always well-formed JSON on stdout, even on a parse error
// (`"ok": false` with whatever records the partial AST still yielded) --
// matching ADR-028 D3's "degrade gracefully, never abort silently"
// convention the rest of abicheck's optional evidence layers already follow.
// The exit code is always 0 for the same reason: a caller should look at
// `"ok"` in the JSON, not the process exit status.

#include "clang/AST/ASTConsumer.h"
#include "clang/AST/ASTContext.h"
#include "clang/AST/Decl.h"
#include "clang/AST/DeclCXX.h"
#include "clang/AST/RecordLayout.h"
#include "clang/AST/RecursiveASTVisitor.h"
#include "clang/Frontend/CompilerInstance.h"
#include "clang/Frontend/FrontendAction.h"
#include "clang/Tooling/CommonOptionsParser.h"
#include "clang/Tooling/Tooling.h"
#include "llvm/Support/CommandLine.h"
#include "llvm/Support/Casting.h"
#include "llvm/Support/raw_ostream.h"

#include <memory>
#include <string>
#include <vector>

using namespace clang;
using namespace clang::tooling;

namespace {

// Minimal JSON string escaping -- the only untrusted-ish content here is
// source-derived identifiers (qualified names), which can't contain control
// characters in valid C++, but escape defensively anyway.
std::string jsonEscape(llvm::StringRef s) {
  std::string out;
  out.reserve(s.size() + 8);
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
    case '\t':
      out += "\\t";
      break;
    default:
      if (static_cast<unsigned char>(c) < 0x20) {
        char buf[8];
        snprintf(buf, sizeof(buf), "\\u%04x", static_cast<unsigned char>(c));
        out += buf;
      } else {
        out += c;
      }
    }
  }
  return out;
}

// The Itanium primary-vtable-pointer's ABSOLUTE bit offset within *RD*, or
// std::nullopt if RD isn't polymorphic (no vtable pointer at all). Every
// clang ASTRecordLayout base-offset accessor already returns an offset
// relative to the record BEING QUERIED (never to some intermediate parent),
// so no manual recursion is needed once the primary base is identified.
std::optional<uint64_t> vptrOffsetBits(const CXXRecordDecl *RD,
                                       const ASTRecordLayout &Layout) {
  if (!RD->isDynamicClass())
    return std::nullopt;
  if (Layout.hasOwnVFPtr())
    return 0;
  const CXXRecordDecl *Primary = Layout.getPrimaryBase();
  if (Primary == nullptr)
    return std::nullopt;
  CharUnits Offset = Layout.isPrimaryBaseVirtual()
                          ? Layout.getVBaseClassOffset(Primary)
                          : Layout.getBaseClassOffset(Primary);
  return static_cast<uint64_t>(Offset.getQuantity()) * 8;
}

// Emits the JSON shared by every record kind (C struct/union or C++ class):
// the opening brace, qualified_name/size/align/dsize, no trailing comma.
void emitRecordHeader(llvm::raw_string_ostream &OS, const RecordDecl *RD,
                      const ASTRecordLayout &Layout) {
  OS << "{";
  OS << "\"qualified_name\":\"" << jsonEscape(RD->getQualifiedNameAsString())
     << "\",";
  OS << "\"size_bits\":" << (Layout.getSize().getQuantity() * 8) << ",";
  OS << "\"alignment_bits\":" << (Layout.getAlignment().getQuantity() * 8)
     << ",";
  OS << "\"data_size_bits\":" << (Layout.getDataSize().getQuantity() * 8)
     << ",";
}

// Emits `"fields": [...]` (no trailing comma) -- FieldDecl/getFieldIndex()/
// getFieldOffset() are all declared on the shared RecordDecl base, so this
// is identical for a plain C record and a C++ class.
void emitFields(llvm::raw_string_ostream &OS, const RecordDecl *RD,
                const ASTRecordLayout &Layout) {
  OS << "\"fields\":[";
  bool firstField = true;
  for (const FieldDecl *FD : RD->fields()) {
    if (!firstField)
      OS << ",";
    firstField = false;
    uint64_t OffsetBits = Layout.getFieldOffset(FD->getFieldIndex());
    OS << "{\"name\":\"" << jsonEscape(FD->getNameAsString())
       << "\",\"offset_bits\":" << OffsetBits << "}";
  }
  OS << "]";
}

class LayoutVisitor : public RecursiveASTVisitor<LayoutVisitor> {
public:
  LayoutVisitor(ASTContext &Ctx, std::vector<std::string> &Records)
      : Context(Ctx), Records(Records) {}

  // A plain C struct/union (`--lang c` / a C header) is an ordinary
  // RecordDecl, not a CXXRecordDecl -- C has no classes, so
  // VisitCXXRecordDecl below never fires for it at all. is_standard_layout/
  // is_trivially_copyable/vptr_offset_bits/bases are C++-only concepts and
  // are simply omitted (not emitted as null) for a C record.
  bool VisitRecordDecl(const RecordDecl *RD) {
    // A C++ class is fully handled by VisitCXXRecordDecl below --
    // RecursiveASTVisitor's WalkUpFromCXXRecordDecl calls WalkUpFromRecordDecl
    // (hence VisitRecordDecl) FIRST and then VisitCXXRecordDecl, so both fire
    // for the SAME node; skip here to avoid emitting a C++ class twice.
    if (isa<CXXRecordDecl>(RD))
      return true;
    if (!RD->isCompleteDefinition())
      return true;

    const ASTRecordLayout &Layout = Context.getASTRecordLayout(RD);
    std::string json;
    llvm::raw_string_ostream OS(json);
    emitRecordHeader(OS, RD, Layout);
    emitFields(OS, RD, Layout);
    OS << "}";
    Records.push_back(OS.str());
    return true;
  }

  bool VisitCXXRecordDecl(const CXXRecordDecl *RD) {
    if (!RD->isCompleteDefinition() || RD->isDependentType())
      return true;
    // An uninstantiated class template's own pattern body has no fixed
    // layout for any one instantiation -- only concrete records and actual
    // specializations (which are NOT dependent) reach getASTRecordLayout.
    if (RD->getDescribedClassTemplate() != nullptr)
      return true;

    const ASTRecordLayout &Layout = Context.getASTRecordLayout(RD);

    std::string json;
    llvm::raw_string_ostream OS(json);
    emitRecordHeader(OS, RD, Layout);
    OS << "\"is_standard_layout\":"
       << (RD->isStandardLayout() ? "true" : "false") << ",";
    OS << "\"is_trivially_copyable\":"
       << (RD->isTriviallyCopyable() ? "true" : "false") << ",";

    OS << "\"vptr_offset_bits\":";
    if (auto vptr = vptrOffsetBits(RD, Layout))
      OS << *vptr;
    else
      OS << "null";
    OS << ",";

    emitFields(OS, RD, Layout);
    OS << ",";

    // Direct non-virtual bases, then every virtual base (clang already
    // de-duplicates a repeated virtual base reached via multiple paths).
    OS << "\"bases\":[";
    bool firstBase = true;
    for (const CXXBaseSpecifier &Base : RD->bases()) {
      if (Base.isVirtual())
        continue;
      const CXXRecordDecl *BaseRD = Base.getType()->getAsCXXRecordDecl();
      if (BaseRD == nullptr)
        continue;
      BaseRD = BaseRD->getDefinition();
      if (BaseRD == nullptr)
        continue;
      if (!firstBase)
        OS << ",";
      firstBase = false;
      CharUnits Offset = Layout.getBaseClassOffset(BaseRD);
      OS << "{\"name\":\"" << jsonEscape(BaseRD->getQualifiedNameAsString())
         << "\",\"offset_bits\":" << (Offset.getQuantity() * 8)
         << ",\"is_virtual\":false}";
    }
    for (const CXXBaseSpecifier &VBase : RD->vbases()) {
      const CXXRecordDecl *BaseRD = VBase.getType()->getAsCXXRecordDecl();
      if (BaseRD == nullptr)
        continue;
      BaseRD = BaseRD->getDefinition();
      if (BaseRD == nullptr)
        continue;
      if (!firstBase)
        OS << ",";
      firstBase = false;
      CharUnits Offset = Layout.getVBaseClassOffset(BaseRD);
      OS << "{\"name\":\"" << jsonEscape(BaseRD->getQualifiedNameAsString())
         << "\",\"offset_bits\":" << (Offset.getQuantity() * 8)
         << ",\"is_virtual\":true}";
    }
    OS << "]";
    OS << "}";
    Records.push_back(OS.str());
    return true;
  }

private:
  ASTContext &Context;
  std::vector<std::string> &Records;
};

class LayoutConsumer : public ASTConsumer {
public:
  explicit LayoutConsumer(std::vector<std::string> &Records)
      : Records(Records) {}
  void HandleTranslationUnit(ASTContext &Context) override {
    LayoutVisitor Visitor(Context, Records);
    Visitor.TraverseDecl(Context.getTranslationUnitDecl());
  }

private:
  std::vector<std::string> &Records;
};

class LayoutAction : public ASTFrontendAction {
public:
  explicit LayoutAction(std::vector<std::string> &Records)
      : Records(Records) {}
  std::unique_ptr<ASTConsumer> CreateASTConsumer(CompilerInstance &,
                                                  llvm::StringRef) override {
    return std::make_unique<LayoutConsumer>(Records);
  }

private:
  std::vector<std::string> &Records;
};

class LayoutActionFactory : public FrontendActionFactory {
public:
  explicit LayoutActionFactory(std::vector<std::string> &Records)
      : Records(Records) {}
  std::unique_ptr<FrontendAction> create() override {
    return std::make_unique<LayoutAction>(Records);
  }

private:
  std::vector<std::string> &Records;
};

} // namespace

static llvm::cl::OptionCategory ToolCategory("abicheck-clang-layout-tool options");

int main(int argc, const char **argv) {
  auto ExpectedParser = CommonOptionsParser::create(argc, argv, ToolCategory);
  if (!ExpectedParser) {
    llvm::errs() << llvm::toString(ExpectedParser.takeError());
    llvm::outs() << "{\"ok\":false,\"error\":\"argument parsing failed\","
                    "\"records\":[]}\n";
    return 0;
  }
  CommonOptionsParser &OptionsParser = ExpectedParser.get();
  ClangTool Tool(OptionsParser.getCompilations(),
                 OptionsParser.getSourcePathList());

  std::vector<std::string> Records;
  LayoutActionFactory Factory(Records);
  int Result = Tool.run(&Factory);

  llvm::outs() << "{\"ok\":" << (Result == 0 ? "true" : "false")
               << ",\"records\":[";
  for (size_t i = 0; i < Records.size(); ++i) {
    if (i > 0)
      llvm::outs() << ",";
    llvm::outs() << Records[i];
  }
  llvm::outs() << "]}\n";
  // Always exit 0: a non-zero clang parse result still leaves partial,
  // still-useful records in a recoverable-error case (matches
  // dumper._castxml_dump's own "parse past recoverable errors" convention);
  // the caller inspects "ok" in the JSON rather than the process exit code.
  return 0;
}
