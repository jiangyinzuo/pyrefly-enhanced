# pyrefly-enhanced

查一个或少数类的直接父类，以及本类 `__init__` 中赋值的实例属性和类型。不导入项目，不构造实例。输出为 Python class 语法，文件路径写在注释中。

需要 Python 3.11+、ripgrep。独立查询需要 PATH 中的 `pyrefly`；复用模式需要 Neovim 0.11+，目标 Neovim 已加载本插件并启动项目的 Pyrefly LSP。

## Python 命令行

在项目目录查询，结果直接写 stdout：

```bash
cd /path/to/project
python3 ~/plugged/pyrefly-enhanced LLM
python3 ~/plugged/pyrefly-enhanced LLM LLMEngine EngineCore
```

复用现有 Neovim（不新启动 LSP）：

```bash
python3 ~/plugged/pyrefly-enhanced LLM LLMEngine --nvim /path/to/nvim.sock
python3 ~/plugged/pyrefly-enhanced LLM LLMEngine --nvim
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
:PyreflyEnhanced LLM LLMEngine EngineCore
```

不带类名时使用光标处单词。查询异步执行，结果在新 tab 的 Python scratch buffer 中显示；后续查询复用同一个结果 buffer。在结果页中可直接再次执行命令，沿用上次成功查询的项目，无需切回源码。命令只使用现有 Pyrefly，不启动新的语言服务器。

结果页中按 `<leader>gd` 跳到原始定义；使用 buffer 局部映射，不改变其他 buffer 的键位。赋值左侧 `self.xxx` 跳到源码赋值位置；右侧名称和类方法映射回原表达式，通过现有 Pyrefly 查询定义；折叠的 `(...)` 没有参数跳转。类、方法及装饰器行继续跳到原始声明。源码修改后请重新查询刷新位置。

多个类名用空格分隔，按输入顺序合并输出到同一 stdout／结果 buffer，并共用一次 LSP 会话；重复命中的类只输出一次。任一类未找到时整次查询报错，不用不完整结果覆盖已有结果页。使用自动发现时，把不带地址的 `--nvim` 放在所有类名后面。

可选配置：

```lua
require('pyrefly_enhanced').setup({ python = 'python3', timeout = 60 })
```

不要在 Neovim 中用同步 shell 调用等待 `--nvim` 客户端完成：客户端还需要向这个 Neovim 发 RPC，容易互相等待。插件命令已经采用异步启动。

## 选择输出内容

默认 `+all`，输出本类成员和方法；规则按书写顺序应用，最右侧匹配规则优先，支持连写：

```bash
python3 ~/plugged/pyrefly-enhanced LLM +all-member
python3 ~/plugged/pyrefly-enhanced LLM -all +method:pub:self
python3 ~/plugged/pyrefly-enhanced LLM -all+member:cls
```

```vim
:PyreflyEnhanced LLM +all-member
:PyreflyEnhanced LLM -all +member:priv:self
```

`+` 输出、`-` 隐藏；`method` 指方法，`member` 指属性，`all` 同时覆盖两者。`method/member` 可追加 `:pub`、`:priv`、`:self`、`:cls`；多个条件取交集，两种次序均可。`pub` 名称不以下划线开头；`priv` 包含单下划线、双下划线及 `__init__` 等特殊名称。`self` 包含实例属性和普通实例方法/property，`cls` 包含类体属性及 classmethod/staticmethod。无符号的规则不接受；`all` 不接受限定条件。

例如 `-member+member` 恢复全部属性，`-member+all` 恢复全部内容，`+all-member` 只输出方法。Neovim 在规则类别和每个冒号后逐项补全，不一次插入多项限定，也不提供冲突条件。

## 输出与边界

```python
# /path/to/project/engine.py:10
# engine.Engine
class Engine(Base):
    count = 0
    def __init__(self, scheduler: Scheduler):
        self.scheduler: Scheduler = scheduler
        self.requests = create_requests(...)
    @classmethod
    def create(cls) -> 'Engine': ...
```

这是初始化和结构摘要，不是可执行还原；不会导入类型或执行赋值。同名类全部输出。类体属性保留源码赋值和原注解，不擅自补 `ClassVar`。实例属性移入 `__init__`：右值调用统一折叠成 `Factory.make(...)`，变量/属性引用等非调用右值附加可用的 LSP 类型；源码显式注解始终保留，不能推导则仅保留赋值。LSP 类型可能是所有分支的联合类型，不宣称是当前分支的精确类型。字面量可能含敏感内容，分享前请检查。

`__init__` 保留通向实例赋值的 if/else、try/except/finally、循环、with、match 等结构，同一属性多次赋值不去重；无关语句省略，空的必要分支用 `pass` 占位。仅剩空构造函数时也用 `pass`，不使用 `...`。局部变量来源不做数据流回溯。`member:self` 控制实例赋值，即使隐藏方法也保留承载它们的 `__init__`；只显示方法时构造体为 `pass`。其余方法保留完整源码签名和装饰器，方法体为同行 `: ...`，方法之间无空行。类装饰器始终保留。排除嵌套函数/类及继承方法，不解析 `setattr` 等动态注入；格式由 AST 规范化，不保留原排版。

ripgrep 先筛 `class 类名` 声明，再用 AST 核对；搜索包括 gitignore 忽略的源码，但跳过隐藏目录、常见虚拟环境、缓存和符号链接。Neovim 模式同时检查已加载的项目缓冲区，并基于未保存内容查询，避免 AST 与 LSP 使用不同版本。源码以 noautocmd 加载，不切窗口、不修改用户文本，也不通过 FileType 自动启动新服务器。

仅查询匹配文件所属项目的现有 Pyrefly；未附着时只可附着已运行的匹配 client，不创建新 client。项目依赖与类型标注影响推断精度。一次查询只检查指定类，不做全项目类型索引。错误和扫描警告写 stderr；未找到类或查询失败返回非零。
