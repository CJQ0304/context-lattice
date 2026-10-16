# 贡献指南

## 这个项目是什么

把长会话压成一份**结构化、可校验、可增量维护**的存储，让模型回顾时只读相关那一支，而不是回翻原文。
一句话定位：**它不是摘要工具，是给对话里的约定一个可反复读写、还能被程序校验的持久形态**（建台账，不是拍照片）。

## 环境

- **Python 3.8+**，**零第三方依赖**（核心与验收集只用标准库）。
- 可选依赖，只在基准与打包时需要：
  - `tiktoken` —— 用真实 token 计量（`tests/benchmarks/` 下的探针）
  - `PyYAML` —— 打包器的 frontmatter 解析（`tests/pack_and_verify.py`）

```bash
python tests/test_all.py      # 期望 14 套件 / 613 断言 / 0 失败 / EXIT 0
```

## 目录分工

| 路径 | 说明 |
|---|---|
| `scripts/ctx.py` | **确定性内核，也是唯一的写入者**。所有语法、不变量、子命令都在这里 |
| `references/` | 判定规则（拆分、作用域与生命周期、格式规范、压缩策略、降级路径、覆盖矩阵） |
| `templates/` | 空库骨架、增量写法示例、会话接入提示 |
| `SKILL.md` | 面向 AI 宿主的技能主指令 |
| `tests/` | 验收集、收敛矩阵规格、行为层评分器与原始产物、基准探针、负向注入 |
| `docs/` | 质检报告、真实场景测试报告、行为层报告、压测与 A/B 报告 |

`tests/` 与 `docs/` **只在仓库里存在，不进技能安装包**。边界定义在 `tests/pkg_boundary.py` —— 打包器与悬空引用检查器都从这里取，**不要另写一份**（两份口径迟早漂移，而漂移的表现是「检查器说 PASS 但包里有问题」，或者反过来）。

## 铁律

1. **绝不手写 `.ctx`。** 头部计数由脚本维护，手改必然被 `validate` 发现。事件节点的行**尤其**不能手写 —— 它带内容哈希，手写等于伪造指纹。
2. **改格式就要同步加不变量。** `.ctx` 是自定义文本格式，没有 schema 兜底；「本地能落盘但下游会误解析」的唯一防线是 `references/store-format.md` 里的不变量表（当前 20 条）。新增能力还要补 `references/degradation.md` 的降级路径。
3. **每类坏法配一条负向夹具，并问一句「这段判定代码这次真的执行了吗」。** 断言全绿可能只是因为它测的不是那段代码。`tests/injection/` 下有三个注入脚本，专门用来证明断言真的会红。
4. **断言红了先怀疑断言。** 本项目的裁判误判累计 5 处，规律是：写断言时不自觉地朝「让自己想证明的结论成立」的方向写。判据要写正面条件，告警词要排除否定语境，改完配正负控。
5. **不许为了让自己好看而挑场景。** 实测数据一律带上不利区间（工作集接近全量时净省为负），负值原样打印、不截断。行为层结论如果只是「和裸模型打平」，就写打平。
6. **口径只有一处真相。** 交付包边界在 `pkg_boundary.py`；收敛夹具的唯一来源是 `tests/convergence_spec.json`；配置文件里的数字不许在文档里手抄第二遍。
7. **报错要指到真实原因。** 「工具自身调错」与「被测对象有问题」必须分开报 —— 报错指向错误比不报错更坑。

## 改完的提交前清单

```bash
python tests/test_all.py                                   # 14 套件 / 613 断言 / 0 失败
python tests/convergence_matrix.py --spec tests/convergence_spec.json --audit scripts/ctx.py
python tests/pack_and_verify.py                            # 需 PyYAML
python tests/injection/inject-drift.py                     # 1/1 命中
python tests/injection/inject-kind.py                      # 4/4 命中
python tests/injection/inject-refs.py                      # 2/2 命中
```

**改了会随包分发的文件（`SKILL.md` / `README.md` / `CHANGELOG.md` / `references/` / `scripts/` / `templates/`）就必须**：

1. 在 `CHANGELOG.md` 顶部加版本条目（patch = 补规则 / 修 bug，minor = 新增能力，major = 破坏性变更）；
2. 同步提升 `SKILL.md` 的 `version`；
3. 重跑打包器（它会校验 `SKILL.md` / `README.md` / `CHANGELOG.md` **三处版本一致**）。

> 打包器会打印一个带校验和的自包含 zip。产物是技能平台的安装包，**不含** `tests/` 与 `docs/`。

## 提交信息

- 中文、祈使句、说明**为什么**而不只是改了什么。
- 修 bug 时把「怎么发现的」写进去（哪条测试 / 哪次真实任务 / 谁指出的）—— 这个项目的缺陷账靠这个维持可追溯。

## 已知限制

见 `docs/QC-REPORT.md` 第 10 节：行为层无法进自动回归、真实场景测试 `n=2` 且执行者与作者同源、悬空引用检查器只认 ASCII 文件名、索引会漂移、作用域判错内核发现不了。**这些是明确记录在案的取舍，不是待办**；要改的话请先说明代价。
