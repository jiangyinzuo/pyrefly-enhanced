import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(not shutil.which('nvim'), reason='Neovim is required')
@pytest.mark.parametrize('lead,expected', [
    ('+member:', ['+member:pub', '+member:priv', '+member:self', '+member:cls']),
    ('-method:pub:', ['-method:pub:self', '-method:pub:cls']),
    ('+member:self:', ['+member:self:pub', '+member:self:priv']),
    ('-all+method:pr', ['-all+method:priv']),
    ('+member:pub:self:', []),
    ('+all:', []),
])
def test_one_qualifier_per_completion(lead, expected, tmp_path):
    out = tmp_path / 'result.json'
    lua = (f'vim.opt.rtp:prepend({json.dumps(str(ROOT))}); '
           f'local result=require("pyrefly_enhanced.completion").complete({json.dumps(lead)}); '
           f'vim.fn.writefile({{vim.json.encode(result)}},{json.dumps(str(out))})')
    result = subprocess.run(['nvim', '--headless', '-u', 'NONE', '+lua ' + lua, '+qa!'],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(out.read_text()) == expected
