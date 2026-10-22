#!/usr/bin/env python3
"""从当前项目查找 Python 类，静态查看父类和 __init__ 实例成员。"""
from __future__ import annotations

import argparse
import ast
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import tokenize
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator
from structure import class_member_lines, extract_rules, included, method_lines, method_scope


SKIP_DIRS = {".git", ".hg", ".svn", ".venv", "venv", "env", "__pycache__",
             "node_modules", ".mypy_cache", ".pyrefly_cache", ".pytest_cache", "site-packages"}


@dataclass
class Member:
    name: str
    line: int
    column: int
    annotation: str | None = None


@dataclass
class Match:
    path: Path
    name: str
    line: int
    bases: list[str]
    members: list[Member]
    source: str
    node: ast.ClassDef


def init_members(node: ast.ClassDef, source: str) -> list[Member]:
    init = next((n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and n.name == "__init__"), None)
    if init is None:
        return []
    parameters = init.args.posonlyargs + init.args.args
    if not parameters or any(isinstance(d, ast.Name) and d.id == "staticmethod" for d in init.decorator_list):
        return []
    receiver = parameters[0].arg
    result: dict[str, Member] = {}
    lines = source.splitlines()

    class Visitor(ast.NodeVisitor):
        # 嵌套函数和类的赋值不属于本构造函数的直接执行体。
        def visit_FunctionDef(self, child):
            pass
        visit_AsyncFunctionDef = visit_FunctionDef
        visit_ClassDef = visit_FunctionDef

        def visit_Attribute(self, child):
            if (isinstance(child.ctx, ast.Store) and isinstance(child.value, ast.Name)
                    and child.value.id == receiver):
                # AST 列号为 UTF-8 字节，LSP 列号为 UTF-16 code units。
                prefix = lines[child.lineno - 1].encode()[:child.value.end_col_offset + 1].decode()
                column = len(prefix.encode("utf-16-le")) // 2
                result.setdefault(child.attr, Member(child.attr, child.lineno, column))
            self.generic_visit(child)

        def visit_AnnAssign(self, child):
            self.generic_visit(child)
            if (isinstance(child.target, ast.Attribute) and isinstance(child.target.value, ast.Name)
                    and child.target.value.id == receiver):
                result[child.target.attr].annotation = ast.unparse(child.annotation)

    visitor = Visitor()
    for statement in init.body:
        visitor.visit(statement)
    return list(result.values())


def find_classes(root: Path, query: str, source_reader: Callable[[Path], str] | None = None,
                 extra_paths: list[Path] | None = None) -> tuple[list[Match], list[str]]:
    matches, warnings = [], []
    # 匹配 class 声明而非引用；兼容关键字和类名之间的显式续行。
    name = re.escape(query.rsplit('.', 1)[-1])
    pattern = r"\bclass(?:[ \t\f]|\\\r?\n)+" + name + r"\b"
    command = ["rg", "--multiline", "--files-with-matches", "--null", "--no-ignore",
               "--glob", "*.py", "--glob", "*.pyi"]
    for directory in sorted(SKIP_DIRS):
        command.extend(("--glob", f"!**/{directory}/**"))
    command.extend(("--", pattern, "."))
    result = subprocess.run(command, cwd=root, capture_output=True)
    if result.returncode not in (0, 1):
        warnings.append(result.stderr.decode(errors="replace").strip())
    paths = sorted(root / Path(os.fsdecode(path))
                   for path in result.stdout.split(b'\0') if path)
    paths = sorted(set(paths) | set(extra_paths or []))
    for path in paths:
        try:
            if source_reader:
                source = source_reader(path)
            else:
                # tokenize.open 遵守源码 encoding cookie，不执行目标代码。
                with tokenize.open(path) as stream:
                    source = stream.read()
            tree = ast.parse(source, filename=str(path))
        except (OSError, SyntaxError, UnicodeError) as exc:
            warnings.append(f"{path}: {exc}")
            continue

        class Visitor(ast.NodeVisitor):
            def __init__(self):
                self.scope = []
            def visit_ClassDef(self, node):
                name = '.'.join([*self.scope, node.name])
                if query == node.name or query == name:
                    bases = [ast.unparse(base) for base in node.bases]
                    matches.append(Match(path, name, node.lineno, bases,
                                         init_members(node, source), source, node))
                self.scope.append(node.name)
                self.generic_visit(node)
                self.scope.pop()
            def visit_FunctionDef(self, node):
                self.scope.extend((node.name, '<locals>'))
                self.generic_visit(node)
                del self.scope[-2:]
            visit_AsyncFunctionDef = visit_FunctionDef
        Visitor().visit(tree)
    return sorted(matches, key=lambda m: (str(m.path), m.line)), warnings


class LspClient:
    def __init__(self, root: Path, timeout: float):
        self.timeout = timeout
        self.process = subprocess.Popen(
            ["pyrefly", "lsp", "--indexing-mode", "none", "--threads", "2"],
            cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        self.messages: queue.Queue = queue.Queue()
        self.next_id = 0
        threading.Thread(target=self.receive, daemon=True).start()

    def receive(self):
        try:
            while True:
                headers = {}
                while True:
                    line = self.process.stdout.readline()
                    if not line:
                        raise EOFError("Pyrefly LSP 已退出")
                    if line == b'\r\n':
                        break
                    key, value = line.decode().split(':', 1)
                    headers[key.lower()] = value.strip()
                size = int(headers['content-length'])
                payload = bytearray()
                while len(payload) < size:
                    chunk = self.process.stdout.read(size - len(payload))
                    if not chunk:
                        raise EOFError("不完整的 LSP 消息")
                    payload.extend(chunk)
                self.messages.put(json.loads(payload))
        except Exception as exc:
            self.messages.put(exc)

    def send(self, message):
        data = json.dumps({'jsonrpc': '2.0', **message}).encode()
        self.process.stdin.write(f'Content-Length: {len(data)}\r\n\r\n'.encode() + data)
        self.process.stdin.flush()

    def notify(self, method, params):
        self.send({'method': method, 'params': params})

    def request(self, method, params, timeout=None):
        self.next_id += 1
        request_id = self.next_id
        self.send({'id': request_id, 'method': method, 'params': params})
        deadline = time.monotonic() + (timeout if timeout is not None else self.timeout)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"LSP 请求超时：{method}")
            try:
                message = self.messages.get(timeout=remaining)
            except queue.Empty:
                raise TimeoutError(f"LSP 请求超时：{method}") from None
            if isinstance(message, Exception):
                raise message
            if 'method' in message and 'id' in message:
                # 本客户端不声明动态配置能力；仍响应服务端请求，避免互相等待。
                self.send({'id': message['id'], 'result': None})
            elif message.get('id') == request_id:
                if 'error' in message:
                    raise RuntimeError(str(message['error']))
                return message.get('result')

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.process.stdin.close()
        self.process.stdout.close()


@contextmanager
def language_server(root: Path, timeout: float) -> Iterator[LspClient]:
    client = LspClient(root, timeout)
    try:
        client.request('initialize', {'processId': None, 'rootUri': root.as_uri(),
                       'workspaceFolders': [{'uri': root.as_uri(), 'name': root.name}],
                       'capabilities': {}})
        client.notify('initialized', {})
        yield client
    finally:
        client.close()


def infer_members(client: LspClient, match: Match) -> dict[str, str]:
    if not match.members:
        return {}
    uri = match.path.as_uri()
    client.notify('textDocument/didOpen', {'textDocument': {
        'uri': uri, 'languageId': 'python', 'version': 1, 'text': match.source}})
    try:
        first = match.members[0]
        deadline = time.monotonic() + client.timeout
        # Pyrefly 可能在 didOpen 后尚未完成文件加载，空结果不等于没有成员。
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"Pyrefly 未返回成员补全：{match.name}")
            response = client.request('textDocument/completion', {
                'textDocument': {'uri': uri},
                'position': {'line': first.line - 1, 'character': first.column},
                'context': {'triggerKind': 2, 'triggerCharacter': '.'}}, remaining)
            items = response.get('items', []) if isinstance(response, dict) else response or []
            types = {item['label']: item['detail'] for item in items if item.get('detail')}
            if any(member.name in types for member in match.members):
                return types
            time.sleep(min(0.2, max(0, deadline - time.monotonic())))
    finally:
        client.notify('textDocument/didClose', {'textDocument': {'uri': uri}})


def render(matches: list[Match], inferred: dict[str, dict[str, str]], rules) -> str:
    lines = []
    for match in matches:
        lines.append(f"# {match.path}:{match.line}")
        if match.name != match.name.rsplit('.', 1)[-1]:
            lines.append(f"# qualified: {match.name}")
        lines.extend('@' + ast.unparse(d) for d in match.node.decorator_list)
        header_node = ast.ClassDef(name=match.node.name, bases=match.node.bases,
                                   keywords=match.node.keywords, body=[ast.Pass()], decorator_list=[])
        if hasattr(match.node, 'type_params'):
            header_node.type_params = match.node.type_params
        lines.append(ast.unparse(ast.fix_missing_locations(header_node)).splitlines()[0])
        types = inferred.get(str(match.path) + ':' + match.name, {})
        body = []
        def member_line(name, annotation):
            value = types.get(name) or annotation
            if not value or value in ('Unknown', 'unknown'):
                return f"    {name}: Any  # 未能推导类型"
            return f"    {name}: {value}"
        body.extend('    ' + line for line in class_member_lines(match.node, rules))
        for member in match.members:
            if included(rules, 'member', member.name, 'self'):
                body.append(member_line(member.name, member.annotation))
        for method in match.node.body:
            if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) and included(
                    rules, 'method', method.name, method_scope(method)):
                body.extend('    ' + line for line in method_lines(method))
        lines.extend(body or ['    ...'])
        lines.append('')
    return '\n'.join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False,
        epilog='输出规则：+all（默认）、-all、±method、±member；可加 :pub/:priv/:self/:cls。按从左到右覆盖，支持 -all+method:pub 连写。')
    parser.add_argument('class_names', nargs='+', help='空格分隔的类名或限定名，例如 LLM LLMEngine Outer.Inner')
    parser.add_argument('--root', type=Path, default=Path.cwd(), help='搜索项目，默认当前目录')
    parser.add_argument('--nvim', nargs='?', const='', default=None, metavar='SOCKET',
                        help='复用 Neovim；省略地址时自动发现并选择实例')
    parser.add_argument('--timeout', type=float, default=60, help='类型查询超时秒数，默认 60')
    try:
        argv, rules = extract_rules(sys.argv[1:])
    except ValueError as exc:
        parser.error(str(exc))
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error('--timeout 必须大于 0')
    if not shutil.which('rg'):
        parser.error('需要 ripgrep（rg）进行源码搜索')
    root = args.root.resolve()
    if not root.is_dir():
        parser.error(f'项目目录不存在：{root}')
    try:
        with ExitStack() as stack:
            nvim = None
            if args.nvim is not None:
                from nvim_client import NvimClient, choose_address
                nvim = NvimClient(choose_address(args.nvim), args.timeout)
            extra = [Path(p) for p in nvim.buffers(root)] if nvim else None
            matches, warnings, missing = [], [], []
            seen = set()
            for query in dict.fromkeys(args.class_names):
                found, notes = find_classes(root, query, nvim.source if nvim else None, extra)
                warnings.extend(notes)
                if not found:
                    missing.append(query)
                for match in found:
                    key = (match.path, match.line)
                    if key not in seen:
                        seen.add(key)
                        matches.append(match)
            if missing:
                for warning in warnings:
                    print(f'警告：{warning}', file=sys.stderr)
                print('未找到类：' + ', '.join(missing), file=sys.stderr)
                return 1
            inferred = {}
            client = None
            if nvim is None:
                if not shutil.which('pyrefly'):
                    raise RuntimeError('未找到 pyrefly；请安装或使用 --nvim 复用现有 LSP')
                client = stack.enter_context(language_server(root, args.timeout))
            for match in matches:
                types = nvim.infer(match) if nvim else infer_members(client, match)
                inferred[str(match.path) + ':' + match.name] = types
            print(render(matches, inferred, rules))
            for warning in warnings:
                print(f'警告：{warning}', file=sys.stderr)
            return 0
    except (OSError, ValueError, RuntimeError, TimeoutError, EOFError, subprocess.TimeoutExpired) as exc:
        print(f'错误：{exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
