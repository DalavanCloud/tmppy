#  Copyright 2017 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS-IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from typing import List, Tuple, Set, Optional, Iterable, Union, Dict
from _py2tmp import ir0

class Writer:
    def write(self, elem: Union[ir0.TemplateDefn, ir0.StaticAssert, ir0.ConstantDef, ir0.Typedef]): ...  # pragma: no cover

    def write_toplevel_elem(self, elem: Union[ir0.TemplateDefn, ir0.StaticAssert, ir0.ConstantDef, ir0.Typedef]): ...  # pragma: no cover

    def new_id(self) -> str: ...  # pragma: no cover

    def new_constant_or_typedef(self, expr: ir0.Expr) -> ir0.AtomicTypeLiteral:
        id = self.new_id()
        if expr.type.kind in (ir0.ExprKind.BOOL, ir0.ExprKind.INT64):
            self.write(ir0.ConstantDef(name=id, expr=expr))
        elif expr.type.kind in (ir0.ExprKind.TYPE, ir0.ExprKind.TEMPLATE):
            self.write(ir0.Typedef(name=id, expr=expr))
        else:
            # TODO: consider handling VARIADIC_TYPE too.
            raise NotImplementedError('Unexpected kind: ' + str(expr.type.kind))

        return ir0.AtomicTypeLiteral.for_local(cpp_type=id, type=expr.type)

    def get_toplevel_writer(self) -> 'ToplevelWriter': ...  # pragma: no cover

class ToplevelWriter(Writer):
    def __init__(self, identifier_generator: Iterable[str], allow_toplevel_elems: bool = True, allow_template_defns: bool = True):
        self.identifier_generator = identifier_generator
        self.template_defns = []  # type: List[ir0.TemplateDefn]
        self.toplevel_elems = []  # type: List[Union[ir0.StaticAssert, ir0.ConstantDef, ir0.Typedef]]
        self.allow_toplevel_elems = allow_toplevel_elems
        self.allow_template_defns = allow_template_defns

    def write(self, elem: Union[ir0.TemplateDefn, ir0.StaticAssert, ir0.ConstantDef, ir0.Typedef]):
        if isinstance(elem, ir0.TemplateDefn):
            assert self.allow_template_defns
            self.template_defns.append(elem)
        else:
            assert self.allow_toplevel_elems
            self.toplevel_elems.append(elem)

    def new_id(self):
        return next(self.identifier_generator)

    def get_toplevel_writer(self):
        return self

class TemplateBodyWriter(Writer):
    def __init__(self, toplevel_writer: ToplevelWriter):
        self.toplevel_writer = toplevel_writer
        self.elems = []  # type: List[ir0.TemplateBodyElement]

    def new_id(self):
        return self.toplevel_writer.new_id()

    def write_toplevel_elem(self, elem: Union[ir0.TemplateDefn, ir0.StaticAssert, ir0.ConstantDef, ir0.Typedef]):
        self.toplevel_writer.write(elem)

    def write(self, elem: ir0.TemplateBodyElement):
        self.elems.append(elem)

    def get_toplevel_writer(self):
        return self.toplevel_writer

class Transformation:
    def transform_header(self, header: ir0.Header, identifier_generator: Iterable[str]) -> ir0.Header:
        writer = ToplevelWriter(identifier_generator)
        for template_defn in header.template_defns:
            self.transform_template_defn(template_defn, writer)

        for elem in header.toplevel_content:
            self.transform_toplevel_elem(elem, writer)

        return ir0.Header(template_defns=writer.template_defns,
                          toplevel_content=writer.toplevel_elems,
                          public_names=header.public_names)

    def transform_toplevel_elem(self, elem: Union[ir0.StaticAssert, ir0.ConstantDef, ir0.Typedef], writer: Writer):
        if isinstance(elem, ir0.StaticAssert):
            self.transform_static_assert(elem, writer)
        elif isinstance(elem, ir0.ConstantDef):
            self.transform_constant_def(elem, writer)
        elif isinstance(elem, ir0.Typedef):
            self.transform_typedef(elem, writer)
        else:
            raise NotImplementedError('Unexpected elem: ' + elem.__class__.__name__)

    def transform_template_defn(self, template_defn: ir0.TemplateDefn, writer: Writer):
        writer.write(ir0.TemplateDefn(args=[self.transform_template_arg_decl(arg_decl) for arg_decl in template_defn.args],
                                      main_definition=self.transform_template_specialization(template_defn.main_definition, writer) if template_defn.main_definition is not None else None,
                                      specializations=[self.transform_template_specialization(specialization, writer) for specialization in template_defn.specializations],
                                      name=template_defn.name,
                                      description=template_defn.description,
                                      result_element_names=template_defn.result_element_names))

    def transform_static_assert(self, static_assert: ir0.StaticAssert, writer: Writer):
        writer.write(ir0.StaticAssert(expr=self.transform_expr(static_assert.expr, writer),
                                      message=static_assert.message))

    def transform_constant_def(self, constant_def: ir0.ConstantDef, writer: Writer):
        writer.write(ir0.ConstantDef(name=constant_def.name,
                                     expr=self.transform_expr(constant_def.expr, writer)))

    def transform_typedef(self, typedef: ir0.Typedef, writer: Writer):
        writer.write(ir0.Typedef(name=typedef.name,
                                 expr=self.transform_expr(typedef.expr, writer)))

    def transform_template_arg_decl(self, arg_decl: ir0.TemplateArgDecl) -> ir0.TemplateArgDecl:
        return arg_decl

    def transform_template_body_elems(self,
                                      elems: List[ir0.TemplateBodyElement],
                                      writer: ToplevelWriter) -> List[ir0.TemplateBodyElement]:
        body_writer = TemplateBodyWriter(writer)
        for elem in elems:
            self.transform_template_body_elem(elem, body_writer)
        return body_writer.elems

    def transform_template_specialization(self, specialization: ir0.TemplateSpecialization, writer: Writer) -> ir0.TemplateSpecialization:
        toplevel_writer = writer.get_toplevel_writer()

        if specialization.patterns is not None:
            patterns = [self.transform_pattern(pattern, writer)
                        for pattern in specialization.patterns]
        else:
            patterns = None

        return ir0.TemplateSpecialization(args=[self.transform_template_arg_decl(arg_decl) for arg_decl in specialization.args],
                                          patterns=patterns,
                                          body=self.transform_template_body_elems(specialization.body, toplevel_writer))

    def transform_pattern(self, expr: ir0.Expr, writer: Writer) -> ir0.Expr:
        return self.transform_expr(expr, writer)

    def transform_expr(self, expr: ir0.Expr, writer: Writer) -> ir0.Expr:
        if isinstance(expr, ir0.Literal):
            return self.transform_literal(expr, writer)
        elif isinstance(expr, ir0.AtomicTypeLiteral):
            return self.transform_type_literal(expr, writer)
        elif isinstance(expr, ir0.ClassMemberAccess):
            return self.transform_class_member_access(expr, writer)
        elif isinstance(expr, ir0.NotExpr):
            return self.transform_not_expr(expr, writer)
        elif isinstance(expr, ir0.UnaryMinusExpr):
            return self.transform_unary_minus_expr(expr, writer)
        elif isinstance(expr, ir0.ComparisonExpr):
            return self.transform_comparison_expr(expr, writer)
        elif isinstance(expr, ir0.Int64BinaryOpExpr):
            return self.transform_int64_binary_op_expr(expr, writer)
        elif isinstance(expr, ir0.TemplateInstantiation):
            return self.transform_template_instantiation(expr, writer)
        elif isinstance(expr, ir0.PointerTypeExpr):
            return self.transform_pointer_type_expr(expr, writer)
        elif isinstance(expr, ir0.ReferenceTypeExpr):
            return self.transform_reference_type_expr(expr, writer)
        elif isinstance(expr, ir0.RvalueReferenceTypeExpr):
            return self.transform_rvalue_reference_type_expr(expr, writer)
        elif isinstance(expr, ir0.ConstTypeExpr):
            return self.transform_const_type_expr(expr, writer)
        elif isinstance(expr, ir0.ArrayTypeExpr):
            return self.transform_array_type_expr(expr, writer)
        elif isinstance(expr, ir0.FunctionTypeExpr):
            return self.transform_function_type_expr(expr, writer)
        elif isinstance(expr, ir0.VariadicTypeExpansion):
            return self.transform_variadic_type_expansion(expr, writer)
        else:
            raise NotImplementedError('Unexpected expr: ' + expr.__class__.__name__)

    def transform_template_body_elem(self, elem: ir0.TemplateBodyElement, writer: TemplateBodyWriter):
        if isinstance(elem, ir0.TemplateDefn):
            self.transform_template_defn(elem, writer)
        elif isinstance(elem, ir0.StaticAssert):
            self.transform_static_assert(elem, writer)
        elif isinstance(elem, ir0.ConstantDef):
            self.transform_constant_def(elem, writer)
        elif isinstance(elem, ir0.Typedef):
            self.transform_typedef(elem, writer)
        else:
            raise NotImplementedError('Unexpected elem: ' + elem.__class__.__name__)

    def transform_literal(self, literal: ir0.Literal, writer: Writer) -> ir0.Expr:
        return literal

    def transform_type_literal(self, type_literal: ir0.AtomicTypeLiteral, writer: Writer) -> ir0.Expr:
        return self._transform_type_literal_default_impl(type_literal, writer)

    def _transform_type_literal_default_impl(self, type_literal: ir0.AtomicTypeLiteral, writer: Writer) -> ir0.AtomicTypeLiteral:
        return ir0.AtomicTypeLiteral(cpp_type=type_literal.cpp_type,
                                     is_metafunction_that_may_return_error=type_literal.is_metafunction_that_may_return_error,
                                     type=type_literal.type,
                                     is_local=type_literal.is_local)

    def transform_class_member_access(self, class_member_access: ir0.ClassMemberAccess, writer: Writer) -> ir0.Expr:
        return ir0.ClassMemberAccess(class_type_expr=self.transform_expr(class_member_access.expr, writer),
                                     member_name=class_member_access.member_name,
                                     member_type=class_member_access.type)

    def transform_not_expr(self, not_expr: ir0.NotExpr, writer: Writer) -> ir0.Expr:
        return ir0.NotExpr(self.transform_expr(not_expr.expr, writer))

    def transform_unary_minus_expr(self, unary_minus: ir0.UnaryMinusExpr, writer: Writer) -> ir0.Expr:
        return ir0.UnaryMinusExpr(self.transform_expr(unary_minus.expr, writer))

    def transform_comparison_expr(self, comparison: ir0.ComparisonExpr, writer: Writer) -> ir0.Expr:
        return ir0.ComparisonExpr(lhs=self.transform_expr(comparison.lhs, writer),
                                  rhs=self.transform_expr(comparison.rhs, writer),
                                  op=comparison.op)

    def transform_int64_binary_op_expr(self, binary_op: ir0.Int64BinaryOpExpr, writer: Writer) -> ir0.Expr:
        return ir0.Int64BinaryOpExpr(lhs=self.transform_expr(binary_op.lhs, writer),
                                     rhs=self.transform_expr(binary_op.rhs, writer),
                                     op=binary_op.op)

    def transform_template_instantiation(self, template_instantiation: ir0.TemplateInstantiation, writer: Writer) -> ir0.Expr:
        return ir0.TemplateInstantiation(template_expr=self.transform_expr(template_instantiation.template_expr, writer),
                                         args=[self.transform_expr(arg, writer) for arg in template_instantiation.args],
                                         instantiation_might_trigger_static_asserts=template_instantiation.instantiation_might_trigger_static_asserts)

    def transform_pointer_type_expr(self, expr: ir0.PointerTypeExpr, writer: Writer):
        return ir0.PointerTypeExpr(self.transform_expr(expr.type_expr, writer))

    def transform_reference_type_expr(self, expr: ir0.ReferenceTypeExpr, writer: Writer):
        return ir0.ReferenceTypeExpr(self.transform_expr(expr.type_expr, writer))

    def transform_rvalue_reference_type_expr(self, expr: ir0.RvalueReferenceTypeExpr, writer: Writer):
        return ir0.RvalueReferenceTypeExpr(self.transform_expr(expr.type_expr, writer))

    def transform_const_type_expr(self, expr: ir0.ConstTypeExpr, writer: Writer):
        return ir0.ConstTypeExpr(self.transform_expr(expr.type_expr, writer))

    def transform_array_type_expr(self, expr: ir0.ArrayTypeExpr, writer: Writer):
        return ir0.ArrayTypeExpr(self.transform_expr(expr.type_expr, writer))

    def transform_function_type_expr(self, expr: ir0.FunctionTypeExpr, writer: Writer):
        return ir0.FunctionTypeExpr(return_type_expr=self.transform_expr(expr.return_type_expr, writer),
                                    arg_exprs=[self.transform_expr(arg_expr, writer)
                                               for arg_expr in expr.arg_exprs])

    def transform_variadic_type_expansion(self, expr: ir0.VariadicTypeExpansion, writer: Writer):
        return ir0.VariadicTypeExpansion(self.transform_expr(expr.expr, writer))

