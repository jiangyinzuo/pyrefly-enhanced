# 测试

从插件根目录运行：

```bash
uv run --with pytest python -m pytest -q
nvim --headless -u NONE -l tests/nvim_integration.lua
```

Python 单元测试覆盖筛选优先级、作用域、完整签名、装饰器、类属性赋值、初始化控制路径和源码位置。补全测试使用独立 headless Neovim。

Lua 集成测试需要 PATH 中的 Python 3、ripgrep、Pyrefly 和 Neovim 0.11+。它创建临时项目及 socket，启动真实 Pyrefly，通过插件命令异步查询；验证左侧属性赋值跳转、右侧类/方法定义跳转、结果页连续查询与 buffer/tab 复用、逐项规则补全。不加载用户配置、不接触用户编辑器，退出时清理临时项目。
