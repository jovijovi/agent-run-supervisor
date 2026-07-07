<!-- Hero -->
<p align="center">
  <img src="docs/assets/branding/readme-hero.png" alt="Agent Run Supervisor" width="860">
</p>

<!-- Language links -->
<p align="center">
  <a href="README.md">English</a>
  &nbsp;·&nbsp;
  <b>简体中文</b>
</p>

<p align="center">
  <a href="https://github.com/jovijovi/agent-run-supervisor/actions/workflows/verify.yml">
    <img src="https://github.com/jovijovi/agent-run-supervisor/actions/workflows/verify.yml/badge.svg" alt="CI">
  </a>
  <a href="https://codecov.io/gh/jovijovi/agent-run-supervisor">
    <img src="https://codecov.io/gh/jovijovi/agent-run-supervisor/graph/badge.svg" alt="codecov">
  </a>
  <a href="https://pypi.org/project/agent-run-supervisor/">
    <img src="https://img.shields.io/pypi/v/agent-run-supervisor.svg" alt="PyPI">
  </a>
  <a href="https://pypi.org/project/agent-run-supervisor/">
    <img src="https://img.shields.io/pypi/pyversions/agent-run-supervisor.svg" alt="Python">
  </a>
  <a href="LICENSE">
    <img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT">
  </a>
</p>

<p align="center">
  一个小而<b>本地优先</b>的 Python 库与开发 CLI，用于监督<br>
  ACP/acpx 外部 AGENT 运行，并将运行器行为转化为<b>脱敏、可审计的证据</b>。
</p>

<p align="center">
  <code>Python&nbsp;≥&nbsp;3.11</code>
  &nbsp;·&nbsp;
  <code>仅标准库</code>
  &nbsp;·&nbsp;
  <code>本地优先</code>
  &nbsp;·&nbsp;
  <code>状态：0.1.0（alpha）</code>
</p>

---

## 它做什么

每一个通过 **ACP/acpx** 驱动外部 AGENT 的项目，最终都会重复实现同一套底层管道：拉起并照看
运行器子进程、编译权限策略、解析观测到的事件流、对退出行为进行分类，并在任何内容落盘之前完成
脱敏。一旦各自为政，每个调用方都会长出自己那份略不安全的副本。

`agent-run-supervisor` 将这套管道收敛为一个独立的**本地**监督层。调用方只需选择角色、提示词与
工作目录；监督层负责校验角色、编译默认拒绝（default-deny）的策略与免 shell 的 argv、监督运行、
将观测输出解析为归一化事件、判定一个**由监督层拥有的状态**，并写出**脱敏、权限受限的本地工件**。
调用方拿到的是可审计的证据 —— 而不是一团运行器生命周期代码。

本产品包含**两种执行模式**，二者均已为本地使用实现：一次性 exec 与本地持久会话生命周期
（创建/发送/状态/关闭/中止/列举 —— 见 [路线图](#路线图)）。它刻意**不是** Sachima、不是 Gateway
插件、不是 IM 适配器，也不是常驻守护进程（daemon），并且永不给出业务结论（`business_verdict`
始终为 `null`）。

## 工作原理

<p align="center">
  <img src="docs/assets/diagrams/how-it-works.zh-CN.svg" alt="agent-run-supervisor 校验角色、监督 ACP/acpx、观测外部 AGENT 并写出脱敏本地工件的工作原理" width="900">
</p>

四条原则保持其诚实：

- **是监督者，不是业务裁判。** 运行器/协议层面的完成永远不等于业务结论；`business_verdict`
  始终为 `null`，归调用方所有。
- **默认可审计。** 运行会产出确定性的、脱敏的工件，并采用受限权限（目录 `0700`、文件 `0600`、
  最终工件原子写入）。
- **不确定即失败关闭（fail closed）。** 无效角色、cwd 越出允许根、stdout 畸形、协议漂移、权限
  被拒、看门狗超时，都会判定为确定性的非成功状态 —— cwd 无效时**根本不会**创建任何工件。
- **诚实的安全声明。** `allowed_roots` 只校验 cwd/配置的**意图**，它**不是**操作系统/文件系统沙箱。

不在范围内 —— 属于调用方/平台领域：公网入口、真实 IM 投递、Gateway 生命周期、生产配置写入、
默认开启/常驻行为、`@all` 扇出，以及 agent 间自动路由。

## 安装与使用

**PyPI：** [`agent-run-supervisor==0.1.0`](https://pypi.org/project/agent-run-supervisor/0.1.0/) ·
**发布说明：** [GitHub `v0.1.0`](https://github.com/jovijovi/agent-run-supervisor/releases/tag/v0.1.0)

```bash
pip install agent-run-supervisor
```

或从源码检出运行（见 [开发](#开发)）。

### CLI

```bash
# 校验一个 AgentRoleSpec（JSON）并打印其稳定的 role hash
agent-run-supervisor validate-role <role-file>.json

# 将观测到的 acpx stdout 回放（确定性，不启动任何 AGENT）
# 源码检出：可使用仓库 fixtures（见下方说明）。
# PyPI 安装：传入自有 .ndjson 路径，或使用 `doctor` 做内置 fixture 回放。
agent-run-supervisor replay <events.ndjson>

# 探测本地就绪状态（只读，绝不启动 AGENT）
agent-run-supervisor doctor

# 干跑（dry-run）：编译策略 + argv 并持久化预览工件，不启动任何进程
agent-run-supervisor run \
  --role <role-file>.json --prompt-file <prompt>.txt --no-real-run

# 真实的一次性 exec：在角色策略下监督一次本地 `acpx exec`
#（需要本地具备 acpx/Node；仅启动一个显式、本地的 AGENT）
agent-run-supervisor run \
  --role <role-file>.json --prompt-file <prompt>.txt

# 本地持久会话生命周期（角色须使用持久会话策略）：
# 创建 → 发送轮次 → 状态 → 关闭/中止。create/send/status/close/abort 会驱动一次真实的本地
# acpx 会话，需要本地具备 Node + acpx；`session list` 为本地只读枚举，不启动任何 AGENT。
agent-run-supervisor session create \
  --role <role-file>.json --session-id <id>
agent-run-supervisor session send \
  --role <role-file>.json --session-id <id> --prompt-file <prompt>.txt
agent-run-supervisor session status \
  --role <role-file>.json --session-id <id>
agent-run-supervisor session close \
  --role <role-file>.json --session-id <id>
agent-run-supervisor session abort \
  --role <role-file>.json --session-id <id>
agent-run-supervisor session list

# 规划或执行本地工件的保留/清理（默认 dry-run；--apply 才真正删除）
agent-run-supervisor cleanup
```

> **Fixture 回放路径：** `fixtures/acpx-0.12.0/...` 仅存在于 **git 仓库** 中。
> PyPI wheel 仅捆绑 `doctor` 冒烟所需的最小 fixture，不含完整 fixture 树。
> 在检出目录中可运行：
> `agent-run-supervisor replay fixtures/acpx-0.12.0/success-codex-sentinel/stdout.ndjson`

未安装时从源码检出运行，将 `agent-run-supervisor` 替换为
`PYTHONPATH=src python3 -m agent_run_supervisor`。

```bash
# 克隆并进入仓库
git clone https://github.com/jovijovi/agent-run-supervisor.git
cd agent-run-supervisor

# 示例：从检出运行 validate-role（无需安装）
PYTHONPATH=src python3 -m agent_run_supervisor validate-role <role-file>.json
```

### Codex/acpx 冒烟 helper

如果要显式检查本地 Codex 链路，并同时覆盖两个受监督表面 —— 先跑一次性 exec，
再跑两轮持久会话 —— 使用维护好的 helper：

```bash
python3 scripts/smoke_codex_acpx.py --model 'gpt-5.5[xhigh]'
```

该 helper 会创建临时 no-tool 角色，要求 Codex 回复精确 sentinel，校验
`business_verdict = null`，关闭持久会话，并默认清理临时工件（`--keep-artifacts`
会保留 temp scratch/runs/sessions 目录）。它刻意使用 `runner.acpx_binary = null`，
因此会走现有编译器的固定 `npx -y acpx@0.12.0` 路径。

Codex ACP 模型名要使用 ACP session 广告出来的精确 ID，例如 `gpt-5.5[xhigh]`、
`gpt-5.5[high]` 或 `gpt-5.4-mini[medium]`。裸 ID（如 `gpt-5.5`）可能被拒绝并报
`the ACP agent did not advertise that model`；helper 会在启动任何东西前拒绝这种写法。

安装本包后（`pip install -e .`），同样的接口也可通过 `agent-run-supervisor <command> …`
控制台脚本使用。

运行工件写入 `.agent-run-supervisor/runs/<run_id>/` —— 包括脱敏后的 prompt/env/argv、生成的
策略、观测 stdout（NDJSON）、归一化事件、stderr、`result.json`（`business_verdict = null`）以及
`redaction-report.json`。持久会话工件写入 `.agent-run-supervisor/sessions/<session_id>/`（本地
记录、脱敏的 `management/` 摘要，以及每次 send 一个脱敏的 `turns/<turn_id>/` 目录）。`cleanup`
命令会规划并（仅在 `--apply` 时）删除过期的运行/会话工件，删除范围被限制在解析出的
`.agent-run-supervisor` 根目录内，且绝不触碰处于打开/活动锁定状态的会话。

## 库用法（Library usage）

本包既是 **Python 库** 也是 CLI。程序化集成时，优先使用通用本地调用方边界
（[`caller.py`](src/agent_run_supervisor/caller.py)，设计细节见
[`docs/design/technical-solution.md`](docs/design/technical-solution.md) §3.10）。

**安装：**

```bash
pip install agent-run-supervisor
```

**推荐 API：** `invoke_caller` + `CallerInvocationSpec`。监督层返回由监督层拥有的状态与脱敏工件；
`business_verdict` 始终为 `null` —— 业务成败由调用方解释。

```python
from importlib.metadata import version

from agent_run_supervisor.caller import CallerInvocationSpec, invoke_caller
from agent_run_supervisor.role import load_role

print(version("agent-run-supervisor"))  # 例如 0.1.0

role = load_role("reviewer.json")

# 一次性 exec（非 dry-run 时会启动真实本地 AGENT）
result = invoke_caller(
    CallerInvocationSpec(
        mode="exec",
        role=role,
        prompt="用 plain language 总结 diff。",
        cwd="/path/to/repo",
    )
)
print(result.supervisor_status)  # 例如 "completed"
print(result.result)             # result.json 载荷（dict）
print(result.run_dir)            # 脱敏工件目录
assert result.business_verdict is None

# 仅 dry-run 编译/预览 —— 无子进程、无 AGENT
preview = invoke_caller(
    CallerInvocationSpec(
        mode="exec_dry_run",
        role=role,
        prompt="仅预览。",
        cwd="/path/to/repo",
    )
)
print(preview.artifact_dir)
```

**持久会话**（角色须使用 `strategy: persistent`）：

```python
session_id = "my-local-session"

invoke_caller(
    CallerInvocationSpec(
        mode="session_create",
        role=role,
        session_id=session_id,
        cwd="/path/to/repo",
    )
)
turn = invoke_caller(
    CallerInvocationSpec(
        mode="session_send",
        role=role,
        session_id=session_id,
        prompt="接续上一轮。",
        cwd="/path/to/repo",
    )
)
print(turn.session_dir)
```

支持的模式：`exec`、`exec_dry_run`、`session_create`、`session_send`、`session_status`、
`session_close`。

**更低层接口**（进阶）：`SupervisorRunner`、`SessionRuntime`、`parse_acpx_stdout_bytes`。
测试中可注入假 subprocess 执行器 —— 见 [`tests/test_caller.py`](tests/test_caller.py)。

**参考调用方：** [`hermes_caller`](src/agent_run_supervisor/hermes_caller/) 展示带调用方 verdict
与视图模型的文档检查集成（仅本地/离线）。

### 实时进度轮询（进阶）

在 **exec** 或 **session_send** 期间，监督层会在子进程仍在运行时写出 `progress.json` 与带
`seq` 的 `normalized-events.jsonl`；运行结束后 **`result.json` 仍是最终权威**。

通过 [`hermes_caller.events`](src/agent_run_supervisor/hermes_caller/events.py) 轮询**结构字段**
（不含 raw agent 文本）：

```python
from agent_run_supervisor.caller import CallerInvocationSpec, invoke_caller
from agent_run_supervisor.hermes_caller.events import load_progress, read_event_page
from agent_run_supervisor.role import load_role

role = load_role("reviewer.json")
result = invoke_caller(
    CallerInvocationSpec(
        mode="exec",
        role=role,
        prompt="总结 diff。",
        cwd="/path/to/repo",
    )
)

# 运行中或结束后 —— 仅结构字段
snap = load_progress(result.run_dir)
if snap:
    print(snap.state, snap.last_seq, snap.event_count)

# 按 seq 游标分页读取 normalized-events.jsonl
page = read_event_page(result.run_dir, after_seq=0, limit=50)
for event in page.events:
    print(event["seq"], event.get("kind"), event.get("text_length"))
```

**边界说明：**

- 适用于 **exec** 与 **session_send** 产生的 run/turn 工件目录。
- `invoke_caller` **不**暴露 `session abort` / `session list` —— 请用 CLI 或更低层
  `SessionRuntime`。
- 仅本地只读 API —— 无 websocket、long-poll 服务或 IM 投递（见 roadmap §5 非批准项）。
- Schema 细节：[`docs/design/result-event-schema.md`](docs/design/result-event-schema.md) §4。

**Schema 稳定性：** `0.1.0` 为 alpha 版本；生产集成请 pin 版本，并阅读
[`docs/design/result-event-schema.md`](docs/design/result-event-schema.md) 了解 `result.json` 字段。

## 环境要求

| 需求 | 要求 |
|---|---|
| 运行时 | **Python ≥ 3.11**，仅标准库 —— 零第三方运行时依赖。 |
| 测试（可选） | `pytest >= 8, < 10`（`dev` 额外依赖）。 |
| 真实 AGENT 运行 / 会话轮次 | 本地具备 **Node + acpx + 目标 AGENT CLI** —— 在不带 `--no-real-run` 的 `run`，以及真实的 `session create/send/status/close/abort` 轮次与管理命令时需要。Codex 冒烟 helper 还需要 `npx`，以及通过 `CODEX_PATH` 或 `PATH` 可用的 Codex CLI。 |
| 不启动 AGENT 的命令 | `validate-role`、`replay`、`doctor`、`run --no-real-run`、`session list` 与 `cleanup`（dry-run）**无需** Node/acpx，且**不启动**任何 AGENT。 |

## 开发

推荐使用 [uv](https://docs.astral.sh/uv/) 获得可复现的开发环境。根目录 [`Makefile`](Makefile)
提供快捷命令：

```bash
git clone https://github.com/jovijovi/agent-run-supervisor.git
cd agent-run-supervisor
make sync      # uv sync --extra dev --extra release
make verify    # 完整本地关卡（与 CI 一致）
make build     # sdist/wheel + twine check
make smoke     # build + 已安装 wheel 冒烟
make clean     # 清理构建产物、缓存与本地临时数据
make help      # 列出全部 target
```

无 Make 时的等价命令：

```bash
uv sync --extra dev --extra release
./scripts/verify_local.sh
```

`make verify` / `./scripts/verify_local.sh` 是单一本地关卡入口 —— 与 CI 及
[`docs/roadmap/current-status.md`](docs/roadmap/current-status.md) §6 对齐（测试、doctor/replay
冒烟、文档索引/漂移、静态安全扫描、build/twine 检查与已安装 wheel 冒烟）。

**pip 回退**（无 uv 时）：

```bash
pip install -e '.[dev,release]'
python3 -m pytest -q
```

## 发布

**当前版本：** [`0.1.0` 已发布至 PyPI](https://pypi.org/project/agent-run-supervisor/0.1.0/) 与
[GitHub Releases](https://github.com/jovijovi/agent-run-supervisor/releases/tag/v0.1.0)，由 tag
触发的 GitHub Actions Trusted Publishing 完成（仓库内无 API token）。

**后续发布**（维护者）：

```bash
make verify              # 或 ./scripts/verify_local.sh
# 在 pyproject.toml 与 CHANGELOG.md 中 bump 版本，合并到 main
make release-tag         # 打印 git tag vX.Y.Z && git push 命令
agent-run-supervisor doctor   # PyPI 安装后验证
```

Trusted Publishing 使用 workflow [`release.yml`](.github/workflows/release.yml) 与 GitHub
environment `pypi`。操作清单见
[`docs/plans/2026-07-06-p3-engineering-basics.md`](docs/plans/2026-07-06-p3-engineering-basics.md)。

**TestPyPI 试发**（本地用环境变量传 token，切勿提交到 git）：

```bash
export TWINE_USERNAME=__token__
export TWINE_PASSWORD=pypi-...    # TestPyPI token
make release-test                 # verify + 上传到 TestPyPI

pip install --index-url https://test.pypi.org/simple/ \
            --extra-index-url https://pypi.org/simple/ \
            agent-run-supervisor==0.1.0
agent-run-supervisor doctor
```

## 质量与测试指标

保持监督层诚实的本地关卡（在仓库根目录执行 `./scripts/verify_local.sh`，或逐步执行）：

| 指标 | 证据 |
|---|---|
| 完整本地关卡 | `make verify` 或 `./scripts/verify_local.sh` —— 与 CI verify workflow 对齐。 |
| 单元 / 集成测试 | **完整 pytest 套件** —— `uv run pytest -q`（当前本地验收：full suite passing）。 |
| acpx 契约 | acpx `0.12.0` 夹具 + 校验器 —— `scripts/validate_contract_fixtures.py fixtures/acpx-0.12.0`。 |
| 导入 / 语法冒烟 | `python -m compileall -q src scripts tests`。 |
| Doctor（只读） | `… doctor` 绝不启动 AGENT（`launched_real_agent = false`）。 |
| 包检查 | `python -m build` + `twine check dist/*`，再用已安装 wheel 运行 `agent-run-supervisor doctor` 冒烟。 |
| 安全工件 | 脱敏工件 · `business_verdict = null` · EventStore `0700`/`0600` 原子 NDJSON。 |

```bash
uv sync --extra dev --extra release
./scripts/verify_local.sh
```

## 路线图

仅高层方向 —— 完整的阶段状态、验收与非批准项见
[`docs/roadmap/current-status.md`](docs/roadmap/current-status.md) 与
[`docs/roadmap/features.md`](docs/roadmap/features.md)。

- **已完成 —— 基础 + 两种执行模式。** 角色/策略/解析器/存储基础、真实的本地 `acpx exec` 监督
  （角色绑定、外层看门狗、终止元数据），以及本地持久会话生命周期（创建/发送/多轮恢复/状态/
  关闭/中止/列举、锁、过期锁恢复）均已实现，并就本地使用而言已收口。
- **已完成 —— 加固 + 本地调用方集成。** 完整只读 doctor 探针集、受限的工件保留/清理、有文档的
  结果/事件 schema、进程存活性崩溃恢复、通用本地调用方边界，以及本地/离线 Hermes 调用方 +
  离线 Feishu 视图模型适配器均已合并。
- **已完成 —— 发布工程（P3）。** uv 开发工作流、`make verify` / `verify_local.sh`、CI 对齐、
  [PyPI `0.1.0`](https://pypi.org/project/agent-run-supervisor/0.1.0/) 与 tag 触发的 Trusted
  Publishing（`release.yml`，经 PR #40 合并）。
- **待办 —— 更深层加固（尚未开始）。** `npx` 严格离线约束、更强的脱敏/DLP 与调用方白名单，以及
  锁释放审计轨迹，仅作为待办记录。任何实时/平台集成（真实 Feishu/IM 投递、Sachima、Gateway
  生命周期、公网入口）仍不在范围内，须经单独批准。

## 许可证

© `agent-run-supervisor` 作者。以 **[MIT](https://opensource.org/license/mit)** 许可证发布
（`pyproject.toml` 中 `license = "MIT"`，并包含 [`LICENSE`](LICENSE)）。**Alpha 版本**
`0.1.0` 已发布至 [PyPI](https://pypi.org/project/agent-run-supervisor/0.1.0/)；接口与结果 schema
在稳定 `1.0.0` 之前仍可能变动。

<p align="center">
  <img src="docs/assets/branding/logo-mark.png" alt="agent-run-supervisor 标志" width="72" height="72">
  <br>
  <sub><b>agent-run-supervisor</b> —— 是监督者，不是业务裁判。</sub>
  <br>
  <sub><a href="README.md">English README</a></sub>
</p>
