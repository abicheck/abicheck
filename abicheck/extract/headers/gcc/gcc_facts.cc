// Experimental GCC fact acquisition. GPL-compatible GCC plugin.
#include "gcc-plugin.h"
#include "plugin-version.h"
#include "tree.h"
#ifdef GCC_CPP
#include "cp/cp-tree.h"
#endif
#include "tree-pretty-print.h"
#include "langhooks.h"
#include "diagnostic.h"
#include <cstdio>
#include <string>
#include <unordered_set>
#include <vector>
int plugin_is_GPL_compatible;
static FILE *out;
static std::unordered_set<unsigned> seen_types, seen_decls;
static std::string q(const char *p) {
  std::string s="\""; if(p) for(const unsigned char c:std::string(p)) {
    switch(c) {case '"':s+="\\\"";break;case '\\':s+="\\\\";break;
    case '\n':s+="\\n";break;case '\r':s+="\\r";break;case '\t':s+="\\t";break;
    default:if(c<32){char b[8];snprintf(b,sizeof(b),"\\u%04x",c);s+=b;}else s+=c;}
  } return s+"\"";
}
static std::string spelled(tree t) {
#ifdef GCC_CPP
  if(TYPE_P(t)) return type_as_string(t,TFF_SCOPE | TFF_NO_OMIT_DEFAULT_TEMPLATE_ARGUMENTS);
#endif
  char *buf=nullptr; size_t n=0; FILE *f=open_memstream(&buf,&n);
  print_generic_expr(f,t,TDF_NONE); fclose(f); std::string s(buf,n);free(buf);return s;
}
static std::string unalias(tree t) {
  std::string text=spelled(TYPE_MAIN_VARIANT(t));
  const char *cv=TYPE_READONLY(t)?(TYPE_VOLATILE(t)?"const volatile":"const"):(TYPE_VOLATILE(t)?"volatile":"");
  if(!*cv)return text;
  return POINTER_TYPE_P(t)?text+" "+cv:std::string(cv)+" "+text;
}
static std::string leaf(tree t) {
#ifdef GCC_CPP
  return type_as_string(t,TFF_NO_OMIT_DEFAULT_TEMPLATE_ARGUMENTS);
#else
  tree n=TYPE_NAME(t);return n?(DECL_P(n)?(DECL_NAME(n)?IDENTIFIER_POINTER(DECL_NAME(n)):""):IDENTIFIER_POINTER(n)):spelled(t);
#endif
}
static std::string number(tree t) {
  if(!t || TREE_CODE(t)!=INTEGER_CST) return "null";
  if(tree_fits_shwi_p(t)) return std::to_string(tree_to_shwi(t));
  if(tree_fits_uhwi_p(t)) return std::to_string(tree_to_uhwi(t));
  return "null";
}
static const char *name(tree d) {return DECL_NAME(d)?IDENTIFIER_POINTER(DECL_NAME(d)):"";}
static std::string identity(tree t) {
  while(t && (POINTER_TYPE_P(t)||TREE_CODE(t)==REFERENCE_TYPE||TREE_CODE(t)==ARRAY_TYPE))t=TREE_TYPE(t);
  if(!t || !(RECORD_OR_UNION_TYPE_P(t)||TREE_CODE(t)==ENUMERAL_TYPE))return "null";
  return q(spelled(TYPE_MAIN_VARIANT(t)).c_str());
}
static unsigned pointer_depth(tree t) {unsigned n=0;while(t&&(POINTER_TYPE_P(t)||TREE_CODE(t)==REFERENCE_TYPE)){n++;t=TREE_TYPE(t);}return n;}
static const char *param_kind(tree t) {return TREE_CODE(t)==REFERENCE_TYPE?(TYPE_REF_IS_RVALUE(t)?"rvalue_ref":"reference"):POINTER_TYPE_P(t)?"pointer":"value";}
static void abi(const char *key,tree t) {
  tree base=t;unsigned HOST_WIDE_INT lanes=0;
  if(TREE_CODE(t)==VECTOR_TYPE) {
    if(!TYPE_VECTOR_SUBPARTS(t).is_constant(&lanes)){fprintf(out,",\"%s\":null",key);return;}
    base=TREE_TYPE(t);
  }
  const char *enc=nullptr;
  switch(TREE_CODE(base)) {
    case BOOLEAN_TYPE:enc="boolean";break;
    case INTEGER_TYPE:case ENUMERAL_TYPE:enc=TYPE_UNSIGNED(base)?"unsigned":"signed";break;
    case REAL_TYPE:enc="float";break;
    default:break;
  }
  if(!enc||!TYPE_SIZE_UNIT(t)||!tree_fits_uhwi_p(TYPE_SIZE_UNIT(t))||!TYPE_SIZE(t)||!tree_fits_uhwi_p(TYPE_SIZE(t))||tree_to_uhwi(TYPE_SIZE(t))%BITS_PER_UNIT) {fprintf(out,",\"%s\":null",key);return;}
  fprintf(out,",\"%s\":{\"encoding\":%s,\"size\":%llu,\"alignment\":%u,\"lanes\":%llu}",key,q(enc).c_str(),(unsigned long long)tree_to_uhwi(TYPE_SIZE_UNIT(t)),TYPE_ALIGN(t)/BITS_PER_UNIT,(unsigned long long)lanes);
}
static void scope(tree ctx) {
  std::vector<tree> scopes;
  while(ctx && (TYPE_P(ctx)||DECL_P(ctx))) {
    if(TYPE_P(ctx) && RECORD_OR_UNION_TYPE_P(ctx))scopes.push_back(ctx);
#ifdef GCC_CPP
    if(TREE_CODE(ctx)==NAMESPACE_DECL && ctx!=global_namespace)scopes.push_back(ctx);
#endif
    ctx=TYPE_P(ctx)?TYPE_CONTEXT(ctx):DECL_CONTEXT(ctx);
  }
  fprintf(out,",\"scope\":[");bool comma=false;
  for(auto it=scopes.rbegin();it!=scopes.rend();++it) {
    tree c=*it;bool record=TYPE_P(c);
    fprintf(out,"%s{\"kind\":%s,\"name\":%s}",comma?",":"",q(record?"record":
#ifdef GCC_CPP
      !DECL_NAME(c)?"anonymous_namespace":DECL_NAMESPACE_INLINE_P(c)?"inline_namespace":
#endif
      "namespace").c_str(),q(record?leaf(c).c_str():name(c)).c_str());comma=true;
  }fprintf(out,"]");
}
static void source(tree d) {
  location_t l=DECL_SOURCE_LOCATION(d);
  fprintf(out,",\"source\":%s,\"line\":%d",q(LOCATION_FILE(l)).c_str(),LOCATION_LINE(l));
  fprintf(out,",\"system_header\":%s",in_system_header_at(l)?"true":"false");
}
static void attributes(tree d) {
  fprintf(out,",\"attribute_details\":[");bool comma=false;
  tree lists[2]={DECL_ATTRIBUTES(d),TREE_TYPE(d)?TYPE_ATTRIBUTES(TREE_TYPE(d)):nullptr};
  for(tree list:lists)for(tree a=list;a;a=TREE_CHAIN(a)) {
    tree id=TREE_PURPOSE(a);if(!id||TREE_CODE(id)!=IDENTIFIER_NODE)continue;
    fprintf(out,"%s{\"name\":%s,\"arguments\":[",comma?",":"",q(IDENTIFIER_POINTER(id)).c_str());comma=true;
    bool argcomma=false;for(tree arg=TREE_VALUE(a);arg && TREE_CODE(arg)==TREE_LIST;arg=TREE_CHAIN(arg)) {
      tree value=TREE_VALUE(arg);
      fprintf(out,"%s%s",argcomma?",":"",value?q(spelled(value).c_str()).c_str():"null");argcomma=true;
    }fprintf(out,"]}");
  }fprintf(out,"]");
  const char *message=nullptr;
  for(tree a=DECL_ATTRIBUTES(d);a;a=TREE_CHAIN(a))if(TREE_CODE(TREE_PURPOSE(a))==IDENTIFIER_NODE && !strcmp(IDENTIFIER_POINTER(TREE_PURPOSE(a)),"deprecated")) {
    tree args=TREE_VALUE(a);if(args && TREE_VALUE(args) && TREE_CODE(TREE_VALUE(args))==STRING_CST)message=TREE_STRING_POINTER(TREE_VALUE(args));
  }
  fprintf(out,",\"deprecated\":%s",TREE_DEPRECATED(d)?q(message?message:"").c_str():"null");
}
static bool local_scope(tree ctx) {
  while(ctx && (TYPE_P(ctx)||DECL_P(ctx))) {
    if(TREE_CODE(ctx)==FUNCTION_DECL)return true;
    ctx=TYPE_P(ctx)?TYPE_CONTEXT(ctx):DECL_CONTEXT(ctx);
  }return false;
}
static void decl(void *data,void *) {
  tree d=(tree)data; if(!d||!DECL_P(d)||!DECL_NAME(d)||local_scope(DECL_CONTEXT(d))||DECL_SOURCE_LOCATION(d)<=BUILTINS_LOCATION||seen_decls.count(DECL_UID(d)))return;
  seen_decls.insert(DECL_UID(d));
  enum tree_code code=TREE_CODE(d);
  if(code!=FUNCTION_DECL && code!=VAR_DECL && code!=TYPE_DECL)return;
  fprintf(out,"{\"kind\":%s,\"name\":%s,\"qualified\":%s,\"type\":%s,\"identity\":%s",
    q(get_tree_code_name(code)).c_str(),q(name(d)).c_str(),q(lang_hooks.decl_printable_name(d,2)).c_str(),
    q(spelled(TREE_TYPE(d)).c_str()).c_str(),identity(TREE_TYPE(d)).c_str());
  source(d); scope(DECL_CONTEXT(d)); attributes(d);
  fprintf(out,",\"uid\":%u,\"type_uid\":%u,\"external_linkage\":%s,\"inline\":%s",DECL_UID(d),TYPE_UID(TYPE_MAIN_VARIANT(TREE_TYPE(d))),TREE_PUBLIC(d)?"true":"false",code==FUNCTION_DECL && DECL_DECLARED_INLINE_P(d)?"true":"false");
  fprintf(out,",\"attributes\":%s",DECL_ATTRIBUTES(d)?q(spelled(DECL_ATTRIBUTES(d)).c_str()).c_str():"null");
  fprintf(out,",\"artificial\":%s,\"access\":%s",DECL_ARTIFICIAL(d)?"true":"false",q(TREE_PRIVATE(d)?"private":TREE_PROTECTED(d)?"protected":"public").c_str());
  if(code==TYPE_DECL) {
    abi("abi",TREE_TYPE(d));
    fprintf(out,",\"underlying_type\":%s",q(unalias(TREE_TYPE(d)).c_str()).c_str());
#ifdef GCC_CPP
    fprintf(out,",\"implicit_typedef\":%s",DECL_IMPLICIT_TYPEDEF_P(d)?"true":"false");
#endif
  }
  if(code==FUNCTION_DECL) {
    tree ft=TREE_TYPE(d); abi("return_abi",TREE_TYPE(ft));fprintf(out,",\"return_pointer_depth\":%u",pointer_depth(TREE_TYPE(ft)));
    fprintf(out,",\"variadic\":%s",TYPE_ARG_TYPES(ft)?(stdarg_p(ft)?"true":"false"):"null");
    fprintf(out,",\"return_type\":%s,\"return_identity\":%s,\"parameters\":[",q(spelled(TREE_TYPE(ft)).c_str()).c_str(),identity(TREE_TYPE(ft)).c_str());
    bool comma=false;unsigned ai=0;tree argument=DECL_ARGUMENTS(d);
    for(tree a=TYPE_ARG_TYPES(ft);a;a=TREE_CHAIN(a)) {
      tree t=TREE_VALUE(a);if(t==void_type_node)break;
      fprintf(out,"%s{\"type\":%s,\"identity\":%s,\"default\":%s",comma?",":"",q(spelled(t).c_str()).c_str(),identity(t).c_str(),TREE_PURPOSE(a)?q(spelled(TREE_PURPOSE(a)).c_str()).c_str():"null");abi("abi",t);fprintf(out,",\"param_kind\":%s,\"pointer_depth\":%u,\"restrict\":%s",q(param_kind(t)).c_str(),pointer_depth(t),TYPE_RESTRICT(t)?"true":"false");fprintf(out,",\"implicit_this\":%s,\"artificial\":%s,\"name\":%s}",TREE_CODE(ft)==METHOD_TYPE && ai==0?"true":"false",argument && DECL_ARTIFICIAL(argument)?"true":"false",argument?q(name(argument)).c_str():"null");if(argument)argument=DECL_CHAIN(argument);ai++;comma=true;
    }
    fprintf(out,"]");
  }
#ifdef GCC_CPP
  if(code==FUNCTION_DECL && DECL_LANG_SPECIFIC(d)) {
    int quals=TREE_CODE(TREE_TYPE(d))==METHOD_TYPE?type_memfn_quals(TREE_TYPE(d)):0;
    fprintf(out,",\"override\":%s",DECL_OVERRIDE_P(d)?"true":"false");
    fprintf(out,",\"const\":%s,\"volatile\":%s,\"static_member\":%s",(quals&TYPE_QUAL_CONST)?"true":"false",(quals&TYPE_QUAL_VOLATILE)?"true":"false",DECL_STATIC_FUNCTION_P(d)?"true":"false");
    fprintf(out,",\"pure_virtual\":%s,\"constructor\":%s,\"destructor\":%s,\"clone\":%s,\"explicit\":%s,\"ref_qualifier\":%s",DECL_PURE_VIRTUAL_P(d)?"true":"false",DECL_CONSTRUCTOR_P(d)?"true":"false",DECL_DESTRUCTOR_P(d)?"true":"false",DECL_CLONED_FUNCTION_P(d)?"true":"false",DECL_NONCONVERTING_P(d)?"true":"false",q(FUNCTION_REF_QUALIFIED(TREE_TYPE(d))?(FUNCTION_RVALUE_QUALIFIED(TREE_TYPE(d))?"&&":"&"):"").c_str());
    fprintf(out,",\"deleted\":%s,\"defaulted\":%s,\"constexpr\":%s,\"virtual\":%s,\"vindex\":%s,\"noexcept\":%s,\"extern_c\":%s",
      DECL_DELETED_FN(d)?"true":"false",DECL_DEFAULTED_FN(d)?"true":"false",DECL_DECLARED_CONSTEXPR_P(d)?"true":"false",DECL_VIRTUAL_P(d)?"true":"false",number(DECL_VINDEX(d)).c_str(),(TYPE_RAISES_EXCEPTIONS(TREE_TYPE(d)) && ((TREE_PURPOSE(TYPE_RAISES_EXCEPTIONS(TREE_TYPE(d))) && TREE_CODE(TREE_PURPOSE(TYPE_RAISES_EXCEPTIONS(TREE_TYPE(d))))!=INTEGER_CST)))?"null":TYPE_NOTHROW_P(TREE_TYPE(d))?"true":"false",DECL_EXTERN_C_P(d)?"true":"false");
    if(!(DECL_CONTEXT(d) && TYPE_P(DECL_CONTEXT(d)) && dependent_type_p(DECL_CONTEXT(d))) && !(DECL_TEMPLATE_INFO(d) && TREE_CODE(DECL_TI_TEMPLATE(d))==TEMPLATE_DECL && DECL_TEMPLATE_RESULT(DECL_TI_TEMPLATE(d))==d) && !uses_template_parms(d) && (!(DECL_CONSTRUCTOR_P(d)||DECL_DESTRUCTOR_P(d)) || DECL_CLONED_FUNCTION_P(d))) {
      tree asmname=DECL_ASSEMBLER_NAME(d);
      if(asmname)fprintf(out,",\"mangled\":%s",q(IDENTIFIER_POINTER(asmname)).c_str());
    }
  }
#endif
#ifndef GCC_CPP
  if(code==FUNCTION_DECL || code==VAR_DECL)fprintf(out,",\"mangled\":%s",q(IDENTIFIER_POINTER(DECL_ASSEMBLER_NAME(d))).c_str());
#endif
#ifdef GCC_CPP
  if(code==VAR_DECL && !(DECL_CONTEXT(d) && TYPE_P(DECL_CONTEXT(d)) && dependent_type_p(DECL_CONTEXT(d))) && !(DECL_LANG_SPECIFIC(d) && DECL_TEMPLATE_INFO(d) && TREE_CODE(DECL_TI_TEMPLATE(d))==TEMPLATE_DECL && DECL_TEMPLATE_RESULT(DECL_TI_TEMPLATE(d))==d) && !dependent_type_p(TREE_TYPE(d))) {
    tree asmname=DECL_ASSEMBLER_NAME(d);if(asmname)fprintf(out,",\"mangled\":%s",q(IDENTIFIER_POINTER(asmname)).c_str());
  }
#endif
  if(code==VAR_DECL) { abi("abi",TREE_TYPE(d)); fprintf(out,",\"const\":%s,\"volatile\":%s,\"tls\":%s",TYPE_READONLY(TREE_TYPE(d))?"true":"false",TYPE_VOLATILE(TREE_TYPE(d))?"true":"false",DECL_THREAD_LOCAL_P(d)?"true":"false"); fprintf(out,",\"size_bits\":%s,\"alignment_bits\":%u",number(DECL_SIZE(d)).c_str(),DECL_ALIGN(d)); fprintf(out,",\"value\":%s",number(DECL_INITIAL(d)).c_str());
    tree init=DECL_INITIAL(d);fprintf(out,",\"value_expression\":%s",init && (TREE_CODE(init)==INTEGER_CST || TREE_CODE(init)==REAL_CST || TREE_CODE(init)==STRING_CST)?q(spelled(init).c_str()).c_str():"null");
  }
  fprintf(out,"}\n");
}
#ifdef GCC_CPP
static void vgroups(tree b, bool &comma, int depth=0) {
  if(!b || depth>32)return;
  if(BINFO_VIRTUALS(b)) {
    fprintf(out,"%s{\"type\":%s,\"offset_bytes\":%s,\"slots\":[",comma?",":"",q(spelled(BINFO_TYPE(b)).c_str()).c_str(),number(BINFO_OFFSET(b)).c_str());comma=true;
    bool sep=false;for(tree v=BINFO_VIRTUALS(b);v;v=TREE_CHAIN(v)) {
      tree f=BV_FN(v);
      fprintf(out,"%s{\"method\":%s,\"this_adjustment\":%s,\"vcall_index\":%s}",sep?",":"",q(lang_hooks.decl_printable_name(f,2)).c_str(),number(BV_DELTA(v)).c_str(),number(BV_VCALL_INDEX(v)).c_str());sep=true;
    }fprintf(out,"]}");
  }
  tree sub;int i;for(i=0;BINFO_BASE_ITERATE(b,i,sub);i++)vgroups(sub,comma,depth+1);
}
#endif
static void type(void *data,void *) {
  tree t=(tree)data; if(!t||!TYPE_P(t)||local_scope(TYPE_CONTEXT(t)))return; t=TYPE_MAIN_VARIANT(t); if(seen_types.count(TYPE_UID(t)))return;
  seen_types.insert(TYPE_UID(t));
  enum tree_code code=TREE_CODE(t);
  if(!RECORD_OR_UNION_TYPE_P(t)&&code!=ENUMERAL_TYPE)return;
  fprintf(out,"{\"kind\":%s,\"name\":%s,\"size_bits\":%s,\"alignment_bits\":%u",
   q(get_tree_code_name(code)).c_str(),q(spelled(t).c_str()).c_str(),number(TYPE_SIZE(t)).c_str(),TYPE_ALIGN(t));
  scope(TYPE_CONTEXT(t));
  tree type_name=TYPE_NAME(t);fprintf(out,",\"anonymous\":%s",!type_name || (DECL_P(type_name) && !DECL_NAME(type_name))?"true":"false");
  fprintf(out,",\"type_uid\":%u,\"leaf_name\":%s",TYPE_UID(t),q(leaf(t).c_str()).c_str());
#ifdef GCC_CPP
  fprintf(out,",\"dependent\":%s",dependent_type_p(t)?"true":"false");
  if(TREE_CODE(t)==RECORD_TYPE && TYPE_LANG_SPECIFIC(t)) {
    fprintf(out,",\"record_kind\":%s,\"specialization_declared\":%s",q(CLASSTYPE_DECLARED_CLASS(t)?"class":"struct").c_str(),CLASSTYPE_TEMPLATE_INFO(t)?(CLASSTYPE_TEMPLATE_SPECIALIZATION(t)?"true":"false"):"null");
  }
  if(TREE_CODE(t)==RECORD_TYPE && TYPE_LANG_SPECIFIC(t) && !dependent_type_p(t) && CLASSTYPE_AS_BASE(t))
    fprintf(out,",\"data_size_bits\":%s",number(CLASSTYPE_SIZE(t)).c_str());
  if(TREE_CODE(t)==RECORD_TYPE && TYPE_LANG_SPECIFIC(t) && !dependent_type_p(t)) {
    fprintf(out,",\"standard_layout\":%s,\"trivially_copyable\":%s,\"abstract\":%s,\"final\":%s",CLASSTYPE_NON_STD_LAYOUT(t)?"false":"true",trivially_copyable_p(t)?"true":"false",ABSTRACT_CLASS_TYPE_P(t)?"true":"false",CLASSTYPE_FINAL(t)?"true":"false");
    fprintf(out,",\"vtable_initializers\":[");bool comma=false;
    for(tree v=CLASSTYPE_VTABLES(t);v;v=DECL_CHAIN(v)) {
      tree init=DECL_INITIAL(v);
      fprintf(out,"%s{\"name\":%s,\"initializer_kind\":%s,\"initializer\":%s",comma?",":"",q(name(v)).c_str(),init?q(get_tree_code_name(TREE_CODE(init))).c_str():"null",init?q(spelled(init).c_str()).c_str():"null");
      if(init && TREE_CODE(init)==CONSTRUCTOR) {
        fprintf(out,",\"entries\":[");unsigned int i;tree ix,value;bool sep=false;
        FOR_EACH_CONSTRUCTOR_ELT(CONSTRUCTOR_ELTS(init),i,ix,value) {
          tree target=value;STRIP_NOPS(target);
          std::string resolved="null";
          if(TREE_CODE(target)==ADDR_EXPR && DECL_P(TREE_OPERAND(target,0))) {
            tree d=TREE_OPERAND(target,0);
            resolved=q(IDENTIFIER_POINTER(DECL_ASSEMBLER_NAME(d)));
          }
          fprintf(out,"%s{\"index\":%s,\"target\":%s,\"value\":%s}",sep?",":"",(ix ? number(ix) : std::to_string(i)).c_str(),resolved.c_str(),q(spelled(value).c_str()).c_str());sep=true;
        }fprintf(out,"]");
      }
      fprintf(out,"}");comma=true;
    } fprintf(out,"]");
  }
  if(RECORD_OR_UNION_TYPE_P(t) && TYPE_BINFO(t)) { fprintf(out,",\"vtable_candidate_groups\":[");bool comma=false;vgroups(TYPE_BINFO(t),comma);fprintf(out,"]"); }
#endif
  tree td=TYPE_NAME(t);if(!td||!DECL_P(td))td=TYPE_STUB_DECL(t);if(td && DECL_P(td))source(td);
#ifndef GCC_CPP
  else {fprintf(out,",\"source\":%s,\"line\":%d",q(LOCATION_FILE(input_location)).c_str(),LOCATION_LINE(input_location));}
#endif
  if(code==ENUMERAL_TYPE) {
    tree underlying=TREE_TYPE(t);
#ifdef GCC_CPP
    fprintf(out,",\"scoped\":%s",SCOPED_ENUM_P(t)?"true":"false");
#else
    fprintf(out,",\"scoped\":false");
#endif
    fprintf(out,",\"underlying_type\":%s",underlying&&TYPE_P(underlying)?q(spelled(underlying).c_str()).c_str():"null");
    fprintf(out,",\"precision\":%u,\"unsigned\":%s,\"members\":[",TYPE_PRECISION(t),TYPE_UNSIGNED(t)?"true":"false");
    bool comma=false;for(tree e=TYPE_VALUES(t);e;e=TREE_CHAIN(e)) {
      tree v=TREE_VALUE(e);if(v&&TREE_CODE(v)==CONST_DECL)v=DECL_INITIAL(v);
      fprintf(out,"%s{\"name\":%s,\"value\":%s}",comma?",":"",q(IDENTIFIER_POINTER(TREE_PURPOSE(e))).c_str(),number(v).c_str());comma=true;
    }fprintf(out,"]");
  } else {
    fprintf(out,",\"fields\":[");bool comma=false;
    for(tree f=TYPE_FIELDS(t);f;f=DECL_CHAIN(f)) if(TREE_CODE(f)==FIELD_DECL) {
      tree bo=DECL_FIELD_OFFSET(f), bi=DECL_FIELD_BIT_OFFSET(f);
      tree ftype=DECL_BIT_FIELD_TYPE(f)?DECL_BIT_FIELD_TYPE(f):TREE_TYPE(f);
      std::string off="null";
      if(bo&&bi&&tree_fits_uhwi_p(bo)&&tree_fits_uhwi_p(bi)) off=std::to_string(tree_to_uhwi(bo)*BITS_PER_UNIT+tree_to_uhwi(bi));
      fprintf(out,"%s{\"name\":%s,\"type\":%s,\"identity\":%s,\"offset_bits\":%s,\"size_bits\":%s,\"bitfield\":%s,\"artificial\":%s,\"access\":%s",comma?",":"",q(name(f)).c_str(),q(spelled(ftype).c_str()).c_str(),identity(ftype).c_str(),off.c_str(),number(DECL_SIZE(f)).c_str(),DECL_BIT_FIELD(f)?"true":"false",DECL_ARTIFICIAL(f)?"true":"false",q(TREE_PRIVATE(f)?"private":TREE_PROTECTED(f)?"protected":"public").c_str());abi("abi",ftype);
      fprintf(out,",\"const\":%s,\"volatile\":%s",TYPE_READONLY(ftype)?"true":"false",TYPE_VOLATILE(ftype)?"true":"false");
#ifdef GCC_CPP
      fprintf(out,",\"mutable\":%s",DECL_MUTABLE_P(f)?"true":"false");
      tree init=DECL_INITIAL(f);fprintf(out,",\"default\":%s",init && init!=error_mark_node?q(spelled(init).c_str()).c_str():"null");
#endif
      fprintf(out,"}");comma=true;
    }fprintf(out,"]");
    if(RECORD_OR_UNION_TYPE_P(t) && TYPE_BINFO(t)) {
      fprintf(out,",\"bases\":[");tree b;int i;bool sep=false;
      for(i=0;BINFO_BASE_ITERATE(TYPE_BINFO(t),i,b);i++) {
        fprintf(out,"%s{\"type\":%s,\"offset_bytes\":%s,\"virtual\":%s}",sep?",":"",q(spelled(BINFO_TYPE(b)).c_str()).c_str(),number(BINFO_OFFSET(b)).c_str(),BINFO_VIRTUAL_P(b)?"true":"false");sep=true;
      }fprintf(out,"]");
    }
  }fprintf(out,"}\n");
}
#ifdef GCC_CPP
static void visit_type(tree);
static void visit_decl(tree d) {
  if(!d || !DECL_P(d))return;
  if(TREE_CODE(d)==NAMESPACE_DECL) {
    if(DECL_NAMESPACE_ALIAS(d))return;
    for(tree x=cp_namespace_decls(d);x;x=DECL_CHAIN(x))visit_decl(x);
  } else if(TREE_CODE(d)==CONCEPT_DECL) {
    fprintf(out,"{\"kind\":\"concept\",\"name\":%s,\"expression\":%s",q(name(d)).c_str(),q(expr_as_string(DECL_INITIAL(d),TFF_SCOPE)).c_str());source(d);scope(DECL_CONTEXT(d));fprintf(out,"}\n");
  } else if(TREE_CODE(d)==TEMPLATE_DECL) {
    tree result=DECL_TEMPLATE_RESULT(d);
    const char *family=result&&TREE_CODE(result)==CONCEPT_DECL?"concept":DECL_CLASS_TEMPLATE_P(d)?"class":result&&TREE_CODE(result)==FUNCTION_DECL?"function":result&&TREE_CODE(result)==VAR_DECL?"variable":"alias";
    fprintf(out,"{\"kind\":\"template\",\"name\":%s,\"family\":%s,\"signature\":%s",q(name(d)).c_str(),q(family).c_str(),result&&TREE_TYPE(result)?q(spelled(TREE_TYPE(result)).c_str()).c_str():"null");source(d);scope(DECL_CONTEXT(d));fprintf(out,",\"parameters\":[");
    bool comma=false;
    for(tree level=DECL_TEMPLATE_PARMS(d);level;level=TREE_CHAIN(level)) {
      tree vec=TREE_VALUE(level);
      for(int i=0;i<TREE_VEC_LENGTH(vec);i++) {
        tree item=TREE_VEC_ELT(vec,i);if(!item)continue;tree parm=TREE_VALUE(item), def=TREE_PURPOSE(item);
        fprintf(out,"%s{\"name\":%s,\"kind\":%s,\"default\":%s",comma?",":"",q(DECL_P(parm)?name(parm):"?").c_str(),q(get_tree_code_name(TREE_CODE(parm))).c_str(),def?q(spelled(def).c_str()).c_str():"null");fprintf(out,"}");comma=true;
      }
    }
    tree constraints=get_constraints(d);
    fprintf(out,"],\"constraint\":%s",constraints?q(expr_as_string(CI_ASSOCIATED_CONSTRAINTS(constraints),TFF_SCOPE)).c_str():"null");
    if(result && TREE_CODE(result)==CONCEPT_DECL)fprintf(out,",\"concept_expression\":%s",q(expr_as_string(DECL_INITIAL(result),TFF_SCOPE)).c_str());
    fprintf(out,"}\n");
    if(result)visit_decl(result);
    for(tree x=DECL_TEMPLATE_INSTANTIATIONS(d);x;x=TREE_CHAIN(x)) {
      tree v=TREE_VALUE(x);if(v&&TYPE_P(v))visit_type(v);else if(v&&DECL_P(v))visit_decl(v);
    }
  } else {
    decl(d,nullptr);
    if(TREE_CODE(d)==FUNCTION_DECL) {tree clone; FOR_EACH_CLONE(clone,d) {decl(clone,nullptr);}}
    if(TREE_CODE(d)==TYPE_DECL) visit_type(TREE_TYPE(d));
  }
}
static void visit_type(tree t) {
  if(!t || !TYPE_P(t))return;
  while(POINTER_TYPE_P(t)||TREE_CODE(t)==REFERENCE_TYPE||TREE_CODE(t)==ARRAY_TYPE)t=TREE_TYPE(t);
  t=TYPE_MAIN_VARIANT(t);
  if(!t || (!RECORD_OR_UNION_TYPE_P(t)&&TREE_CODE(t)!=ENUMERAL_TYPE) || local_scope(TYPE_CONTEXT(t)) || seen_types.count(TYPE_UID(t)))return;
  type(t,nullptr);
  if(RECORD_OR_UNION_TYPE_P(t)) for(tree d=TYPE_FIELDS(t);d;d=DECL_CHAIN(d)) {
    if(TREE_CODE(d)==FIELD_DECL)visit_type(TREE_TYPE(d));else visit_decl(d);
  }
}
#endif
static void finish(void *,void *) {
#ifdef GCC_CPP
  if(!errorcount)visit_decl(global_namespace);
#endif
fprintf(out,"{\"kind\":\"end\",\"errors\":%d}\n",errorcount);fclose(out);}
int plugin_init(struct plugin_name_args *args,struct plugin_gcc_version *v) {
  if(!plugin_default_version_check(v,&gcc_version))return 1;
  const char *path=nullptr;for(int i=0;i<args->argc;i++)if(!strcmp(args->argv[i].key,"output"))path=args->argv[i].value;
  if(!path)return 1;out=fopen(path,"w");if(!out)return 1;
  fprintf(out,"{\"kind\":\"compiler\",\"version\":%s,\"schema\":2,\"pointer_bits\":%d,\"char_bits\":%d}\n",q(v->basever).c_str(),POINTER_SIZE,BITS_PER_UNIT);
#ifndef GCC_CPP
  register_callback(args->base_name,PLUGIN_FINISH_TYPE,type,nullptr);
  register_callback(args->base_name,PLUGIN_FINISH_DECL,decl,nullptr);
#endif
  register_callback(args->base_name,PLUGIN_FINISH,finish,nullptr);return 0;
}
