import ast
from pathlib import Path

from test_structure import inspector
from structure import extract_rules


def test_locations_follow_filtered_members_decorators_and_methods():
    source = '''@decorate
class Demo:
    shared = []
    def __init__(self):
        中文 = 0; self.value = 3
    @classmethod
    def create(cls):pass
'''
    node = ast.parse(source).body[0]
    match = inspector.Match(Path('/project/demo.py'), 'Demo', 2, [],
                            inspector.init_members(node, source), source, node)
    _, rules = extract_rules([])
    locations = []
    output = inspector.render([match], {}, rules, locations)
    lines = output.split('\n')
    assert len(lines) == len(locations)
    expected = {'@decorate': 1, 'class Demo:': 2, '    shared = []': 3,
                '    def __init__(self):': 4, '    @classmethod': 6,
                '    def create(cls): ...': 7}
    for text, line in expected.items():
        assert locations[lines.index(text)]['line'] == line
    member_index = next(i for i, line in enumerate(lines) if 'self.value = ' in line)
    location = locations[member_index]
    assert location['line'] == 5
    span = next(s for s in location['spans'] if s['kind'] == 'assignment' and
                lines[member_index].encode()[s['start']:s['end']].decode() == 'value')
    assert source.splitlines()[4].encode()[span['column']:].decode().startswith('value')
    assert locations[-1] is None
