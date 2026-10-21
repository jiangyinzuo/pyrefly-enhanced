"""通过 Neovim 的原生 RPC 客户端访问现有编辑器，不接管 LSP 管道。"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
from pathlib import Path


class NvimClient:
    def __init__(self, address: str, timeout: float):
        self.address = address
        self.timeout = timeout

    def call(self, lua: str):
        expression = 'json_encode(luaeval(' + json.dumps(lua, ensure_ascii=False) + '))'
        result = subprocess.run(
            ['nvim', '--headless', '--server', self.address, '--remote-expr', expression],
            capture_output=True, text=True, timeout=self.timeout + 5,
        )
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or f'无法连接 Neovim：{self.address}')
        return json.loads(result.stdout)

    def source(self, path: Path) -> str:
        return self.call('require("pyrefly_enhanced").source(' + json.dumps(str(path)) + ')')
    def buffers(self, root: Path) -> list[str]:
        return self.call('require("pyrefly_enhanced").buffers(' + json.dumps(str(root)) + ')')

    def infer(self, match) -> dict[str, str]:
        if not match.members:
            return {}
        member = match.members[0]
        args = ','.join((json.dumps(str(match.path)), str(member.line - 1), str(member.column),
                         str(int(self.timeout * 1000))))
        return self.call('require("pyrefly_enhanced").complete(' + args + ')')


def discover() -> list[tuple[str, str]]:
    """只探测本用户拥有的 socket；PID 不是 RPC 地址。"""
    addresses = set()
    if os.environ.get('NVIM'):
        addresses.add(os.environ['NVIM'])
    roots = {Path(tempfile.gettempdir()), Path(f'/run/user/{os.getuid()}')}
    if os.environ.get('XDG_RUNTIME_DIR'):
        roots.add(Path(os.environ['XDG_RUNTIME_DIR']))
    # Neovim 默认 socket 常在 /tmp/nvim.USER/...；也包括自定义的 /tmp/*.sock。
    for root in roots:
        if not root.is_dir():
            continue
        pending = [(root, 0)]
        while pending:
            directory, depth = pending.pop()
            try:
                entries = list(directory.iterdir())
            except OSError:
                continue
            for path in entries:
                try:
                    info = path.lstat()
                except OSError:
                    continue
                if info.st_uid != os.getuid():
                    continue
                if stat.S_ISSOCK(info.st_mode) and 'nvim' in str(path):
                    addresses.add(str(path))
                elif stat.S_ISDIR(info.st_mode) and depth < 3 and ('nvim' in path.name or root != Path(tempfile.gettempdir())):
                    pending.append((path, depth + 1))
    # ss 可发现位于任意自定义目录的 Unix socket；不可用时仍可探测默认目录。
    try:
        result = subprocess.run(['ss', '-xlpH'], capture_output=True, text=True, timeout=3)
        for line in result.stdout.splitlines():
            if '"nvim"' not in line:
                continue
            for word in line.split():
                if word.startswith('/'):
                    try:
                        info = Path(word).stat()
                        if info.st_uid == os.getuid() and stat.S_ISSOCK(info.st_mode):
                            addresses.add(word)
                    except OSError:
                        pass
    except (OSError, subprocess.TimeoutExpired):
        pass
    found = []
    for address in sorted(addresses):
        try:
            result = NvimClient(address, 0.4).call(
                '(function() return {pid=vim.fn.getpid(),cwd=vim.fn.getcwd()} end)()')
            found.append((address, f"pid={result['pid']} cwd={result['cwd']}"))
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
            continue
    return found


def choose_address(explicit: str | None) -> str:
    if explicit:
        return explicit
    found = discover()
    if not found:
        raise RuntimeError('未发现可连接的 Neovim；请用 --nvim SOCKET 指定 v:servername')
    if len(found) == 1:
        return found[0][0]
    # 提示不占 stdout，stdout 始终只含类分析结果。
    try:
        with open('/dev/tty', 'w') as terminal, open('/dev/tty') as input_stream:
            terminal.write('发现多个 Neovim：\n')
            for i, (address, description) in enumerate(found, 1):
                terminal.write(f'  {i}. {address}  {description}\n')
            terminal.write('选择编号：')
            terminal.flush()
            answer = input_stream.readline().strip()
    except OSError:
        raise RuntimeError('发现多个 Neovim，当前无交互终端；请用 --nvim SOCKET 指定') from None
    if not answer.isdecimal() or not 1 <= int(answer) <= len(found):
        raise RuntimeError('无效的 Neovim 编号')
    return found[int(answer) - 1][0]
