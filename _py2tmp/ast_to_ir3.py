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
import re
import textwrap
from _py2tmp import ir3
import typed_ast.ast3 as ast
from typing import List, Tuple, Dict, Optional, Union, Callable
from _py2tmp.utils import ast_to_string

class Symbol:
    def __init__(self, name: str, type: ir3.ExprType, is_function_that_may_throw: bool):
        if is_function_that_may_throw:
            assert isinstance(type, ir3.FunctionType)
        self.type = type
        self.name = name
        self.is_function_that_may_throw = is_function_that_may_throw

class SymbolLookupResult:
    def __init__(self, symbol: Symbol, ast_node: ast.AST, is_only_partially_defined: bool, symbol_table: 'SymbolTable'):
        self.symbol = symbol
        self.ast_node = ast_node
        self.is_only_partially_defined = is_only_partially_defined
        self.symbol_table = symbol_table

class SymbolTable:
    def __init__(self, parent=None):
        self.symbols_by_name = dict()  # type: Dict[str, Tuple[Symbol, ast.AST, bool]]
        self.parent = parent

    def get_symbol_definition(self, name: str):
        result = self.symbols_by_name.get(name)
        if result:
            symbol, ast_node, is_only_partially_defined = result
            return SymbolLookupResult(symbol, ast_node, is_only_partially_defined, self)
        if self.parent:
            return self.parent.get_symbol_definition(name)
        return None

    def add_symbol(self,
                   name: str,
                   type: ir3.ExprType,
                   definition_ast_node: ast.AST,
                   is_only_partially_defined: bool,
                   is_function_that_may_throw: bool):
        if is_function_that_may_throw:
            assert isinstance(type, ir3.FunctionType)
        self.symbols_by_name[name] = (Symbol(name, type, is_function_that_may_throw),
                                      definition_ast_node,
                                      is_only_partially_defined)

class CompilationContext:
    def __init__(self,
                 symbol_table: SymbolTable,
                 custom_types_symbol_table: SymbolTable,
                 filename: str,
                 source_lines: List[str],
                 function_name: Optional[str] = None,
                 partially_typechecked_function_definitions_by_name: Dict[str, ast.FunctionDef] = None):
        self.symbol_table = symbol_table
        self.custom_types_symbol_table = custom_types_symbol_table
        self.partially_typechecked_function_definitions_by_name = partially_typechecked_function_definitions_by_name or dict()
        self.filename = filename
        self.source_lines = source_lines
        self.current_function_name = function_name

    def create_child_context(self, function_name=None):
        return CompilationContext(SymbolTable(parent=self.symbol_table),
                                  self.custom_types_symbol_table,
                                  self.filename,
                                  self.source_lines,
                                  function_name=function_name or self.current_function_name,
                                  partially_typechecked_function_definitions_by_name=self.partially_typechecked_function_definitions_by_name)

    def add_symbol(self,
                   name: str,
                   type: ir3.ExprType,
                   definition_ast_node: ast.AST,
                   is_only_partially_defined: bool,
                   is_function_that_may_throw: bool):
        """
        Adds a symbol to the symbol table.

        This throws an error (created by calling `create_already_defined_error(previous_type)`) if a symbol with the
        same name and different type was already defined in this scope.
        """
        if is_function_that_may_throw:
            assert isinstance(type, ir3.FunctionType)

        self._check_not_already_defined(name, definition_ast_node)

        self.symbol_table.add_symbol(name=name,
                                     type=type,
                                     definition_ast_node=definition_ast_node,
                                     is_only_partially_defined=is_only_partially_defined,
                                     is_function_that_may_throw=is_function_that_may_throw)

    def add_custom_type_symbol(self,
                               custom_type: ir3.CustomType,
                               definition_ast_node: ast.ClassDef):
        self.add_symbol(name=custom_type.name,
                        type=ir3.FunctionType(argtypes=[arg.type for arg in custom_type.arg_types],
                                              returns=custom_type),
                        definition_ast_node=definition_ast_node,
                        is_only_partially_defined=False,
                        is_function_that_may_throw=False)
        self.custom_types_symbol_table.add_symbol(name=custom_type.name,
                                                  type=custom_type,
                                                  definition_ast_node=definition_ast_node,
                                                  is_only_partially_defined=False,
                                                  is_function_that_may_throw=False)

    def add_symbol_for_function_with_unknown_return_type(self,
                                                         name: str,
                                                         definition_ast_node: ast.FunctionDef):
        self._check_not_already_defined(name, definition_ast_node)

        self.partially_typechecked_function_definitions_by_name[name] = definition_ast_node

    def get_symbol_definition(self, name: str):
        return self.symbol_table.get_symbol_definition(name)

    def get_partial_function_definition(self, name: str):
        return self.partially_typechecked_function_definitions_by_name.get(name)

    def get_type_symbol_definition(self, name: str):
        return self.custom_types_symbol_table.get_symbol_definition(name)

    def set_function_type(self, name: str, type: ir3.FunctionType):
        if name in self.partially_typechecked_function_definitions_by_name:
            ast_node = self.partially_typechecked_function_definitions_by_name[name]
            del self.partially_typechecked_function_definitions_by_name[name]
            self.symbol_table.add_symbol(name=name,
                                         type=type,
                                         definition_ast_node=ast_node,
                                         is_only_partially_defined=False,
                                         is_function_that_may_throw=True)
        else:
            assert self.get_symbol_definition(name).symbol.type == type

    def _check_not_already_defined(self, name: str, definition_ast_node: ast.AST):
        symbol_lookup_result = self.symbol_table.get_symbol_definition(name)
        if not symbol_lookup_result:
            symbol_lookup_result = self.custom_types_symbol_table.get_symbol_definition(name)
        if symbol_lookup_result:
            is_only_partially_defined = symbol_lookup_result.is_only_partially_defined
            previous_definition_ast_node = symbol_lookup_result.ast_node
        elif name in self.partially_typechecked_function_definitions_by_name:
            is_only_partially_defined = False
            previous_definition_ast_node = self.partially_typechecked_function_definitions_by_name[name]
        else:
            is_only_partially_defined = None
            previous_definition_ast_node = None
        if previous_definition_ast_node:
            if is_only_partially_defined:
                raise CompilationError(self, definition_ast_node,
                                       '%s could be already initialized at this point.' % name,
                                       notes=[(previous_definition_ast_node, 'It might have been initialized here (depending on which branch is taken).')])
            else:
                raise CompilationError(self, definition_ast_node,
                                       '%s was already defined in this scope.' % name,
                                       notes=[(previous_definition_ast_node, 'The previous declaration was here.')])

class CompilationError(Exception):
    def __init__(self, compilation_context: CompilationContext, ast_node: ast.AST, error_message: str, notes: List[Tuple[ast.AST, str]] = []):
        error_message = CompilationError._diagnostic_to_string(compilation_context=compilation_context,
                                                               ast_node=ast_node,
                                                               message='error: ' + error_message)
        notes = [CompilationError._diagnostic_to_string(compilation_context=compilation_context,
                                                        ast_node=note_ast_node,
                                                        message='note: ' + note_message)
                 for note_ast_node, note_message in notes]
        super().__init__(''.join([error_message] + notes))

    @staticmethod
    def _diagnostic_to_string(compilation_context: CompilationContext, ast_node: ast.AST, message: str):
        first_line_number = ast_node.lineno
        first_column_number = ast_node.col_offset
        error_marker = ' ' * first_column_number + '^'
        return textwrap.dedent('''\
            {filename}:{first_line_number}:{first_column_number}: {message}
            {line}
            {error_marker}
            ''').format(filename=compilation_context.filename,
                        first_line_number=first_line_number,
                        first_column_number=first_column_number,
                        message=message,
                        line=compilation_context.source_lines[first_line_number - 1],
                        error_marker=error_marker)

def module_ast_to_ir3(module_ast_node: ast.Module, filename: str, source_lines: List[str]):
    compilation_context = CompilationContext(SymbolTable(),
                                             SymbolTable(),
                                             filename,
                                             source_lines)

    function_defns = []
    toplevel_assertions = []
    custom_types = []

    # First pass: process everything except function bodies and toplevel assertions
    for ast_node in module_ast_node.body:
        if isinstance(ast_node, ast.FunctionDef):
            function_name, arg_types, return_type = function_def_ast_to_symbol_info(ast_node, compilation_context)

            if return_type:
                compilation_context.add_symbol(
                    name=function_name,
                    type=ir3.FunctionType(argtypes=arg_types,
                                          returns=return_type),
                    definition_ast_node=ast_node,
                    is_only_partially_defined=False,
                    is_function_that_may_throw=True)
            else:
                compilation_context.add_symbol_for_function_with_unknown_return_type(
                    name=function_name,
                    definition_ast_node=ast_node)
        elif isinstance(ast_node, ast.ImportFrom):
            supported_imports_by_module = {
                'tmppy': ('Type', 'empty_list', 'empty_set', 'match'),
                'typing': ('List', 'Set', 'Callable')
            }
            supported_imports = supported_imports_by_module.get(ast_node.module)
            if not supported_imports:
                raise CompilationError(compilation_context, ast_node,
                                       'The only modules that can be imported in TMPPy are: ' + ', '.join(sorted(supported_imports_by_module.keys())))
            if len(ast_node.names) == 0:
                raise CompilationError(compilation_context, ast_node, 'Imports must import at least 1 symbol.')  # pragma: no cover
            for imported_name in ast_node.names:
                if not isinstance(imported_name, ast.alias) or imported_name.asname:
                    raise CompilationError(compilation_context, ast_node, 'TMPPy only supports imports of the form "from some_module import some_symbol, some_other_symbol".')
                if imported_name.name not in supported_imports:
                    raise CompilationError(compilation_context, ast_node, 'The only supported imports from %s are: %s.' % (ast_node.module, ', '.join(sorted(supported_imports))))
        elif isinstance(ast_node, ast.Import):
            raise CompilationError(compilation_context, ast_node,
                                   'TMPPy only supports imports of the form "from some_module import some_symbol, some_other_symbol".')
        elif isinstance(ast_node, ast.ClassDef):
            custom_type = class_definition_ast_to_ir3(ast_node, compilation_context)
            compilation_context.add_custom_type_symbol(custom_type=custom_type,
                                                       definition_ast_node=ast_node)
            custom_types.append(custom_type)
        elif isinstance(ast_node, ast.Assert):
            # We'll process this in the 2nd pass (since we need to infer function return types first).
            pass
        else:
            # raise CompilationError(compilation_context, ast_node, 'This Python construct is not supported in TMPPy:\n%s' % ast_to_string(ast_node))
            raise CompilationError(compilation_context, ast_node, 'This Python construct is not supported in TMPPy')

    # 2nd pass: process function bodies and toplevel assertions
    for ast_node in module_ast_node.body:
        if isinstance(ast_node, ast.FunctionDef):
            new_function_defn = function_def_ast_to_ir3(ast_node, compilation_context)
            function_defns.append(new_function_defn)

            compilation_context.set_function_type(
                name=ast_node.name,
                type=ir3.FunctionType(returns=new_function_defn.return_type,
                                      argtypes=[arg.type
                                                   for arg in new_function_defn.args]))
        elif isinstance(ast_node, ast.Assert):
            toplevel_assertions.append(assert_ast_to_ir3(ast_node, compilation_context))

    public_names = set()
    for function_defn in function_defns:
      if not function_defn.name.startswith('_'):
        public_names.add(function_defn.name)

    return ir3.Module(function_defns=function_defns,
                      assertions=toplevel_assertions,
                      custom_types=custom_types,
                      public_names=public_names)

def match_expression_ast_to_ir3(ast_node: ast.Call, compilation_context: CompilationContext, in_match_pattern: bool, check_var_reference: Callable[[ast.Name], None]):
    assert isinstance(ast_node.func, ast.Call)
    if ast_node.keywords:
        raise CompilationError(compilation_context, ast_node.keywords[0].value, 'Keyword arguments are not allowed in match()')
    if ast_node.func.keywords:
        raise CompilationError(compilation_context, ast_node.func.keywords[0].value, 'Keyword arguments are not allowed in match()')
    if not ast_node.func.args:
        raise CompilationError(compilation_context, ast_node.func, 'Found match() with no arguments; it must have at least 1 argument.')
    matched_exprs = []
    for expr_ast in ast_node.func.args:
        expr = expression_ast_to_ir3(expr_ast, compilation_context, in_match_pattern, check_var_reference)
        if expr.type != ir3.TypeType():
            raise CompilationError(compilation_context, expr_ast,
                                   'All arguments passed to match must have type Type, but an argument with type %s was specified.' % str(expr.type))
        matched_exprs.append(expr)

    if len(ast_node.args) != 1 or not isinstance(ast_node.args[0], ast.Lambda):
        raise CompilationError(compilation_context, ast_node, 'Malformed match()')
    [lambda_expr_ast] = ast_node.args
    lambda_args = lambda_expr_ast.args
    if lambda_args.vararg:
        raise CompilationError(compilation_context, lambda_args.vararg,
                               'Malformed match(): vararg lambda arguments are not supported')
    assert not lambda_args.kwonlyargs
    assert not lambda_args.kw_defaults
    assert not lambda_args.defaults

    lambda_arg_ast_node_by_name = {arg.arg: arg
                                   for arg in lambda_args.args}
    lambda_arg_index_by_name = {arg.arg: i
                                for i, arg in enumerate(lambda_args.args)}
    lambda_arg_names = {arg.arg for arg in lambda_args.args}
    unused_lambda_arg_names = {arg.arg for arg in lambda_args.args}

    lambda_body_compilation_context = compilation_context.create_child_context()
    lambda_arguments = []
    for arg in lambda_args.args:
        lambda_arguments.append(arg.arg)
        lambda_body_compilation_context.add_symbol(name=arg.arg,
                                                   type=ir3.TypeType(),
                                                   definition_ast_node=arg,
                                                   is_only_partially_defined=False,
                                                   is_function_that_may_throw=False)

    if not isinstance(lambda_expr_ast.body, ast.Dict):
        raise CompilationError(compilation_context, ast_node, 'Malformed match()')
    dict_expr_ast = lambda_expr_ast.body

    if not dict_expr_ast.keys:
        raise CompilationError(compilation_context, dict_expr_ast,
                               'An empty mapping dict was passed to match(), but at least 1 mapping is required.')

    parent_function_name = compilation_context.current_function_name
    assert parent_function_name

    main_definition = None
    main_definition_key_expr_ast = None
    last_result_expr_type = None
    last_result_expr_ast_node = None
    match_cases = []
    for key_expr_ast, value_expr_ast in zip(dict_expr_ast.keys, dict_expr_ast.values):
        if isinstance(key_expr_ast, ast.Tuple):
            pattern_ast_nodes = key_expr_ast.elts
        else:
            pattern_ast_nodes = [key_expr_ast]

        if len(pattern_ast_nodes) != len(matched_exprs):
            raise CompilationError(lambda_body_compilation_context, key_expr_ast,
                                   '%s type patterns were provided, while %s were expected' % (len(pattern_ast_nodes), len(matched_exprs)),
                                   [(ast_node.func, 'The corresponding match() was here')])

        pattern_exprs = []
        for pattern_ast_node in pattern_ast_nodes:
            pattern_expr = expression_ast_to_ir3(pattern_ast_node, lambda_body_compilation_context, in_match_pattern=True, check_var_reference=check_var_reference)
            pattern_exprs.append(pattern_expr)
            if pattern_expr.type != ir3.TypeType():
                raise CompilationError(lambda_body_compilation_context, pattern_ast_node,
                                       'Type patterns must have type Type but this pattern has type %s' % str(pattern_expr.type),
                                       [(ast_node.func, 'The corresponding match() was here')])

        lambda_args_used_in_pattern = {var.name
                                       for pattern_expr in pattern_exprs
                                       for var in pattern_expr.get_free_variables()
                                       if var.name in lambda_arg_names}
        for var in lambda_args_used_in_pattern:
            unused_lambda_arg_names.discard(var)

        def check_var_reference_in_result_expr(ast_node: ast.Name):
            check_var_reference(ast_node)
            if ast_node.id in lambda_arg_names and not ast_node.id in lambda_args_used_in_pattern:
                raise CompilationError(lambda_body_compilation_context, ast_node,
                                       '%s was used in the result of this match branch but not in any of its patterns' % ast_node.id)

        result_expr = expression_ast_to_ir3(value_expr_ast, lambda_body_compilation_context,
                                            in_match_pattern=in_match_pattern,
                                            check_var_reference=check_var_reference_in_result_expr)

        if last_result_expr_type and result_expr.type != last_result_expr_type:
            raise CompilationError(lambda_body_compilation_context, value_expr_ast,
                                   'All branches in a match() must return the same type, but this branch returns a %s '
                                   'while a previous branch in this match expression returns a %s' % (
                                       str(result_expr.type), str(last_result_expr_type)),
                                   notes=[(last_result_expr_ast_node,
                                           'A previous branch returning a %s was here.' % str(last_result_expr_type))])
        last_result_expr_type = result_expr.type
        last_result_expr_ast_node = value_expr_ast

        match_case = ir3.MatchCase(matched_var_names=lambda_args_used_in_pattern,
                                   type_patterns=pattern_exprs,
                                   expr=result_expr)
        match_cases.append(match_case)

        if match_case.is_main_definition():
            if main_definition:
                assert main_definition_key_expr_ast
                raise CompilationError(lambda_body_compilation_context, key_expr_ast,
                                       'Found multiple specializations that specialize nothing',
                                       notes=[(main_definition_key_expr_ast, 'A previous specialization that specializes nothing was here')])
            main_definition = match_case
            main_definition_key_expr_ast = key_expr_ast

    if unused_lambda_arg_names:
        unused_arg_name = max(unused_lambda_arg_names, key=lambda arg_name: lambda_arg_index_by_name[arg_name])
        unused_arg_ast_node = lambda_arg_ast_node_by_name[unused_arg_name]
        raise CompilationError(compilation_context, unused_arg_ast_node,
                               'The lambda argument %s was not used in any pattern, it should be removed.' % unused_arg_name)

    return ir3.MatchExpr(matched_exprs=matched_exprs,
                         match_cases=match_cases)

def return_stmt_ast_to_ir3(ast_node: ast.Return,
                           compilation_context: CompilationContext):
    expression = ast_node.value
    if not expression:
        raise CompilationError(compilation_context, ast_node,
                               'Return statements with no returned expression are not supported.')

    expression = expression_ast_to_ir3(expression, compilation_context, in_match_pattern=False, check_var_reference=lambda ast_node: None)

    return ir3.ReturnStmt(expr=expression)

def if_stmt_ast_to_ir3(ast_node: ast.If,
                       compilation_context: CompilationContext,
                       previous_return_stmt: Optional[Tuple[ir3.ExprType, ast.Return]],
                       check_always_returns: bool):
    cond_expr = expression_ast_to_ir3(ast_node.test, compilation_context, in_match_pattern=False, check_var_reference=lambda ast_node: None)
    if cond_expr.type != ir3.BoolType():
        raise CompilationError(compilation_context, ast_node,
                               'The condition in an if statement must have type bool, but was: %s' % str(cond_expr.type))

    if_branch_compilation_context = compilation_context.create_child_context()
    if_stmts, first_return_stmt = statements_ast_to_ir3(ast_node.body, if_branch_compilation_context,
                                                       previous_return_stmt=previous_return_stmt,
                                                       check_block_always_returns=check_always_returns,
                                                       stmts_are_toplevel_in_function=False)

    if not previous_return_stmt and first_return_stmt:
        previous_return_stmt = first_return_stmt

    else_branch_compilation_context = compilation_context.create_child_context()

    if ast_node.orelse:
        else_stmts, first_return_stmt = statements_ast_to_ir3(ast_node.orelse, else_branch_compilation_context,
                                                             previous_return_stmt=previous_return_stmt,
                                                             check_block_always_returns=check_always_returns,
                                                             stmts_are_toplevel_in_function=False)

        if not previous_return_stmt and first_return_stmt:
            previous_return_stmt = first_return_stmt
    else:
        else_stmts = []
        if check_always_returns:
            raise CompilationError(compilation_context, ast_node,
                                   'Missing return statement. You should add an else branch that returns, or a return after the if.')

    _join_definitions_in_branches(compilation_context,
                                  if_branch_compilation_context,
                                  if_stmts,
                                  else_branch_compilation_context,
                                  else_stmts)

    return ir3.IfStmt(cond_expr=cond_expr, if_stmts=if_stmts, else_stmts=else_stmts), previous_return_stmt

def _join_definitions_in_branches(parent_context: CompilationContext,
                                  branch1_context: CompilationContext,
                                  branch1_stmts: List[ir3.Stmt],
                                  branch2_context: CompilationContext,
                                  branch2_stmts: List[ir3.Stmt]):
    branch1_return_info = branch1_stmts[-1].get_return_type()
    if branch2_stmts:
        branch2_return_info = branch2_stmts[-1].get_return_type()
    else:
        branch2_return_info = ir3.ReturnTypeInfo(type=None, always_returns=False)

    symbol_names = set()
    if not branch1_return_info.always_returns:
        symbol_names = symbol_names.union(branch1_context.symbol_table.symbols_by_name.keys())
    if not branch2_return_info.always_returns:
        symbol_names = symbol_names.union(branch2_context.symbol_table.symbols_by_name.keys())

    for symbol_name in symbol_names:
        if branch1_return_info.always_returns or symbol_name not in branch1_context.symbol_table.symbols_by_name:
            branch1_symbol = None
            branch1_definition_ast_node = None
            branch1_symbol_is_only_partially_defined = None
        else:
            branch1_symbol, branch1_definition_ast_node, branch1_symbol_is_only_partially_defined = branch1_context.symbol_table.symbols_by_name[symbol_name]

        if branch2_return_info.always_returns or symbol_name not in branch2_context.symbol_table.symbols_by_name:
            branch2_symbol = None
            branch2_definition_ast_node = None
            branch2_symbol_is_only_partially_defined = None
        else:
            branch2_symbol, branch2_definition_ast_node, branch2_symbol_is_only_partially_defined = branch2_context.symbol_table.symbols_by_name[symbol_name]

        if branch1_symbol and branch2_symbol:
            if branch1_symbol.type != branch2_symbol.type:
                raise CompilationError(parent_context, branch2_definition_ast_node,
                                       'The variable %s is defined with type %s here, but it was previously defined with type %s in another branch.' % (
                                           symbol_name, str(branch2_symbol.type), str(branch1_symbol.type)),
                                       notes=[(branch1_definition_ast_node, 'A previous definition with type %s was here.' % str(branch1_symbol.type))])
            symbol = branch1_symbol
            definition_ast_node = branch1_definition_ast_node
            is_only_partially_defined = branch1_symbol_is_only_partially_defined or branch2_symbol_is_only_partially_defined
        elif branch1_symbol:
            symbol = branch1_symbol
            definition_ast_node = branch1_definition_ast_node
            if branch2_return_info.always_returns:
                is_only_partially_defined = branch1_symbol_is_only_partially_defined
            else:
                is_only_partially_defined = True
        else:
            assert branch2_symbol
            symbol = branch2_symbol
            definition_ast_node = branch2_definition_ast_node
            if branch1_return_info.always_returns:
                is_only_partially_defined = branch2_symbol_is_only_partially_defined
            else:
                is_only_partially_defined = True

        parent_context.add_symbol(name=symbol.name,
                                  type=symbol.type,
                                  definition_ast_node=definition_ast_node,
                                  is_only_partially_defined=is_only_partially_defined,
                                  is_function_that_may_throw=isinstance(symbol.type, ir3.FunctionType))

def raise_stmt_ast_to_ir3(ast_node: ast.Raise, compilation_context: CompilationContext):
    if ast_node.cause:
        raise CompilationError(compilation_context, ast_node.cause,
                               '"raise ... from ..." is not supported. Use a plain "raise ..." instead.')
    exception_expr = expression_ast_to_ir3(ast_node.exc, compilation_context, in_match_pattern=False, check_var_reference=lambda ast_node: None)
    if not (isinstance(exception_expr.type, ir3.CustomType) and exception_expr.type.is_exception_class):
        if isinstance(exception_expr.type, ir3.CustomType):
            custom_type_defn = compilation_context.get_type_symbol_definition(exception_expr.type.name).ast_node
            notes = [(custom_type_defn, 'The type %s was defined here.' % exception_expr.type.name)]
        else:
            notes = []
        raise CompilationError(compilation_context, ast_node.exc,
                               'Can\'t raise an exception of type "%s", because it\'s not a subclass of Exception.' % str(exception_expr.type),
                               notes=notes)
    return ir3.RaiseStmt(expr=exception_expr)

def try_stmt_ast_to_ir3(ast_node: ast.Try,
                       compilation_context: CompilationContext,
                       previous_return_stmt: Optional[Tuple[ir3.ExprType, ast.Return]],
                       check_always_returns: bool,
                       is_toplevel_in_function: bool):

    if not is_toplevel_in_function:
        raise CompilationError(compilation_context, ast_node,
                               'try-except blocks are only supported at top-level in functions (not e.g. inside if-else statements).')

    body_compilation_context = compilation_context.create_child_context()
    body_stmts, body_first_return_stmt = statements_ast_to_ir3(ast_node.body,
                                                              body_compilation_context,
                                                              check_block_always_returns=check_always_returns,
                                                              previous_return_stmt=previous_return_stmt,
                                                              stmts_are_toplevel_in_function=False)
    if not previous_return_stmt:
        previous_return_stmt = body_first_return_stmt

    if not ast_node.handlers:
        raise CompilationError(compilation_context, ast_node,
                               '"try" blocks must have an "except" clause.')
    # TODO: consider supporting this case too.
    if len(ast_node.handlers) > 1:
        raise CompilationError(compilation_context, ast_node.handlers[1],
                               '"try" blocks with multiple "except" clauses are not currently supported.')
    [handler] = ast_node.handlers
    if not (isinstance(handler, ast.ExceptHandler)
            and isinstance(handler.type, ast.Name)
            and isinstance(handler.type.ctx, ast.Load)
            and handler.name):
        raise CompilationError(compilation_context, handler,
                               '"except" clauses must be of the form: except SomeType as some_var')

    # TODO: consider adding support for this.
    if handler.type.id == 'Exception':
        raise CompilationError(compilation_context, handler.type,
                               'Catching all exceptions is not supported, you must catch a specific exception type.')

    caught_exception_type = type_declaration_ast_to_ir3_expression_type(handler.type, compilation_context)

    if ast_node.orelse:
        raise CompilationError(compilation_context, ast_node.orelse[0], '"else" clauses are not supported in try-except.')

    if ast_node.finalbody:
        raise CompilationError(compilation_context, ast_node.finalbody[0], '"finally" clauses are not supported.')

    except_body_compilation_context = compilation_context.create_child_context()
    except_body_compilation_context.add_symbol(name=handler.name,
                                               type=caught_exception_type,
                                               definition_ast_node=handler,
                                               is_only_partially_defined=False,
                                               is_function_that_may_throw=False)
    except_body_stmts, except_body_first_return_stmt = statements_ast_to_ir3(handler.body,
                                                                            except_body_compilation_context,
                                                                            check_block_always_returns=check_always_returns,
                                                                            previous_return_stmt=previous_return_stmt,
                                                                            stmts_are_toplevel_in_function=False)
    if not previous_return_stmt:
        previous_return_stmt = except_body_first_return_stmt

    _join_definitions_in_branches(compilation_context,
                                  body_compilation_context,
                                  body_stmts,
                                  except_body_compilation_context,
                                  except_body_stmts)

    try_except_stmt = ir3.TryExcept(try_body=body_stmts,
                                    caught_exception_type=caught_exception_type,
                                    caught_exception_name=handler.name,
                                    except_body=except_body_stmts)

    return try_except_stmt, previous_return_stmt

def statements_ast_to_ir3(ast_nodes: List[ast.AST],
                         compilation_context: CompilationContext,
                         previous_return_stmt: Optional[Tuple[ir3.ExprType, ast.Return]],
                         check_block_always_returns: bool,
                         stmts_are_toplevel_in_function: bool):
    assert ast_nodes

    statements = []
    first_return_stmt = None
    for statement_node in ast_nodes:
        if statements and statements[-1].get_return_type().always_returns:
            raise CompilationError(compilation_context, statement_node, 'Unreachable statement.')

        check_stmt_always_returns = check_block_always_returns and statement_node is ast_nodes[-1]

        if isinstance(statement_node, ast.Assert):
            statements.append(assert_ast_to_ir3(statement_node, compilation_context))
        elif isinstance(statement_node, ast.Assign) or isinstance(statement_node, ast.AnnAssign) or isinstance(statement_node, ast.AugAssign):
            statements.append(assignment_ast_to_ir3(statement_node, compilation_context))
        elif isinstance(statement_node, ast.Return):
            return_stmt = return_stmt_ast_to_ir3(statement_node, compilation_context)
            if previous_return_stmt:
                previous_return_stmt_type, previous_return_stmt_ast_node = previous_return_stmt
                if return_stmt.expr.type != previous_return_stmt_type:
                    raise CompilationError(compilation_context, statement_node,
                                           'Found return statement with different return type: %s instead of %s.' % (
                                               str(return_stmt.expr.type), str(previous_return_stmt_type)),
                                           notes=[(previous_return_stmt_ast_node, 'A previous return statement returning a %s was here.' % (
                                               str(previous_return_stmt_type),))])
            if not first_return_stmt:
                first_return_stmt = (return_stmt.expr.type, statement_node)
            if not previous_return_stmt:
                previous_return_stmt = first_return_stmt
            statements.append(return_stmt)
        elif isinstance(statement_node, ast.If):
            if_stmt, first_return_stmt_in_if = if_stmt_ast_to_ir3(statement_node,
                                                                 compilation_context,
                                                                 previous_return_stmt,
                                                                 check_stmt_always_returns)
            if not first_return_stmt:
                first_return_stmt = first_return_stmt_in_if
            if not previous_return_stmt:
                previous_return_stmt = first_return_stmt
            statements.append(if_stmt)
        elif isinstance(statement_node, ast.Raise):
            statements.append(raise_stmt_ast_to_ir3(statement_node, compilation_context))
        elif isinstance(statement_node, ast.Try):
            try_except_stmt, first_return_stmt_in_try_except = try_stmt_ast_to_ir3(statement_node,
                                                                                  compilation_context,
                                                                                  previous_return_stmt=previous_return_stmt,
                                                                                  check_always_returns=check_stmt_always_returns,
                                                                                  is_toplevel_in_function=stmts_are_toplevel_in_function)
            if not first_return_stmt:
                first_return_stmt = first_return_stmt_in_try_except
            if not previous_return_stmt:
                previous_return_stmt = first_return_stmt
            statements.append(try_except_stmt)
        else:
            raise CompilationError(compilation_context, statement_node, 'Unsupported statement.')

    if check_block_always_returns and not statements[-1].get_return_type().always_returns:
        raise CompilationError(compilation_context, ast_nodes[-1],
                               'Missing return statement.')

    return statements, first_return_stmt

def function_def_ast_to_symbol_info(ast_node: ast.FunctionDef, compilation_context: CompilationContext):
    function_body_compilation_context = compilation_context.create_child_context(function_name=ast_node.name)
    arg_types = []
    for arg in ast_node.args.args:
        if not arg.annotation:
            if arg.type_comment:
                raise CompilationError(compilation_context, arg, 'All function arguments must have a type annotation. Note that type comments are not supported.')
            else:
                raise CompilationError(compilation_context, arg, 'All function arguments must have a type annotation.')
        arg_type = type_declaration_ast_to_ir3_expression_type(arg.annotation, compilation_context)
        function_body_compilation_context.add_symbol(name=arg.arg,
                                                     type=arg_type,
                                                     definition_ast_node=arg,
                                                     is_only_partially_defined=False,
                                                     is_function_that_may_throw=isinstance(arg_type, ir3.FunctionType))
        arg_types.append(arg_type)
    if not arg_types:
        raise CompilationError(compilation_context, ast_node, 'Functions with no arguments are not supported.')

    if ast_node.args.vararg:
        raise CompilationError(compilation_context, ast_node, 'Function vararg arguments are not supported.')
    if ast_node.args.kwonlyargs:
        raise CompilationError(compilation_context, ast_node, 'Keyword-only function arguments are not supported.')
    if ast_node.args.kw_defaults or ast_node.args.defaults:
        raise CompilationError(compilation_context, ast_node, 'Default values for function arguments are not supported.')
    if ast_node.args.kwarg:
        raise CompilationError(compilation_context, ast_node, 'Keyword function arguments are not supported.')
    if ast_node.decorator_list:
        raise CompilationError(compilation_context, ast_node, 'Function decorators are not supported.')

    if ast_node.returns:
        return_type = type_declaration_ast_to_ir3_expression_type(ast_node.returns, compilation_context)
    else:
        return_type = None

    return ast_node.name, arg_types, return_type

def function_def_ast_to_ir3(ast_node: ast.FunctionDef, compilation_context: CompilationContext):
    function_body_compilation_context = compilation_context.create_child_context(function_name=ast_node.name)
    args = []
    for arg in ast_node.args.args:
        arg_type = type_declaration_ast_to_ir3_expression_type(arg.annotation, compilation_context)
        function_body_compilation_context.add_symbol(name=arg.arg,
                                                     type=arg_type,
                                                     definition_ast_node=arg,
                                                     is_only_partially_defined=False,
                                                     is_function_that_may_throw=isinstance(arg_type, ir3.FunctionType))
        args.append(ir3.FunctionArgDecl(type=arg_type, name=arg.arg))

    statements, first_return_stmt = statements_ast_to_ir3(ast_node.body, function_body_compilation_context,
                                                         previous_return_stmt=None,
                                                         check_block_always_returns=True,
                                                         stmts_are_toplevel_in_function=True)

    if first_return_stmt:
        return_type, first_return_stmt_ast_node = first_return_stmt

    if ast_node.returns:
        declared_return_type = type_declaration_ast_to_ir3_expression_type(ast_node.returns, compilation_context)

        # first_return_stmt can be None if the function raises an exception instead of returning in all branches.
        if first_return_stmt:
            if declared_return_type != return_type:
                raise CompilationError(compilation_context, ast_node.returns,
                                       '%s declared %s as return type, but the actual return type was %s.' % (
                                           ast_node.name, str(declared_return_type), str(return_type)),
                                       notes=[(first_return_stmt_ast_node, 'A %s was returned here' % str(return_type))])

        return_type = declared_return_type

    if not first_return_stmt and not ast_node.returns:
        return_type = ir3.BottomType()

    return ir3.FunctionDefn(name=ast_node.name,
                            args=args,
                            body=statements,
                            return_type=return_type)

def assert_ast_to_ir3(ast_node: ast.Assert, compilation_context: CompilationContext):
    expr = expression_ast_to_ir3(ast_node.test, compilation_context, in_match_pattern=False, check_var_reference=lambda ast_node: None)

    if not isinstance(expr.type, ir3.BoolType):
        raise CompilationError(compilation_context, ast_node.test,
                               'The value passed to assert must have type bool, but got a value with type %s.' % expr.type)

    if ast_node.msg:
        assert isinstance(ast_node.msg, ast.Str)
        message = ast_node.msg.s
    else:
        message = ''

    first_line_number = ast_node.lineno
    message = 'TMPPy assertion failed: {message}\n{filename}:{first_line_number}: {line}'.format(
        filename=compilation_context.filename,
        first_line_number=first_line_number,
        message=message,
        line=compilation_context.source_lines[first_line_number - 1])
    message = message.replace('\\', '\\\\').replace('"', '\"').replace('\n', '\\n')

    return ir3.Assert(expr=expr, message=message)

def assignment_ast_to_ir3(ast_node: Union[ast.Assign, ast.AnnAssign, ast.AugAssign],
                          compilation_context: CompilationContext):
    if isinstance(ast_node, ast.AugAssign):
        raise CompilationError(compilation_context, ast_node, 'Augmented assignments are not supported.')
    if isinstance(ast_node, ast.AnnAssign):
        raise CompilationError(compilation_context, ast_node, 'Assignments with type annotations are not supported.')
    assert isinstance(ast_node, ast.Assign)
    if ast_node.type_comment:
        raise CompilationError(compilation_context, ast_node, 'Type comments in assignments are not supported.')
    if len(ast_node.targets) > 1:
        raise CompilationError(compilation_context, ast_node, 'Multi-assignment is not supported.')
    [target] = ast_node.targets
    if isinstance(target, ast.List) or isinstance(target, ast.Tuple):
        # This is an "unpacking" assignment
        for lhs_elem_ast_node in target.elts:
            if not isinstance(lhs_elem_ast_node, ast.Name):
                raise CompilationError(compilation_context, lhs_elem_ast_node,
                                       'This kind of unpacking assignment is not supported. Only unpacking assignments of the form x,y=... or [x,y]=... are supported.')

        expr = expression_ast_to_ir3(ast_node.value, compilation_context, in_match_pattern=False, check_var_reference=lambda ast_node: None)
        if not isinstance(expr.type, ir3.ListType):
            raise CompilationError(compilation_context, ast_node,
                                   'Unpacking requires a list on the RHS, but the value on the RHS has type %s' % str(expr.type))
        elem_type = expr.type.elem_type

        var_refs = []
        for lhs_elem_ast_node in target.elts:
            compilation_context.add_symbol(name=lhs_elem_ast_node.id,
                                           type=elem_type,
                                           definition_ast_node=lhs_elem_ast_node,
                                           is_only_partially_defined=False,
                                           is_function_that_may_throw=isinstance(elem_type, ir3.FunctionType))

            var_ref = ir3.VarReference(type=elem_type,
                                       name=lhs_elem_ast_node.id,
                                       is_global_function=False,
                                       is_function_that_may_throw=isinstance(elem_type, ir3.FunctionType))
            var_refs.append(var_ref)

        first_line_number = ast_node.lineno
        message = 'unexpected number of elements in the TMPPy list unpacking at:\n{filename}:{first_line_number}: {line}'.format(
            filename=compilation_context.filename,
            first_line_number=first_line_number,
            line=compilation_context.source_lines[first_line_number - 1])
        message = message.replace('\\', '\\\\').replace('"', '\"').replace('\n', '\\n')

        return ir3.UnpackingAssignment(lhs_list=var_refs,
                                          rhs=expr,
                                          error_message=message)

    elif isinstance(target, ast.Name):
        # This is a "normal" assignment
        expr = expression_ast_to_ir3(ast_node.value, compilation_context, in_match_pattern=False, check_var_reference=lambda ast_node: None)

        compilation_context.add_symbol(name=target.id,
                                       type=expr.type,
                                       definition_ast_node=target,
                                       is_only_partially_defined=False,
                                       is_function_that_may_throw=isinstance(expr.type, ir3.FunctionType))

        return ir3.Assignment(lhs=ir3.VarReference(type=expr.type,
                                                   name=target.id,
                                                   is_global_function=False,
                                                   is_function_that_may_throw=isinstance(expr.type, ir3.FunctionType)),
                                 rhs=expr)
    else:
        raise CompilationError(compilation_context, ast_node, 'Assignment not supported.')

def int_comparison_ast_to_ir3(lhs_ast_node: ast.AST,
                              rhs_ast_node: ast.AST,
                              op: str,
                              compilation_context: CompilationContext,
                              in_match_pattern: bool,
                              check_var_reference: Callable[[ast.Name], None]):
    lhs = expression_ast_to_ir3(lhs_ast_node, compilation_context, in_match_pattern, check_var_reference)
    rhs = expression_ast_to_ir3(rhs_ast_node, compilation_context, in_match_pattern, check_var_reference)

    if lhs.type != ir3.IntType():
        raise CompilationError(compilation_context, lhs_ast_node,
                               'The "%s" operator is only supported for ints, but this value has type %s.' % (op, str(lhs.type)))
    if rhs.type != ir3.IntType():
        raise CompilationError(compilation_context, rhs_ast_node,
                               'The "%s" operator is only supported for ints, but this value has type %s.' % (op, str(rhs.type)))

    return ir3.IntComparisonExpr(lhs=lhs, rhs=rhs, op=op)

def compare_ast_to_ir3(ast_node: ast.Compare,
                       compilation_context: CompilationContext,
                       in_match_pattern: bool,
                       check_var_reference: Callable[[ast.Name], None]):
    if len(ast_node.ops) != 1 or len(ast_node.comparators) != 1:
        raise CompilationError(compilation_context, ast_node, 'Comparison not supported.')  # pragma: no cover

    if in_match_pattern:
        raise CompilationError(compilation_context, ast_node,
                               'Comparisons are not allowed in match patterns')

    lhs = ast_node.left
    op = ast_node.ops[0]
    rhs = ast_node.comparators[0]

    if isinstance(op, ast.Eq):
        return eq_ast_to_ir3(lhs, rhs, compilation_context, in_match_pattern, check_var_reference)
    elif isinstance(op, ast.NotEq):
        return not_eq_ast_to_ir3(lhs, rhs, compilation_context, in_match_pattern, check_var_reference)
    elif isinstance(op, ast.Lt):
        return int_comparison_ast_to_ir3(lhs, rhs, '<', compilation_context, in_match_pattern, check_var_reference)
    elif isinstance(op, ast.LtE):
        return int_comparison_ast_to_ir3(lhs, rhs, '<=', compilation_context, in_match_pattern, check_var_reference)
    elif isinstance(op, ast.Gt):
        return int_comparison_ast_to_ir3(lhs, rhs, '>', compilation_context, in_match_pattern, check_var_reference)
    elif isinstance(op, ast.GtE):
        return int_comparison_ast_to_ir3(lhs, rhs, '>=', compilation_context, in_match_pattern, check_var_reference)
    else:
        raise CompilationError(compilation_context, ast_node, 'Comparison not supported.')  # pragma: no cover

def attribute_expression_ast_to_ir3(ast_node: ast.Attribute,
                                    compilation_context: CompilationContext,
                                    in_match_pattern: bool,
                                    check_var_reference: Callable[[ast.Name], None]):
    if in_match_pattern:
        raise CompilationError(compilation_context, ast_node,
                               'Attribute access is not allowed in match patterns')

    value_expr = expression_ast_to_ir3(ast_node.value, compilation_context, in_match_pattern, check_var_reference)
    if isinstance(value_expr.type, ir3.TypeType):
        return ir3.AttributeAccessExpr(expr=value_expr,
                                       attribute_name=ast_node.attr,
                                       type=ir3.TypeType())
    elif isinstance(value_expr.type, ir3.CustomType):
        for arg in value_expr.type.arg_types:
            if arg.name == ast_node.attr:
                return ir3.AttributeAccessExpr(expr=value_expr,
                                               attribute_name=ast_node.attr,
                                               type=arg.type)
        else:
            lookup_result = compilation_context.get_type_symbol_definition(value_expr.type.name)
            assert lookup_result
            raise CompilationError(compilation_context, ast_node.value,
                                   'Values of type "%s" don\'t have the attribute "%s". The available attributes for this type are: {"%s"}.' % (
                                       str(value_expr.type), ast_node.attr, '", "'.join(sorted(arg.name
                                                                                               for arg in value_expr.type.arg_types))),
                                   notes=[(lookup_result.ast_node, '%s was defined here.' % str(value_expr.type))])
    else:
        raise CompilationError(compilation_context, ast_node.value,
                               'Attribute access is not supported for values of type %s.' % str(value_expr.type))

def number_literal_expression_ast_to_ir3(ast_node: ast.Num,
                                         compilation_context: CompilationContext,
                                         in_match_pattern: bool,
                                         check_var_reference: Callable[[ast.Name], None],
                                         positive: bool):
    n = ast_node.n
    if isinstance(n, float):
        raise CompilationError(compilation_context, ast_node, 'Floating-point values are not supported.')
    if isinstance(n, complex):
        raise CompilationError(compilation_context, ast_node, 'Complex values are not supported.')
    assert isinstance(n, int)
    if not positive:
        n = -n
    if n <= -2**63:
        raise CompilationError(compilation_context, ast_node,
                               'int value out of bounds: values lower than -2^63+1 are not supported.')
    if n >= 2**63:
        raise CompilationError(compilation_context, ast_node,
                               'int value out of bounds: values greater than 2^63-1 are not supported.')
    return ir3.IntLiteral(value=n)

def and_expression_ast_to_ir3(ast_node: ast.BoolOp,
                              compilation_context: CompilationContext,
                              in_match_pattern: bool,
                              check_var_reference: Callable[[ast.Name], None]):
    assert isinstance(ast_node.op, ast.And)

    if in_match_pattern:
        raise CompilationError(compilation_context, ast_node,
                               'The "and" operator is not allowed in match patterns')

    if not compilation_context.current_function_name:
        raise CompilationError(compilation_context, ast_node,
                               'The "and" operator is only supported in functions, not at toplevel.')

    assert len(ast_node.values) >= 2

    exprs = []
    for expr_ast_node in ast_node.values:
        expr = expression_ast_to_ir3(expr_ast_node, compilation_context, in_match_pattern, check_var_reference)
        if expr.type != ir3.BoolType():
            raise CompilationError(compilation_context, expr_ast_node,
                                   'The "and" operator is only supported for booleans, but this value has type %s.' % str(expr.type))
        exprs.append(expr)

    final_expr = exprs[-1]
    for expr in reversed(exprs[:-1]):
        final_expr = ir3.AndExpr(lhs=expr, rhs=final_expr)

    return final_expr

def or_expression_ast_to_ir3(ast_node: ast.BoolOp,
                             compilation_context: CompilationContext,
                             in_match_pattern: bool,
                             check_var_reference: Callable[[ast.Name], None]):
    assert isinstance(ast_node.op, ast.Or)

    if in_match_pattern:
        raise CompilationError(compilation_context, ast_node,
                               'The "or" operator is not allowed in match patterns')

    if not compilation_context.current_function_name:
        raise CompilationError(compilation_context, ast_node,
                               'The "or" operator is only supported in functions, not at toplevel.')

    assert len(ast_node.values) >= 2

    exprs = []
    for expr_ast_node in ast_node.values:
        expr = expression_ast_to_ir3(expr_ast_node, compilation_context, in_match_pattern, check_var_reference)
        if expr.type != ir3.BoolType():
            raise CompilationError(compilation_context, expr_ast_node,
                                   'The "or" operator is only supported for booleans, but this value has type %s.' % str(expr.type))
        exprs.append(expr)

    final_expr = exprs[-1]
    for expr in reversed(exprs[:-1]):
        final_expr = ir3.OrExpr(lhs=expr, rhs=final_expr)

    return final_expr

def not_expression_ast_to_ir3(ast_node: ast.UnaryOp,
                              compilation_context: CompilationContext,
                              in_match_pattern: bool,
                              check_var_reference: Callable[[ast.Name], None]):
    assert isinstance(ast_node.op, ast.Not)

    if in_match_pattern:
        raise CompilationError(compilation_context, ast_node,
                               'The "not" operator is not allowed in match patterns')

    expr = expression_ast_to_ir3(ast_node.operand, compilation_context, in_match_pattern, check_var_reference)

    if expr.type != ir3.BoolType():
        raise CompilationError(compilation_context, ast_node.operand,
                               'The "not" operator is only supported for booleans, but this value has type %s.' % str(expr.type))

    return ir3.NotExpr(expr=expr)

def unary_minus_expression_ast_to_ir3(ast_node: ast.UnaryOp,
                                      compilation_context: CompilationContext,
                                      in_match_pattern: bool,
                                      check_var_reference: Callable[[ast.Name], None]):
    assert isinstance(ast_node.op, ast.USub)

    if in_match_pattern:
        raise CompilationError(compilation_context, ast_node,
                               'The "-" operator is not allowed in match patterns')

    expr = expression_ast_to_ir3(ast_node.operand, compilation_context, in_match_pattern, check_var_reference)

    if expr.type != ir3.IntType():
        raise CompilationError(compilation_context, ast_node.operand,
                               'The "-" operator is only supported for ints, but this value has type %s.' % str(expr.type))

    return ir3.IntUnaryMinusExpr(expr=expr)

def int_binary_op_expression_ast_to_ir3(ast_node: ast.BinOp,
                                        op: str,
                                        compilation_context: CompilationContext,
                                        in_match_pattern: bool,
                                        check_var_reference: Callable[[ast.Name], None]):
    lhs = expression_ast_to_ir3(ast_node.left, compilation_context, in_match_pattern, check_var_reference)
    rhs = expression_ast_to_ir3(ast_node.right, compilation_context, in_match_pattern, check_var_reference)

    if in_match_pattern:
        raise CompilationError(compilation_context, ast_node,
                               'The "%s" operator is not allowed in match patterns' % op)

    if lhs.type != ir3.IntType():
        raise CompilationError(compilation_context, ast_node.left,
                               'The "%s" operator is only supported for ints, but this value has type %s.' % (op, str(lhs.type)))

    if rhs.type != ir3.IntType():
        raise CompilationError(compilation_context, ast_node.right,
                               'The "%s" operator is only supported for ints, but this value has type %s.' % (op, str(rhs.type)))

    return ir3.IntBinaryOpExpr(lhs=lhs, rhs=rhs, op=op)

def list_comprehension_ast_to_ir3(ast_node: ast.ListComp,
                                  compilation_context: CompilationContext,
                                  in_match_pattern: bool,
                                  check_var_reference: Callable[[ast.Name], None]):
    assert ast_node.generators
    if len(ast_node.generators) > 1:
        raise CompilationError(compilation_context, ast_node.generators[1].target,
                               'List comprehensions with multiple "for" clauses are not currently supported.')

    if in_match_pattern:
        raise CompilationError(compilation_context, ast_node,
                               'List comprehensions are not allowed in match patterns')

    [generator] = ast_node.generators
    if generator.ifs:
        raise CompilationError(compilation_context, generator.ifs[0],
                               '"if" clauses in list comprehensions are not currently supported.')
    if not isinstance(generator.target, ast.Name):
        raise CompilationError(compilation_context, generator.target,
                               'Only list comprehensions of the form [... for var_name in ...] are supported.')

    list_expr = expression_ast_to_ir3(generator.iter, compilation_context, in_match_pattern, check_var_reference)
    if not isinstance(list_expr.type, ir3.ListType):
        notes = []
        if isinstance(list_expr, ir3.VarReference):
            lookup_result = compilation_context.get_symbol_definition(list_expr.name)
            assert lookup_result
            notes.append((lookup_result.ast_node, '%s was defined here' % list_expr.name))
        raise CompilationError(compilation_context, ast_node.generators[0].target,
                               'The RHS of a list comprehension should be a list, but this value has type "%s".' % str(list_expr.type),
                               notes=notes)

    child_context = compilation_context.create_child_context(function_name=compilation_context.current_function_name)
    child_context.add_symbol(name=generator.target.id,
                             type=list_expr.elem_type,
                             definition_ast_node=generator.target,
                             is_only_partially_defined=False,
                             is_function_that_may_throw=False)
    result_elem_expr = expression_ast_to_ir3(ast_node.elt, child_context, in_match_pattern, check_var_reference)

    if isinstance(result_elem_expr.type, ir3.FunctionType):
        raise CompilationError(compilation_context, ast_node,
                               'Creating lists of functions is not supported. The elements of this list have type: %s' % str(result_elem_expr.type))

    return ir3.ListComprehension(list_expr=list_expr,
                                 loop_var=ir3.VarReference(name=generator.target.id,
                                                           type=list_expr.elem_type,
                                                           is_global_function=False,
                                                           is_function_that_may_throw=False),
                                 result_elem_expr=result_elem_expr)

def set_comprehension_ast_to_ir3(ast_node: ast.SetComp,
                                 compilation_context: CompilationContext,
                                 in_match_pattern: bool,
                                 check_var_reference: Callable[[ast.Name], None]):
    assert ast_node.generators
    if len(ast_node.generators) > 1:
        raise CompilationError(compilation_context, ast_node.generators[1].target,
                               'Set comprehensions with multiple "for" clauses are not currently supported.')

    if in_match_pattern:
        raise CompilationError(compilation_context, ast_node,
                               'Set comprehensions are not allowed in match patterns')

    [generator] = ast_node.generators
    if generator.ifs:
        raise CompilationError(compilation_context, generator.ifs[0],
                               '"if" clauses in set comprehensions are not currently supported.')
    if not isinstance(generator.target, ast.Name):
        raise CompilationError(compilation_context, generator.target,
                               'Only set comprehensions of the form {... for var_name in ...} are supported.')

    set_expr = expression_ast_to_ir3(generator.iter, compilation_context, in_match_pattern, check_var_reference)
    if not isinstance(set_expr.type, ir3.SetType):
        notes = []
        if isinstance(set_expr, ir3.VarReference):
            lookup_result = compilation_context.get_symbol_definition(set_expr.name)
            assert lookup_result
            notes.append((lookup_result.ast_node, '%s was defined here' % set_expr.name))
        raise CompilationError(compilation_context, ast_node.generators[0].target,
                               'The RHS of a set comprehension should be a set, but this value has type "%s".' % str(set_expr.type),
                               notes=notes)

    child_context = compilation_context.create_child_context(function_name=compilation_context.current_function_name)
    child_context.add_symbol(name=generator.target.id,
                             type=set_expr.elem_type,
                             definition_ast_node=generator.target,
                             is_only_partially_defined=False,
                             is_function_that_may_throw=False)
    result_elem_expr = expression_ast_to_ir3(ast_node.elt, child_context, in_match_pattern, check_var_reference)

    if isinstance(result_elem_expr.type, ir3.FunctionType):
        raise CompilationError(compilation_context, ast_node,
                               'Creating sets of functions is not supported. The elements of this set have type: %s' % str(result_elem_expr.type))

    return ir3.SetComprehension(set_expr=set_expr,
                                loop_var=ir3.VarReference(name=generator.target.id,
                                                          type=set_expr.elem_type,
                                                          is_global_function=False,
                                                          is_function_that_may_throw=False),
                                result_elem_expr=result_elem_expr)


def add_expression_ast_to_ir3(ast_node: ast.BinOp,
                              compilation_context: CompilationContext,
                              in_match_pattern: bool,
                              check_var_reference: Callable[[ast.Name], None]):
    lhs = expression_ast_to_ir3(ast_node.left, compilation_context, in_match_pattern, check_var_reference)
    rhs = expression_ast_to_ir3(ast_node.right, compilation_context, in_match_pattern, check_var_reference)

    if in_match_pattern:
        raise CompilationError(compilation_context, ast_node,
                               'The "+" operator is not allowed in match patterns')

    if not isinstance(lhs.type, (ir3.IntType, ir3.ListType)):
        raise CompilationError(compilation_context, ast_node.left,
                               'The "+" operator is only supported for ints and lists, but this value has type %s.' % str(lhs.type))

    if not isinstance(rhs.type, (ir3.IntType, ir3.ListType)):
        raise CompilationError(compilation_context, ast_node.right,
                               'The "+" operator is only supported for ints and lists, but this value has type %s.' % str(rhs.type))

    if lhs.type != rhs.type:
        raise CompilationError(compilation_context, ast_node.left,
                               'Type mismatch: the LHS of "+" has type %s but the RHS has type %s.' % (str(lhs.type), str(rhs.type)))

    if lhs.type == ir3.IntType():
        return ir3.IntBinaryOpExpr(lhs=lhs, rhs=rhs, op='+')
    else:
        return ir3.ListConcatExpr(lhs=lhs, rhs=rhs)

def expression_ast_to_ir3(ast_node: ast.AST,
                          compilation_context: CompilationContext,
                          in_match_pattern: bool,
                          check_var_reference: Callable[[ast.Name], None]) -> ir3.Expr:
    if isinstance(ast_node, ast.NameConstant):
        return name_constant_ast_to_ir3(ast_node, compilation_context, in_match_pattern, check_var_reference)
    elif isinstance(ast_node, ast.Call) and isinstance(ast_node.func, ast.Name) and ast_node.func.id == 'Type':
        return atomic_type_literal_ast_to_ir3(ast_node, compilation_context, in_match_pattern, check_var_reference)
    elif (isinstance(ast_node, ast.Call)
          and isinstance(ast_node.func, ast.Attribute)
          and isinstance(ast_node.func.value, ast.Name) and ast_node.func.value.id == 'Type'):
        return type_factory_method_ast_to_ir3(ast_node, compilation_context, in_match_pattern, check_var_reference)
    elif isinstance(ast_node, ast.Call) and isinstance(ast_node.func, ast.Name) and ast_node.func.id == 'empty_list':
        return empty_list_literal_ast_to_ir3(ast_node, compilation_context, in_match_pattern, check_var_reference)
    elif isinstance(ast_node, ast.Call) and isinstance(ast_node.func, ast.Name) and ast_node.func.id == 'empty_set':
        return empty_set_literal_ast_to_ir3(ast_node, compilation_context, in_match_pattern, check_var_reference)
    elif isinstance(ast_node, ast.Call) and isinstance(ast_node.func, ast.Name) and ast_node.func.id == 'sum':
        return int_iterable_sum_expr_ast_to_ir3(ast_node, compilation_context, in_match_pattern, check_var_reference)
    elif isinstance(ast_node, ast.Call) and isinstance(ast_node.func, ast.Name) and ast_node.func.id == 'all':
        return bool_iterable_all_expr_ast_to_ir3(ast_node, compilation_context, in_match_pattern, check_var_reference)
    elif isinstance(ast_node, ast.Call) and isinstance(ast_node.func, ast.Name) and ast_node.func.id == 'any':
        return bool_iterable_any_expr_ast_to_ir3(ast_node, compilation_context, in_match_pattern, check_var_reference)
    elif isinstance(ast_node, ast.Call) and isinstance(ast_node.func, ast.Call) and isinstance(ast_node.func.func, ast.Name) and ast_node.func.func.id == 'match':
        return match_expression_ast_to_ir3(ast_node, compilation_context, in_match_pattern, check_var_reference)
    elif isinstance(ast_node, ast.Call):
        return function_call_ast_to_ir3(ast_node, compilation_context, in_match_pattern, check_var_reference)
    elif isinstance(ast_node, ast.Compare):
        return compare_ast_to_ir3(ast_node, compilation_context, in_match_pattern, check_var_reference)
    elif isinstance(ast_node, ast.Name) and isinstance(ast_node.ctx, ast.Load):
        return var_reference_ast_to_ir3(ast_node, compilation_context, in_match_pattern, check_var_reference)
    elif isinstance(ast_node, ast.List) and isinstance(ast_node.ctx, ast.Load):
        return list_expression_ast_to_ir3(ast_node, compilation_context, in_match_pattern, check_var_reference)
    elif isinstance(ast_node, ast.Set):
        return set_expression_ast_to_ir3(ast_node, compilation_context, in_match_pattern, check_var_reference)
    elif isinstance(ast_node, ast.Attribute) and isinstance(ast_node.ctx, ast.Load):
        return attribute_expression_ast_to_ir3(ast_node, compilation_context, in_match_pattern, check_var_reference)
    elif isinstance(ast_node, ast.Num):
        return number_literal_expression_ast_to_ir3(ast_node, compilation_context, in_match_pattern, check_var_reference, positive=True)
    elif isinstance(ast_node, ast.UnaryOp) and isinstance(ast_node.op, ast.USub) and isinstance(ast_node.operand, ast.Num):
        return number_literal_expression_ast_to_ir3(ast_node.operand, compilation_context, in_match_pattern, check_var_reference, positive=False)
    elif isinstance(ast_node, ast.BoolOp) and isinstance(ast_node.op, ast.And):
        return and_expression_ast_to_ir3(ast_node, compilation_context, in_match_pattern, check_var_reference)
    elif isinstance(ast_node, ast.BoolOp) and isinstance(ast_node.op, ast.Or):
        return or_expression_ast_to_ir3(ast_node, compilation_context, in_match_pattern, check_var_reference)
    elif isinstance(ast_node, ast.UnaryOp) and isinstance(ast_node.op, ast.Not):
        return not_expression_ast_to_ir3(ast_node, compilation_context, in_match_pattern, check_var_reference)
    elif isinstance(ast_node, ast.UnaryOp) and isinstance(ast_node.op, ast.USub):
        return unary_minus_expression_ast_to_ir3(ast_node, compilation_context, in_match_pattern, check_var_reference)
    elif isinstance(ast_node, ast.BinOp) and isinstance(ast_node.op, ast.Add):
        return add_expression_ast_to_ir3(ast_node, compilation_context, in_match_pattern, check_var_reference)
    elif isinstance(ast_node, ast.BinOp) and isinstance(ast_node.op, ast.Sub):
        return int_binary_op_expression_ast_to_ir3(ast_node, '-', compilation_context, in_match_pattern, check_var_reference)
    elif isinstance(ast_node, ast.BinOp) and isinstance(ast_node.op, ast.Mult):
        return int_binary_op_expression_ast_to_ir3(ast_node, '*', compilation_context, in_match_pattern, check_var_reference)
    elif isinstance(ast_node, ast.BinOp) and isinstance(ast_node.op, ast.FloorDiv):
        return int_binary_op_expression_ast_to_ir3(ast_node, '//', compilation_context, in_match_pattern, check_var_reference)
    elif isinstance(ast_node, ast.BinOp) and isinstance(ast_node.op, ast.Mod):
        return int_binary_op_expression_ast_to_ir3(ast_node, '%', compilation_context, in_match_pattern, check_var_reference)
    elif isinstance(ast_node, ast.ListComp):
        return list_comprehension_ast_to_ir3(ast_node, compilation_context, in_match_pattern, check_var_reference)
    elif isinstance(ast_node, ast.SetComp):
        return set_comprehension_ast_to_ir3(ast_node, compilation_context, in_match_pattern, check_var_reference)
    else:
        # raise CompilationError(compilation_context, ast_node, 'This kind of expression is not supported: %s' % ast_to_string(ast_node))
        raise CompilationError(compilation_context, ast_node, 'This kind of expression is not supported.')  # pragma: no cover

def name_constant_ast_to_ir3(ast_node: ast.NameConstant,
                             compilation_context: CompilationContext,
                             in_match_pattern: bool,
                             check_var_reference: Callable[[ast.Name], None]):
    if isinstance(ast_node.value, bool):
        return ir3.BoolLiteral(value=ast_node.value)
    else:
        raise CompilationError(compilation_context, ast_node, 'NameConstant not supported: ' + str(ast_node.value))  # pragma: no cover

_check_atomic_type_regex = re.compile(r'[A-Za-z_][A-Za-z0-9_]*(::[A-Za-z_][A-Za-z0-9_]*)*')

def _check_atomic_type(ast_node: ast.Str, compilation_context: CompilationContext):
    if not _check_atomic_type_regex.fullmatch(ast_node.s):
        raise CompilationError(compilation_context, ast_node,
                               'Invalid atomic type. Atomic types should be C++ identifiers (possibly namespace-qualified).')

def atomic_type_literal_ast_to_ir3(ast_node: ast.Call,
                                   compilation_context: CompilationContext,
                                   in_match_pattern: bool,
                                   check_var_reference: Callable[[ast.Name], None]):
    if ast_node.keywords:
        raise CompilationError(compilation_context, ast_node.keywords[0].value,
                               'Keyword arguments are not supported in Type()')

    if len(ast_node.args) != 1:
        raise CompilationError(compilation_context, ast_node, 'Type() takes 1 argument. Got: %s' % len(ast_node.args))
    [arg] = ast_node.args
    if not isinstance(arg, ast.Str):
        raise CompilationError(compilation_context, arg, 'The argument passed to Type should be a string constant.')
    _check_atomic_type(arg, compilation_context)
    return ir3.AtomicTypeLiteral(cpp_type=arg.s)

def _extract_single_type_expr_arg(ast_node: ast.Call,
                                  called_fun_name: str,
                                  compilation_context: CompilationContext,
                                  in_match_pattern: bool,
                                  check_var_reference: Callable[[ast.Name], None]):
    if ast_node.keywords:
        raise CompilationError(compilation_context, ast_node.keywords[0].value,
                               'Keyword arguments are not supported in %s()' % called_fun_name)

    if len(ast_node.args) != 1:
        raise CompilationError(compilation_context, ast_node, '%s() takes 1 argument. Got: %s' % (called_fun_name, len(ast_node.args)))
    [arg] = ast_node.args

    arg_ir = expression_ast_to_ir3(arg, compilation_context, in_match_pattern, check_var_reference)
    if arg_ir.type != ir3.TypeType():
        raise CompilationError(compilation_context, arg, 'The argument passed to %s() should have type Type, but was: %s' % (called_fun_name, str(arg_ir.type)))
    return arg_ir

def type_pointer_expr_ast_to_ir3(ast_node: ast.Call,
                                 compilation_context: CompilationContext,
                                 in_match_pattern: bool,
                                 check_var_reference: Callable[[ast.Name], None]):
    return ir3.PointerTypeExpr(_extract_single_type_expr_arg(ast_node,
                                                             'Type.pointer',
                                                             compilation_context,
                                                             in_match_pattern,
                                                             check_var_reference))

def type_reference_expr_ast_to_ir3(ast_node: ast.Call,
                                   compilation_context: CompilationContext,
                                   in_match_pattern: bool,
                                   check_var_reference: Callable[[ast.Name], None]):
    return ir3.ReferenceTypeExpr(_extract_single_type_expr_arg(ast_node,
                                                               'Type.reference',
                                                               compilation_context,
                                                               in_match_pattern,
                                                               check_var_reference))

def type_rvalue_reference_expr_ast_to_ir3(ast_node: ast.Call,
                                          compilation_context: CompilationContext,
                                          in_match_pattern: bool,
                                          check_var_reference: Callable[[ast.Name], None]):
    return ir3.RvalueReferenceTypeExpr(_extract_single_type_expr_arg(ast_node,
                                                                     'Type.rvalue_reference',
                                                                     compilation_context,
                                                                     in_match_pattern,
                                                                     check_var_reference))

def const_type_expr_ast_to_ir3(ast_node: ast.Call,
                               compilation_context: CompilationContext,
                               in_match_pattern: bool,
                               check_var_reference: Callable[[ast.Name], None]):
    return ir3.ConstTypeExpr(_extract_single_type_expr_arg(ast_node,
                                                           'Type.const',
                                                           compilation_context,
                                                           in_match_pattern,
                                                           check_var_reference))

def type_array_expr_ast_to_ir3(ast_node: ast.Call,
                               compilation_context: CompilationContext,
                               in_match_pattern: bool,
                               check_var_reference: Callable[[ast.Name], None]):
    return ir3.ArrayTypeExpr(_extract_single_type_expr_arg(ast_node,
                                                           'Type.array',
                                                           compilation_context,
                                                           in_match_pattern,
                                                           check_var_reference))

def function_type_expr_ast_to_ir3(ast_node: ast.Call,
                                  compilation_context: CompilationContext,
                                  in_match_pattern: bool,
                                  check_var_reference: Callable[[ast.Name], None]):
    if ast_node.keywords:
        raise CompilationError(compilation_context, ast_node.keywords[0].value,
                               'Keyword arguments are not supported in Type.function()')

    if len(ast_node.args) != 2:
        raise CompilationError(compilation_context, ast_node, 'Type.function() takes 2 arguments. Got: %s' % len(ast_node.args))
    [return_type_ast_node, arg_list_ast_node] = ast_node.args

    return_type_ir = expression_ast_to_ir3(return_type_ast_node, compilation_context, in_match_pattern, check_var_reference)
    if return_type_ir.type != ir3.TypeType():
        raise CompilationError(compilation_context, return_type_ast_node,
                               'The first argument passed to Type.function should have type Type, but was: %s' % str(return_type_ir.type))

    arg_list_ir = expression_ast_to_ir3(arg_list_ast_node, compilation_context, in_match_pattern, check_var_reference)
    if arg_list_ir.type != ir3.ListType(ir3.TypeType()):
        raise CompilationError(compilation_context, arg_list_ast_node,
                               'The second argument passed to Type.function should have type List[Type], but was: %s' % str(arg_list_ir.type))

    return ir3.FunctionTypeExpr(return_type_expr=return_type_ir,
                                arg_list_expr=arg_list_ir)

def template_instantiation_ast_to_ir3(ast_node: ast.Call,
                                      compilation_context: CompilationContext,
                                      in_match_pattern: bool,
                                      check_var_reference: Callable[[ast.Name], None]):
    if ast_node.keywords:
        raise CompilationError(compilation_context, ast_node.keywords[0].value,
                               'Keyword arguments are not supported in Type.template_instantiation()')

    if len(ast_node.args) != 2:
        raise CompilationError(compilation_context, ast_node, 'Type.template_instantiation() takes 2 arguments. Got: %s' % len(ast_node.args))
    [template_atomic_cpp_type_ast_node, arg_list_ast_node] = ast_node.args

    if not isinstance(template_atomic_cpp_type_ast_node, ast.Str):
        raise CompilationError(compilation_context, template_atomic_cpp_type_ast_node,
                               'The first argument passed to Type.template_instantiation should be a string')
    _check_atomic_type(template_atomic_cpp_type_ast_node, compilation_context)

    arg_list_ir = expression_ast_to_ir3(arg_list_ast_node, compilation_context, in_match_pattern, check_var_reference)
    if arg_list_ir.type != ir3.ListType(ir3.TypeType()):
        raise CompilationError(compilation_context, arg_list_ast_node,
                               'The second argument passed to Type.template_instantiation should have type List[Type], but was: %s' % str(arg_list_ir.type))

    return ir3.TemplateInstantiationExpr(template_atomic_cpp_type=template_atomic_cpp_type_ast_node.s,
                                         arg_list_expr=arg_list_ir)

_cxx_identifier_regex = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')

def template_member_access_ast_to_ir3(ast_node: ast.Call,
                                      compilation_context: CompilationContext,
                                      in_match_pattern: bool,
                                      check_var_reference: Callable[[ast.Name], None]):
    if in_match_pattern:
        raise CompilationError(compilation_context, ast_node,
                               'Type.template_member() is not allowed in match patterns')

    if ast_node.keywords:
        raise CompilationError(compilation_context, ast_node.keywords[0].value,
                               'Keyword arguments are not supported in Type.template_member()')

    if len(ast_node.args) != 3:
        raise CompilationError(compilation_context, ast_node, 'Type.template_member() takes 3 arguments. Got: %s' % len(ast_node.args))
    [class_type_ast_node, member_name_ast_node, arg_list_ast_node] = ast_node.args

    class_type_expr_ir = expression_ast_to_ir3(class_type_ast_node, compilation_context, in_match_pattern, check_var_reference)
    if class_type_expr_ir.type != ir3.TypeType():
        raise CompilationError(compilation_context, class_type_ast_node,
                               'The first argument passed to Type.template_member should have type Type, but was: %s' % str(class_type_expr_ir.type))

    if not isinstance(member_name_ast_node, ast.Str):
        raise CompilationError(compilation_context, member_name_ast_node,
                               'The second argument passed to Type.template_member should be a string')
    if not _cxx_identifier_regex.fullmatch(member_name_ast_node.s):
        raise CompilationError(compilation_context, member_name_ast_node,
                               'The second argument passed to Type.template_member should be a valid C++ identifier')

    arg_list_ir = expression_ast_to_ir3(arg_list_ast_node, compilation_context, in_match_pattern, check_var_reference)
    if arg_list_ir.type != ir3.ListType(ir3.TypeType()):
        raise CompilationError(compilation_context, arg_list_ast_node,
                               'The third argument passed to Type.template_member should have type List[Type], but was: %s' % str(arg_list_ir.type))

    return ir3.TemplateMemberAccessExpr(class_type_expr=class_type_expr_ir,
                                        member_name=member_name_ast_node.s,
                                        arg_list_expr=arg_list_ir)

def type_factory_method_ast_to_ir3(ast_node: ast.Call,
                                   compilation_context: CompilationContext,
                                   in_match_pattern: bool,
                                   check_var_reference: Callable[[ast.Name], None]):
    assert isinstance(ast_node, ast.Call)
    assert isinstance(ast_node.func, ast.Attribute)
    assert isinstance(ast_node.func.value, ast.Name)
    assert ast_node.func.value.id == 'Type'

    if ast_node.func.attr == 'pointer':
        return type_pointer_expr_ast_to_ir3(ast_node, compilation_context, in_match_pattern, check_var_reference)
    elif ast_node.func.attr == 'reference':
        return type_reference_expr_ast_to_ir3(ast_node, compilation_context, in_match_pattern, check_var_reference)
    elif ast_node.func.attr == 'rvalue_reference':
        return type_rvalue_reference_expr_ast_to_ir3(ast_node, compilation_context, in_match_pattern, check_var_reference)
    elif ast_node.func.attr == 'const':
        return const_type_expr_ast_to_ir3(ast_node, compilation_context, in_match_pattern, check_var_reference)
    elif ast_node.func.attr == 'array':
        return type_array_expr_ast_to_ir3(ast_node, compilation_context, in_match_pattern, check_var_reference)
    elif ast_node.func.attr == 'function':
        return function_type_expr_ast_to_ir3(ast_node, compilation_context, in_match_pattern, check_var_reference)
    elif ast_node.func.attr == 'template_instantiation':
        return template_instantiation_ast_to_ir3(ast_node, compilation_context, in_match_pattern, check_var_reference)
    elif ast_node.func.attr == 'template_member':
        return template_member_access_ast_to_ir3(ast_node, compilation_context, in_match_pattern, check_var_reference)
    else:
        raise CompilationError(compilation_context, ast_node,
                               'Undefined Type factory method')

def empty_list_literal_ast_to_ir3(ast_node: ast.Call,
                                  compilation_context: CompilationContext,
                                  in_match_pattern: bool,
                                  check_var_reference: Callable[[ast.Name], None]):
    if ast_node.keywords:
        raise CompilationError(compilation_context, ast_node.keywords[0].value, 'Keyword arguments are not supported.')
    if len(ast_node.args) != 1:
        raise CompilationError(compilation_context, ast_node, 'empty_list() takes 1 argument. Got: %s' % len(ast_node.args))
    [arg] = ast_node.args
    elem_type = type_declaration_ast_to_ir3_expression_type(arg, compilation_context)
    return ir3.ListExpr(elem_type=elem_type, elem_exprs=[])

def empty_set_literal_ast_to_ir3(ast_node: ast.Call,
                                 compilation_context: CompilationContext,
                                 in_match_pattern: bool,
                                 check_var_reference: Callable[[ast.Name], None]):
    if ast_node.keywords:
        raise CompilationError(compilation_context, ast_node.keywords[0].value, 'Keyword arguments are not supported.')
    if len(ast_node.args) != 1:
        raise CompilationError(compilation_context, ast_node, 'empty_set() takes 1 argument. Got: %s' % len(ast_node.args))
    [arg] = ast_node.args
    elem_type = type_declaration_ast_to_ir3_expression_type(arg, compilation_context)
    return ir3.SetExpr(elem_type=elem_type, elem_exprs=[])

def int_iterable_sum_expr_ast_to_ir3(ast_node: ast.Call,
                                     compilation_context: CompilationContext,
                                     in_match_pattern: bool,
                                     check_var_reference: Callable[[ast.Name], None]):
    if in_match_pattern:
        raise CompilationError(compilation_context, ast_node,
                               'sum() is not allowed in match patterns')

    if ast_node.keywords:
        raise CompilationError(compilation_context, ast_node.keywords[0].value, 'Keyword arguments are not supported.')
    if len(ast_node.args) != 1:
        raise CompilationError(compilation_context, ast_node, 'sum() takes 1 argument. Got: %s' % len(ast_node.args))
    [arg] = ast_node.args
    arg_expr = expression_ast_to_ir3(arg, compilation_context, in_match_pattern, check_var_reference)
    if not (isinstance(arg_expr.type, (ir3.ListType, ir3.SetType)) and isinstance(arg_expr.type.elem_type, ir3.IntType)):
        notes = []
        if isinstance(arg_expr, ir3.VarReference):
            lookup_result = compilation_context.get_symbol_definition(arg_expr.name)
            assert lookup_result
            assert not lookup_result.is_only_partially_defined
            notes.append((lookup_result.ast_node, '%s was defined here' % arg_expr.name))
        raise CompilationError(compilation_context, arg,
                               'The argument of sum() must have type List[int] or Set[int]. Got type: %s' % str(arg_expr.type),
                               notes=notes)
    if isinstance(arg_expr.type, ir3.ListType):
        return ir3.IntListSumExpr(list_expr=arg_expr)
    else:
        return ir3.IntSetSumExpr(set_expr=arg_expr)

def bool_iterable_all_expr_ast_to_ir3(ast_node: ast.Call,
                                      compilation_context: CompilationContext,
                                      in_match_pattern: bool,
                                      check_var_reference: Callable[[ast.Name], None]):
    if in_match_pattern:
        raise CompilationError(compilation_context, ast_node,
                               'all() is not allowed in match patterns')

    if ast_node.keywords:
        raise CompilationError(compilation_context, ast_node.keywords[0].value, 'Keyword arguments are not supported.')
    if len(ast_node.args) != 1:
        raise CompilationError(compilation_context, ast_node, 'all() takes 1 argument. Got: %s' % len(ast_node.args))
    [arg] = ast_node.args
    arg_expr = expression_ast_to_ir3(arg, compilation_context, in_match_pattern, check_var_reference)
    if not (isinstance(arg_expr.type, (ir3.ListType, ir3.SetType)) and isinstance(arg_expr.type.elem_type, ir3.BoolType)):
        notes = []
        if isinstance(arg_expr, ir3.VarReference):
            lookup_result = compilation_context.get_symbol_definition(arg_expr.name)
            assert lookup_result
            assert not lookup_result.is_only_partially_defined
            notes.append((lookup_result.ast_node, '%s was defined here' % arg_expr.name))
        raise CompilationError(compilation_context, arg,
                               'The argument of all() must have type List[bool] or Set[bool]. Got type: %s' % str(arg_expr.type),
                               notes=notes)
    if isinstance(arg_expr.type, ir3.ListType):
        return ir3.BoolListAllExpr(list_expr=arg_expr)
    else:
        return ir3.BoolSetAllExpr(set_expr=arg_expr)

def bool_iterable_any_expr_ast_to_ir3(ast_node: ast.Call,
                                      compilation_context: CompilationContext,
                                      in_match_pattern: bool,
                                      check_var_reference: Callable[[ast.Name], None]):
    if in_match_pattern:
        raise CompilationError(compilation_context, ast_node,
                               'any() is not allowed in match patterns')

    if ast_node.keywords:
        raise CompilationError(compilation_context, ast_node.keywords[0].value, 'Keyword arguments are not supported.')
    if len(ast_node.args) != 1:
        raise CompilationError(compilation_context, ast_node, 'any() takes 1 argument. Got: %s' % len(ast_node.args))
    [arg] = ast_node.args
    arg_expr = expression_ast_to_ir3(arg, compilation_context, in_match_pattern, check_var_reference)
    if not (isinstance(arg_expr.type, (ir3.ListType, ir3.SetType)) and isinstance(arg_expr.type.elem_type, ir3.BoolType)):
        notes = []
        if isinstance(arg_expr, ir3.VarReference):
            lookup_result = compilation_context.get_symbol_definition(arg_expr.name)
            assert lookup_result
            assert not lookup_result.is_only_partially_defined
            notes.append((lookup_result.ast_node, '%s was defined here' % arg_expr.name))
        raise CompilationError(compilation_context, arg,
                               'The argument of any() must have type List[bool] or Set[bool]. Got type: %s' % str(arg_expr.type),
                               notes=notes)
    if isinstance(arg_expr.type, ir3.ListType):
        return ir3.BoolListAnyExpr(list_expr=arg_expr)
    else:
        return ir3.BoolSetAnyExpr(set_expr=arg_expr)

def _is_structural_equality_check_supported_for_type(type: ir3.ExprType):
    if isinstance(type, ir3.BoolType):
        return True
    elif isinstance(type, ir3.IntType):
        return True
    elif isinstance(type, ir3.TypeType):
        return True
    elif isinstance(type, ir3.FunctionType):
        return False
    elif isinstance(type, ir3.ListType):
        return _is_structural_equality_check_supported_for_type(type.elem_type)
    elif isinstance(type, ir3.SetType):
        return False
    elif isinstance(type, ir3.CustomType):
        return all(_is_structural_equality_check_supported_for_type(arg_type.type)
                   for arg_type in type.arg_types)
    else:
        raise NotImplementedError('Unexpected type: %s' % type.__class__.__name__)

def _is_equality_check_supported_for_type(type: ir3.ExprType):
    if isinstance(type, ir3.SetType):
        return _is_structural_equality_check_supported_for_type(type.elem_type)
    else:
        return _is_structural_equality_check_supported_for_type(type)

def eq_ast_to_ir3(lhs_node: ast.AST,
                  rhs_node: ast.AST,
                  compilation_context: CompilationContext,
                  in_match_pattern: bool,
                  check_var_reference: Callable[[ast.Name], None]):
    assert not in_match_pattern

    lhs = expression_ast_to_ir3(lhs_node, compilation_context, in_match_pattern, check_var_reference)
    rhs = expression_ast_to_ir3(rhs_node, compilation_context, in_match_pattern, check_var_reference)
    if lhs.type != rhs.type:
        raise CompilationError(compilation_context, lhs_node, 'Type mismatch in ==: %s vs %s' % (
            str(lhs.type), str(rhs.type)))
    if not _is_equality_check_supported_for_type(lhs.type):
        raise CompilationError(compilation_context, lhs_node, 'Type not supported in equality comparison: ' + str(lhs.type))
    return ir3.EqualityComparison(lhs=lhs, rhs=rhs)

def not_eq_ast_to_ir3(lhs_node: ast.AST,
                      rhs_node: ast.AST,
                      compilation_context: CompilationContext,
                      in_match_pattern: bool,
                      check_var_reference: Callable[[ast.Name], None]):
    assert not in_match_pattern

    lhs = expression_ast_to_ir3(lhs_node, compilation_context, in_match_pattern, check_var_reference)
    rhs = expression_ast_to_ir3(rhs_node, compilation_context, in_match_pattern, check_var_reference)
    if lhs.type != rhs.type:
        raise CompilationError(compilation_context, lhs_node, 'Type mismatch in !=: %s vs %s' % (
            str(lhs.type), str(rhs.type)))
    if not _is_equality_check_supported_for_type(lhs.type):
        raise CompilationError(compilation_context, lhs_node, 'Type not supported in equality comparison: ' + str(lhs.type))
    return ir3.NotExpr(expr=ir3.EqualityComparison(lhs=lhs, rhs=rhs))

def _construct_note_diagnostic_for_function_signature(function_lookup_result: SymbolLookupResult):
    if isinstance(function_lookup_result.ast_node, ast.ClassDef):
        # The __init__ method.
        [fun_definition_ast_node] = function_lookup_result.ast_node.body
        return fun_definition_ast_node, 'The definition of %s.__init__ was here' % function_lookup_result.symbol.name
    else:
        return function_lookup_result.ast_node, 'The definition of %s was here' % function_lookup_result.symbol.name

def _construct_note_diagnostic_for_function_arg(function_arg_ast_node: ast.arg):
    return function_arg_ast_node, 'The definition of %s was here' % function_arg_ast_node.arg

def function_call_ast_to_ir3(ast_node: ast.Call,
                             compilation_context: CompilationContext,
                             in_match_pattern: bool,
                             check_var_reference: Callable[[ast.Name], None]):
    # TODO: allow calls to custom types' constructors.
    if in_match_pattern:
        raise CompilationError(compilation_context, ast_node,
                               'Function calls are not allowed in match patterns')

    fun_expr = expression_ast_to_ir3(ast_node.func, compilation_context, in_match_pattern, check_var_reference)
    if not isinstance(fun_expr.type, ir3.FunctionType):
        raise CompilationError(compilation_context, ast_node,
                               'Attempting to call an object that is not a function. It has type: %s' % str(fun_expr.type))

    if ast_node.keywords and ast_node.args:
        raise CompilationError(compilation_context, ast_node, 'Function calls with a mix of keyword and non-keyword arguments are not supported. Please choose either style.')

    if ast_node.keywords:
        if not isinstance(fun_expr, ir3.VarReference):
            raise CompilationError(compilation_context, ast_node.keywords[0].value,
                                   'Keyword arguments can only be used when calling a specific function or constructing a specific type, not when calling other callable objects. Please switch to non-keyword arguments.')
        lookup_result = compilation_context.get_symbol_definition(fun_expr.name)
        assert lookup_result
        assert not lookup_result.is_only_partially_defined
        if isinstance(lookup_result.ast_node, ast.ClassDef):
            is_constructor_call = True
            # The __init__ method.
            [fun_definition_ast_node] = lookup_result.ast_node.body
        else:
            # It might still end up being a constructor call, e.g. if the custom type is assigned to a var and then used
            # as a function.
            is_constructor_call = False
            fun_definition_ast_node = lookup_result.ast_node

        if not isinstance(fun_definition_ast_node, ast.FunctionDef):
            raise CompilationError(compilation_context, ast_node.keywords[0].value,
                                   'Keyword arguments can only be used when calling a specific function or constructing a specific type, not when calling other callable objects. Please switch to non-keyword arguments.',
                                   notes=[(fun_definition_ast_node, 'The definition of %s was here' % ast_node.func.id)])

        if is_constructor_call:
            # We skip the 'self' parameter.
            fun_definition_ast_node_args = fun_definition_ast_node.args.args[1:]
        else:
            fun_definition_ast_node_args = fun_definition_ast_node.args.args

        arg_expr_by_name = {keyword_arg.arg: expression_ast_to_ir3(keyword_arg.value, compilation_context, in_match_pattern, check_var_reference)
                            for keyword_arg in ast_node.keywords}
        formal_arg_names = {arg.arg for arg in fun_definition_ast_node_args}
        specified_nonexisting_args = arg_expr_by_name.keys() - formal_arg_names
        missing_args = formal_arg_names - arg_expr_by_name.keys()
        if specified_nonexisting_args and missing_args:
            raise CompilationError(compilation_context, ast_node,
                                   'Incorrect arguments in call to %s. Missing arguments: {%s}. Specified arguments that don\'t exist: {%s}' % (
                                       fun_expr.name, ', '.join(sorted(missing_args)), ', '.join(sorted(specified_nonexisting_args))),
                                   notes=[_construct_note_diagnostic_for_function_signature(lookup_result)]
                                         + [_construct_note_diagnostic_for_function_arg(arg)
                                            for arg in sorted(fun_definition_ast_node_args, key=lambda arg: arg.arg)
                                            if arg.arg in missing_args])
        elif specified_nonexisting_args:
            raise CompilationError(compilation_context, ast_node,
                                   'Incorrect arguments in call to %s. Specified arguments that don\'t exist: {%s}' % (
                                       fun_expr.name, ', '.join(sorted(specified_nonexisting_args))),
                                   notes=[_construct_note_diagnostic_for_function_signature(lookup_result)])
        elif missing_args:
            raise CompilationError(compilation_context, ast_node,
                                   'Incorrect arguments in call to %s. Missing arguments: {%s}' % (
                                       fun_expr.name, ', '.join(sorted(missing_args))),
                                   notes=[_construct_note_diagnostic_for_function_arg(arg)
                                          for arg in sorted(fun_definition_ast_node_args, key=lambda arg: arg.arg)
                                          if arg.arg in missing_args])

        args = [arg_expr_by_name[arg.arg] for arg in fun_definition_ast_node_args]

        for expr, keyword_arg, arg_type, arg_decl_ast_node in zip(args, ast_node.keywords, fun_expr.type.argtypes, fun_definition_ast_node_args):
            if expr.type != arg_type:
                notes = [_construct_note_diagnostic_for_function_arg(arg_decl_ast_node)]

                if isinstance(keyword_arg.value, ast.Name):
                    lookup_result = compilation_context.get_symbol_definition(keyword_arg.value.id)
                    assert not lookup_result.is_only_partially_defined
                    notes.append((lookup_result.ast_node, 'The definition of %s was here' % keyword_arg.value.id))

                raise CompilationError(compilation_context, keyword_arg.value,
                                       'Type mismatch for argument %s: expected type %s but was: %s' % (
                                           keyword_arg.arg, str(arg_type), str(expr.type)),
                                       notes=notes)
    else:
        ast_node_args = ast_node.args or []
        args = [expression_ast_to_ir3(arg_node, compilation_context, in_match_pattern, check_var_reference) for arg_node in ast_node_args]
        if len(args) != len(fun_expr.type.argtypes):
            if isinstance(ast_node.func, ast.Name):
                lookup_result = compilation_context.get_symbol_definition(ast_node.func.id)
                assert lookup_result
                assert not lookup_result.is_only_partially_defined
                raise CompilationError(compilation_context, ast_node,
                                       'Argument number mismatch in function call to %s: got %s arguments, expected %s' % (
                                           ast_node.func.id, len(args), len(fun_expr.type.argtypes)),
                                       notes=[_construct_note_diagnostic_for_function_signature(lookup_result)])
            else:
                raise CompilationError(compilation_context, ast_node,
                                       'Argument number mismatch in function call: got %s arguments, expected %s' % (
                                           len(args), len(fun_expr.type.argtypes)))

        for arg_index, (expr, expr_ast_node, arg_type) in enumerate(zip(args, ast_node_args, fun_expr.type.argtypes)):
            if expr.type != arg_type:
                notes = []

                if isinstance(ast_node.func, ast.Name):
                    lookup_result = compilation_context.get_symbol_definition(ast_node.func.id)
                    assert lookup_result

                    if isinstance(lookup_result.ast_node, ast.ClassDef):
                        is_constructor_call = True
                        # The __init__ method.
                        [fun_definition_ast_node] = lookup_result.ast_node.body
                    else:
                        # It might still end up being a constructor call, e.g. if the custom type is assigned to a var and then used
                        # as a function.
                        is_constructor_call = False
                        fun_definition_ast_node = lookup_result.ast_node

                    if not isinstance(fun_definition_ast_node, ast.FunctionDef):
                        notes.append(_construct_note_diagnostic_for_function_signature(lookup_result))
                    else:
                        if is_constructor_call:
                            # We skip the 'self' parameter.
                            fun_definition_ast_node_args = fun_definition_ast_node.args.args[1:]
                        else:
                            fun_definition_ast_node_args = fun_definition_ast_node.args.args
                        notes.append(_construct_note_diagnostic_for_function_arg(fun_definition_ast_node_args[arg_index]))

                if isinstance(expr_ast_node, ast.Name):
                    lookup_result = compilation_context.get_symbol_definition(expr_ast_node.id)
                    assert lookup_result

                    notes.append((lookup_result.ast_node, 'The definition of %s was here' % expr_ast_node.id))

                raise CompilationError(compilation_context, expr_ast_node,
                                       'Type mismatch for argument %s: expected type %s but was: %s' % (
                                           arg_index, str(arg_type), str(expr.type)),
                                       notes=notes)

    return ir3.FunctionCall(fun_expr=fun_expr,
                            args=args,
                            may_throw=not isinstance(fun_expr, ir3.VarReference)
                                         or fun_expr.is_function_that_may_throw)

def var_reference_ast_to_ir3(ast_node: ast.Name,
                             compilation_context: CompilationContext,
                             in_match_pattern: bool,
                             check_var_reference: Callable[[ast.Name], None]):
    assert isinstance(ast_node.ctx, ast.Load)
    lookup_result = compilation_context.get_symbol_definition(ast_node.id)
    if lookup_result:
        if lookup_result.is_only_partially_defined:
            raise CompilationError(compilation_context, ast_node,
                                   'Reference to a variable that may or may not have been initialized (depending on which branch was taken)',
                                   notes=[(lookup_result.ast_node, '%s might have been initialized here' % ast_node.id)])
        check_var_reference(ast_node)
        return ir3.VarReference(type=lookup_result.symbol.type,
                                name=lookup_result.symbol.name,
                                is_global_function=lookup_result.symbol_table.parent is None,
                                is_function_that_may_throw=isinstance(lookup_result.symbol.type, ir3.FunctionType)
                                                              and lookup_result.symbol.is_function_that_may_throw)
    else:
        definition_ast_node = compilation_context.get_partial_function_definition(ast_node.id)
        if definition_ast_node:
            if compilation_context.current_function_name == ast_node.id:
                raise CompilationError(compilation_context, ast_node, 'Recursive function references are only allowed if the return type is declared explicitly.',
                                       notes=[(definition_ast_node, '%s was defined here' % ast_node.id)])
            else:
                raise CompilationError(compilation_context, ast_node, 'Reference to a function whose return type hasn\'t been determined yet. Please add a return type declaration in %s or move its declaration before its use.' % ast_node.id,
                                       notes=[(definition_ast_node, '%s was defined here' % ast_node.id)])
        else:
            raise CompilationError(compilation_context, ast_node, 'Reference to undefined variable/function')

def list_expression_ast_to_ir3(ast_node: ast.List,
                               compilation_context: CompilationContext,
                               in_match_pattern: bool,
                               check_var_reference: Callable[[ast.Name], None]):
    elem_exprs = [expression_ast_to_ir3(elem_expr_node, compilation_context, in_match_pattern, check_var_reference) for elem_expr_node in ast_node.elts]
    if len(elem_exprs) == 0:
        raise CompilationError(compilation_context, ast_node, 'Untyped empty lists are not supported. Please import empty_list from pytmp and then write e.g. empty_list(int) to create an empty list of ints.')
    elem_type = elem_exprs[0].type
    for elem_expr, elem_expr_ast_node in zip(elem_exprs, ast_node.elts):
        if elem_expr.type != elem_type:
            raise CompilationError(compilation_context, elem_expr_ast_node,
                                   'Found different types in list elements, this is not supported. The type of this element was %s instead of %s' % (
                                       str(elem_expr.type), str(elem_type)),
                                   notes=[(ast_node.elts[0], 'A previous list element with type %s was here.' % str(elem_type))])
    if isinstance(elem_type, ir3.FunctionType):
        raise CompilationError(compilation_context, ast_node,
                               'Creating lists of functions is not supported. The elements of this list have type: %s' % str(elem_type))

    return ir3.ListExpr(elem_type=elem_type, elem_exprs=elem_exprs)

def set_expression_ast_to_ir3(ast_node: ast.Set,
                              compilation_context: CompilationContext,
                              in_match_pattern: bool,
                              check_var_reference: Callable[[ast.Name], None]):
    elem_exprs = [expression_ast_to_ir3(elem_expr_node, compilation_context, in_match_pattern, check_var_reference) for elem_expr_node in ast_node.elts]
    assert elem_exprs
    elem_type = elem_exprs[0].type
    for elem_expr, elem_expr_ast_node in zip(elem_exprs, ast_node.elts):
        if elem_expr.type != elem_type:
            raise CompilationError(compilation_context, elem_expr_ast_node,
                                   'Found different types in set elements, this is not supported. The type of this element was %s instead of %s' % (
                                       str(elem_expr.type), str(elem_type)),
                                   notes=[(ast_node.elts[0], 'A previous set element with type %s was here.' % str(elem_type))])
    if isinstance(elem_type, ir3.FunctionType):
        raise CompilationError(compilation_context, ast_node,
                               'Creating sets of functions is not supported. The elements of this set have type: %s' % str(elem_type))

    return ir3.SetExpr(elem_type=elem_type, elem_exprs=elem_exprs)

def type_declaration_ast_to_ir3_expression_type(ast_node: ast.AST, compilation_context: CompilationContext):
    if isinstance(ast_node, ast.Name) and isinstance(ast_node.ctx, ast.Load):
        if ast_node.id == 'bool':
            return ir3.BoolType()
        elif ast_node.id == 'int':
            return ir3.IntType()
        elif ast_node.id == 'Type':
            return ir3.TypeType()
        else:
            lookup_result = compilation_context.get_type_symbol_definition(ast_node.id)
            if lookup_result:
                return lookup_result.symbol.type
            else:
                raise CompilationError(compilation_context, ast_node, 'Unsupported (or undefined) type: ' + ast_node.id)

    if (isinstance(ast_node, ast.Subscript)
        and isinstance(ast_node.value, ast.Name)
        and isinstance(ast_node.value.ctx, ast.Load)
        and isinstance(ast_node.ctx, ast.Load)
        and isinstance(ast_node.slice, ast.Index)):
        if ast_node.value.id == 'List':
            return ir3.ListType(type_declaration_ast_to_ir3_expression_type(ast_node.slice.value, compilation_context))
        if ast_node.value.id == 'Set':
            return ir3.SetType(type_declaration_ast_to_ir3_expression_type(ast_node.slice.value, compilation_context))
        elif (ast_node.value.id == 'Callable'
              and isinstance(ast_node.slice.value, ast.Tuple)
              and len(ast_node.slice.value.elts) == 2
              and isinstance(ast_node.slice.value.elts[0], ast.List)
              and isinstance(ast_node.slice.value.elts[0].ctx, ast.Load)
              and all(isinstance(elem, ast.Name) and isinstance(elem.ctx, ast.Load)
                      for elem in ast_node.slice.value.elts[0].elts)):
            return ir3.FunctionType(
                argtypes=[type_declaration_ast_to_ir3_expression_type(arg_type_decl, compilation_context)
                          for arg_type_decl in ast_node.slice.value.elts[0].elts],
                returns=type_declaration_ast_to_ir3_expression_type(ast_node.slice.value.elts[1], compilation_context))

    raise CompilationError(compilation_context, ast_node, 'Unsupported type declaration.')

# Checks if the statement is of the form:
# self.some_field = <expr>
def _is_class_field_initialization(ast_node: ast.AST):
    return (isinstance(ast_node, ast.Assign)
            and not ast_node.type_comment
            and len(ast_node.targets) == 1
            and isinstance(ast_node.targets[0], ast.Attribute)
            and isinstance(ast_node.targets[0].ctx, ast.Store)
            and isinstance(ast_node.targets[0].value, ast.Name)
            and ast_node.targets[0].value.id == 'self'
            and isinstance(ast_node.targets[0].value.ctx, ast.Load))

def class_definition_ast_to_ir3(ast_node: ast.ClassDef, compilation_context: CompilationContext):
    if ast_node.bases:
        if len(ast_node.bases) > 1:
            raise CompilationError(compilation_context, ast_node.bases[1],
                                   'Multiple base classes are not supported.')
        [base] = ast_node.bases
        if not (isinstance(base, ast.Name) and isinstance(base.ctx, ast.Load) and base.id == 'Exception'):
            raise CompilationError(compilation_context, base,
                                   '"Exception" is the only supported base class.')
        is_exception_class = True
    else:
        is_exception_class = False

    if ast_node.keywords:
        raise CompilationError(compilation_context, ast_node.keywords[0].value,
                               'Keyword class arguments are not supported.')
    if ast_node.decorator_list:
        raise CompilationError(compilation_context, ast_node.decorator_list[0],
                               'Class decorators are not supported.')

    if len(ast_node.body) != 1 or not isinstance(ast_node.body[0], ast.FunctionDef) or ast_node.body[0].name != '__init__':
        raise CompilationError(compilation_context, ast_node,
                               'Custom classes must contain an __init__ method (and nothing else).')

    [init_defn_ast_node] = ast_node.body

    init_args_ast_node = init_defn_ast_node.args

    if init_args_ast_node.vararg:
        raise CompilationError(compilation_context, init_args_ast_node.vararg,
                               'Vararg arguments are not supported in __init__.')
    if init_args_ast_node.kwonlyargs:
        raise CompilationError(compilation_context, init_args_ast_node.kwonlyargs[0],
                               'Keyword-only arguments are not supported in __init__.')
    if init_args_ast_node.kw_defaults or init_args_ast_node.defaults:
        raise CompilationError(compilation_context, init_defn_ast_node,
                               'Default arguments are not supported in __init__.')
    if init_args_ast_node.kwarg:
        raise CompilationError(compilation_context, init_defn_ast_node,
                               'Keyword arguments are not supported in __init__.')

    init_args_ast_nodes = init_args_ast_node.args

    if not init_args_ast_nodes or init_args_ast_nodes[0].arg != 'self':
        raise CompilationError(compilation_context, init_defn_ast_node,
                               'Expected "self" as first argument of __init__.')

    if init_args_ast_nodes[0].annotation:
        raise CompilationError(compilation_context, init_args_ast_nodes[0].annotation,
                               'Type annotations on the "self" argument are not supported.')

    for arg in init_args_ast_nodes:
        if arg.type_comment:
            raise CompilationError(compilation_context, arg,
                                   'Type comments on arguments are not supported.')

    init_args_ast_nodes = init_args_ast_nodes[1:]

    if not init_args_ast_nodes:
        raise CompilationError(compilation_context, init_defn_ast_node,
                               'Custom types must have at least 1 constructor argument (and field).')

    arg_decl_nodes_by_name = dict()
    arg_types = []
    for arg in init_args_ast_nodes:
        if not init_args_ast_nodes[0].annotation:
            raise CompilationError(compilation_context, arg,
                                   'All arguments of __init__ (except "self") must have a type annotation.')
        if arg.arg in arg_decl_nodes_by_name:
            previous_arg_node = arg_decl_nodes_by_name[arg.arg]
            raise CompilationError(compilation_context, arg,
                                   'Found multiple arguments with name "%s".' % arg.arg,
                                   notes=[(previous_arg_node, 'A previous argument with name "%s" was declared here.' % arg.arg)])

        arg_decl_nodes_by_name[arg.arg] = arg
        arg_types.append(ir3.CustomTypeArgDecl(name=arg.arg,
                                               type = type_declaration_ast_to_ir3_expression_type(arg.annotation, compilation_context)))

    init_body_ast_nodes = init_defn_ast_node.body
    if is_exception_class:
        first_stmt = init_body_ast_nodes[0]
        if not (_is_class_field_initialization(first_stmt)
                and isinstance(first_stmt.value, ast.Str)
                and first_stmt.targets[0].attr == 'message'):
            raise CompilationError(compilation_context, first_stmt,
                                   'Unexpected statement. The first statement in the constructor of an exception class must be of the form: self.message = \'...\'.')
        exception_message = first_stmt.value.s
        init_body_ast_nodes = init_body_ast_nodes[1:]

    arg_assign_nodes_by_name = dict()
    for stmt_ast_node in init_body_ast_nodes:
        if not (_is_class_field_initialization(stmt_ast_node)
                and isinstance(stmt_ast_node.value, ast.Name)
                and isinstance(stmt_ast_node.value.ctx, ast.Load)):
            raise CompilationError(compilation_context, stmt_ast_node,
                                   'Unexpected statement. All statements in __init__ methods must be of the form "self.some_var = some_var".')

        if stmt_ast_node.value.id in arg_assign_nodes_by_name:
            previous_assign_node = arg_assign_nodes_by_name[stmt_ast_node.value.id]
            raise CompilationError(compilation_context, stmt_ast_node,
                                   'Found multiple assignments to the field "%s".' % stmt_ast_node.value.id,
                                   notes=[(previous_assign_node, 'A previous assignment to "self.%s" was here.' % stmt_ast_node.value.id)])

        if stmt_ast_node.targets[0].attr != stmt_ast_node.value.id:
            raise CompilationError(compilation_context, stmt_ast_node,
                                   '__init__ arguments must be assigned to a field of the same name, but "%s" was assigned to "%s".' % (stmt_ast_node.value.id, stmt_ast_node.targets[0].attr))

        if stmt_ast_node.value.id not in arg_decl_nodes_by_name:
            raise CompilationError(compilation_context, stmt_ast_node,
                                   'Unsupported assignment. All assigments in __init__ methods must assign a parameter to a field with the same name.')

        arg_assign_nodes_by_name[stmt_ast_node.value.id] = stmt_ast_node

    for arg_name, decl_ast_node in arg_decl_nodes_by_name.items():
        if arg_name not in arg_assign_nodes_by_name:
            raise CompilationError(compilation_context, decl_ast_node,
                                   'All __init__ arguments must be assigned to fields, but "%s" was never assigned.' % arg_name)

    return ir3.CustomType(name=ast_node.name,
                          arg_types=arg_types,
                          is_exception_class=is_exception_class,
                          exception_message=exception_message if is_exception_class else None)
