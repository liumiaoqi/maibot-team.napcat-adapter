"""插件**自己仓**里的 session_id 镜像探针：本地算法 × 本地副本，逐行对拍。

## 这条探针在防什么

`session_id` 是宿主与插件之间的**跨进程键**：插件算出的 key 必须与宿主算出的**同一个**，
否则同一个人在两侧会被当成两个会话——而这种不一致**不会抛异常**，只会让消息落到错的会话里。

两侧算法是**两份实现**（插件跑独立进程、`import` 不到宿主 `src/`，只能在插件内复制算法，
见 `session_mapper.py` docstring），所以"两份一致"必须被**数据**钉住，不能靠"两处代码看起来一样"。
本探针的判据：**插件自己的算法**（`session_mapper.SessionIdMapper.calculate_session_id`）
跑**插件自己的那份副本**（`tests/fixtures/session_id_vectors.json`）⇒ 逐行 == 表里 `expected`。

⛔ **刻意不 import 主仓任何东西**：一旦 `from src.common.utils...` 进来，测的就成了
"主仓与主仓一致"——探针会在两份实现真正漂移时**依然全绿**（这是本探针存在的全部理由）。
副本路径也从**本文件**出发解析（`parents[1]` = 插件仓根），不写 `../../src/...`：
插件进程读不到宿主 `src/`，路径必须在插件仓内自足。

## 怎么跑

本仓**没有自己的 venv**（实测：无 `pyproject.toml` / `.venv`）⇒ 本机用主仓那套解释器跑：

```sh
uv run --project <主仓根> pytest plugins/maibot-team.napcat-adapter/tests/test_session_id_vectors_mirror.py -q
```

⚠️ **本探针现在跑在「宿主环境」下**（借用主仓的 `uv run`）：这足以证明
"插件算法 × 插件副本"对得上宿主表，但**不能**证明插件在**自己的依赖环境**里也跑得绿。
**插件仓自己的 CI 应在其自有环境跑本探针**（本仓 `_manifest.json` 声明了 sdk/host 版本面，
那份环境是插件的真实验收面；这里借来的解释器只是权宜）。

## 副本是哪来的

副本是**生成物**：由主仓 `scripts/gen_session_id_vectors.py --emit-plugin-copy` 与宿主表
**同一序列化器只渲染一次**写出（⇒ 逐字节相同是构造性质）。头注同款："改它请改生成器"。
手改副本一行 ⇒ 主仓 `--check` 的第②支（plugin-vs-host）红 ⇒ 本探针也会红。
"""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType
from typing import Any

import ast
import json

# 从**本文件**出发：parents[0] = tests/，parents[1] = 插件仓根（与 test_config_contract.py 同款）。
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
MAPPER_FILE = PLUGIN_ROOT / "session_mapper.py"
COPY_FILE = Path(__file__).resolve().parent / "fixtures" / "session_id_vectors.json"

# 副本里**必须**出现的 case（防"扫了个空也算绿"）。挑的是分属不同分支的几条：
# 私聊支 / 群支 / route 两段齐全 / 群支优先 / Unicode。表被换成一个空壳时会在这里红。
_REQUIRED_CASES = (
    "私聊-无account-无scope",
    "私聊-有account-有scope",
    "群聊-无account-无scope",
    "群聊-有account-有scope",
    "两id都给-群支优先-有account-有scope",
    "unicode-user_id中文",
)


def _load_mapper() -> ModuleType:
    """按**路径**加载插件的 `session_mapper.py`。

    不 `import plugins...`：目录名 `maibot-team.napcat-adapter` 含 `-`，不是合法包名；
    `plugin.py` 里走的是 `from .session_mapper import ...`（由插件框架按包加载），
    独立进程里读不到那条路。`session_mapper.py` 自身只依赖 `hashlib` / `typing`（stdlib）
    ⇒ 按路径加载不会牵出任何插件侧依赖。
    """
    spec = spec_from_file_location("_napcat_plugin_session_mapper", MAPPER_FILE)
    assert spec is not None and spec.loader is not None, f"加载不了插件算法文件：{MAPPER_FILE}"
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _vectors() -> list[dict[str, Any]]:
    return json.loads(COPY_FILE.read_text(encoding="utf-8"))["vectors"]


def _calculate(vector: dict[str, Any]) -> str:
    """把一条向量交给**插件自己的算法**（不是主仓的、也不是本文件重写的）。"""
    mapper = _load_mapper().SessionIdMapper
    return mapper.calculate_session_id(
        vector["platform"],
        user_id=vector["user_id"],
        group_id=vector["group_id"],
        account_id=vector["account_id"],
        scope=vector["scope"],
    )


def test_plugin_algorithm_matches_every_vector() -> None:
    """**主判据**：逐行 —— 插件算法(输入) == 副本里 expected。失败时列出全部对不上的 case。"""
    vectors = _vectors()
    assert vectors, f"副本里一条向量都没有（探针自己瞎了）：{COPY_FILE}"

    mismatches = []
    for vector in vectors:
        actual = _calculate(vector)
        if actual != vector["expected"]:
            mismatches.append(f"{vector['case']}：副本期望 {vector['expected']} / 插件实算 {actual}")
    assert not mismatches, (
        f"插件算法与副本期望对不上（共 {len(mismatches)} 条）——"
        "要么 session_mapper.py 改了没同步，要么副本没重生成：\n" + "\n".join(mismatches)
    )


def test_probe_sees_the_required_cases() -> None:
    """自检：副本里确实有那几条必需 case（防"副本被换成空壳/只留一条"也算绿）。"""
    cases = {vector["case"] for vector in _vectors()}
    missing = set(_REQUIRED_CASES) - cases
    assert not missing, f"副本缺这些 case（覆盖不足）：{sorted(missing)}；实有 {len(cases)} 条"


def test_probe_would_go_red_on_altered_expected() -> None:
    """鉴别力：把一个 expected 改一位 ⇒ 主判据形状的断言必须红（否则本探针等于没有）。"""
    vectors = _vectors()
    tampered = dict(vectors[0])
    tampered["expected"] = "0" * 32
    actual = _calculate(tampered)
    assert actual != tampered["expected"], (
        "改了 expected 还能对上 ⇒ 探针的比对根本没在读 expected（看起来绿）"
    )


def test_probe_would_go_red_on_drifted_algorithm() -> None:
    """鉴别力：给插件算法打一个"真算完再加后缀"的替身 ⇒ 主判据形状的断言必须红。

    这正是两份实现漂移的形状（插件侧算法被改动、副本没跟着变）。
    """
    vectors = _vectors()
    vector = vectors[0]
    real = _load_mapper().SessionIdMapper.calculate_session_id
    drifted = real(
        vector["platform"],
        user_id=vector["user_id"],
        group_id=vector["group_id"],
        account_id=vector["account_id"],
        scope=vector["scope"],
    ) + "x"
    assert drifted != vector["expected"], "打了后缀还能对上 ⇒ 主判据比不出算法漂移"


def _imported_modules(path: Path) -> set[str]:
    """一个 .py 文件里 `import x` / `from x import ...` 的全部 x（绝对模块名，不展开 `from .` 的相对项）。"""
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module)
    return modules


def test_probe_reads_plugin_local_copy_not_host_tree() -> None:
    """边界（约束 2）：副本路径**在插件仓内自足**、且算法文件不引主仓、不拉插件侧专有依赖。

    三条都具名断言：① 副本路径前缀是插件仓根、无 `..`；② `session_mapper.py` 的 import 面
    不含 `src`；③ 该 import 面**恰好**是 `{__future__, hashlib, typing}`（全 stdlib）——这条同时把
    "插件算法是否依赖插件侧专有依赖"摆到台面上（一变就红，不会静默分叉）。
    """
    relative = COPY_FILE.relative_to(PLUGIN_ROOT)
    assert relative.parts == ("tests", "fixtures", "session_id_vectors.json"), (
        f"副本必须在插件仓内自足：{relative}"
    )
    assert ".." not in relative.parts, "不许用 ../../src/... 往上爬（插件进程读不到宿主 src/）"

    # 用 AST 拿 import 面，不在源码里找字符串：本文件的注释/断言里**必须**提到那两个字才说得清规矩,
    # 字符串自查会连自己的说明一起抓（本条是真踩过的假阳性）。
    mapper_imports = _imported_modules(MAPPER_FILE)
    assert not [name for name in mapper_imports if name == "src" or name.startswith("src.")], (
        f"插件算法文件不许引主仓：{sorted(mapper_imports)}"
    )
    assert mapper_imports == {"__future__", "hashlib", "typing"}, (
        "插件算法文件的 import 面变了 —— 若引入第三方/插件侧依赖，"
        f"「插件在自有环境跑本探针」这条验收就会与宿主环境分叉：{sorted(mapper_imports)}"
    )
