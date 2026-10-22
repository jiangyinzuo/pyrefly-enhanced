import ast
from initializer import initializer_lines
from structure import extract_rules


def summarize(source, tokens=(), types=None):
    method = ast.parse(source).body[0].body[0]
    _, rules = extract_rules(list(tokens))
    rows = initializer_lines(method, types or {}, rules)
    text = '\n'.join(line for line, _ in rows)
    return ast.parse(text).body[0], text, rows


def test_branch_sources_calls_and_reference_types():
    source = '''class C:
    def __init__(self, enabled, client):
        unrelated()
        if enabled:
            self.client = Factory.make(long_arg, config=True)
        else:
            self.client = client
        self.count: int = compute(1, 2)
'''
    node, text, rows = summarize(source, types={'client': 'Client | None'})
    assert isinstance(node.body[0], ast.If)
    branch = node.body[0]
    assert ast.unparse(branch.body[0]) == 'self.client = Factory.make(...)'
    assert ast.unparse(branch.orelse[0]) == 'self.client: Client | None = client'
    assert ast.unparse(node.body[1]) == 'self.count: int = compute(...)'
    assert 'unrelated' not in text and 'long_arg' not in text
    assert text.splitlines()[0].endswith(':') and not text.splitlines()[0].endswith(': ...')
    factory_row = next((text, loc) for text, loc in rows if 'Factory.make' in text)
    spans = factory_row[1]['spans']
    assert any(s['kind']=='definition' and factory_row[0].encode()[s['start']:s['end']]==b'make' for s in spans)
    assert any(s['kind']=='assignment' and factory_row[0].encode()[s['start']:s['end']]==b'client' for s in spans)


def test_control_structures_and_repeated_writes():
    source = '''class C:
    def __init__(self, items, ctx):
        self.value = None
        try:
            for item in items:
                if item:
                    self.value = Factory(item)
        except ValueError:
            self.value = None
        else:
            self.ok = True
        finally:
            self.done = True
        with ctx:
            self.value = 3
        match items:
            case []:
                self.empty = True
            case _:
                ignore()
'''
    node, text, _ = summarize(source)
    assert sum(isinstance(n, (ast.Assign, ast.AnnAssign)) and
               any(isinstance(x, ast.Attribute) and x.attr=='value' for x in ast.walk(n))
               for n in ast.walk(node)) == 4
    assert any(isinstance(n, ast.Try) for n in ast.walk(node))
    assert any(isinstance(n, ast.With) for n in ast.walk(node))
    assert any(isinstance(n, ast.Match) for n in ast.walk(node))
    assert 'ignore' not in text


def test_filtered_empty_branch_and_empty_initializer_use_pass():
    source = '''class C:
    def __init__(self, enabled):
        if enabled:
            self.public = 1
        else:
            self._private = 2
'''
    node, _, _ = summarize(source, ['-all+member:priv:self'])
    assert isinstance(node.body[0].body[0], ast.Pass)
    node, text, _ = summarize(source, ['-all'])
    assert isinstance(node.body[0], ast.Pass)
    assert '...' not in text
