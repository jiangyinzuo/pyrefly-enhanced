"""保留实例赋值的控制路径；调用参数仅展示省略号，不执行源码。"""
from __future__ import annotations
import ast
import copy
from structure import included


def summarize_init(method, types, rules):
    args = method.args.posonlyargs + method.args.args
    receiver = args[0].arg if args else 'self'

    def chosen(node):
        return isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == receiver and included(rules, 'member', node.attr, 'self')

    class Calls(ast.NodeTransformer):
        def visit_Call(self, node):
            result = copy.copy(node)
            result.func = self.visit(node.func)
            result.args = [ast.Constant(value=Ellipsis)]
            result.keywords = []
            return result

    def rhs(node):
        return Calls().visit(copy.deepcopy(node))

    def target(node):
        if chosen(node):
            return copy.deepcopy(node)
        if isinstance(node, (ast.Tuple, ast.List)):
            children = [target(n) for n in node.elts]
            if any(children):
                result = copy.copy(node)
                result.elts = [child or ast.Name(id='_', ctx=ast.Store()) for child in children]
                return result
        return None

    def prune(body):
        result = []
        for statement in body:
            clone = copy.deepcopy(statement)
            if isinstance(statement, ast.Assign):
                targets = [t for n in statement.targets if (t := target(n)) is not None]
                if not targets:
                    continue
                clone.targets = targets
                clone.value = rhs(statement.value)
                if len(targets) == 1 and isinstance(targets[0], ast.Attribute) and not isinstance(statement.value, ast.Call):
                    annotation = types.get(targets[0].attr)
                    if annotation and annotation not in ('Unknown', 'unknown'):
                        try:
                            annotation_node = ast.parse(annotation, mode='eval').body
                        except SyntaxError:
                            annotation_node = None
                        if annotation_node:
                            clone = ast.copy_location(ast.AnnAssign(target=targets[0], annotation=annotation_node,
                                                      value=clone.value, simple=0), statement)
                result.append(clone)
            elif isinstance(statement, ast.AnnAssign):
                if chosen(statement.target):
                    clone.value = rhs(statement.value) if statement.value else None
                    result.append(clone)
            elif isinstance(statement, ast.AugAssign):
                if chosen(statement.target):
                    clone.value = rhs(statement.value)
                    result.append(clone)
            elif isinstance(statement, ast.If):
                yes, no = prune(statement.body), prune(statement.orelse)
                if yes or no:
                    clone.body, clone.orelse = yes or [ast.copy_location(ast.Pass(), statement)], no
                    result.append(clone)
            elif isinstance(statement, (ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith)):
                kept = prune(statement.body)
                other = prune(getattr(statement, 'orelse', []))
                if kept or other:
                    clone.body = kept or [ast.copy_location(ast.Pass(), statement)]
                    if hasattr(clone, 'orelse'):
                        clone.orelse = other
                    result.append(clone)
            elif isinstance(statement, (ast.Try, ast.TryStar)):
                main, other, final = prune(statement.body), prune(statement.orelse), prune(statement.finalbody)
                handlers = [prune(h.body) for h in statement.handlers]
                if main or other or final or any(handlers):
                    clone.body = main or [ast.copy_location(ast.Pass(), statement)]
                    clone.orelse, clone.finalbody = other, final
                    for h, kept in zip(clone.handlers, handlers):
                        h.body = kept or [ast.copy_location(ast.Pass(), h)]
                    result.append(clone)
            elif isinstance(statement, ast.Match):
                cases = [prune(c.body) for c in statement.cases]
                if any(cases):
                    for case, kept in zip(clone.cases, cases):
                        case.body = kept or [ast.copy_location(ast.Pass(), statement)]
                    result.append(clone)
        return result

    clone = copy.copy(method)
    clone.body = prune(method.body) or [ast.copy_location(ast.Pass(), method)]
    return ast.fix_missing_locations(clone)


def initializer_lines(method, types, rules):
    """返回文本及逐行源码位置，附带左右表达式的 byte span 映射。"""
    clone = summarize_init(method, types, rules)
    text = ast.unparse(clone)
    parsed = ast.parse(text).body[0]
    lines = text.splitlines()
    locations = [{'line': method.lineno, 'column': method.col_offset, 'spans': []} for _ in lines]

    def pair(original, rendered):
        if isinstance(original, ast.stmt):
            for line in range(rendered.lineno - 1, rendered.end_lineno):
                locations[line]['line'] = original.lineno
                locations[line]['column'] = original.col_offset
        # 保存只涉及属性引用的 span，函数参数 (...) 没有虚假的源码对应。
        if isinstance(original, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            old_targets = original.targets if isinstance(original, ast.Assign) else [original.target]
            new_targets = rendered.targets if isinstance(rendered, ast.Assign) else [rendered.target]
            def spans(old, new, kind):
                if isinstance(old, (ast.Name, ast.Attribute)) and isinstance(new, type(old)):
                    if isinstance(old, ast.Attribute):
                        spans(old.value, new.value, kind)
                        old_start = old.end_col_offset - len(old.attr.encode())
                        new_start = new.end_col_offset - len(new.attr.encode())
                    else:
                        old_start, new_start = old.col_offset, new.col_offset
                    locations[new.lineno - 1]['spans'].append({
                        'start': new_start, 'end': new.end_col_offset, 'line': old.end_lineno,
                        'column': old_start, 'kind': kind})
                elif isinstance(old, (ast.Tuple, ast.List)) and isinstance(new, type(old)):
                    for a,b in zip(old.elts,new.elts):spans(a,b,kind)
                elif isinstance(old, ast.Call) and isinstance(new, ast.Call):
                    spans(old.func,new.func,kind)
            for old,new in zip(old_targets,new_targets):spans(old,new,'assignment')
            old_value, new_value = getattr(original,'value',None), getattr(rendered,'value',None)
            if old_value is not None and new_value is not None:spans(old_value,new_value,'definition')
        for field, value in ast.iter_fields(original):
            other = getattr(rendered, field, None)
            if isinstance(value, list) and isinstance(other, list):
                for a,b in zip(value,other):
                    if isinstance(a, ast.AST) and isinstance(b, ast.AST):pair(a,b)
            elif isinstance(value, ast.AST) and isinstance(other, ast.AST):
                pair(value,other)
    pair(clone, parsed)
    for decorator, rendered in zip(clone.decorator_list, parsed.decorator_list):
        locations[rendered.lineno - 1].update(line=decorator.lineno,column=decorator.col_offset)
    for location in locations:
        if not location['spans']:
            location.pop('spans')
    return list(zip(lines, locations))
