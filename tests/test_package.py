import ast
from test_structure import inspector
from structure import extract_rules


def test_package_module_and_nested_class(tmp_path):
    package = tmp_path / 'pkg'
    module = package / 'sub'
    module.mkdir(parents=True)
    for directory in (package, module):
        (directory / '__init__.py').write_text('')
    path = module / 'engine.py'
    source = 'class Engine:pass\n'
    node = ast.parse(source).body[0]
    match = inspector.Match(path, 'Outer.Engine', 1, [], [], source, node)
    assert inspector.qualified_class_name(match) == 'pkg.sub.engine.Outer.Engine'
    _, rules = extract_rules([])
    locations = []
    output = inspector.render([match], {}, rules, locations)
    assert output.splitlines()[:2] == [f'# {path}:1', '# pkg.sub.engine.Outer.Engine']
    assert locations[0] == locations[1]
    match.path = module / '__init__.py'
    assert inspector.qualified_class_name(match) == 'pkg.sub.Outer.Engine'


def test_standalone_module(tmp_path):
    node = ast.parse('class Demo:pass').body[0]
    match = inspector.Match(tmp_path / 'demo.py', 'Demo', 1, [], [], '', node)
    assert inspector.qualified_class_name(match) == 'demo.Demo'
