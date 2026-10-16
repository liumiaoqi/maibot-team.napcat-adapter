"""P0-c① 插件**自己仓**里的镜像探针：`server_config` 的读取面 ⊆ 声明面 ∪ 方法。

## 怎么跑

不需要任何新依赖、没有配置文件（本仓此前没有测试约定，故按最小可跑的样子来）：

```sh
cd plugins/maibot-team.napcat-adapter
python -m pytest tests/ -q
```

⚠️ 本仓**没有自己的 venv**（实测），所以在本机就用主仓那套解释器跑：

```sh
uv run --project <主仓根> python -m pytest tests/ -q
```

## 为什么和主仓那份同时存在（不是重复劳动）

同一份契约在**两处 CI** 上各有一份判据，是**故意**的：

- 主仓那份（`tests/plugin_runtime_v2/runner/test_plugin_config_field_probe.py`）保证**主仓 CI**
  能拦住跨边界的不一致——插件仓的测试主仓跑不到；
- 这份保证**改插件的人**在插件仓里就能拦住自己（不然要等主仓 CI 才发现）。
- 两侧都读**源码**、都用 AST（`config.py` 用相对导入 `from .constants import ...`，
  独立进程里 import 不进来；而且源级对拍本来就不该依赖"能 import"）。

## 这一条对的是哪个真机事故

`transport.py:148` 曾读 `server_config.action_timeout_sec`，而 `NapCatServerConfig` 没有这个字段
（2026-10-16 修，`cf2f59c`）⇒ 每次发送都在 `AttributeError` 上失败、日志里只留一句
"能听不能说"。**同仓两个文件都没对上，任何单侧测试都不会红**——本探针就是那条缺失的判据。

⚠️ 两处坑（写错就出假阳性/假阴性）：

1. **方法要算进声明集**：`server_config.build_ws_url()` 读的是**方法**；只比 dataclass 字段
   会把 `build_ws_url` 报成违规（假阳性）。
2. **读取面不止一处**：`heartbeat_interval` / `reconnect_delay_sec` / `token` 也要扫到；
   漏了它们，探针就只剩"看起来绿"。
"""

import ast
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DECLARATION_FILE = PLUGIN_ROOT / "config.py"
READ_FILE = PLUGIN_ROOT / "transport.py"
TARGET_CLASS = "NapCatServerConfig"


def declared_members(source: str, class_name: str = TARGET_CLASS) -> set[str]:
    """可被属性访问到的名字 = 带注解的字段 ∪ 类属性 ∪ **方法**（方法必须算）。"""
    members: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.ClassDef) or node.name != class_name:
            continue
        for item in node.body:
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                members.add(item.target.id)
            elif isinstance(item, ast.Assign):
                members.update(
                    target.id for target in item.targets if isinstance(target, ast.Name)
                )
            elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                members.add(item.name)
    return members


def read_attributes(source: str) -> set[str]:
    """读到的属性名（只收 Load；`x.attr = 1` 是写，不算读）。"""
    reads: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Attribute) or not isinstance(node.ctx, ast.Load):
            continue
        if isinstance(node.value, ast.Name) and node.value.id == "server_config":
            reads.add(node.attr)
            continue
        inner = node.value
        if (
            isinstance(inner, ast.Attribute)
            and isinstance(inner.value, ast.Name)
            and inner.value.id == "self"
            and inner.attr.startswith("_server_config")
        ):
            reads.add(node.attr)
    return reads


def find_violations(declaration_source: str, read_source: str) -> list[str]:
    """读取面 ⊆ 声明面；两侧任一扫空 ⇒ 报"扫描器瞎了"（不许静默通过）。"""
    declared = declared_members(declaration_source)
    reads = read_attributes(read_source)

    if not declared:
        return [f"扫描不到 {TARGET_CLASS} 的声明——探针自己瞎了（扫描失败不许当通过）"]
    if not reads:
        return ["扫不到任何 server_config.<attr> 读取——探针自己瞎了（扫描失败不许当通过）"]
    return [
        f"读取了未声明的成员：server_config.{name}"
        for name in sorted(reads - declared)
    ]


def test_server_config_reads_are_declared() -> None:
    """正向：`transport.py` 读的每个成员，`NapCatServerConfig` 都必须有（字段或方法）。"""
    violations = find_violations(
        DECLARATION_FILE.read_text(encoding="utf-8"),
        READ_FILE.read_text(encoding="utf-8"),
    )
    assert violations == [], "配置声明与读取对不上：\n" + "\n".join(violations)


def test_probe_sees_the_known_reads_and_methods() -> None:
    """自检：真源码里确实扫到了已知读取与方法（防"扫了个空"也算过）。"""
    reads = read_attributes(READ_FILE.read_text(encoding="utf-8"))
    declared = declared_members(DECLARATION_FILE.read_text(encoding="utf-8"))

    missing_reads = {"action_timeout_sec", "token", "heartbeat_interval", "reconnect_delay_sec"} - reads
    assert not missing_reads, f"漏扫了这些读取 {sorted(missing_reads)}（实扫到 {sorted(reads)}）"
    assert "build_ws_url" in declared, f"方法没被算进声明集（假阳性的来源）：{sorted(declared)}"


def test_probe_goes_red_when_reading_undeclared_field() -> None:
    """鉴别力：读一个类里没有的字段 ⇒ 必须红（`action_timeout_sec` 那次事故的形状）。"""
    declaration = "class NapCatServerConfig:\n    ws_url: str = 'ws://x'\n"
    bad_read = "def f(server_config):\n    return server_config.action_timeout_sec\n"
    violations = find_violations(declaration, bad_read)
    assert violations and "action_timeout_sec" in violations[0], f"没抓到: {violations}"


# ── 以下 4 条：2026-10-16 由 `fu-xuan` 自 worktree 那份镜像并入（lead 裁定：两份留一份）──
# 为什么值得多这 4 条：上面 `find_violations` **有**"两侧任一扫空 ⇒ 报扫描器瞎了"的 guard，
# 但下面第 2/3 条之前**没有任何用例证明那个 guard 会触发** —— **guard 会悄悄烂掉**，
# 而那就是"探针自己瞎了却没人知道"的形状（每个组件都没坏 ⇒ 没有监视器会响）。


def test_probe_counts_methods_as_declared() -> None:
    """**方法要算进声明集**（独立成条）：`build_ws_url` 是方法、不是 dataclass 字段。

    只比字段会把它报成"读了未声明的成员" —— 假阳性会让人干脆关掉这条探针，比没有探针更糟。
    单列一条，坏掉时一眼看得出是这里。
    """
    declared = declared_members(DECLARATION_FILE.read_text(encoding="utf-8"))
    assert "build_ws_url" in declared, f"方法没被算进声明集（假阳性来源）：{sorted(declared)}"
    assert "action_timeout_sec" in declared, f"字段没被扫到：{sorted(declared)}"


def test_probe_goes_red_when_reads_scanned_nothing() -> None:
    """鉴别力：读取侧扫不到任何读取 ⇒ 必须报"扫描器瞎了"，**不许静默通过**。

    这是最经典的"看起来绿"：扫描器一旦瞎了，`reads - declared` 就是空集 ⇒ 零违规 ⇒ 通过。
    """
    declaration = f"class {TARGET_CLASS}:\n    ws_url: str = 'x'\n"
    violations = find_violations(declaration, "x = 1\n")
    assert violations and "扫不到任何" in violations[0], f"空扫描没被抓到: {violations}"


def test_probe_goes_red_when_target_class_is_gone() -> None:
    """鉴别力：声明侧找不到目标类 ⇒ 必须红（**类被改名/挪走时不许当通过**）。"""
    violations = find_violations("class Other:\n    x: int = 1\n", "y = server_config.ws_url\n")
    assert violations and "扫描不到" in violations[0], f"类不见了没抓到: {violations}"


def test_probe_ignores_writes() -> None:
    """假阳性防线：`server_config.x = 1`（Store 上下文）不是"读"，不该进读取面。"""
    declaration = f"class {TARGET_CLASS}:\n    ws_url: str = 'x'\n"
    write_only = "def f(server_config):\n    server_config.never_declared = 1\n"

    assert read_attributes(write_only) == set()
    # 只有写入 ⇒ 读取面为空 ⇒ 触发"扫描器瞎了"自检，而不是静默通过
    assert find_violations(declaration, write_only) != []
