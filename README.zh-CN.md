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
  一个<b>本地优先</b>的外部编码 AGENT 监督层。<br>
  一个非特权守护进程，每次运行一个进程，产出<b>脱敏、可审计的本地证据</b>。
</p>

---

## 这是什么

凡是要驱动外部编码 AGENT 的程序，最后都会重复造同一套管道：拉起 agent 进程并照看它、决定它能碰
什么、读取协议事件流、判断这次运行究竟怎么结束的，以及在任何东西落盘前把敏感信息清理干净。各写
各的，结果就是每个调用方都长出一份自己的副本。

**Agent Run Supervisor（ARS）** 把这一层抽出来，并且完全留在本地。你的应用提交一次运行 —— 用哪个
已注册 agent、哪个模型、哪个工作区、什么提示词 —— 然后 ARS 按运维方注册的那条命令只拉起一个受监督
的进程，以默认拒绝（default-deny）中介每一次权限请求，把 agent 输出归一化为有序事件，判定一个由
监督层拥有的状态，并写出脱敏工件。你拿回来的是**可审计的证据**，而不是一团进程生命周期代码：
*它想做什么、被允许做什么、最后怎么结束的？*

| ARS 拥有 | ARS 刻意不拥有 |
|---|---|
| 它启动的进程：PID/PGID、超时、信号、回收 | 它启动的软件 —— agent 由你自己安装与升级 |
| ACP 会话：能力协商、精确的 model/effort、连续性 | agent 自己的对话与上下文状态 |
| 针对调用方冻结授权的权限中介 | 业务结论 —— 那属于你的应用 |
| 单一 supervisor root 下的脱敏运行证据 | agent 的 `$HOME`、凭据库、插件、缓存与配置 |
| 本地套接字上的调用方认证 | 凭据 —— ARS 不解析、不签发、不刷新、不存储 |

> **版本说明。** 本 README 描述的是 **`0.6.0`** 代码线：由运维方拥有的 agent 注册表、`--agents-file`
> 与 `arsd` API v2。`0.6.0` 已在本仓库中准备好，但**尚未发布** —— PyPI 上目前仍是 `0.5.x`，它实现的
> 是更早的 Runtime Binding 架构、用的是 `--binding-root`。要照本文操作请从源码检出安装；在动线上部署
> 之前，请先读[从 0.5.x 升级](#从-05x-升级)。

## 工作原理

<p align="center">
  <img src="docs/assets/diagrams/how-it-works.zh-CN.svg" alt="受信任的本地调用方通过 arsd Unix 域套接字提交；arsd 完成对端认证与准入；ars-core 以一个 RunTask 通过 Native ACP 驱动已注册的外部 AGENT；归一化事件、状态与脱敏本地工件沿返回路径回到调用方" width="900">
</p>

```text
受信任的调用方  →  arsd（本地 UDS）  →  ars-core / Native ACP  →  外部 AGENT 进程
```

每一跳都在同一台机器上、同一个非特权用户下：

1. **`arsd` 启动：** 只解析一次 agent 注册表 → 对持久的 Run/Session 事实做 reconciliation → 之后
   才绑定套接字。注册表只要有任何缺陷，就会在写入任何状态之前拒绝监听。
2. **你的应用连接** `0700` 目录里的 `0600` 套接字 —— 没有 TCP、没有 root、没有公网入口。`arsd` 读取
   对端凭据并映射为 principal，再用你自己的 `request_id`（同时就是幂等键）做准入。Run 与 Session
   按 owner 归属：只有拥有它的调用方能查询、跟随、取消或关闭。
3. **`ars-core` 执行运行：** 一个进程内 `RunTask` 独占一个受监督进程和一条 Native ACP 连接，由派生
   之前就封存好的不可变规格驱动。agent 就是你注册的那条命令，按声明原样启动 —— 协议上不存在任何
   command/argv/环境变量透传，服务期间也不会再读一次注册表。

返回方向上，通过同一个套接字拿到归一化、按 `seq` 有序的事件、一个由监督层拥有的状态，以及落在本地
磁盘上的脱敏工件。

**两个协议、两条版本线，谁也推不出谁。** **下游**是 ARS 与 agent 进程之间、通过 stdio JSON-RPC 讲的
**ACP Protocol v1**，本次发布不变；**上游**是你的应用与 `arsd` 之间、由 ARS 自己拥有的 **`arsd` API
v2**：客户端发出的每一个**请求**信封都带 `api_version`，未知版本一律拒绝而不是猜测；结果帧与错误帧带
的是用于关联的 `request_id`，不带版本号。API 升到 2，是因为 `submit` 的含义变了 —— `profile_id` 不再
选择启动方式，改由 `agent_id` 选择。在排空窗口内，`submit` 是**唯一**在
`api_version: 1` 上被拒绝的操作；其余七个照常接受，包括 `server_info` —— 旧调用方正是靠它才能发现
「自己必须升级」。

## 环境要求

| 需求 | 要求 |
|---|---|
| 运行时 | **Python ≥ 3.11**，**零第三方运行时依赖**。 |
| 驱动真实 agent | `native` 额外依赖，钉住官方 ACP 客户端 `agent-client-protocol==0.11.1`。基础安装可正常导入，只在真正用到 SDK 时才失败。 |
| 运行 `arsd` | Linux，且有可放置 AF_UNIX 套接字的 POSIX 用户会话，另需 supervisor root、一个 agents 文件与至少一条 caller 映射。崩溃遏制还需要用户级 service manager 的 cgroup 与带 pidfd 支持的 CPython。 |
| 运行 agent | 由你安装好的 agent，以及一条写明其命令的注册表条目。 |

## 安装与快速上手

```bash
pip install 'agent-run-supervisor[native]'      # 或：uv pip install 'agent-run-supervisor[native]'
```

这装到的是当前已发布的代码线，不是 `0.6.0`。由于运行时仅依赖标准库，源码检出无需安装即可直接运行：

```bash
git clone https://github.com/jovijovi/agent-run-supervisor.git
cd agent-run-supervisor
PYTHONPATH=src python3 -m agent_run_supervisor --help
# 先写一个小的 agents 文件（见下一节），再检查它 —— 只读
PYTHONPATH=src python3 -m agent_run_supervisor agents validate --agents-file <your-agents.toml>
```

需要测试套件与开发工具时：`pip install -e '.[dev,native]'`，或用 [uv](https://docs.astral.sh/uv/)
执行 `make sync`。这里没有任何东西会隐式启动 agent：`agents validate`、`run inspect` 与
`--print-service-unit` 都是只读的。`agents doctor` 是唯一**会**启动外部子进程的诊断命令，那个子进程
会写它自己的、由 agent 拥有的状态；它在每条路径上都会被回收，若某个进程组连 `SIGTERM` 和 `SIGKILL`
都活了下来，会被报成一次失败的探测，而不是被悄悄留在那里。

## Agent 注册表

`--agents-file` 指向**一个由运维方拥有的 TOML 文件，在守护进程启动时恰好读一次**，读入一份不可变的
内存快照。你以原子方式替换它；替换在**下一次守护进程启动**时生效。

```toml
schema_version = 1

[agents.native-agent]                              # 表键 = 调用方使用的 agent_id
profile         = "standard-native-acp-v1"         # 如何讲 ACP，而不是一份 agent 清单
command         = "some-agent"                     # 裸名字走 PATH，或写绝对路径
args            = ["acp"]
mediation       = "ask-privileged-tool-families-v1"  # 选择一份源码拥有的绑定
env_passthrough = ["SSH_AUTH_SOCK", "SOME_PROVIDER_TOKEN"]
env_overlay     = { SOME_AGENT_HOME = "/home/<service-user>/.some-agent" }
forbidden_capabilities = ["terminal"]
```

上面每个值都是**占位符**。完整且封闭的字段集是 `profile`、`command`、`args`、`mediation`、
`env_passthrough`、`env_overlay`、`model_selector`、`effort_selector`、`forbidden_capabilities` 与
`session_epoch` —— 除此以外没有别的，任何层级上的未知键都会被拒绝。CLI 本身不讲 ACP 的 agent 也没有
区别：把 `command` 指向你安装的那个 ACP 适配器，profile 照旧，因为适配器是部署事实而不是源码常量。
只有 `claude-agent-acp-compat-v1` 不同，且仅用于一处有据可查的 ACP 层偏差。

- **你的命令按声明原样启动。** `argv[0]` 逐字节就是你声明的那个字符串；裸名字由**子进程**投影出的
  `PATH` 按普通查找定位，因此 shim、符号链接农场以及 agent 自更新都照常可用。这里没有任何预检解析；
  exec 失败会被报为 `COMMAND_NOT_FOUND`、`COMMAND_NOT_EXECUTABLE` 或 `SPAWN_FAILED` —— 这是配置错误，
  不是安全拒绝。
- **`PATH` 是「在我 shell 里能跑、在 ARS 下不行」最常见的原因。** 用户级守护进程继承到的环境很小，
  所以你的 agent 需要什么就声明什么。`SSH_AUTH_SOCK` 刻意需要显式开启：转发它等于把你 SSH 私钥的
  实时使用权交给 agent。
- **只读一次，有代价也有回报。** 编辑注册表的代价是一次守护进程重启；而**在同一条已注册命令背后升级
  agent** 完全免费，已有 Session 仍会通过真实的 `session/load` 复用。

ARS 只检查它自己的配置文件，此外一概不查：解析后的 agents 文件必须是一个不可被 group 或 world 写入的
普通文件；对 `command`、对其祖先目录、对 agent 后续加载的任何东西，**都不做所有者、权限位、祖先、
符号链接或摘要检查**。完整契约（语法、边界、每一个拒绝码、环境层、`session_epoch` 与诚实的限制）见
[`docs/design/agent-registry.md`](docs/design/agent-registry.md)。

## 运行 `arsd`

运维命令在 `agent-run-supervisor` 控制台脚本上，守护进程本身则是一个 module 入口。顺序很重要：

```bash
# 1. 离线检查文件 —— 无副作用
agent-run-supervisor agents validate --agents-file <agents-file>
# 2. 逐 agent 诊断；不加 --no-probe 时会启动已注册的那条命令
agent-run-supervisor agents doctor --agents-file <agents-file> --agent <agent-id>
# 3. 把 user 作用域的 systemd unit 渲染到 stdout —— 纯文本，什么都不安装
python3 -m agent_run_supervisor.arsd --agents-file /absolute/path/to/agents.toml --print-service-unit
# 4. 启动守护进程
python3 -m agent_run_supervisor.arsd \
  --supervisor-root <supervisor-root> \
  --agents-file /absolute/path/to/agents.toml \
  --caller-mapping <UID>:<principal_id>:<owner>:<namespace>
```

在第 2 步与第 3 步之间，为每个 agent 跑一次**强制的拒绝动作中介 canary**：中介是协作式 agent 策略，
不是操作系统沙箱，而「零权限事件」并不能证明拒绝生效。

`--print-service-unit` 与守护进程模式**都**要求 `--agents-file` 是**绝对路径** —— 两边跑的是同一套
校验，所以渲染出的 unit 不可能带上一个守护进程会拒绝的路径。守护进程模式还必须提供
`--supervisor-root` 和至少一条 `--caller-mapping`（**零条映射会拒绝监听**），并且拒绝以 root 运行。
`--socket` 默认取 `$XDG_RUNTIME_DIR/agent-run-supervisor/arsd.sock`，否则退化为
`<supervisor-root>/arsd/arsd.sock`。
caller 映射、套接字路径与注册表路径都是部署侧的值：放在权限 `0600` 的 unit 文件里，绝不进仓库。重启后
守护进程只对持久事实做 reconciliation、绝不重发提示词，而且比一个宽容的读取器更严格：损坏的终态记录、
无法归属的不确定性、或一条没有对应 spec 的 launch 记录，都会拒绝监听；可能已派发却没有可信终态结果的
Run 会落到 `unknown` / `quarantined` / `retryable=false`。

## 用 Python 调用

[`ArsdClient`](src/agent_run_supervisor/arsd/client.py) 是受支持的调用方边界：显式连接、context-managed，绝不静默重连，也绝不重放请求。

```python
from agent_run_supervisor.arsd.client import ArsdClient

socket_path = "<XDG_RUNTIME_DIR>/agent-run-supervisor/arsd.sock"

with ArsdClient(socket_path) as client:
    client.server_info()                      # 协议/版本握手事实

    # 以下均为占位符。请把 owner/namespace/agent、model/effort、授权与策略哈希，
    # 以及 input_refs 换成你自己准入与授权流程产出的值；已配置的守护进程会原样拒绝它们。
    ack = client.submit(                      # 调用方自有的 request_id = 幂等键
        request_id="my-caller-request-id",
        payload={
            "request": {
                "owner": "my-team",
                "namespace": "my-team/docs",
                "agent_id": "native-agent",           # 你注册表里的某个 agent_id
                "session_reuse": "none",              # "none" 表示新开一个 Session
                "ars_session_id": None,               # session_reuse="reuse" 时才填
                "expected_binding_hash": None,
                "input_refs": [
                    {"ref": "prompt:inline", "content_hash": "sha256:" + "a" * 64},
                ],
                "requested_model": "<model-the-agent-advertises>",
                "requested_effort": "<effort-the-agent-advertises>",
                "grant_ref": "grant:my-caller-grant-1",
                "grant_hash": "sha256:" + "b" * 64,
                "grant_role_hash": "sha256:" + "c" * 64,
                "grant_capabilities": ["read"],
                "mcp_snapshot_hashes": [],
                "credential_refs": [],
                "limits": {},                         # {} 表示采用封存的默认值
                "evidence_policy_hash": "sha256:" + "d" * 64,
                "recovery_policy_hash": "sha256:" + "e" * 64,
            },
            "prompt_text": "用平实的语言总结这个 diff。",
            "workspace_root": "/path/to/bound/workspace",
        },
    )
    run_id = ack["run_id"]                    # ack 形如 {"run_id": ..., "accepted_at": ...}

    client.run_status(run_id)                          # accepted → progress → 唯一终态结果
    client.run_events(run_id, from_seq=0, limit=100)   # 有界、按 seq 有序的分页
    client.run_cancel(run_id)                          # 协作式取消；绝不改写终态事实

    with client.run_events(run_id, follow=True) as stream:   # 实时事件帧
        for frame in stream:
            ...
```

`session_list()`、`session_status(id)`、`session_close(id)` 补全其余接口，同样按 owner 限定。
上面 `request` 的键集是封闭且完整的：未知键会被拒绝；它不携带 shell 文本、argv、环境变量值、可执行
文件路径或凭据内容 —— 这些字段在协议上根本不存在，而 `credential_refs` 只是**引用**，ARS 从不把它
解析成值。错误是类型化且失败关闭的：异常携带 `PEER_UID_DENIED`、`OWNER_MISMATCH`、
`IDEMPOTENCY_CONFLICT`、`CAPACITY_EXHAUSTED` 这样的稳定错误码，服务端文本绝不会被回显进异常。

## 从 0.5.x 升级

`0.6.0` 对运维方与调用方都是**破坏性**变更，没有原地升级，也没有兼容垫片 —— 这是有意为之：静默地按新
语义重新解释旧输入，正是本项目拒绝的那种失败模式。

| 你原来有（`0.5.x`） | 你现在需要（`0.6.0`） |
|---|---|
| `--binding-root` 与已晋级的 generation | `--agents-file` 与一个 TOML 注册表 |
| `runtime-binding` 命令组 | `agents validate`、`agents doctor`、`run inspect` |
| `api_version: 1`，submit 带 `profile_id` | `api_version: 2`，submit 带 `agent_id` |
| 四个已注册 profile | 两个：`standard-native-acp-v1`、`claude-agent-acp-compat-v1` |

要为两件事做好准备。**所有活动 Session 会被一次性终结：** 在已退役身份模型下创建的 Session 会以稳定
错误码被拒绝重新加载，同时仍按 owner 归属可读、可关闭；要继续那些工作，就得开一个新 Session，并由调用
方自己完成上下文交接。以及 **ARS 不迁移、也不删除任何东西：** 旧的 Binding root、工件树，以及每一个
历史 Run 与 Session 字节都原样保留。完整说明见 [`CHANGELOG.md`](CHANGELOG.md)。

## 保证与边界

| ARS 保证 | ARS 不声称 |
|---|---|
| 对照调用方冻结的授权做默认拒绝中介，每次决策都产出脱敏证据；权限中介所用的环境绑定在**键与值两侧**都由源码拥有、最后施加，条目永远无法编写或禁用它 | **是沙箱。** 这是协作式 agent 策略，不是操作系统隔离：agent 以守护进程的 UID 运行，拥有该 UID 的全部权限 |
| 任何 ARS 工件、哈希输入、日志、错误、事件、inspect 响应或 API 响应里都不含环境值 —— 连它的摘要、指纹或长度都没有 | 值不会到达子进程，或能识别出**变形后**的泄露（片段、编码、哈希、改写） |
| 确定性的脱敏工件：目录 `0700`、文件 `0600`、最终写入原子化，且只有两个可写面 —— supervisor root 与套接字路径 | **完整性或供应链校验。** ARS 不验证它启动的可执行文件是不是你想要的那个、来自哪个发布者 |
| 终止其直接子进程，以及仍留在 ARS 所创建进程组内的全部后代 | **是完备的终止开关。** 离开该进程组的后代在保证之外 —— 若工作确实在别处继续，该 Run 会响亮地落到 `unknown` / `quarantined` |
| 不确定即失败关闭：可能已派发的提示词不会被自动重试、重放或续跑，也不存在解除 quarantine 的工具 | **本身构成崩溃遏制。** 生产环境依赖用户级 service manager 的 cgroup（`Restart=on-failure`、`KillMode=control-group`） |
| 只报告技术监督事实 —— `business_verdict` 始终为 `null`，归调用方所有 | **是凭据管理器、入口、网关或聊天集成。** agent 用它自己 `HOME` 下的认证库；投递与路由属于你的平台 |

真正的隔离属于操作系统层 —— 专用 UID、user namespace、`seccomp`/Landlock、`bwrap`/容器/VM 边界、cgroup 限额 —— 并且能与这里组合：把隔离 wrapper 本身注册为那条命令即可。

## 文档

| 阅读 | 用于 |
|---|---|
| [`docs/design/agent-registry.md`](docs/design/agent-registry.md) | 运维契约：语法、边界、拒绝码、环境层、重启语义 |
| [`docs/design/architecture.md`](docs/design/architecture.md) | 系统形态、四个权威层、reconciliation、存储 |
| [`docs/design/result-event-schema.md`](docs/design/result-event-schema.md) | ARS 发出的、对调用方稳定的 JSON 形状 |
| [`docs/roadmap/current-status.md`](docs/roadmap/current-status.md) | 项目当前的真实位置，以及哪些事项未获批准 |

## 开发

```bash
make sync      # uv sync --locked --extra dev --extra release --extra native
make verify    # 单一本地关卡 —— 与 CI 完全一致
make build     # sdist/wheel + twine check
make help      # 列出全部 target
```

无 Make 时：`uv sync --locked --extra dev --extra release --extra native` 后跑
`./scripts/verify_local.sh`；无 [uv](https://docs.astral.sh/uv/) 时：
`pip install -e '.[dev,release,native]'` 后跑 `python3 -m pytest -q`。`make verify` 包含测试、只读 CLI
冒烟、文档索引检查、静态安全扫描与打包检查，说明见
[`docs/roadmap/verification.md`](docs/roadmap/verification.md)。套件以密封的 fake agent 与临时套接字
驱动 Native ACP 核心与 `arsd`；需要真实 agent 运行时的套件默认跳过、需显式开启，且绝不在 CI 中运行。

## 参与贡献

欢迎提 issue 与 pull request。本项目文档先于代码，请先读权威链：[`GOAL.md`](GOAL.md) →
[`docs/product/prd.md`](docs/product/prd.md) → [`docs/design/`](docs/design/) →
[`docs/roadmap/`](docs/roadmap/)，其中
[`non-approvals.md`](docs/roadmap/non-approvals.md) 记录了明确不在范围内的事项；`docs/archive/` 下的
一切都是冷历史，绝不是当前权威。

从 `main` 切出短生命周期的 `feat/` · `fix/` · `docs/` · `cicd/` 分支，行为变更先写测试，保持运行时仅
依赖标准库，开 PR 前 `make verify` 必须是绿的，使用 Conventional Commits，并且**绝不提交机密** ——
不提交 API key、token、真实 UID 映射、套接字路径或其他部署值，文档与示例中请使用 `[REDACTED]`。
完整流程见 [`docs/AI_FLOW.md`](docs/AI_FLOW.md)。

## 许可证

© `agent-run-supervisor` 作者。以 **[MIT](https://opensource.org/license/mit)** 许可证发布（见 [`LICENSE`](LICENSE)）。
