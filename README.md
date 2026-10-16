# context-lattice · 上下文双层键压缩存储

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-3776ab.svg)](https://www.python.org/)
[![Dependencies: none](https://img.shields.io/badge/dependencies-0-brightgreen.svg)](#环境)
[![Tests: 615 assertions](https://img.shields.io/badge/tests-615%20assertions-brightgreen.svg)](tests/)
[![Version](https://img.shields.io/badge/version-1.2.1-blue.svg)](CHANGELOG.md)

> **把长会话压成一份结构化、可校验、可增量维护的存储**，让模型回顾时只读相关的那一支，而不是回翻大量原文。
>
> 它不是摘要工具，也不是窗口扩展器 —— 它是**给对话里的约定一个可反复读写、还能被程序校验的持久形态**（≈ 建台账，不是拍照片）。

版本：`1.2.1` · 作者：CJQ0304 · 纯 Python 标准库 · 零第三方依赖 · MIT

[**中文**](#中文) | [English](#english)

---

## 中文

### 它解决什么问题

长会话里，模型每次「回顾之前聊的」都要把大段原始对话重新读一遍：**贵**（token 线性增长）、**不准**（每次理解重心会漂）、**爱丢**（真做摘要又最容易吞掉否定条件与硬约束）。

本项目的做法是**先把内容按归属归档，再按需取层** —— 压缩靠「不读无关的部分」，而不是「丢掉细节」。

归档之后紧接着两个问题，各有对应机制：

| 问题 | 机制 |
|---|---|
| 这些内容**该在多大范围生效**？同一句「以后回复都用中文」和「这次先按 A 方案试一版」结构上一样，寿命却差几个数量级 | **三层作用域** `user` / `proj` / `task` |
| 它**该活多久**？把临时结论当长期规范，会变成**记忆污染**（错误结论在错误范围的持续生效），比记漏更糟 | **生命周期标记** `钉住` / `长期` / `暂态` + 老化 |
| 有些素材**根本压不动**（要逐字改的代码、要逐行核的数据），拆成键既丢原文又没换来压缩 | **事件节点**：库里只留「摘要 + 来源指针 + 字节数 + 内容哈希」，原文不进库也不进窗口 |

> **一条必须说清的边界**：本技能**不改变窗口上限**。它不压缩正在读进去的内容 —— 改变的是「项目背景以什么形态进窗口」与「回顾时取多大一块」。**决定收益的是任务的局部性，不是技能。**

### 一句话定位（对外统一口径）

不是摘要工具、不是窗口扩展器，而是**给对话里的约定一个可反复读写、还能被程序校验的持久形态**。

三条别人给不了的性质：

- **确定性** —— 同输入两次渲染**字节一致**；
- **可校验** —— 20 条不变量，坏文件拒载（手改文件必然被发现）；
- **可审计** —— 改口留 `[已废]` / `[最新]`，注入留 `[注入-未执行]`，越界留 `[越界·未执行]`。
  **这些标记不是内容是证据** —— 裸模型会把它们抹平成「均已完成」。

### 它不做什么

| 不做 | 原因 |
|---|---|
| 不压缩「已经发出去的历史消息」 | 那一段归宿主管，技能碰不到 |
| 不在单次会话里省钱 | 原文本来就在窗口里，既没有「重贴」可省，也没有「取回」要做 —— **同一个会话里的多轮对话，恰恰是最不需要它的场合** |
| 不产生新信息 | 它归并和组织，不推断、不补全（缺口一律写 `[待补]`） |
| 不管叙事 | 它的产出是台账，不是会议纪要 |

**判据是「任务状态需要离开窗口再回来」，不是「对话多轮」。** 真实触发只有三种：跨会话 / 长会话被自动压缩或截断 / 窗口吃紧需主动卸载。

### 快速开始

需要 **Python 3.8+**，无需安装任何依赖。

```bash
git clone https://github.com/CJQ0304/context-lattice.git
cd context-lattice

# 0) 确认可用
python scripts/ctx.py --help

# 1) 建库（--date 必须显式给，固定值保证可复现）
python scripts/ctx.py init -f .ctxstore/store.ctx --date 2026-10-15 \
  --top "新中式养生茶饮品牌作业" --stdin <<'EOF'
T 品牌定位
P 确定目标人群
R 25-35 岁新中产 [约束]
R 注重健康养生 [偏好]
EOF

# 2) 每轮追加（幂等归并：同义合键、同文去重）
python scripts/ctx.py add -f .ctxstore/store.ctx --stdin <<'EOF'
T 品牌定位
P 确定价格带 [进行中]
R 客单价 18-28 元 [约束]
EOF

# 3) 回顾时渐进下钻（不要直接读全量）
python scripts/ctx.py read -f .ctxstore/store.ctx --level 1     # 先只看事物名
python scripts/ctx.py read -f .ctxstore/store.ctx --topic T1    # 再取相关那一支

# 4) 自检与统计
python scripts/ctx.py validate -f .ctxstore/store.ctx
python scripts/ctx.py stats -f .ctxstore/store.ctx --raw-chars 4200

# 5) 压不动的整块素材：登记为事件节点（原文不进库、也不进窗口）
python scripts/ctx.py event -f .ctxstore/store.ctx --topic T1 --purpose P1.2 \
    --src src/kernel.py --summary "旧版内核实现"
python scripts/ctx.py fetch -f .ctxstore/store.ctx --summary "旧版内核实现" --check

# 6) 不确定某块素材该不该压？让脚本判，不要自己估窗口占用
python scripts/ctx.py plan --root src --work kernel.py,main.py
```

> **别把 `--fetch` 常态化。** 事件节点的意义就是「摘要进窗口、原文留在盘上」。先靠默认视图里的摘要判断要不要看原文，确实需要再取。

想肉眼看看每种视图长什么样：`python tests/smoke.py`（自建演示库，逐条打印）。

### 实测数据（含负值，可复现）

**语料 = 本交付包自身**（14 个文件 / 86,276 token，机械量）。复现：`python tests/benchmarks/realworld-probe.py`

| 任务 | 工作集（保守 / 乐观） | 净省（保守 / 乐观） |
|---|---|---:|
| 给 `ctx.py` 加一个子命令 | 35,536 / 14,994 | **57.7% / 81.5%** |
| 改 `SKILL.md` 的 description | 7,581 / 1,584 | **90.1% / 97.1%** |
| 新增一条拆分规则 | 3,992 / 1,204 | **94.3% / 97.5%** |
| **反例**：任务要读完全部素材 | 86,276 = 100% | **−6.7%（反而更贵）** |

- **保守口径** = 任务涉及的文件整份读；**乐观口径** = 按结构边界（markdown 标题 / `def`）切片后只读命中段。
- **门控**（`plan`，脚本算、不让模型估）：局部任务判 `STORE +52.1%`，反例判 `INLINE −6.7%`，库级断点 `1 − I/T = 93.3%`。
- **事件节点**：14 个节点指向 **245,156 字节**原文，库内只占 **1,749 字符**（每 1 字符指向约 140 字节原文，原文一个字节都不进窗口）。
- **体积无关性**：源文件正文翻倍（92,484 → 184,969 字节），视图 token 只从 82 变 84。

> ⚠️ **绝对值绑定交付包版本** —— 语料就是本包自身，改一次文档 T 就变。引用时请连版本号一起引；相对量（净省百分比）才是稳定的那个。
>
> ⚠️ **如实告知反膨胀**：存储有固定开销（头部 + 键行），短对话会「反膨胀」。实测一段 12 轮 / 234 字符的短对话 → 存储 529 字符（**↑ 126%**）。`stats` 报出负压缩率时会**原样打印负数**，不做截断。

### 结构：两级键 + 双轴链

```
T1 品牌定位 ──────► T2 竞品分析 ──────► T3 视觉元素        ← 上层键：事物（横向 next）
  │                    │                    │
  ▼                    ▼                    ▼
P1.1 定人群           P2.1 盘点           P3.1 定主色       ← 下层键：目的（纵向 down）
  │
  ├─► R 25-35 岁新中产 [约束]                              ← 叶子：要求·描述（横向 next）
  └─► R 注重健康养生 [偏好]
```

图中的「十字链表」有两个指针方向（横向兄弟、纵向父子）。落盘时归一化为 **行序 = next**、**缩进 = down**，语义等价但更抗损：没有野指针可损坏，父子关系断裂会被校验器直接抓到。

### 为什么不用 JSON 存

| 对比项 | `.ctx` | JSON |
|---|---|---|
| 单节点 token 开销 | 约 6–10 | 约 20–35（键名与引号占大头） |
| 唯一读者是模型 | ✅ 零语法噪音 | ❌ 大量括号引号属纯噪音 |
| 供外部工具消费 | 需 `export --format json` | 原生 |

**结论：模型读 `.ctx`，程序读 `export` 出来的 JSON。** 两者是「源 → 投影」关系，不存在第二份真相。

实测（给定「原始对话 4200 字符」这个前提，压成 12 个节点）：

| 视图 | 字符数 | 相对原始 |
|---|---:|---:|
| 原始对话（前提） | 4200 | — |
| 全量 `.ctx` | 238 | ↓ 94.3% |
| 骨架层 `--level 2` | 158 | ↓ 96.2% |
| 索引层 `--level 1` | 99 | ↓ 97.6% |
| 单事物 `--topic` | 172 | ↓ 95.9% |
| 预算裁剪 `--budget 120` | 225 | ↓ 94.6% |

### 三层作用域与生命周期

**作用域**（头部 `| scope:` 段）回答「该在多大范围生效」：

| 作用域 | 判定问句 | 典型内容 |
|---|---|---|
| `user` | 换个工作区还成立吗？ | 称呼与语气偏好、语言选择 |
| `proj` | 只在本工作区成立，但跨会话还要用？ | 目录约定、构建命令、架构决策 |
| `task` | 只是这次的事？ | 临时方案、本轮待办 |

判不准一律落 `task` —— **域判错的代价是不对称的：污染 > 漏记**。

**生命周期标记**（复用行尾 ` [xxx]` 语法）回答「该活多久」：

| 标记 | 老化后的动作 |
|---|---|
| `钉住` | 永不自动老化 |
| `长期` | 不删，列为**升格候选**（输出成可喂给持久库的增量文件，**不自动升格**） |
| `暂态` | 超过 `--days` 阈值即折叠 |
| 无标记 | **默认保留**，不做任何自动删减 |

```bash
# 老化：--today 必须显式给，否则同一条命令不同天跑出不同结果
python scripts/ctx.py prune -f .ctxstore/store.ctx --force \
  --aging --days 30 --today 2026-10-15 --emit-promote /tmp/promote.delta

# 多库导航：先读索引 → 再定位 → 最后才取子树
python scripts/ctx.py index --root .ctxstore --date 2026-10-15 --out .ctxstore/index.txt
python scripts/ctx.py find  --root .ctxstore --topic 品牌定位
```

三档作用域恰好映射到常见的三层记忆（用户级 `MEMORY.md` / 工作区 `MEMORY.md` / 每日日志），接线方式见 [`references/host-memory-adapter.md`](references/host-memory-adapter.md)。**内核完全不绑宿主** —— 换一个环境内核照常可用，只需重画那张映射表。

### 命令参考

共 13 个子命令。核心为 `init` / `add` / `read` / `tree` / `validate` / `stats` / `prune` / `export`，加事件节点三件套 `event` / `fetch` / `plan`，加多库 `index` / `find`。

| 命令 | 用途 |
|---|---|
| `init --file F --date D [--scope S] [--top T] [--stdin] [--force]` | 建库（`--scope` 声明作用域） |
| `add --file F --stdin` / `add --file F --topic X --purpose Y [--item Z] [--tag T]` | 并入增量（幂等归并） |
| `add --file F --scope user\|proj\|task\|-` | 改作用域（`-` 清除声明） |
| `read --file F [--topic\|--purpose] [--level 1\|2\|3] [--budget N] [--no-header] [--fetch]` | 渲染读取视图（**严格只读**）。`--fetch` 额外取回事件节点原文并校验；其产物不是合法存储，不可回灌 |
| `tree --file F [--topic\|--purpose]` | 框线树形视图 |
| `validate --file F` | 结构校验（20 条不变量，含作用域合法性、事件节点计数与指向） |
| `stats --file F [--raw-chars N]` | 节点计数与压缩率（含事件节点指向的原文总量） |
| `prune --file F --force [--drop-stale\|--collapse-done\|--compact]` | 折叠与降级（**不可逆**） |
| `prune --file F --force --aging --days N --today D [--emit-promote FILE]` | 按生命周期标记老化 |
| `event --file F --topic X --purpose Y --src PATH [--summary S] [--tag T] [--embed]` | 登记**事件节点**；默认**库外指针**，`--embed` 才复制正文到 `<库>.atoms/` |
| `fetch --file F [--summary S] [--topic X] [--purpose Y] [--check] [--out P]` | 取回原文并校验；源被改写报 `DRIFT`、找不到报 `MISS`（**均非零退出**） |
| `plan --root DIR [--work f1,f2,…] [--break-even]` | **门控判定（只读）**：算出工作集占比，给出 `STORE` / `INLINE` |
| `index --root DIR --date D [--out F] [--kind user\|proj\|task]` | 生成跨库索引（只含键层） |
| `find --root DIR --topic X [--purpose Y] [--kind …] [--exact]` | 跨库定位到具体库与子树 |
| `export --file F --format json\|md [--out P]` | 导出互操作格式（**不含正文**） |

**退出码**：`0` 成功 / `1` 受控失败（中文提示，无 traceback）/ `2` 用法错误。

### 存储格式（`.ctx`）

```
#CTX 1 | 2026-10-15 | rev 1 | T1 P2 R3 | E1 | scope:proj
top: 本次会话的总目标
T1 品牌定位
  P1.1 确定目标人群
    R 25-35 岁新中产 [约束]
  P1.2 旧版内核
    E 旧版内核实现 | src:scripts/ctx.py | bytes:90554 | sha256:3f6ed874c97053c2
```

- 缩进是语义：`T` 顶格、`P` 缩进 2 格、`R` 与 `E` 缩进 4 格；
- 头部可选的 `| scope:user|proj|task[:限定名]` 声明作用域（顺序固定写在计数之后）；
- 头部可选的 `| E<n>` 段声明事件节点数（位置固定在 R 计数之后、`scope:` 之前；**有 E 行却不声明是结构错误**）；
- `E` 行**只存指针与指纹，不放正文**。`read` 默认只回摘要，要原文须显式 `--fetch`（带回哈希校验）。`E` 这个行首标记本身就是原子性标记，不占行尾标记位；
- 行尾 ` [xxx]` 是标记（`约束` / `偏好` / `待办` / `进行中` / `已完成` / `已结` / `已废` / `待补` / `钉住` / `长期` / `暂态`）；
- `//` 起首为注释，解析时忽略；
- 编号一经分配**不复用、不重排**（`prune --compact` 例外，且会声明引用失效）；
- 头部计数自动维护，**手改文件必然被 `validate` 发现**。

完整语法、20 条不变量与跨库索引格式见 [`references/store-format.md`](references/store-format.md)；事件节点的判定规则（读完就压存）与门控见 [`SKILL.md`](SKILL.md) §一之二。

> ⚠️ **唯一的写入者是脚本。** 请勿手写 `.ctx` —— 事件节点行尤其不能手写，它带内容哈希，手写等于伪造指纹。需要程序化消费时用 `export --format json`。

### 仓库结构

```
context-lattice/
├── SKILL.md                    面向 AI 宿主的技能主指令
├── README.md                   本文件
├── CHANGELOG.md                变更日志（语义化版本）
├── LICENSE                     MIT
├── CONTRIBUTING.md             贡献指南（含铁律与提交前清单）
├── references/                 判定规则（随包分发）
│   ├── split-rules.md          划分规则（判断内核：作用域归因 + T/P/R 归因）
│   ├── lifecycle.md            作用域、生命周期、老化与升格判据
│   ├── host-memory-adapter.md  宿主记忆适配层
│   ├── store-format.md         .ctx 语法、20 条不变量、跨库索引格式
│   ├── compression-strategy.md 压缩策略与效率对照
│   ├── degradation.md          19 项降级路径
│   └── coverage-matrix.md      八维输入空间覆盖矩阵
├── scripts/
│   └── ctx.py                  确定性内核（唯一写入者，纯标准库）
├── templates/                  空库骨架 / 增量示例 / 会话接入提示
│
│   ── 以下只在仓库里存在，不进技能安装包 ──
├── tests/                      验收集：14 套件 / 615 断言 / 收敛矩阵 / 行为层裁判 / 基准探针 / 负向注入
└── docs/                       质检报告、真实场景测试报告、行为层报告、压测与 A/B 报告
```

### 项目成果

这个仓库把**验证过程**一并公开，而不只是结果：

| 文档 | 内容 |
|---|---|
| [`docs/QC-REPORT.md`](docs/QC-REPORT.md) | 质检报告：5 大铁律自评、套件明细、收敛关卡、**六轮对抗探测的缺陷账**、已知限制（19 条） |
| [`docs/REPORT-realworld.md`](docs/REPORT-realworld.md) | 真实场景测试：真实语料上的层 A 机械测量 + 层 B 两个独立实例的对照 |
| [`docs/REPORT-behavior.md`](docs/REPORT-behavior.md) | 行为层：A/B 对照与作用域探针，**含「这组数据没证明什么」的披露** |
| [`tests/README.md`](tests/README.md) | 怎么跑验收集，以及四条比套件本身更重要的方法论 |
| [`tests/injection/`](tests/injection/) | 负向注入：把判定代码故意弄坏，确认断言真的会红 |

几个数字（都可在本机复现）：

- 验收集 **14 套件 / 615 断言 / 0 失败**，零第三方依赖；
- 收敛矩阵 **35 条规则 / 38 个覆盖格 / 61 个夹具 / 格均 1.00 / 漂移 0**；
- 打包器做**五层平台视角模拟**（落盘 / 路径 / 编码 / 元数据 / 内容），并配 **6 个坏包**，必须全部被拦下；
- 四处负向注入全部被断言捕获（1/1、4/4、2/2、2/2）。

### 安装为 AI 技能

本目录同时是一份 WorkBuddy 技能包：

```bash
cp -r context-lattice ~/.workbuddy/skills/
```

`SKILL.md` 的 frontmatter 里带了触发条件、能力描述与允许的工具集，宿主会按它加载。
**技能安装包只含运行件**（`SKILL.md` / `README.md` / `CHANGELOG.md` / `LICENSE` / `references/` / `scripts/` / `templates/`），不含 `tests/` 与 `docs/`（平台只接受两级目录且顶层有白名单）。

### 设计取舍（诚实声明）

1. **不是 JSON**：外部工具需先 `export --format json`。这是为模型阅读的 token 效率付出的代价。
2. **`read` 是视图不是副本**：头部计数按视图实际内容重算，所以任何视图都可以原样回灌为合法存储。
3. **`read` 严格只读**：不记录访问、不产生任何副作用。因此「升格」只用**写入侧可见的显式信号**判定，不靠引用计数 —— 代价是升格偏保守、可能漏升（有意识地选择保守：漏升少升一条，误升会污染长期记忆）。
4. **`--budget` 是有损的**：它按行序就地在原位丢弃 R 行（保持父子归属不变），并在尾部留 `// 已按预算裁剪 R x/y 条` 明示有损，不假装完整。
5. **要求节点无独立编号**：链上位置即身份，因此「第 3 条要求」这类引用在插入后会漂移。
6. **划分质量取决于模型判断**：结构化存储本身是确定性的，但「一句话该归到哪个事物」「该归到哪一层作用域」都是模型判断。
7. **作用域是自描述、非强制**：内核不因 `scope:` 改变任何读写行为，只做保留与合法性校验。分区语义由宿主适配层赋予，换环境时语义可能不同。
8. **作用域判错无法被内核发现**：内核只能保证值是合法枚举，不能保证值判得对。缓解手段是「宁可窄不可宽」的默认策略 + `mismatch` 提示。
9. **索引会漂移**：索引是某一时刻的快照。**索引缺失或过期时逐库扫描仍完全可用** —— 索引只是加速与导航，不是主干硬依赖。
10. **无环检测**：缩进树天然无环，只可能断裂，断裂由校验器抓。
11. **事件节点不省读取成本**：原文该读还得读。它买的是**「重新找到它」**的成本。压缩买「读得更少」，事件节点买「找得更快」—— 两笔账不要混。
12. **事件节点默认存库外指针**：原文留原处，所以原文件被移走会让节点报 `MISS`，而**不会**把内容还给你；库也不再自包含。要自包含就用 `--embed`，代价是库体涨回原文大小 —— 等于把压缩收益还回去。
13. **「内容校验」与「指针状态」分开报**：内嵌副本能让取回成功，但那不代表指针还指得准。`DRIFT` / `MISS` 影响退出码，`STALE-SRC` 只提示。这是刻意设计 —— 若合并成一条，会退化成「只要有一份副本就永远绿灯」。
14. **未知叶子种类会直接报错，不默认当 R**：内核用显式白名单（`LEAF_KINDS`）分派叶子。将来新增行类型时必须同步登记，否则第一次渲染/计数就会失败 —— **这是刻意的**，因为「缺省当 R」会让新类型被静默降级（改它的内容被判 no-op、折叠时被删掉，一个错误都不报）。

### 降级

无 Python 运行环境、目录不可写、输入超长、编码损坏、作用域判不出来、索引缺失、事件节点取回失败等 **19 种情况**都有明确处置与声明口径，见 [`references/degradation.md`](references/degradation.md)。核心原则：**不静默失败，不编造结果**。

### 许可

MIT，见 [LICENSE](LICENSE)。版权归 **CJQ0304 &lt;cjq0304@outlook.com&gt;** 所有。

---

## English

> **Compress a long conversation into a structured, verifiable, incrementally maintainable store** so a model can recall by reading just the relevant branch instead of re-scanning the raw transcript.
>
> It is **not** a summarizer and **not** a context-window extender. It gives the agreements inside a conversation a persistent form that can be read and written repeatedly **and checked by a program** — think *keeping a ledger*, not *taking a snapshot*.

Version `1.2.1` · Author: CJQ0304 · Pure Python standard library · **Zero dependencies** · MIT

### The problem it solves

Re-reading a long conversation to "recall what we discussed" is **expensive** (tokens grow linearly), **unstable** (the model's focus drifts each time), and **lossy** (summaries are the fastest way to swallow negations and hard constraints). This project instead **files content by ownership first, then reads the layer it needs** — compression comes from *not reading the irrelevant parts*, not from dropping detail.

Three follow-up problems, each with a mechanism:

| Problem | Mechanism |
|---|---|
| **How wide should this apply?** "Always reply in Chinese" and "let's try plan A this once" have identical structure but wildly different lifespans | **Three scopes:** `user` / `proj` / `task` |
| **How long should it live?** Treating a one-off conclusion as a permanent rule causes **memory pollution** — worse than forgetting | **Lifecycle tags:** `钉住` (pinned) / `长期` (long-lived) / `暂态` (ephemeral) + aging |
| **Some material simply won't compress** — code you must edit verbatim, data you must verify line by line | **Event nodes:** the store keeps only *summary + source pointer + byte count + content hash*. The raw text enters neither the store nor the window |

> **A boundary that must be stated:** this does **not** raise your context limit. It changes *the form in which project background enters the window* and *how much you fetch on recall*. **What determines the payoff is the locality of the task, not the tool.**

### What it does not do

| Not | Why |
|---|---|
| Compress already-sent history | That belongs to the host; the skill cannot reach it |
| Save anything within a single session | The raw text is already in the window — there is nothing to re-paste and nothing to fetch back. **Multi-turn chat inside one session is exactly the case that needs this least** |
| Invent information | It merges and organizes; gaps are written as `[待补]` (to be filled) |

### Quick start

**Python 3.8+**, no dependencies.

```bash
git clone https://github.com/CJQ0304/context-lattice.git && cd context-lattice

python scripts/ctx.py init -f .ctxstore/store.ctx --date 2026-10-15 \
  --top "Brand assignment" --stdin <<'EOF'
T Brand positioning
P Define target audience
R 25-35 urban middle class [约束]
R Health-conscious [偏好]
EOF

python scripts/ctx.py read  -f .ctxstore/store.ctx --level 1    # topic names only
python scripts/ctx.py read  -f .ctxstore/store.ctx --topic T1   # the relevant branch
python scripts/ctx.py validate -f .ctxstore/store.ctx

# Incompressible chunk: register it as an event node (raw text never enters the window)
python scripts/ctx.py event -f .ctxstore/store.ctx --topic T1 --purpose P1.1 \
    --src src/kernel.py --summary "Legacy kernel implementation"
python scripts/ctx.py fetch -f .ctxstore/store.ctx --summary "Legacy kernel implementation" --check

# Not sure whether a chunk is worth compressing? Let the script decide.
python scripts/ctx.py plan --root src --work kernel.py,main.py
```

### Measured results (including the negative one)

**Corpus = this very package** (14 files / 86,276 tokens, a mechanical quantity). Reproduce with `python tests/benchmarks/realworld-probe.py`.

| Task | Working set (conservative / optimistic) | Net saving (conservative / optimistic) |
|---|---|---:|
| Add a subcommand to `ctx.py` | 35,536 / 14,994 | **57.7% / 81.5%** |
| Rewrite the `SKILL.md` description | 7,581 / 1,584 | **90.1% / 97.1%** |
| Write a new splitting rule | 3,992 / 1,204 | **94.3% / 97.5%** |
| **Counter-example**: task needs everything | 86,276 = 100% | **−6.7% (more expensive)** |

- **Conservative** = read the involved files whole; **optimistic** = slice at structural boundaries and read only matching sections.
- **Gate** (`plan`, computed by the script — never estimated by the model): local task → `STORE +52.1%`; counter-example → `INLINE −6.7%`; store-level break-even `1 − I/T = 93.3%`.
- **Event nodes**: 14 nodes point at **245,156 bytes** of raw text while occupying **1,749 characters** in the store.
- **Volume invariance**: doubling the raw text (92,484 → 184,969 bytes) moved the view from 82 to 84 tokens.

> ⚠️ **Absolute values are tied to the package revision** — the corpus *is* this package, so editing a doc changes T. Quote the version alongside any number; the ratios are the stable part.
>
> ⚠️ **Anti-expansion, stated honestly**: the store has fixed overhead (header + key lines), so short conversations **expand**. A measured 12-turn / 234-character conversation became 529 characters (**↑ 126%**). `stats` prints negative compression ratios as-is, without truncation.

### Store format (`.ctx`)

```
#CTX 1 | 2026-10-15 | rev 1 | T1 P2 R3 | E1 | scope:proj
top: overall goal of this session
T1 Brand positioning
  P1.1 Define target audience
    R 25-35 urban middle class [约束]
  P1.2 Legacy kernel
    E Legacy kernel implementation | src:scripts/ctx.py | bytes:90554 | sha256:3f6ed874c97053c2
```

- Indentation is semantics: `T` at column 0, `P` at 2, `R` and `E` at 4.
- Optional header segments: `| scope:user|proj|task[:name]` and `| E<n>` (event-node count).
- An `E` line stores **a pointer and a fingerprint, never the body**. `read` returns only the summary; `--fetch` retrieves the original with hash verification.
- Counters in the header are maintained automatically, so **hand-editing the file is always detected by `validate`**.

### Commands

13 subcommands: `init` / `add` / `read` / `tree` / `validate` / `stats` / `prune` / `export`, plus the event-node trio `event` / `fetch` / `plan`, plus multi-store `index` / `find`. Full table in the [Chinese section](#命令参考).

Exit codes: `0` success / `1` controlled failure (message, no traceback) / `2` usage error.

### Repository layout

The runtime package (`SKILL.md`, `references/`, `scripts/`, `templates/`) ships to AI skill platforms. `tests/` and `docs/` exist **only in this repository** — the platform accepts two directory levels and a fixed top-level whitelist.

### Project evidence

This repository publishes the **verification process**, not just the result:

- `tests/` — 14 suites / **615 assertions** / 0 failures, zero third-party dependencies; a convergence matrix (35 rules / 38 cells / 61 fixtures); behavior-layer scorers with their own positive/negative fixtures; benchmark probes; and **negative-injection** scripts that deliberately break the decision code to prove the assertions actually fire.
- `docs/QC-REPORT.md` — quality report including a **defect ledger across six adversarial rounds** and 19 documented limitations.
- `docs/REPORT-realworld.md` — real-corpus measurements plus a two-arm behavioral comparison, **including the case where the skill showed no attributable gain**.
- `docs/REPORT-behavior.md` — A/B and scope probes, with an explicit disclosure of **what the data does not prove**.

### Known limitations

See `docs/QC-REPORT.md` §10. Highlights: `read` is strictly read-only, scope misclassification cannot be detected by the kernel, indexes drift (but per-store scanning always works), event nodes do not reduce reading cost (they reduce *re-finding* cost), and single-session use is a net loss by design.

### License

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 **CJQ0304 &lt;cjq0304@outlook.com&gt;**.
