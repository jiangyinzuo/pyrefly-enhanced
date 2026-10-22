"""源码结构展示和顺序覆盖规则；不执行目标代码。"""
from __future__ import annotations

import ast
import copy
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Rule:
    enabled: bool
    category: str
    qualifiers: frozenset[str]


def extract_rules(argv: list[str]) -> tuple[list[str], list[Rule]]:
    remaining = []
    rules = [Rule(True, 'all', frozenset())]
    # 参数值不作为规则解析，例如 --root -member。
    value_options = {'--root', '--timeout'}
    value_pending = False
    for token in argv:
        if value_pending:
            remaining.append(token)
            value_pending = False
            continue
        if token in value_options:
            remaining.append(token)
            value_pending = True
            continue
        if token == '-h' or not token.startswith(('+', '-')) or token.startswith('--'):
            remaining.append(token)
            continue
        pieces = list(re.finditer(r'([+-])(method|member|all)((?::(?:pub|priv|self|cls))*)', token))
        if not pieces or ''.join(m.group() for m in pieces) != token:
            raise ValueError(f'无效输出规则：{token}')
        for match in pieces:
            qualifiers = frozenset(filter(None, match[3].split(':')))
            if match[2] == 'all' and qualifiers:
                raise ValueError('all 不接受限定条件')
            if {'pub', 'priv'} <= qualifiers or {'self', 'cls'} <= qualifiers:
                raise ValueError(f'相互冲突的限定条件：{match.group()}')
            rules.append(Rule(match[1] == '+', match[2], qualifiers))
    return remaining, rules


def included(rules: list[Rule], category: str, name: str, scope: str) -> bool:
    tags = {scope, 'priv' if name.startswith('_') else 'pub'}
    enabled = True
    for rule in rules:
        if rule.category in ('all', category) and rule.qualifiers <= tags:
            enabled = rule.enabled
    return enabled


def decorator_name(node: ast.expr) -> str:
    if isinstance(node, ast.Call):
        node = node.func
    return node.attr if isinstance(node, ast.Attribute) else node.id if isinstance(node, ast.Name) else ''


def method_scope(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    return 'cls' if any(decorator_name(d) in ('classmethod', 'staticmethod') for d in node.decorator_list) else 'self'


def method_lines(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    stub = copy.copy(node)
    stub.body = [ast.Expr(value=ast.Constant(value=Ellipsis))]
    # ast.unparse 保留 /、*、默认值、返回注解和 overload 重复声明。
    text = ast.unparse(ast.fix_missing_locations(stub))
    signature, body = text.rsplit('\n', 1)
    assert body.strip() == '...'
    return (signature + ' ...').splitlines()


def class_members(node: ast.ClassDef) -> list[ast.Assign | ast.AnnAssign]:
    result = []

    def statements(body):
        for statement in body:
            if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                result.append(statement)
            elif not isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                for child in ast.iter_child_nodes(statement):
                    if isinstance(child, (ast.stmt, ast.ExceptHandler, ast.match_case)):
                        statements([child] if isinstance(child, ast.stmt) else child.body)
    statements(node.body)
    return result


def class_member_lines(node: ast.ClassDef, rules: list[Rule]) -> list[str]:
    lines = []
    for statement in class_members(node):
        def selected(target):
            return [n for n in ast.walk(target) if isinstance(n, ast.Name)
                    and isinstance(n.ctx, ast.Store) and included(rules, 'member', n.id, 'cls')]
        if isinstance(statement, ast.AnnAssign):
            if isinstance(statement.target, ast.Name) and selected(statement.target):
                lines.extend(ast.unparse(statement).splitlines())
        else:
            targets = [copy.deepcopy(t) for t in statement.targets if selected(t)]
            if not targets:
                continue
            # 解包仍保留位置，隐藏字段用 _ 占位；不执行或改写右值表达式。
            for target in targets:
                for child in ast.walk(target):
                    if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                        if not included(rules, 'member', child.id, 'cls'):
                            child.id = '_'
            clone = copy.copy(statement)
            clone.targets = targets
            lines.extend(ast.unparse(clone).splitlines())
    return lines
