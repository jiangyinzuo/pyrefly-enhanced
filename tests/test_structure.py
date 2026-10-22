import ast
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from structure import class_members, extract_rules, included, method_lines, method_scope

spec = importlib.util.spec_from_file_location('enhanced_inspector', ROOT / '__main__.py')
inspector = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = inspector
spec.loader.exec_module(inspector)


@pytest.mark.parametrize('tokens,member,method', [
    ([], True, True),
    (['-member+member'], True, True),
    (['-member+all'], True, True),
    (['+all-member'], False, True),
    (['-all+member'], True, False),
    (['+member', '-all'], False, False),
])
def test_rightmost_matching_rule_wins(tokens, member, method):
    _, rules = extract_rules(tokens)
    assert included(rules, 'member', 'value', 'self') is member
    assert included(rules, 'method', 'run', 'self') is method


@pytest.mark.parametrize('name,scope,expected', [
    ('run', 'self', False), ('_run', 'self', True),
    ('__init__', 'self', True), ('_run', 'cls', False),
])
def test_qualifiers_are_intersections(name, scope, expected):
    _, rules = extract_rules(['-all', '+method:priv:self'])
    assert included(rules, 'method', name, scope) is expected


@pytest.mark.parametrize('token', ['+method:pub:priv', '-member:self:cls', '+all:pub', '+bogus', '+member:bogus'])
def test_invalid_rules_rejected(token):
    with pytest.raises(ValueError):
        extract_rules([token])


def test_options_and_class_order_preserved():
    args, rules = extract_rules(['Demo', '-all+member:cls', 'Other', '--root', '-member'])
    assert args == ['Demo', 'Other', '--root', '-member']
    assert included(rules, 'member', 'x', 'cls')
    assert not included(rules, 'member', 'x', 'self')


SOURCE = '''
@decorate(flag=True)
class Demo(Base, metaclass=Meta):
    shared: int = 3
    if True:
        _class_secret = 'secret'
    def __init__(this):
        this.value: int = 1
        this._secret = 2
        def nested():
            this.excluded = 0
    def method(self, x: int, /, *args: str, key: bool=True, **kw: int) -> str:
        return 'body excluded'
    @classmethod
    def make(cls) -> 'Demo': pass
    @staticmethod
    async def worker(x: int) -> int: return x
    @property
    def prop(self) -> int: return 1
    @prop.setter
    def prop(self, value: int): pass
    @overload
    def over(self, x: int) -> int: ...
    @overload
    def over(self, x: str) -> str: ...
    def over(self, x): return x
'''


def render_source(tokens):
    node = ast.parse(SOURCE).body[0]
    match = inspector.Match(Path('/project/demo.py'), 'Demo', node.lineno, ['Base'],
                            inspector.init_members(node, SOURCE), SOURCE, node)
    _, rules = extract_rules(tokens)
    return inspector.render([match], {}, rules)


def test_decorators_signatures_and_compact_bodies():
    output = render_source([])
    parsed = ast.parse(output).body[0]
    assert ast.unparse(parsed.decorator_list[0]) == 'decorate(flag=True)'
    assert parsed.keywords[0].arg == 'metaclass'
    methods = [n for n in parsed.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    assert len([m for m in methods if m.name == 'over']) == 3
    method = next(m for m in methods if m.name == 'method')
    assert method.args.posonlyargs[-1].arg == 'x'
    assert method.args.vararg.arg == 'args' and method.args.kwarg.arg == 'kw'
    assert method.args.kwonlyargs[0].arg == 'key'
    assert ast.unparse(method.returns) == 'str'
    assert isinstance(next(m for m in methods if m.name == 'worker'), ast.AsyncFunctionDef)
    assert [ast.unparse(m.decorator_list[0]) for m in methods if m.name == 'prop'] == ['property', 'prop.setter']
    for m in methods:
        if m.name != '__init__':
            assert len(m.body) == 1 and m.body[0].value.value is Ellipsis
    assert all(line.endswith(': ...') for line in output.splitlines()
               if line.lstrip().startswith(('def ', 'async def ')) and 'def __init__(' not in line)
    assert '\n\n    def ' not in output
    assert 'body excluded' not in output


def test_filtered_class_and_instance_members():
    parsed = ast.parse(render_source(['-all', '+member:self:priv'])).body[0]
    init = next(n for n in parsed.body if isinstance(n, ast.FunctionDef) and n.name == '__init__')
    attrs = [n.targets[0].attr for n in init.body if isinstance(n, ast.Assign)]
    assert attrs == ['_secret']
    declarations = class_members(ast.parse(SOURCE).body[0])
    assert isinstance(declarations[0], ast.AnnAssign)
    assert declarations[0].value.value == 3
    assert isinstance(declarations[1], ast.Assign)
    assert declarations[1].value.value == 'secret'
    parsed = ast.parse(render_source(['-all', '+method:pub:cls'])).body[0]
    assert [n.name for n in parsed.body] == ['make', 'worker']


@pytest.mark.skipif(not shutil.which('nvim'), reason='Neovim is required for command completion')
def test_command_completion_matches_all_valid_combinations(tmp_path):
    destination = tmp_path / 'completion.json'
    lua = (
        f"vim.opt.rtp:prepend({json.dumps(str(ROOT))}); vim.cmd('runtime plugin/pyrefly_enhanced.lua'); "
        "local all=vim.fn.getcompletion('PyreflyEnhanced ', 'cmdline'); "
        "local chained=vim.fn.getcompletion('PyreflyEnhanced Demo -all+method:pu', 'cmdline'); "
        f"vim.fn.writefile({{vim.json.encode({{all=all,chained=chained}})}}, {json.dumps(str(destination))})"
    )
    result = subprocess.run(['nvim', '--headless', '-u', 'NONE', '+lua ' + lua, '+qa!'], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    data = json.loads(destination.read_text())
    expected = {sign + category for sign in '+-' for category in ('all', 'method', 'member')}
    assert set(data['all']) == expected
    assert set(data['chained']) == {'-all+method:pub'}
    for token in data['all']:
        extract_rules([token])


def test_class_assignments_preserve_values_and_annotations():
    output = render_source(['-all+member:cls'])
    node = ast.parse(output).body[0]
    assert ast.unparse(node.body[0]) == 'shared: int = 3'
    assert ast.unparse(node.body[1]) == "_class_secret = 'secret'"
    assert 'value:' not in output


def test_unpacking_and_chained_assignments_respect_visibility():
    from structure import class_member_lines
    node = ast.parse('class C:\n a, _b = factory()\n x = _y = []\n only_type: str\n').body[0]
    _, rules = extract_rules(['-all+member:pub:cls'])
    lines = class_member_lines(node, rules)
    assert lines == ['a, _ = factory()', 'x = []', 'only_type: str']
