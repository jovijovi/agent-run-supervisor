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
  <img src="https://img.shields.io/badge/python-3.11%2B-blue.svg" alt="Python 3.11+">
  <a href="LICENSE">
    <img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT">
  </a>
</p>

<p align="center">
  一个小而<b>本地优先</b>的外部编码 AGENT 监督层。<br>
  一个本地守护进程，每次运行一个进程，产出<b>脱敏、可审计的本地证据</b>。
</p>

---

## 目录

[这是什么](#这是什么) ·
[文档描述的目标与已发布的代码](#文档描述的目标与已发布的代码) ·
[工作原理](#工作原理) ·
[环境要求](#环境要求) ·
[安装](#安装) ·
[升级](#升级) ·
[卸载](#卸载) ·
[运行 `arsd`](#运行-arsd) ·
[Agent 注册表](#agent-注册表) ·
[用 Python 调用](#用-python-调用) ·
[ACP 协议支持](#acp-协议支持) ·
[支持哪些 agent](#支持哪些-agent) ·
[由你安装的 agent 运行时](#由你安装的-agent-运行时) ·
[运维可见的变化](#运维可见的变化) ·
[保证与边界](#保证与边界) ·
[开发](#开发) ·
[参与贡献](#参与贡献) ·
[许可证](#许可证)

## 这是什么

凡是要驱动外部编码 AGENT 的程序，最后都会重复造同一套底层管道：拉起并照看 agent 进程、决定
agent 能碰什么、读取协议事件流、判断运行到底是怎么结束的，以及在任何东西落盘之前把敏感信息清理
干净。各写各的，结果就是每个调用方都长出一份略不安全的副本。

**Agent Run Supervisor（ARS）** 把这件事收敛成一个独立的本地层。你的应用提交一次运行 —— 用哪个
已注册 agent、哪个模型、哪个工作区、什么提示词 —— 剩下的交给 ARS：准入请求、按运维方注册的那条
命令只拉起一个受监督的 agent 进程、在默认拒绝（default-deny）策略下中介每一次权限请求、把 agent
的输出归一化为有序事件、判定一个由监督层拥有的状态，并以受限权限写出脱敏工件。

你拿回来的是**可审计的证据**，而不是一团进程生命周期代码 —— 足以随时答得出「它想做什么、被允许
做什么、最后到底怎么结束的」。

**ARS 监督的是它并不拥有的外部 AGENT。** 它不安装、不打包、不复制、不冻结、不晋级、不钉版本、
不托管、也不对 agent 及其 ACP 适配器、家目录、凭据库、插件树、缓存或配置做任何完整性证明。这些都
由你用自己的包管理器安装与升级。ARS 拥有的是**它启动的进程**，你拥有的是**它启动的软件**。

ARS 只报告**技术监督事实**，业务结论归你的应用。

## 文档描述的目标与已发布的代码

> **在照抄下文任何命令之前先读这一段。** 本 README 描述的是 **agent 注册表边界**：一个由运维方拥有
> 的注册表文件、`--agents-file`，以及 `agents` / `run inspect` 运维命令面。这是本项目当前被跟踪的
> 架构，也是分阶段工作的目标。
>
> 但**已发布的 `v0.5.x` 代码线实现的是更早的工件/Binding 架构**：另一个必填守护进程参数、一个由运维
> 方拥有并需要晋级 generation 的 Binding root，以及被冻结的工件身份。该架构**作为目标已退役**，因此
> 本文不再记录它的操作方式。如果你今天运维的是 `v0.5.x` 部署，请以该版本自己的发布说明
> （[`CHANGELOG.md`](CHANGELOG.md)）和冷归档
> [`docs/archive/binding-era-2026-07/`](docs/archive/binding-era-2026-07/README.md) 为准 —— 本 README
> 不描述如何操作它。
>
> 具体地说：注册表文件、环境值防护，以及 `api_version` 2 **目前都还不可运行**。当前差距与收敛顺序记录
> 在看板 [`docs/roadmap/current-status.md`](docs/roadmap/current-status.md)。这里的任何文档变更都不会
> 部署、重启或迁移任何东西。

## 工作原理

<p align="center">
  <img src="docs/assets/diagrams/how-it-works.zh-CN.svg" alt="受信任的本地调用方通过 arsd Unix 域套接字提交；arsd 完成对端认证与准入；ars-core 以一个 RunTask 通过 Native ACP 驱动已注册的外部 AGENT；归一化事件、状态与脱敏本地工件沿返回路径回到调用方" width="900">
</p>

主路径完全在本地：

1. **你的应用**连接 `arsd` —— 那个小而非特权的监督守护进程。
2. **`arsd` 监听一个 Unix 域套接字** —— `0700` 目录里的 `0600` 套接字。没有 TCP、没有 root、
   没有公网入口。
3. **启动时，`arsd` 只解析你的 agent 注册表一次**，随后做 reconciliation，之后才绑定套接字。注册表
   只要有任何缺陷，就会在写入任何状态之前拒绝监听。
4. **完成对端认证与请求准入。** `arsd` 从套接字读取对端凭据并映射为 principal，再用你自己提供的
   `request_id`（同时就是幂等键）做准入。Run 与 Session 按 owner 归属：只有拥有它的调用方才能
   查询、跟随、取消或关闭。
5. **`ars-core` 执行运行。** 一个进程内 `RunTask` 独占一个受监督的 agent 进程和一条 Native ACP
   连接，由准入时就封存下来的不可变运行规格驱动。
6. **agent 就是你注册的那条命令**，按声明原样启动 —— 协议上不存在任意 command/argv/环境变量透传，
   服务期间也不会再读一次注册表。

返回方向上，你会通过同一个套接字拿到归一化、按 `seq` 有序的事件与一个由监督层拥有的状态，以及落
在本地磁盘上的脱敏工件。

### 四个权威层

启动语义与部署事实从不混在一起，也没有第五层：

| 层 | 拥有者 | 承载 |
|---|---|---|
| ACP 兼容 profile | ARS 源码，经评审 | 如何对一类 agent 讲 ACP：协议 major、必需与禁止能力、会话语义、选择器 ID 约定、基础环境白名单、权限中介语义 |
| **agent 注册表条目** | **运维方** | 在这里哪条命令就是那个 agent、它的 argv、环境声明、选择器 ID 提示、能力收窄，以及可选的连续性 epoch |
| 每次运行封存的 spec + launch 快照 | 一次运行 | profile × 条目 × 请求的一次性投影，在派生之前完成 |
| 观测证据 | 一次运行 | 解析到与观测到了什么 —— 只记录，绝不作为门禁 |

这四层调用方一个都挑不了。profile 不含任何路径、版本、摘要、模型字面量或部署事实；条目不含任何能力
要求、协议版本、中介键值对、摘要或 transport。准入**在内存中、零文件系统访问**地按启动快照解析
agent，封存结果后再也不重读：派生、终结与 reconciliation 根本没有读注册表的代码路径。

### 两个协议，两条版本线

ARS 夹在两个独立演进的协议之间，谁也推不出谁：

- **ACP Protocol v1** —— **下游**的 Agent Client Protocol，由 ARS 与外部 AGENT 进程之间通过 stdio
  JSON-RPC 讲。
- **`arsd` API** —— **上游**由 ARS 自己拥有的协议，即你的应用与 `arsd` 之间那一层。每一帧都带
  `api_version`；未知版本一律拒绝，而不是猜测。

随注册表边界，`arsd` API 升到 **2**，因为主选择器的含义变了：`profile_id` 不再选择启动方式，改由
`agent_id` 选择；而静默地按新语义重新解释一个旧帧，正是本项目禁止的那种无声回退。在排空窗口内，只有
`submit` 在 `api_version: 1` 上被拒绝，其余七个操作照常接受 —— 包括 `server_info`，旧调用方正是靠它
才能发现「自己必须升级」。关闭排空是另一套机制且不变：一旦开始关闭，任何帧都以 `SHUTTING_DOWN` 应答。

设计细节见 [`docs/design/architecture.md`](docs/design/architecture.md)。

## 环境要求

| 需求 | 要求 |
|---|---|
| 运行时 | **Python ≥ 3.11**，仅标准库 —— 零第三方运行时依赖。 |
| 驱动真实 agent | `native` 额外依赖，钉住官方 ACP 客户端库（`agent-client-protocol==0.11.1`）。基础安装可正常导入，只在真正用到 SDK 时才失败。 |
| 运行 `arsd` | Linux，且有可放置 AF_UNIX 套接字的 POSIX 用户会话，另需你提供 supervisor root、一个 agent 注册表文件与至少一条 caller 映射。 |
| 崩溃遏制 | 用户级 service manager 的 cgroup，以及带 pidfd 支持的 CPython 构建。 |
| 运行 agent | 由你在本地安装好的 agent，以及一条指明其命令的注册表条目。见[由你安装的 agent 运行时](#由你安装的-agent-运行时)。 |

## 安装

从 PyPI 安装：

```bash
# 基础安装
pip install agent-run-supervisor

# 推荐：带上驱动真实 agent 所需的 ACP 客户端库
pip install 'agent-run-supervisor[native]'
```

使用 [uv](https://docs.astral.sh/uv/)：

```bash
uv pip install 'agent-run-supervisor[native]'
```

从源码检出安装 —— 这样才能拿到测试套件、fixture 与开发工具：

```bash
git clone https://github.com/jovijovi/agent-run-supervisor.git
cd agent-run-supervisor

# 运行时仅依赖标准库，检出后无需安装即可直接使用
PYTHONPATH=src python3 -m agent_run_supervisor doctor

# 或以 editable 方式安装
pip install -e '.[dev,native]'
```

ARS 不会隐式启动任何 agent。`doctor`、`replay`、`--print-service-unit` 与 `run inspect` 对 ARS 与运维
方状态而言都是只读的。`agents doctor` 是唯一**会**启动外部子进程的诊断命令 —— 而那个子进程会写它自己
的、由 agent 拥有的状态，本文不对此含糊其辞。

## 升级

```bash
pip install --upgrade 'agent-run-supervisor[native]'
```

查看当前安装的版本：

```bash
python3 -c "import agent_run_supervisor as a; print(a.__version__)"
```

新的包版本从不重启正在运行的守护进程，也从不改动运维方存储。升级后是否重启 `arsd` 由你决定。

**跨越边界重置的升级。** 已退役的工件/Binding 运维输入 —— 它那个必填守护进程参数、带已晋级
generation 的 Binding root，以及 `runtime-binding` 命令组 —— 被一个注册表文件、`--agents-file` 与
`agents` 命令取代。这个变化是失败关闭而不是靠猜：旧的 service unit 不会静默地继续可用。它同时会**一次性
终结所有活动 Session**，因为在已退役身份模型下创建的 Session 会以稳定错误码被拒绝重新加载，同时仍然可读。
旧的工件根与 Binding root 只是不再被引用 —— ARS 从不删除它们，是否删除是你另外的决定。

ARS 不会替你迁移运维方存储。完整历史见 [`CHANGELOG.md`](CHANGELOG.md)。

## 卸载

```bash
pip uninstall agent-run-supervisor
```

移除包不会动你的本地状态与运维方存储。按以下顺序清理，只保留你还需要的：

```bash
# 1. 停止并移除 user service（用你当初安装时的 unit 名）
systemctl --user disable --now <your-unit>.service
rm -f ~/.config/systemd/user/<your-unit>.service
systemctl --user daemon-reload

# 2. 删除任何东西之前先查看本地工件 —— 默认 dry-run
agent-run-supervisor cleanup --help

# 3. 移除 supervisor root（证据、Session、套接字目录）
rm -rf <supervisor-root>        # user service 默认：~/.local/share/agent-run-supervisor

# 4. 清理检出目录里的构建产物与缓存
make clean
```

你的 **agent 注册表文件**与**你自己安装的那些 agent** 都由运维方拥有、位于 ARS 之外，请单独、有意识地
移除。

## 运行 `arsd`

`arsd` 是一个 module 入口，不是控制台脚本：

```bash
# 查看选项与边界（只读）
python3 -m agent_run_supervisor.arsd --help

# 把 user 作用域的 systemd unit 渲染到 stdout 后退出。
# 纯文本：不检查权限、不做 reconciliation、不绑定套接字 —— 不安装、不启用、不启动任何东西。
# 这里同样必须给 --agents-file，渲染出的 unit 才不会静默漏掉它；该路径只是 argv 数据，不会被访问。
python3 -m agent_run_supervisor.arsd \
  --agents-file <agents-file> \
  --print-service-unit

# 启动守护进程
python3 -m agent_run_supervisor.arsd \
  --supervisor-root <supervisor-root> \
  --agents-file <agents-file> \
  --caller-mapping <UID>:<principal_id>:<owner>:<namespace>
```

未安装时从检出运行，请加前缀 `PYTHONPATH=src`。

守护进程模式必须提供 `--supervisor-root`、`--agents-file` 和至少一条 `--caller-mapping` ——
**零条映射会拒绝监听**，以 root 身份启动同样会被拒绝。`--socket` 默认取
`$XDG_RUNTIME_DIR/agent-run-supervisor/arsd.sock`，否则退化为 `<supervisor-root>/arsd/arsd.sock`；
`--max-concurrent-runs`、`--max-connections`、`--log-level` 约束其余行为。

caller 映射、套接字路径与注册表路径都属于部署侧的值，应放在权限为 `0600` 的 unit 文件里，绝不进入仓库。

启动顺序是严格的：**只解析注册表一次 → reconciliation → 绑定**。重启后守护进程只对持久事实做
reconciliation，而且它比一个宽容的读取器更严格：损坏的终态记录、无法归属的不确定性，或一条没有对应
spec 的 launch 记录，都会**拒绝监听**而不是猜测。可能已派发、但没有可信终态结果的 Run 会落到
`unknown` / `quarantined` / `retryable=false`，且永不重发提示词。

## Agent 注册表

`--agents-file` 指向**一个由运维方拥有的 TOML 文件，在守护进程启动时恰好读一次**，读入一份不可变的
内存快照。你以原子方式替换它；替换在**下一次守护进程启动**时生效。

```toml
schema_version = 1

# 一个符合标准的原生 ACP agent —— 最常见的情况。
[agents.native-agent]
profile   = "standard-native-acp-v1"
command   = "some-agent"          # 按 PATH 解析的裸名字，与你键入的完全一致
args      = ["acp"]
mediation = "ask-privileged-tool-families-v1"   # 选择一份源码拥有的绑定

# 一个经独立安装的 ACP 适配器命令接入的 agent。
# profile 相同：适配器是部署事实，不是源码常量。
[agents.adapter-backed-agent]
profile = "standard-native-acp-v1"
command = "/home/<service-user>/.local/bin/<some-acp-adapter>"
env_passthrough = ["SSH_AUTH_SOCK", "SOME_PROVIDER_TOKEN"]
env_overlay     = { SOME_AGENT_HOME = "/home/<service-user>/.some-agent", NO_BROWSER = "1" }
forbidden_capabilities = ["terminal"]
```

上面每个值都是**占位符**。完整且封闭的字段集是 `profile`、`command`、`args`、`mediation`、
`env_passthrough`、`env_overlay`、`model_selector`、`effort_selector`、`forbidden_capabilities` 与
`session_epoch` —— 除此以外没有别的，任何层级上的未知键都会被拒绝。`transport` 会作为未知键被拒绝：
v1 按定义就是 stdio。

**ARS 检查什么、不检查什么。** 它解析注册表路径、跟随符号链接，并要求解析后的目标是一个不可被 group
或 world 写入的普通文件 —— 这只是 ARS 拒绝听命于一个人人可改的文件，且**仅限于它自己的配置文件**。
它对 `command`、对其祖先目录、以及对 agent 后续加载的任何东西，**都不做所有者、权限位、祖先、符号链接
或摘要检查**。

**你的命令按声明原样启动。** `argv[0]` 逐字节就是你声明的那个字符串；裸名字由子进程投影出的 `PATH`
按普通查找定位。因此 shim、符号链接农场、按包相对解析，以及 agent 自己的自更新逻辑都照常可用。这里没有
任何预检解析：exec 失败会被分类为 `COMMAND_NOT_FOUND`、`COMMAND_NOT_EXECUTABLE` 或 `SPAWN_FAILED`，
它们读起来就是普通的配置错误，不是安全拒绝。

**只读一次的代价。** **在同一条已注册命令背后升级 agent** —— 同一个 PATH 名字、重新指向的 shim、重装
后的符号链接目标、同一绝对路径下的新版本 —— 代价是**零**：不用重启、不用重新验收，已有 Session 仍会通过
真实的 `session/load` 复用。**编辑注册表**的代价是一次守护进程重启，也就意味着要先排空在途的 Run。那次
重启是一次服务动作，**不是晋级**：没有度量、没有 manifest、没有验收凭据、不用重跑 canary，也**不会让任何
Session 失效**，因为没有任何 Session 身份字段派生自注册表字节。

运维侧是一套独立的 CLI：

```bash
agent-run-supervisor agents validate --agents-file <path>
agent-run-supervisor agents doctor   --agents-file <path> [--agent <agent-id>]
agent-run-supervisor run inspect     --run-dir <native-run-dir>
```

`agents validate` 解析文件、检查形状与边界，并施加与守护进程启动时**完全相同**的中介键冲突检查 ——
只打印条目 id、计数、环境变量**名字**、来源类别与规则结论，绝不打印取值。`agents doctor` 为每个 agent
跑一次零提示词的 ACP `initialize`，并报告投影出的环境变量**名字**集合 —— 这正是你定位那个导致「在我
shell 里能跑、在 ARS 下不行」的 `PATH` 缺口的方式。`run inspect` 报告单次运行的证据。

这里没有 `promote`、没有 `rollback`、也没有 `--force`。其中没有任何命令会安装软件、修改 unit 文件、
提升权限或重启守护进程。

完整契约 —— 语法、边界、每一个拒绝码、环境层与优先级、`session_epoch`，以及诚实的限制 —— 见
[`docs/design/agent-registry.md`](docs/design/agent-registry.md)。

## 用 Python 调用

[`ArsdClient`](src/agent_run_supervisor/arsd/client.py) 是受支持的调用方边界：显式连接、
context-managed，绝不静默重连，也绝不重放请求。

```python
from agent_run_supervisor.arsd.client import ArsdClient

socket_path = "<XDG_RUNTIME_DIR>/agent-run-supervisor/arsd.sock"

with ArsdClient(socket_path) as client:
    client.server_info()                      # 协议/版本握手事实

    ack = client.submit(                      # 调用方自有的 request_id = 幂等键
        request_id="my-caller-request-id",
        payload={
            "request": {...},                 # 带版本的 AgentRunRequest（见下）
            "prompt_text": "用平实的语言总结这个 diff。",
            "workspace_root": "/path/to/bound/workspace",
        },
    )
    run_id = ack["run_id"]

    client.run_status(run_id)                          # accepted → progress → 唯一终态结果
    client.run_events(run_id, from_seq=0, limit=100)   # 有界、按 seq 有序的分页
    client.run_cancel(run_id)                          # 协作式取消；绝不改写终态事实

    client.session_list()                     # 按 owner 限定的 Session 清单
    client.session_status("my-session-id")
    client.session_close("my-session-id")
```

实时跟随 —— `follow=True` 返回一个 context-managed 的事件帧订阅：

```python
with ArsdClient(socket_path) as client:
    with client.run_events(run_id, from_seq=0, follow=True) as stream:
        for frame in stream:
            ...
```

`request` 是带版本的 `AgentRunRequest`：`owner` / `namespace`、**`agent_id`**、session 复用选择、
`requested_model` / `requested_effort`、输入引用、冻结的 `execution_grant` 引用与哈希、凭据**引用**，
以及各项 limits。`agent_id` 指向运维方注册表里的一条条目；它在任何解析之前先通过自己的语法校验，并且
不指代任何路径、可执行文件、argv token、环境变量键、摘要或版本。

它绝不携带 shell 文本、argv、环境变量值、可执行文件路径或凭据内容 —— 这些字段在协议上根本不存在。

错误是类型化且失败关闭的。客户端异常携带稳定的错误码（例如 `PEER_UID_DENIED`、`OWNER_MISMATCH`、
`IDEMPOTENCY_CONFLICT`、`CAPACITY_EXHAUSTED`）；服务端的消息文本绝不会被回显进异常。

## ACP 协议支持

ARS 通过 stdio JSON-RPC 讲 **ACP Protocol v1**（`protocolVersion: 1`），使用官方 Python 客户端库
`agent-client-protocol`，由 `native` 额外依赖钉在 **0.11.1**。

每个 profile 都冻结 ACP 协议版本 `1`。活动 agent 若报出别的版本，该 Run 会在 `initialize` 阶段、派发
任何提示词之前就失败。每个 profile 也都要求 `loadSession` 能力，因为同 Session 的连续性走的是对未变的
外部 session ID 做真实 `session/load`。

**一个复用请求永远不会变成新建 Session。** 不作为回退、不在失败之后、也不在任何错误类别下 —— 这是结构性
保证，而不是条件判断。缺失或损坏的 Session 记录、缺失的已存外部 ID、或绑定发生变化，都会在拿到 lease
**之前**失败；而任何身份冲突的回调都在入口处即被拒绝，早于任何 handler、事件、文件系统访问或权限决策。

派发任何提示词之前，一条连接必须完成 `initialize` → `session/new` 或 `session/load` → discovery →
设置 model → 重新 discovery → 设置 effort → **精确读回**。能力缺失、未广告的取值或读回不精确，都会产出
零 Turn 且不发提示词。域的权威是**活动 agent 当下广告出来的取值集合**：ARS 不冻结任何 model 或 effort
取值域，所以「agent 今天新增了一个模型」对 ARS 来说不是事件。

带 ACP 世代号的 profile id —— `standard-native-acp-v1` —— 精确冻结那个协议 major。未来的 v2 会是另一个
profile 与另一个 Session 域，而不是它的一次 revision。

## 支持哪些 agent

profile 是一份小而由源码拥有、带版本的说明，描述**如何对一类 agent 讲 ACP**。它不是 agent 清单，也不含
任何路径、版本、摘要、模型字面量或 agent 名字。

| `profile` | 用于 | 额外的 ACP 语义 |
|---|---|---|
| `standard-native-acp-v1` | 所有 agent，无论原生讲 ACP，还是经由你自己安装的 ACP 适配器命令接入 | —— |
| `claude-agent-acp-compat-v1` | 一个 ACP 行为本身存在偏差、且该偏差无法由 live discovery 表达的适配器 | 在 `session/new` **与** `session/load` 上都发送被冻结的会话元数据，另加一个必须由精确读回证明的权限模式选择器 |

**支持哪些 agent 取决于你的注册表，而不是这张表。** 任何通过 stdio 讲 ACP v1 的 agent —— 直接讲，或
经由你安装的 ACP 适配器命令 —— 都只是一条对 `standard-native-acp-v1` 的注册表条目。非 ACP 的 CLI 需要
一个适配器命令，但它本身并不需要一个 profile。

新增一个兼容 profile 必须同时满足三点：在 ACP 层有可复现、可引用的观测；能证明该偏差无法由 live
discovery、精确读回、选择器 ID 提示或运维方环境变量表达；以及经过评审。这道门槛的存在理由很直接：
一个按 agent 定制的 profile 会把那个 agent 的日常升级重新耦合到 ARS 的发版上 —— 而这正是本架构要消除的
成本。

model 与 effort 字面量来自运行中的 agent，而不是来自 ARS。你按运行传入它们，agent 必须精确读回。

## 由你安装的 agent 运行时

ARS 启动 agent；它不分发、不安装、不托管、不冻结、也不校验它们。对每个你想用的 agent：

1. 用你自己的包管理器安装它，装在哪都行 —— 包括 `$HOME` 之下、版本管理器 shim 之后，或符号链接农场里；
2. 加一条注册表条目，写明它的 `command`（若其 ACP 模式需要子命令，再写 `args`）；
3. 声明基础白名单没覆盖到、而它确实需要的环境变量 —— 首先是 `PATH`；
4. 依次跑 `agents validate`、`agents doctor`，以及该 agent 的**强制拒绝动作 canary**，然后才使用它；
5. 重启 `arsd`，让新的注册表被读入。

此后在同一条已注册命令背后升级那个 agent，不需要 ARS 做任何事。

**不拥有任何工件。** 这里没有由 ARS 拥有的工件前缀、没有包闭包、没有树摘要、没有被冻结的解释器身份、
没有晋级、也没有完整性证明。ARS 对你的命令及其加载的任何东西都不做所有者、权限位、祖先、符号链接或摘要
检查。ARS 每次运行记录的是：声明的命令、精确的 argv、解析出的环境变量**名字**，以及作为**明确非权威**
证据的观测结果 —— PATH 命中项、内核实际映射的镜像，以及 agent 自报的名字与版本。这些都不构成门禁，也不
阻断连续性。

**由此放弃了什么。** ARS 不再能检测出被替换或被修改的可执行文件。这是有意的取舍：那种检测与随后以全部
权限执行该 agent 的，是同一个 UID，所以它从未真正约束一个字节相同的 agent 能做什么。可执行文件的完整性
属于你的操作系统与部署工具 —— 包签名、不可变镜像、文件系统权限、主机完整性工具 —— 而 ARS 的贡献是每次
运行留下的、可供事后审计的证据。

## 运维可见的变化

注册表边界上有七处变化值得写进你自己的运维手册：

1. **对 agent 自身项目配置文件的工作区拒绝消失了。** ARS 不再因为工作区里存在 agent 自己的项目配置文件
   而拒绝它 —— 那个文件由 agent 拥有。
2. **基础白名单没覆盖的，由你写 `env_passthrough` / `env_overlay`。** `PATH` 是「在我 shell 里能跑、在
   ARS 下不行」最常见的原因；`SSH_AUTH_SOCK` 刻意需要显式开启，因为转发它等于把你 SSH 私钥的实时使用权
   交给 agent。
3. **新的 launch 记录只携带环境变量名字、来源类别与优先级。** 没有取值、没有取值的摘要、也没有长度。更早
   的、含取值的记录会以 value-blind 方式读取，其自由文本以稳定的类别化标记被扣留。
4. **编辑注册表在下一次守护进程启动时才生效，而不是下一次运行** —— 而在同一条已注册命令背后升级 agent
   则完全免费。
5. **首次为某条条目加上 `session_epoch` 会切断该 agent 已有的 Session**，因为「缺失 ≠ 1」。比较是对称
   相等，所以这与一次 bump 是同样有意的动作。如果你不想切断，就不要加这个字段。除此之外没有任何东西会
   改动它 —— 不是 agent 升级、不是 ARS 升级、不是 command/args/环境/选择器编辑、不是替换文件、也不是重启。
6. **防护那些短而常见的环境值会擦掉大量证据。** `TERM`、`LANG`、`TZ`、`USER`、`HOME` 以及 `PATH` 的各段
   都在防护器的字面量集合里，因此任何回显了它们的运行文本都会被替换或扣留。机密性优先于证据完整性：不存在
   最小机密长度，也不因为不方便就豁免。粗粒度的抑制计数让这种损失可度量而非无形 —— 这也是你在排查一次失败
   运行时最可能感到意外的取舍。
7. **工作区规范根路径与生效的 `cwd` 仍是完整字面量，并且仍被哈希覆盖。** 它们是独立推导出的权威事实，
   而不是环境值的流动，因此**刻意**位于被防护集合之外 —— 所以 `$HOME` 之下的工作区会在 `spec.json` 里
   完整出现。防护它们会破坏工作区绑定、reconciliation 归属与审计。

此外，在一次切换（cutover）时：**所有活动 Session 会被一次性终结。** 在已退役身份模型下创建的 Session
会以稳定错误码被拒绝重新加载，同时仍按 owner 归属可读；要继续那些工作，就得开一个新 Session，并由调用方
自己完成上下文交接。

## 保证与边界

**ARS 保证什么**

- **是监督者，不是业务裁判。** 协议或进程层面的完成永远不等于业务结论；`business_verdict`
  始终为 `null`，归调用方所有。
- **默认拒绝，由调用方冻结权限。** 调用方冻结执行授权，ARS 只执行它，绝不放宽或刷新。已注册的
  工作区内读取可以被允许；write、terminal、execute 以及未知操作一律拒绝。每次决策都产出脱敏的
  中介证据。权限中介所用的环境绑定在**键与值两侧**都由源码拥有、最后施加；注册表条目可以选一份或不选，
  但永远无法编写、替换或禁用它。
- **任何 ARS 落点都不含环境值。** 每个环境值都被视为敏感，无论键名、长度或形状。任何被投影出去的字面量
  —— 以及它的摘要、指纹或长度 —— 都不会进入 ARS 的工件、哈希输入、日志、错误、事件、inspect 响应或
  API 响应。
- **默认可审计。** 运行产出确定性的、脱敏的工件，并采用受限权限：目录 `0700`、文件 `0600`、
  最终工件原子写入。ARS 只写两个面 —— supervisor root 与它自己的套接字路径 —— 别无其他。
- **不确定即失败关闭。** 无效输入、协议漂移、权限被拒、超时以及不可信的恢复，都会落到确定性的
  非成功状态，而不是猜一个结果。任何可能已经派发过的提示词都不会被自动重试、重放或续跑，而且不存在
  解除 quarantine 的工具。
- **仅本地、非特权。** `0700` 目录里的 `0600` 套接字，基于对端凭据、对照显式 caller 策略完成
  认证，且不使用 root。

**ARS 不是什么**

- **不是沙箱。** 这是协作式 agent 的策略中介，不是操作系统级隔离，不是敌对进程遏制，也不是多租户。
  agent 以守护进程的 UID 运行，拥有该 UID 的全部权限。真正的隔离属于操作系统层 —— 专用 UID、user
  namespace、`seccomp`/Landlock、`bwrap`/容器/VM 边界、cgroup 限额 —— 并且能与这里组合：你可以把隔离
  wrapper 本身注册为那条命令。
- **不是完整性或供应链校验。** ARS 不验证它启动的可执行文件是否就是你想要的那个、是否未被修改，或是否
  来自可信发布者。
- **不是一个完备的终止开关。** ARS 能可靠终止它的直接子进程，以及仍留在它所创建进程组里的全部后代。
  离开进程组的后代、被交给 service manager 成为独立 unit 的负载、把负载搬到别处的容器运行时，或自行
  double-fork 的 agent，都在该保证之外。若工作确实在别处继续，该 Run 会响亮地落到 `unknown` /
  `quarantined`，而不是无声通过。
- **本身不构成崩溃遏制。** 生产环境依赖用户级 service manager 的 cgroup（`Restart=on-failure`、
  `KillMode=control-group`），使得杀死守护进程能连带杀死仍在其中的全部 agent 后代。
- **不是凭据管理器。** ARS 不解析、不签发、不刷新、也不存储任何凭据。agent 用它自己 `HOME` 下的认证库。
  如果你把某个 token 或 agent socket 投影给子进程，那个值是**按你的声明**到达子进程的 —— ARS 只记录它的
  名字与来源类别，并且无法阻止子进程写下它、发送它，或以变形后的形式泄露它。
- **不是入口、网关或聊天集成。** 没有公网入口、没有消息投递、没有 agent 间自动路由 —— 这些属于
  调用方及其平台。

## 开发

推荐使用 [uv](https://docs.astral.sh/uv/)；根目录 [`Makefile`](Makefile) 封装了常用命令。

```bash
make sync      # uv sync --locked --extra dev --extra release --extra native
make verify    # 完整本地关卡（与 CI 一致）
make build     # sdist/wheel + twine check
make clean     # 清理构建产物、缓存与本地临时数据
make help      # 列出全部 target
```

无 Make 时的等价命令：

```bash
uv sync --locked --extra dev --extra release --extra native
./scripts/verify_local.sh
```

`make verify` / `./scripts/verify_local.sh` 是单一本地关卡：测试、只读 CLI 冒烟、文档索引检查、
静态安全扫描与打包检查。CI 跑的就是它，说明见
[`docs/roadmap/verification.md`](docs/roadmap/verification.md)。

套件覆盖 Native ACP 核心与 `arsd` 守护进程 —— 协议分帧、对端认证与归属、准入与幂等、
reconciliation、客户端往返 —— 全部走密封的 fake agent 与临时套接字。需要真实 agent 运行时的套件
默认跳过、需显式开启，且绝不在 CI 中运行。

**pip 回退**（无 uv 时）：

```bash
pip install -e '.[dev,release,native]'
python3 -m pytest -q
```

## 参与贡献

欢迎提 issue 与 pull request。

1. **先读权威链。** 本项目文档先于代码：
   [`GOAL.md`](GOAL.md) → [`docs/product/prd.md`](docs/product/prd.md) →
   [`docs/design/architecture.md`](docs/design/architecture.md) →
   [`docs/design/technical-solution.md`](docs/design/technical-solution.md) →
   [`docs/roadmap/features.md`](docs/roadmap/features.md) →
   [`docs/roadmap/current-status.md`](docs/roadmap/current-status.md)。
   [`docs/design/agent-registry.md`](docs/design/agent-registry.md) 是运维契约，
   [`docs/roadmap/non-approvals.md`](docs/roadmap/non-approvals.md) 记录明确不在范围内的事项。
   `docs/archive/` 下的一切都是冷历史，绝不是当前权威。
2. **从 `main` 切分支**，使用短生命周期的任务分支：`feat/`、`fix/`、`docs/` 或 `cicd/`。
3. **行为变更先写测试**，并保持运行时仅依赖标准库，除非该变更被明确批准引入依赖。
4. **过关卡**：开 PR 之前 `make verify` 必须是绿的。
5. **使用 Conventional Commits**，并说明这个变更**为什么**存在，而不是复述 diff。
6. **绝不提交机密** —— 不提交 API key、token、cookie、真实 UID 映射、套接字路径或其他部署值。文档与
   示例中请使用 `[REDACTED]`。

一个 PR 应说明：变更摘要、它触及的权威文档、对 roadmap 的影响、带命令与结果的测试计划，以及机密安全
声明。完整流程见 [`docs/AI_FLOW.md`](docs/AI_FLOW.md)。

## 许可证

© `agent-run-supervisor` 作者。以 **[MIT](https://opensource.org/license/mit)** 许可证发布
（见 [`LICENSE`](LICENSE)）。
