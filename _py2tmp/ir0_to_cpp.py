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

from typing import List, Iterator, Tuple, Union, Callable
from _py2tmp import ir0

class Writer:
    def new_id(self) -> str: ...  # pragma: no cover

    def write_toplevel_elem(self, s: str): ...  # pragma: no cover

    def write_template_body_elem(self, s: str): ...  # pragma: no cover

    def create_child_writer(self) -> 'TemplateElemWriter': ...  # pragma: no cover

    def get_toplevel_writer(self) -> 'ToplevelWriter': ...  # pragma: no cover

class ToplevelWriter(Writer):
    def __init__(self, identifier_generator: Iterator[str]):
        self.identifier_generator = identifier_generator
        self.strings = []

    def new_id(self):
        return next(self.identifier_generator)

    def write_toplevel_elem(self, s: str):
        self.strings.append(s)

    def write_template_body_elem(self, s: str):
        self.write_toplevel_elem(s)

    def write_expr_fragment(self, s: str):
        self.write_toplevel_elem(s)

    def create_child_writer(self):
        return TemplateElemWriter(self)

    def get_toplevel_writer(self):
        return self

class TemplateElemWriter(Writer):
    def __init__(self, toplevel_writer: ToplevelWriter):
        self.toplevel_writer = toplevel_writer
        self.strings = []

    def new_id(self):
        return self.toplevel_writer.new_id()

    def write_toplevel_elem(self, s: str):
        self.toplevel_writer.write_toplevel_elem(s)

    def write_template_body_elem(self, s: str):
        self.strings.append(s)

    def write_expr_fragment(self, s: str):
        self.strings.append(s)

    def create_child_writer(self):
        return TemplateElemWriter(self.toplevel_writer)

    def get_toplevel_writer(self):
        return self.toplevel_writer

class ExprWriter(Writer):
    def __init__(self, parent_writer: Writer):
        self.parent_writer = parent_writer
        self.strings = []

    def new_id(self):
        return self.parent_writer.new_id()

    def write_toplevel_elem(self, s: str):
        self.parent_writer.write_toplevel_elem(s)

    def write_template_body_elem(self, s: str):
        self.parent_writer.write_template_body_elem(s)

    def write_expr_fragment(self, s: str):
        self.strings.append(s)

    def create_child_writer(self):
        raise NotImplementedError('This is not supported at the expression level')

    def get_toplevel_writer(self):
        return self.parent_writer.get_toplevel_writer()

def expr_to_cpp(expr: ir0.Expr,
                enclosing_function_defn_args: List[ir0.TemplateArgDecl],
                writer: Writer) -> str:
    if isinstance(expr, ir0.Literal):
        return literal_to_cpp(expr)
    elif isinstance(expr, ir0.ComparisonExpr):
        return comparison_expr_to_cpp(expr, enclosing_function_defn_args, writer)
    elif isinstance(expr, ir0.NotExpr):
        return not_expr_to_cpp(expr, enclosing_function_defn_args, writer)
    elif isinstance(expr, ir0.UnaryMinusExpr):
        return unary_minus_expr_to_cpp(expr, enclosing_function_defn_args, writer)
    elif isinstance(expr, ir0.Int64BinaryOpExpr):
        return int64_binary_op_expr_to_cpp(expr, enclosing_function_defn_args, writer)
    else:
        writer = ExprWriter(writer)
        type_expr_to_cpp(expr, enclosing_function_defn_args, writer)
        return ''.join(writer.strings)

def static_assert_to_cpp(assert_stmt: ir0.StaticAssert,
                         enclosing_function_defn_args: List[ir0.TemplateArgDecl],
                         writer: Writer):
    if enclosing_function_defn_args:
        bound_variables = {arg_decl.name
                           for arg_decl in enclosing_function_defn_args}
        assert bound_variables

    cpp_meta_expr = expr_to_cpp(assert_stmt.expr, enclosing_function_defn_args, writer)
    message = assert_stmt.message
    if not enclosing_function_defn_args or assert_stmt.expr.references_any_of(bound_variables):
        writer.write_template_body_elem('static_assert({cpp_meta_expr}, "{message}");'.format(**locals()))
    else:
        # The expression is constant, we need to add a reference to a variable bound in this function to prevent the
        # static_assert from being evaluated before the template is instantiated.

        # TODO: We could avoid adding a param dependency in more cases by checking for references to local variables
        # that depend (directly or indirectly) on a param.

        for arg_decl in enclosing_function_defn_args:
            if arg_decl.type.kind == ir0.ExprKind.BOOL:
                bound_var = arg_decl.name
                writer.write_template_body_elem('static_assert(AlwaysTrueFromBool<{bound_var}>::value && {cpp_meta_expr}, "{message}");'.format(**locals()))
                return
            elif arg_decl.type.kind == ir0.ExprKind.INT64:
                bound_var = arg_decl.name
                writer.write_template_body_elem('static_assert(AlwaysTrueFromInt64<{bound_var}>::value && {cpp_meta_expr}, "{message}");'.format(**locals()))
                return
            elif arg_decl.type.kind == ir0.ExprKind.TYPE:
                bound_var = arg_decl.name
                writer.write_template_body_elem('static_assert(AlwaysTrueFromType<{bound_var}>::value && {cpp_meta_expr}, "{message}");'.format(**locals()))
                return

        # All of this function's params are functions, we can't use any of the predefined AlwaysTrue* templates.
        # We need to define a new AlwaysTrueFromType variant for this specific function type.
        always_true_id = writer.new_id()
        template_param_decl = _type_to_template_param_declaration(type=enclosing_function_defn_args[0].type)
        template_param = enclosing_function_defn_args[0].name
        writer.write_template_body_elem('''\
            // Custom AlwaysTrueFor* template
            template <{template_param_decl}>
            struct {always_true_id} {{
              static constexpr bool value = true;
            }};
            static_assert({always_true_id}<{template_param}>::value && {cpp_meta_expr}, "{message}");
            '''.format(**locals()))

def constant_def_to_cpp(constant_def: ir0.ConstantDef,
                        enclosing_function_defn_args: List[ir0.TemplateArgDecl],
                        writer: Writer):
    if isinstance(constant_def.expr.type, ir0.BoolType):
        type_cpp = 'bool'
    elif isinstance(constant_def.expr.type, ir0.Int64Type):
        type_cpp = 'int64_t'
    else:
        raise NotImplementedError('Unexpected expression type: %s' % constant_def.expr.type)

    name = constant_def.name
    cpp_meta_expr = expr_to_cpp(constant_def.expr, enclosing_function_defn_args, writer)
    writer.write_template_body_elem('''\
        static constexpr {type_cpp} {name} = {cpp_meta_expr};
        '''.format(**locals()))

def typedef_to_cpp(typedef: ir0.Typedef,
                   enclosing_function_defn_args: List[ir0.TemplateArgDecl],
                   writer: Writer):
    name = typedef.name
    if typedef.expr.type.kind == ir0.ExprKind.TYPE:
        cpp_meta_expr = expr_to_cpp(typedef.expr, enclosing_function_defn_args, writer)
        writer.write_template_body_elem('''\
            using {name} = {cpp_meta_expr};
            '''.format(**locals()))
    elif typedef.expr.type.kind == ir0.ExprKind.TEMPLATE:
        assert isinstance(typedef.expr.type, ir0.TemplateType)

        template_args = [ir0.TemplateArgDecl(type=arg_type, name=writer.new_id())
                         for arg_type in typedef.expr.type.argtypes]
        template_args_decl = ', '.join(template_arg_decl_to_cpp(arg)
                                       for arg in template_args)

        template_instantiation_expr = ir0.TemplateInstantiation(template_expr=typedef.expr,
                                                                args=[ir0.AtomicTypeLiteral.for_local(type=arg.type,
                                                                                                cpp_type=arg.name)
                                                                      for arg in template_args],
                                                                # TODO: use static analysis to determine when it's
                                                                # safe to set this to False.
                                                                instantiation_might_trigger_static_asserts=True)

        cpp_meta_expr = template_instantiation_to_cpp(template_instantiation_expr, enclosing_function_defn_args, writer)

        writer.write_template_body_elem('''\
            template <{template_args_decl}>
            using {name} = {cpp_meta_expr};
            '''.format(**locals()))
    else:
        raise NotImplementedError('Unexpected expression type kind: %s' % typedef.expr.type.kind)

def _type_to_template_param_declaration(type: ir0.ExprType):
    if type.kind == ir0.ExprKind.BOOL:
        return 'bool'
    elif type.kind == ir0.ExprKind.INT64:
        return 'int64_t'
    elif type.kind == ir0.ExprKind.TYPE:
        return 'typename'
    elif type.kind == ir0.ExprKind.TEMPLATE:
        assert isinstance(type, ir0.TemplateType)
        return ('template <'
                + ', '.join(_type_to_template_param_declaration(arg_type)
                            for arg_type in type.argtypes)
                + '> class')
    elif type.kind == ir0.ExprKind.VARIADIC_TYPE:
        assert isinstance(type, ir0.VariadicType)
        return 'typename...'
    else:
        raise NotImplementedError('Unsupported argument kind: ' + str(type.kind))

def template_arg_decl_to_cpp(arg_decl: ir0.TemplateArgDecl):
    return _type_to_template_param_declaration(arg_decl.type) + ' ' + arg_decl.name

def template_specialization_to_cpp(specialization: ir0.TemplateSpecialization,
                                   cxx_name: str,
                                   enclosing_function_defn_args: List[ir0.TemplateArgDecl],
                                   writer: Writer):
    template_elem_writer = writer.create_child_writer()
    for elem in specialization.body:
        if isinstance(elem, ir0.StaticAssert):
            static_assert_to_cpp(elem,
                                 enclosing_function_defn_args=specialization.args,
                                 writer=template_elem_writer)
        elif isinstance(elem, ir0.ConstantDef):
            constant_def_to_cpp(elem,
                                enclosing_function_defn_args=specialization.args,
                                writer=template_elem_writer)
        elif isinstance(elem, ir0.Typedef):
            typedef_to_cpp(elem,
                           enclosing_function_defn_args=specialization.args,
                           writer=template_elem_writer)
        elif isinstance(elem, ir0.TemplateDefn):
            template_defn_to_cpp(elem,
                                 enclosing_function_defn_args=specialization.args,
                                 writer=template_elem_writer)
        else:
            raise NotImplementedError('Unsupported element: ' + str(elem))

    asserts_and_assignments_str = ''.join(template_elem_writer.strings)
    template_args = ', '.join(template_arg_decl_to_cpp(arg)
                              for arg in specialization.args)
    if specialization.patterns is not None:
        patterns_str = ', '.join(expr_to_cpp(pattern, enclosing_function_defn_args, writer)
                                 for pattern in specialization.patterns)
        writer.write_template_body_elem('''\
            template <{template_args}>
            struct {cxx_name}<{patterns_str}> {{
              {asserts_and_assignments_str}
            }};
            '''.format(**locals()))
    else:
        writer.write_template_body_elem('''\
            template <{template_args}>
            struct {cxx_name} {{
              {asserts_and_assignments_str}
            }};
            '''.format(**locals()))

def template_defn_to_cpp_forward_decl(template_defn: ir0.TemplateDefn,
                                      enclosing_function_defn_args: List[ir0.TemplateArgDecl],
                                      writer: Writer):
    template_name = template_defn.name
    template_args = ', '.join(template_arg_decl_to_cpp(arg)
                              for arg in template_defn.args)
    writer.write_toplevel_elem('''\
        template <{template_args}>
        struct {template_name};
        '''.format(**locals()))

def template_defn_to_cpp(template_defn: ir0.TemplateDefn,
                         enclosing_function_defn_args: List[ir0.TemplateArgDecl],
                         writer: Writer):
    template_name = template_defn.name
    if template_defn.main_definition:
        if template_defn.description:
            writer.write_toplevel_elem('// %s\n' % template_defn.description)
        template_specialization_to_cpp(template_defn.main_definition,
                                       cxx_name=template_name,
                                       enclosing_function_defn_args=enclosing_function_defn_args,
                                       writer=writer)

    for specialization in template_defn.specializations:
        if template_defn.description:
            writer.write_toplevel_elem('// %s\n' % template_defn.description)
        template_specialization_to_cpp(specialization,
                                       cxx_name=template_name,
                                       enclosing_function_defn_args=enclosing_function_defn_args,
                                       writer=writer)

def literal_to_cpp(literal: ir0.Literal):
    if isinstance(literal.value, bool):
        return {
            True: 'true',
            False: 'false',
        }[literal.value]
    elif isinstance(literal.value, int):
        return str(literal.value) + 'LL'
    else:
        raise NotImplementedError('Unexpected literal value: %s' % repr(literal.value))

def comparison_expr_to_cpp(comparison: ir0.ComparisonExpr,
                           enclosing_function_defn_args: List[ir0.TemplateArgDecl],
                           writer: Writer):
    return '(%s) %s (%s)' % (
        expr_to_cpp(comparison.lhs, enclosing_function_defn_args, writer),
        comparison.op,
        expr_to_cpp(comparison.rhs, enclosing_function_defn_args, writer))

def int64_binary_op_expr_to_cpp(expr: ir0.Int64BinaryOpExpr,
                                enclosing_function_defn_args: List[ir0.TemplateArgDecl],
                                writer: Writer):
    return '(%s) %s (%s)' % (
        expr_to_cpp(expr.lhs, enclosing_function_defn_args, writer),
        expr.op,
        expr_to_cpp(expr.rhs, enclosing_function_defn_args, writer))

def _select_best_arg_decl_for_select1st(args: List[ir0.TemplateArgDecl]):
    for arg in args:
        if not isinstance(arg.type, ir0.TemplateType):
            return arg
    return args[0]

def _select_best_arg_expr_index_for_select1st(args: List[ir0.Expr]):
    assert args
    for i, arg in enumerate(args):
        if not isinstance(arg.type, ir0.TemplateType):
            return i
    return 0

def template_instantiation_to_cpp(instantiation_expr: ir0.TemplateInstantiation,
                                  enclosing_function_defn_args: List[ir0.TemplateArgDecl],
                                  writer: Writer,
                                  omit_typename=False):
    args = instantiation_expr.args

    if instantiation_expr.instantiation_might_trigger_static_asserts and enclosing_function_defn_args:
        bound_variables = {arg_decl.name
                           for arg_decl in enclosing_function_defn_args}
        assert bound_variables

        # TODO: We could avoid adding a param dependency in more cases by checking for references to local variables
        # that depend (directly or indirectly) on a param.
        if not any(arg.references_any_of(bound_variables)
                   for arg in args):
            # All template arguments are (or might be) constants, we need to add a reference to a variable bound in this
            # function to prevent the instantiation from happening early, potentially triggering static asserts.

            arg_decl = _select_best_arg_decl_for_select1st(enclosing_function_defn_args)
            arg_index = _select_best_arg_expr_index_for_select1st(args)
            arg_to_replace = args[arg_index]

            if arg_decl.type.kind != ir0.ExprKind.TEMPLATE and arg_to_replace.type.kind != ir0.ExprKind.TEMPLATE:
                # We use lambdas here just to make sure we collect code coverage of each "branch". They are not necessary.
                # Note that we use the *Type variants for variadic types too. That's ok, since e.g.
                # Select1stBoolType<b, Args> will be expanded as e.g. Select1stBoolType<b, Args>... so it's exactly what
                # we want in the variadic case too.
                select1st_variant = {
                    (ir0.ExprKind.BOOL, ir0.ExprKind.BOOL):  lambda: 'Select1stBoolBool',
                    (ir0.ExprKind.BOOL, ir0.ExprKind.INT64): lambda: 'Select1stBoolInt64',
                    (ir0.ExprKind.BOOL, ir0.ExprKind.TYPE):  lambda: 'Select1stBoolType',
                    (ir0.ExprKind.BOOL, ir0.ExprKind.VARIADIC_TYPE):  lambda: 'Select1stBoolType',
                    (ir0.ExprKind.INT64, ir0.ExprKind.BOOL):  lambda: 'Select1stInt64Bool',
                    (ir0.ExprKind.INT64, ir0.ExprKind.INT64): lambda: 'Select1stInt64Int64',
                    (ir0.ExprKind.INT64, ir0.ExprKind.TYPE):  lambda: 'Select1stInt64Type',
                    (ir0.ExprKind.INT64, ir0.ExprKind.VARIADIC_TYPE):  lambda: 'Select1stInt64TypeType',
                    (ir0.ExprKind.TYPE, ir0.ExprKind.BOOL):  lambda: 'Select1stTypeBool',
                    (ir0.ExprKind.TYPE, ir0.ExprKind.INT64): lambda: 'Select1stTypeInt64',
                    (ir0.ExprKind.TYPE, ir0.ExprKind.TYPE):  lambda: 'Select1stTypeType',
                    (ir0.ExprKind.TYPE, ir0.ExprKind.VARIADIC_TYPE):  lambda: 'Select1stTypeType',
                }[(arg_to_replace.type.kind, arg_decl.type.kind)]()
            else:
                # We need to define a new Select1st variant for the desired function type.
                select1st_variant = writer.new_id()
                forwarded_param_id = writer.new_id()
                template_param_decl1 = _type_to_template_param_declaration(type=arg_to_replace.type)
                template_param_decl2 = _type_to_template_param_declaration(type=arg_decl.type)

                select1st_variant_body_writer = TemplateElemWriter(writer.get_toplevel_writer())
                if arg_to_replace.type.kind in (ir0.ExprKind.BOOL, ir0.ExprKind.INT64):
                    select1st_variant_body = ir0.ConstantDef(name='value',
                                                             expr=ir0.AtomicTypeLiteral.for_local(cpp_type=forwarded_param_id,
                                                                                            type=arg_to_replace.type))
                    constant_def_to_cpp(select1st_variant_body, enclosing_function_defn_args, select1st_variant_body_writer)
                else:
                    replaced_type = arg_to_replace.type
                    if replaced_type.kind == ir0.ExprKind.VARIADIC_TYPE:
                        replaced_type = ir0.TypeType()
                    assert replaced_type.kind in (ir0.ExprKind.TYPE, ir0.ExprKind.TEMPLATE)
                    select1st_variant_body = ir0.Typedef(name='value',
                                                         expr=ir0.AtomicTypeLiteral.for_local(cpp_type=forwarded_param_id,
                                                                                              type=replaced_type))
                    typedef_to_cpp(select1st_variant_body, enclosing_function_defn_args, select1st_variant_body_writer)

                select1st_variant_body_str = ''.join(select1st_variant_body_writer.strings)

                writer.write_template_body_elem('''
                    // Custom Select1st* template
                    template <{template_param_decl1} {forwarded_param_id}, {template_param_decl2}>
                    struct {select1st_variant} {{
                      {select1st_variant_body_str}
                    }};
                    '''.format(**locals()))

            select1st_type = ir0.TemplateType(argtypes=[arg_to_replace.type, arg_decl.type])
            select1st_instantiation = ir0.TemplateInstantiation(template_expr=ir0.AtomicTypeLiteral.for_local(cpp_type=select1st_variant,
                                                                                                        type=select1st_type),
                                                                args=[arg_to_replace,
                                                                      ir0.AtomicTypeLiteral.for_local(cpp_type=arg_decl.name,
                                                                                                type=arg_decl.type)],
                                                                instantiation_might_trigger_static_asserts=False)
            new_arg = ir0.ClassMemberAccess(class_type_expr=select1st_instantiation,
                                            member_name='value',
                                            member_type=arg_to_replace.type)

            args = args[:arg_index] + (new_arg,) + args[arg_index + 1:]

    template_params = ', '.join(expr_to_cpp(arg, enclosing_function_defn_args, writer)
                                for arg in args)

    if isinstance(instantiation_expr.template_expr, ir0.ClassMemberAccess):
        cpp_fun = class_member_access_to_cpp(instantiation_expr.template_expr,
                                             enclosing_function_defn_args,
                                             writer,
                                             omit_typename=omit_typename,
                                             parent_expr_is_template_instantiation=True)
    else:
        cpp_fun = expr_to_cpp(instantiation_expr.template_expr, enclosing_function_defn_args, writer)

    return '{cpp_fun}<{template_params}>'.format(**locals())

def class_member_access_to_cpp(expr: ir0.ClassMemberAccess,
                               enclosing_function_defn_args: List[ir0.TemplateArgDecl],
                               writer: Writer,
                               omit_typename: bool = False,
                               parent_expr_is_template_instantiation: bool = False):
    if isinstance(expr.expr, ir0.TemplateInstantiation):
        cpp_fun = template_instantiation_to_cpp(expr.expr, enclosing_function_defn_args, writer, omit_typename=True)
    elif isinstance(expr.expr, ir0.ClassMemberAccess):
        cpp_fun = class_member_access_to_cpp(expr.expr, enclosing_function_defn_args, writer, omit_typename=True)
    else:
        cpp_fun = expr_to_cpp(expr.expr, enclosing_function_defn_args, writer)
    member_name = expr.member_name
    if isinstance(expr.type, (ir0.BoolType, ir0.Int64Type)):
        cpp_str_template = '{cpp_fun}::{member_name}'
    elif isinstance(expr.type, (ir0.TypeType, ir0.TemplateType)):
        if omit_typename or (isinstance(expr.type, ir0.TemplateType) and not parent_expr_is_template_instantiation):
            maybe_typename = ''
        else:
            maybe_typename = 'typename '
        if isinstance(expr.type, ir0.TemplateType):
            maybe_template = 'template '
        else:
            maybe_template = ''

        cpp_str_template = '{maybe_typename}{cpp_fun}::{maybe_template}{member_name}'
    else:
        raise NotImplementedError('Member type: %s' % expr.type.__class__.__name__)
    return cpp_str_template.format(**locals())

def variadic_type_expansion_to_cpp(expr: ir0.VariadicTypeExpansion,
                                   enclosing_function_defn_args: List[ir0.TemplateArgDecl],
                                   writer: Writer):
    return expr_to_cpp(expr.expr, enclosing_function_defn_args, writer) + '...'

def not_expr_to_cpp(expr: ir0.NotExpr,
                    enclosing_function_defn_args: List[ir0.TemplateArgDecl],
                    writer: Writer):
    inner_expr = expr_to_cpp(expr.expr, enclosing_function_defn_args, writer)
    return '!({inner_expr})'.format(**locals())

def unary_minus_expr_to_cpp(expr: ir0.UnaryMinusExpr,
                            enclosing_function_defn_args: List[ir0.TemplateArgDecl],
                            writer: Writer):
    inner_expr = expr_to_cpp(expr.expr, enclosing_function_defn_args, writer)
    return '-({inner_expr})'.format(**locals())

def toplevel_elem_to_cpp(elem: Union[ir0.StaticAssert, ir0.ConstantDef, ir0.Typedef], writer: ToplevelWriter):
    if isinstance(elem, ir0.StaticAssert):
        static_assert_to_cpp(elem,
                             enclosing_function_defn_args=[],
                             writer=writer)
    elif isinstance(elem, ir0.ConstantDef):
        constant_def_to_cpp(elem,
                            enclosing_function_defn_args=[],
                            writer=writer)
    elif isinstance(elem, ir0.Typedef):
        typedef_to_cpp(elem,
                       enclosing_function_defn_args=[],
                       writer=writer)
    else:
        raise NotImplementedError('Unexpected toplevel element: %s' % str(elem.__class__))

def header_to_cpp(header: ir0.Header, identifier_generator: Iterator[str]):
    writer = ToplevelWriter(identifier_generator)
    writer.write_toplevel_elem('''\
        #include <tmppy/tmppy.h>
        #include <type_traits>
        ''')
    for elem in header.template_defns:
        # TODO: only do this when needed, many of these forward declarations are unnecessary.
        template_defn_to_cpp_forward_decl(elem,
                                          enclosing_function_defn_args=[],
                                          writer=writer)
    for elem in header.template_defns:
        template_defn_to_cpp(elem,
                             enclosing_function_defn_args=[],
                             writer=writer)
    for elem in header.toplevel_content:
        toplevel_elem_to_cpp(elem, writer)
    return ''.join(writer.strings)

def type_expr_to_cpp(expr: ir0.Expr,
                     enclosing_function_defn_args: List[ir0.TemplateArgDecl],
                     writer: ExprWriter):
    write_prefix, write_suffix = type_expr_to_cpp_prefix_suffix(expr, enclosing_function_defn_args, writer, has_modifiers=False)
    write_prefix()
    write_suffix()

# We can't generate code like "int & &&", so we need to collapse reference types ourselves. The C++ compiler has similar
# collapsing logic, but it's only applied to cases where a ref/rref is hidden inside a typedef and one isn't.
def _simplify_toplevel_references(expr: Union[ir0.RvalueReferenceTypeExpr, ir0.ReferenceTypeExpr]):
    has_reference = False
    while isinstance(expr, (ir0.RvalueReferenceTypeExpr, ir0.ReferenceTypeExpr)):
        has_reference |= isinstance(expr, ir0.ReferenceTypeExpr)
        expr = expr.type_expr
    if has_reference:
        return ir0.ReferenceTypeExpr(expr)
    else:
        return ir0.RvalueReferenceTypeExpr(expr)

def type_expr_to_cpp_prefix_suffix(expr: ir0.Expr,
                                   enclosing_function_defn_args: List[ir0.TemplateArgDecl],
                                   writer: ExprWriter,
                                   has_modifiers: bool) -> Tuple[Callable[[], None], Callable[[], None]]:
    if isinstance(expr, (ir0.RvalueReferenceTypeExpr, ir0.ReferenceTypeExpr)):
        expr = _simplify_toplevel_references(expr)

    if isinstance(expr, ir0.FunctionTypeExpr):
        return function_type_expr_to_cpp_prefix_suffix(expr, enclosing_function_defn_args, writer, has_modifiers)
    elif isinstance(expr, ir0.PointerTypeExpr):
        return unary_modifier_type_expr_to_cpp_prefix_suffix('*', expr.type_expr, enclosing_function_defn_args, writer)
    elif isinstance(expr, ir0.ReferenceTypeExpr):
        return unary_modifier_type_expr_to_cpp_prefix_suffix(' &',  expr.type_expr, enclosing_function_defn_args, writer)
    elif isinstance(expr, ir0.RvalueReferenceTypeExpr):
        return unary_modifier_type_expr_to_cpp_prefix_suffix(' &&', expr.type_expr, enclosing_function_defn_args, writer)
    elif isinstance(expr, ir0.ConstTypeExpr):
        return unary_modifier_type_expr_to_cpp_prefix_suffix(' const ', expr.type_expr, enclosing_function_defn_args, writer)
    elif isinstance(expr, ir0.ArrayTypeExpr):
        return unary_modifier_type_expr_to_cpp_prefix_suffix('[]', expr.type_expr, enclosing_function_defn_args, writer)

    if isinstance(expr, ir0.AtomicTypeLiteral):
        expr_cpp_code = atomic_type_literal_expr_to_cpp(expr)
    elif isinstance(expr, ir0.TemplateInstantiation):
        expr_cpp_code = template_instantiation_to_cpp(expr, enclosing_function_defn_args, writer)
    elif isinstance(expr, ir0.ClassMemberAccess):
        expr_cpp_code = class_member_access_to_cpp(expr, enclosing_function_defn_args, writer)
    elif isinstance(expr, ir0.VariadicTypeExpansion):
        expr_cpp_code = variadic_type_expansion_to_cpp(expr, enclosing_function_defn_args, writer)
    else:
        raise NotImplementedError('Unexpected type expr: %s' % str(expr.__class__))

    def write_prefix():
        writer.write_expr_fragment(expr_cpp_code)
    def write_suffix():
        pass
    return write_prefix, write_suffix

def function_type_expr_to_cpp_prefix_suffix(expr: ir0.FunctionTypeExpr,
                                            enclosing_function_defn_args: List[ir0.TemplateArgDecl],
                                            writer: ExprWriter,
                                            has_modifiers: bool):
    # X1 -> Y                          |  Y(*) (X1)
    # X2 -> (X1 -> Y)                  |  Y(*(*) (X2)) (X1)
    # X3 -> (X2 -> (X1 -> Y))          |  Y(*(*(*) (X3)) (X2)) (X1)
    # X4 -> (X3 -> (X2 -> (X1 -> Y)))  |  Y(*(*(*(*) (X4)) (X3)) (X2)) (X1)
    #
    # Y -> X1                          |  X1(*) (Y)
    # (Y -> X1) -> X2                  |  X2(*) (X1(*) (Y))
    # ((Y -> X1) -> X2) -> X3          |  X3(*) (X2(*) (X1(*) (Y)))
    # (((Y -> X1) -> X2) -> X3) -> X4  |  X4(*) (X3(*) (X2(*) (X1(*) (Y))))

    return_type_write_prefix, return_type_write_suffix = type_expr_to_cpp_prefix_suffix(expr.return_type_expr, enclosing_function_defn_args, writer, has_modifiers=False)

    def write_prefix():
        return_type_write_prefix()
        if has_modifiers:
            writer.write_expr_fragment('(')

    def write_suffix():
        if has_modifiers:
            writer.write_expr_fragment(')')
        writer.write_expr_fragment(' (')
        for i, arg in enumerate(expr.arg_exprs):
            if i != 0:
                writer.write_expr_fragment(', ')
            type_expr_to_cpp(arg, enclosing_function_defn_args, writer)
        writer.write_expr_fragment(')')
        return_type_write_suffix()

    return write_prefix, write_suffix

def unary_modifier_type_expr_to_cpp_prefix_suffix(modifier_str: str,
                                                  sub_expr: ir0.Expr,
                                                  enclosing_function_defn_args: List[ir0.TemplateArgDecl],
                                                  writer: ExprWriter):
    write_subexpr_prefix, write_subexpr_suffix = type_expr_to_cpp_prefix_suffix(sub_expr, enclosing_function_defn_args, writer, has_modifiers=True)
    def write_prefix():
        write_subexpr_prefix()
        writer.write_expr_fragment(modifier_str)
    return write_prefix, write_subexpr_suffix

def atomic_type_literal_expr_to_cpp(expr: ir0.AtomicTypeLiteral):
    return expr.cpp_type
