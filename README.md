# pyrefly-enhanced

查一个或少数类的直接父类，以及本类 `__init__` 中赋值的实例属性和类型。不导入项目，不构造实例。输出为 Python class 语法，文件路径写在注释中。

需要 Python 3.11+、ripgrep。独立查询需要 PATH 中的 `pyrefly`；复用模式需要 Neovim 0.11+，目标 Neovim 已加载本插件并启动项目的 Pyrefly LSP。

## Python 命令行

在项目目录查询，结果直接写 stdout：

```bash
cd /path/to/project
python3 ~/plugged/pyrefly-enhanced LLM
```

复用现有 Neovim（不新启动 LSP）：

```bash
python3 ~/plugged/pyrefly-enhanced LLM --nvim /path/to/nvim.sock
python3 ~/plugged/pyrefly-enhanced LLM --nvim
```

不填 socket 时自动发现本用户可连接的 Unix socket。只有一个实例直接使用；多个实例在终端列出编号供选择。没有交互终端时需指定地址。可在 Neovim 中用 `:echo v:servername` 查看地址；内置终端通常也有 `$NVIM`。

```bash
python3 ~/plugged/pyrefly-enhanced Outer.Inner --root /path/to/project --timeout 120
python3 ~/plugged/pyrefly-enhanced LLM > /tmp/LLM.py
```

指定 `--nvim` 后，连接、client 匹配或 LSP 查询失败会报错，不静默启动独立 LSP。实例发现以默认临时/运行时目录为基础；若系统有 `ss`，也检查其他路径的 socket。无法发现自定义地址时显式传入即可。

## Neovim

用 lazy.nvim 安装：

```lua
{ 'jiangyinzuo/pyrefly-enhanced', cmd = 'PyreflyEnhanced', ft = 'python' }
```

打开目标项目 Python 文件，确认 Pyrefly 已连接，然后：

```vim
:PyreflyEnhanced LLM
:PyreflyEnhanced Outer.Inner
```

不带类名时使用光标处单词。查询异步执行，结果在新 tab 的 Python scratch buffer 中显示；后续查询复用同一个结果 buffer。命令只使用现有 Pyrefly，不启动新的语言服务器。

可选配置：

```lua
require('pyrefly_enhanced').setup({ python = 'python3', timeout = 60 })
```

不要在 Neovim 中用同步 shell 调用等待 `--nvim` 客户端完成：客户端还需要向这个 Neovim 发 RPC，容易互相等待。插件命令已经采用异步启动。

## 输出与边界

```python
# /path/to/project/engine.py:10
class Engine(Base):
    scheduler: Scheduler
    requests: list[Request]
    dynamic_value: Any  # 未能推导类型
```

这是结构摘要，不是可独立运行的类实现；不会自动导入父类或类型。同名类全部输出，嵌套类额外注释限定名。类型优先用 LSP 补全，缺少时使用源码显式注解，再无信息用 `Any` 并标明未知。

只读取本类 `__init__` 的直接属性赋值，支持条件、循环、解包和非 self 命名的接收参数。排除嵌套函数/类，不展开继承构造函数或其他方法，不解析 `setattr` 和运行时动态注入。父类保留源码表达式，不展开 MRO。

ripgrep 先筛 `class 类名` 声明，再用 AST 核对；搜索包括 gitignore 忽略的源码，但跳过隐藏目录、常见虚拟环境、缓存和符号链接。Neovim 模式同时检查已加载的项目缓冲区，并基于未保存内容查询，避免 AST 与 LSP 使用不同版本。源码以 noautocmd 加载，不切窗口、不修改用户文本，也不通过 FileType 自动启动新服务器。

仅查询匹配文件所属项目的现有 Pyrefly；未附着时只可附着已运行的匹配 client，不创建新 client。项目依赖与类型标注影响推断精度。一次查询只检查指定类，不做全项目类型索引。错误和扫描警告写 stderr；未找到类或查询失败返回非零。
