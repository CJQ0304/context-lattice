# 行为层：评分器与原始产物

这里放两类东西：**裁判**（评分器，可自动跑、有正负夹具）与**它判过的原始产物**（真实模型采样，不可自动复现，必须归档）。

## 裁判

| 文件 | 用途 |
|---|---|
| `ab-rubric.py` | 判「有没有编造 / 关键约束是否存活 / 改口轨迹是否保留」，满分 100（反编造 40 / 约束保真 30 / 变更轨迹 30） |
| `scope-score.py` | 判 13 条作用域探针的判定（严格准确率 + 混淆矩阵 + 两次采样是否一致） |
| `scope-probe.json` | 13 条探针题面与标准答案 |

两个评分器**自己也受负向夹具保护**，见验收集**套件 12** —— 裁判失效会让结论变成假证据。
本项目把这条当硬规矩：裁判误判累计已抓到 5 处，规律一致（判据不自觉地朝「让自己想证明的结论成立」的方向写）。

单独调用：

```bash
RAW=tests/behavior/raw
python tests/behavior/ab-rubric.py \
  "A=$RAW/arm-a-artifact.md" "B1=$RAW/arm-b1-record.md" "B2=$RAW/arm-b2-record.md"
python tests/behavior/scope-score.py \
  "R1=$RAW/scope-r1-answers.json" "R2=$RAW/scope-r2-answers.json" \
  "R3=$RAW/scope-r3-answers.json" "R4=$RAW/scope-r4-answers.json" \
  "N1=$RAW/scope-n1-answers.json" "N2=$RAW/scope-n2-answers.json"
```

## 原始产物（`raw/`）

临时沙箱随时可能被系统清掉，所以**归档件才是权威副本**。这里装了两组互不相关的实验：

### 一、A/B 增益对照（对应 `docs/ab-report.md`）

同一道 12 轮中文需求对话，同判据。

| 文件 | 是哪一臂 |
|---|---|
| `arm-a-artifact.md` | 挂技能的 A 组产物 |
| `arm-a-store.ctx` | A 组产物落成的存储（可用 `python scripts/ctx.py validate -f` 复核，须 EXIT 0） |
| `arm-b1-record.md` / `arm-b2-record.md` | 无任何规则的裸模型两次采样（用来量抖动） |
| `ab-side-a.store.ctx` | 采集脚本 `tests/benchmarks/ab_test.py` 落盘的 A 组存储（`validate` 通过，`read` 可原样回灌） |

**结论是 0%**：三臂都是 100/100。旧版报告里的「400%」来自**作者手写对照组**并叠加了「裸模型结构上拿不到」的维度，方法学已被推翻并留在 `docs/ab-report.md` 里。

### 二、作用域判定探针（对应 `docs/REPORT-behavior.md`）

13 条作用域探针，六次采样：

| 文件 | 是哪一臂 | 实测严格准确率 |
|---|---|---:|
| `scope-r1-answers.json` / `scope-r2-answers.json` | R 组：1.1.0 原规则 | 92.3% / 92.3% |
| `scope-r3-answers.json` / `scope-r4-answers.json` | R 组：1.1.1 补规则后 | 100% / 100% |
| `scope-n1-answers.json` / `scope-n2-answers.json` | N 组：无规则 | 100% / 92.3%（均值 96.2%） |

**这组数据最该看的是它没证明什么**：无规则的裸模型就有 96.2%，说明这一层是**常识主导层**，太容易；
补规则前 R 组（92.3%）甚至**低于**无规则 N 组。真正稳定复现的差异是**抖动**（带规则 0 分歧 vs 无规则 7.7pp）。
详见 `docs/REPORT-behavior.md` 第 122 行的披露。

### 三、真实任务对照（对应 `docs/REPORT-realworld.md`）

给存储格式新增一种行类型 —— 一道**落在技能不利区间**的题。

| 文件 | 是哪一臂 |
|---|---|
| `realworld-answer-A.md` | 臂 A：告知有技能可用 |
| `realworld-answer-B.md` | 臂 B：不告知 |

**两臂同分 8/8**，技能没有带来可归因于技能的收益。判据在看到产物**之前**冻结，沙箱在工作区之外。

```bash
python tests/benchmarks/rw-behavior-rubric.py tests/behavior/raw/realworld-answer-A.md   # 8/8
python tests/benchmarks/rw-behavior-rubric.py tests/behavior/raw/realworld-answer-B.md   # 8/8
```

## 一条必要的声明

行为层结论的采样量是 `n=2`，且执行者与作者同源。**抖动幅度只能当指示值，不能当分布参数**；0 抖动也可能是采样不足的表现，而非真稳定。这道边界写在 `docs/REPORT-behavior.md` 与 `docs/QC-REPORT.md` 第 10 节。

## 关于归档件的行尾

本仓库统一按 LF 落盘（见 `.gitattributes`）。归档产物里只有 `arm-a-artifact.md` 原本是 CRLF，已归一为 LF —— 归一前后重跑判分**均为 100/100、相对增益 0%**，结论未变。除这一类换行差异外，`raw/` 下的内容按原样保存。
