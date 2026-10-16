# 为 `.ctx` 格式新增 `F <内容>`（外部依赖，缩进 4 格、带 `w:<权重>`）的改动清单

仓库根：`context-lattice/`
唯一实现文件：`context-lattice/scripts/ctx.py`（1984 行，纯标准库，唯一写入者）。

> 关键背景（决定本次答案形态）：仓库里已经存在一个**完全同构的先例**——事件节点 `E` 行。
> `E` 同样是「缩进 4 格、与 R 同级、带 `|` 分隔的元信息字段、在头部占一个可选计数段」的行类型，
> 它**在 `ctx.py` 里被完整实现，却几乎没有写进任何文档**（见下文 §三 末尾的发现）。
> 因此「正确支持 F」= 把 E 走过的**全部代码位点**再走一遍，并补上 E 当年漏做的文档与不变量。

---

## 一、必须修改的文件与具体位置

### A. `scripts/ctx.py`（核心，几乎全部改动都在这里）

#### A-1 模块级常量与正则（文件顶部，L24–L110）

| 位置 | 现有内容 | 必须怎么改 |
|---|---|---|
| `SIGIL_TOPIC/PURPOSE/REQUEST` L27–29 | 行首标记常量 | 新增 `SIGIL_DEP = "F"`（与 L62 的 `SIGIL_EVENT = "E"` 并列） |
| L62 附近（事件节点区块） | `SIGIL_EVENT` / `MAX_SUMMARY` / `RE_EVENT_META` | 新增 `RE_DEP_META = re.compile(r"^F (.+?) \| w:(\d+)$")`（照抄 `RE_EVENT_META` 的「内容非贪婪 + 元信息锚定行尾」写法）；新增权重上限常量（如 `MAX_WEIGHT`）；F 是**同级叶**，不需要新 `INDENT_*`（沿用 `INDENT_REQUEST = 4`，L76） |
| `RE_HEADER` L85–90 | `...#T(\d+) P(\d+) R(\d+)(?: \| E(\d+))?(?: \| scope:...)?(?: \| view:...)?$` | 若 F 独立计数，追加可选段 `(?: \| F(\d+))?`，顺序固定为 **计数 → E → F → scope → view**（与文档「顺序固定」纪律一致）。**注意：新增捕获组会让 `scope` 的组号后移**（现在 `parse()` 用 `head.group(8)` 取 scope，L253），必须同步改 |
| `RE_REQUEST` L94 | `^    R (.+)$` | F 需新增 `RE_DEP`（或直接在 `parse()` 里用 `RE_DEP_META` 前缀匹配，见 A-2） |
| `RE_IDX_*` L102–110 | 索引行正则（含 `RE_IDX_EVENT`） | 仅当**决定把 F 也放进索引**时才需要（默认索引只含键层，R 都不进，建议 F 也不进；见 A-9） |

#### A-2 `parse()`（L223–379）——读盘解析

- L252–254：`meta` 字典增加 `"deps"`；`_check_scope(meta["scope"], ...)` 里的组号随头部正则改动同步。
- L326–356（`if indent == INDENT_REQUEST:` 分支）：
  - 在 `RE_REQUEST` 与 `RE_EVENT_META` 之间插入 F 分支：`m_f = RE_DEP_META.match(text)` → 校验内容（复用 `_check_text`，并**禁用半角竖线 `|`**，与 `_check_summary` 同理）→ 校验权重范围 → 要求 `cur_purpose is not None`（否则报「依赖行之前没有目的行（层级跳跃）」）→ `append({"kind":"F","text":...,"tag":...,"weight":n})`。
  - **L355–356 那条兜底报错必须改文案**：`"缩进 4 格的行必须是 `    R 内容`（要求）或 `    E ...`（事件节点）"` → 加上 `F`。
- L365–377（计数校验）：`_counts4()` 要能数出 F；若 F 独立计数，则像 L371–377 的 E 段一样新增一段 F 计数一致性检查（含 ` | F<n>` 缺失的定向报错）。

#### A-3 `render()`（L386–403）——写盘渲染

- L390–393：新增 `f_seg`，并把它按固定顺序拼进头部。
- L401–402：叶子行统一走 `_fmt_item()`，见 A-4。

#### A-4 `_fmt_item()`（L406–411）

- 现在只有 `E` 分支与「其他=R」的兜底。**必须插入 `kind == "F"` 分支**，输出 `"F %s | w:%d%s"`。否则（若 `parse` 已接受 F 而此处未改）F 会**被静默渲染成 R 行**——这是最典型的丢语义点。

#### A-5 `_counts4()`（L418–429）——计数

- `n_r = sum(... if kind != "E")` 会把 F 误当成 R。必须显式区分 F（新增 `_counts5` 返回 `(T,P,R,E,F)`，或改造签名）。
- **该函数的每个调用点都要改**（4 元组解包会全部报错）：
  - `parse()` L365
  - `render()` L388
  - `_fmt_counts()` L734
  - `cmd_event()` L1169
  - `cmd_validate()` L1359
  - `cmd_stats()` L1388
  - `cmd_prune()` L1560

#### A-6 `_body_key()`（L441–460）——内容指纹（决定是否写盘/递增 rev）

- L458–459 的 `else` 把非 E 一律当 R。**必须加 F 分支**（`F|text|weight|tag`），否则「只改权重」会被判为 no-op → **不写盘、不递增 rev**（谎报未修改）。

#### A-7 `apply_delta()`（L533–684）——增量写入路径

- L540–542：`stat` 字典新增 `f_new / f_dup / f_upd / f_retag`。
- L555–556：分派正则 `^(T|P|R|E)(?:[ \t]+(.*))?$` **必须加入 `SIGIL_DEP`**，否则写 F 直接报错。
- L557–565：`"增量行无法识别"` 的报错文案与 `.行首标记只能是 T/P/R/E` 提示要补 F。
- 在 `sigil == SIGIL_EVENT` 分支（L612–655）之后新增 `sigil == SIGIL_DEP` 分支：用 `RE_DEP_META` 解析 `"%s %s" % (SIGIL_DEP, text)`；要求 `cur_purpose` 非空；按归一化内容去重；支持 `w` 权重与 tag 的更新（对齐 E 的 `e_upd/e_retag`）。

#### A-8 读取/视图侧

- `_fmt_counts()` L732–735、`_fmt_stat()` L738–750：补 F 的计数与 `+xF` 展示。
- `cmd_add()` L784（报错文案「T/P/R 增量」）与 **L792** `if has_delta and sum(stat.values()) == 0` —— 若 `apply_delta` 新增了 `f_*` 键，这里会自动包含；但若忘记加，纯 F 增量会被误报「增量内容为空」。
- `cmd_read()` L871–879 + `_count_lines()` L888–894：`_count_lines` 目前只数 `    R `/`    E ` 前缀，**必须加 F 计数**，否则视图头部计数与内容不符 → 视图无法回灌（破坏「read 视图可原样回灌」这条既有不变量）。
- `_apply_budget()` L897–920：目前只对 `    R ` 行做丢弃、其余「一律保留」。F 会**自动落入保留分支**。必须**显式决策**并写入文档：F 跟 R 一样可裁，还是像 E 一样受保护。

#### A-9 其他子命令

- `cmd_tree()` L1349–1353：`else` 分支默认按 R 打印一条 `· `。**必须加 F 分支**。
- `cmd_validate()` L1357–1383：头部打印串；新增 F 的结构/计数一致性校验。
- `cmd_stats()` L1386–1410：`nodes` 行与 `total` 加入 F。
- `cmd_prune()` L1456–1571（**风险最高**）：
  - `--drop-stale` L1478–1482：`removed_r += ... if kind != "E"` 会把 F 算进 R，需拆出 `removed_f`。
  - `--collapse-done` L1490：`kept = [it for it in p["items"] if it.get("kind") == "E"]` —— **F 会被一并删除**（静默删数据），必须显式决策 F 的去留。
  - `--aging` L1524–1545：L1525 `if it.get("kind") == "E"` 的 else 把 F 当 R 参与老化，需显式分支。
  - L1560–1570：收尾计数/输出串。
- `_emit_promote()` L1429–1454：L1444 写死 `"%s %s [%s]" % (SIGIL_REQUEST, ...)`——升格一个 F 时**会输出成 R 行**，必须按 `kind` 选择行首标记。
- `_export_item()` L1574–1579 / `cmd_export()` L1582–1647：JSON 的 items 与 md 分支（L1623–1629）都要加 F 表示（含 `weight`）。
- `cmd_index()` L1713–1780：索引按设计「只含键层」，R 都不进，建议 **F 也不进**（保持体积与内容量无关）；若要进则需同时改 `RE_IDX_*` L108–110 与 L1743–1747 的发射逻辑。**无论选哪种都要在文档写明**。
- `_iter_events()` L432–438：若新增需要遍历 F 的命令，需要并列的 `_iter_deps()`。
- `cmd_event/fetch/_do_fetch/_read_fetch` L1011–1178：若 F 不做「取回原文」这类动作，则**不需要改**（F 只是带权重的文本叶）。
- `build_parser()` L1847–1959：如需 `dep` 子命令或 `add --weight`/`--dep` 参数；同时 `description` L1853、`add` 帮助 L1862/L1873、`read --budget` 帮助 L1880（「裁剪 R 层」）等**列举 T/P/R 的文案**都要同步。

#### A-10 版本号

- `FMT_VERSION = "1"` L24 与 `store-format.md` 开头的「改格式必须同步改解析器并递增 version」。F 是**纯增量可选段**（同 E 先例，当年未升版本），建议保持 `"1"`，但 `SKILL.md` / `README.md` 的技能 version（现 `1.1.1`）应升 minor。

### B. `references/store-format.md`（格式规范的唯一口径来源）

- **§二 行语法**：新增产生式 `依赖行 ::= "    " "F " 内容 " | w:" 正整数 (标记)?`，并补「F 无编号、与 R 同级」说明；头部可选的 ` | F<n>` 段需并入头部语法描述。
- **§二 缩进表**（L41–45）：第 4 格那行目前只写 `    R `，要加上 `    F `。
- **§三 十字链表落盘归一化**（L47–63）：补 F 作为 R 的兄弟叶的示例。
- **§四 编号规则**：F 与 R 一样**无编号**。
- **§五 标记体系**：说明 F 同样可带行尾标记（含生命周期标记）。
- **§六 不变量（V1–V15）**：见 §二（本回答第二部分）。
- **§七 跨库索引格式**：写明 F 是否进索引。
- **§八 已知取舍**：新增一条（F 与 R 的语义差异、权重取值边界）。

### C. 其他文档

- `SKILL.md`：**§五 存储格式速览**（L116–137，含缩进说明与示例）、**§四 子命令速查**（L98–112，如有新命令）、**§二/§三**中「T/P/R 三行式」措辞（L62、L66）、frontmatter 描述（L3–7）。
- `README.md`：L5 版本、结构图（L28–40）、「存储格式」节（L162–179）、命令参考（L143–158）、设计取舍（L207–218）。
- `references/split-rules.md`：**§一 第 4 步**（「要做什么」→P /「做成什么样」→R）需补「外部依赖」的归因判据；**§七** 的「T/P/R 三行式增量」措辞。
- `references/compression-strategy.md`：**§一 手段 3**（`--budget` 只提 R）、**§三 prune 表**（对 F 的效果）、**§四 绝对不能压掉的东西**。
- `references/coverage-matrix.md`：**§一 D3/新增维度**、**§四 N5** 报错文案。
- `templates/skeleton.ctx`（L3「三种行首标记」）与 `templates/delta.txt`（L2「T/P/R」）：若想让模型学会写 F，应补示例。
- `CHANGELOG.md`：新增版本条目（当前停在 1.1.1，**连 E 都没记**）。

---

## 二、必须新增/更新的结构不变量

现有 V1–V15 定义在 `store-format.md §六`，逐条实现在 `ctx.py parse()`。F 需要：

**更新（改语义的既有规则）**

- **V6**（现：「缩进 4 格的行必须匹配**要求行**语法」）→ 改为「缩进 4 格的行必须是 **R 行**或 **F 行**，且二者各自匹配自己的完整语法」。实现点是 `parse()` L326–356 的缩进 4 分支。
- **V12**（名称不得含半角方括号）**扩展到 F 的内容**；并**新增**：F 内容不得含半角竖线 `|`（它是 ` | w:` 的分隔符，与 `_check_summary` 禁 `|` 同源）。

**新增**

- **F 权重必填且合法**：F 行必须带 ` | w:<正整数>` 段，权重在允许区间内（如 `1..N`）；缺失/非数字/超界 → `EXIT 1`（对应实现：`RE_DEP_META` 不匹配时的定向报错）。
- **F 的层级不变量**：F 行之前必须已出现目的行（P），否则「层级跳跃」——与 V8（R 行前须有 P）对称。实现：`parse()` 里 `cur_purpose is None` 检查。
- **F 计数一致**（若采用独立计数段）：头部 ` | F<n>` 必须与实际 F 行数逐项相符；有 F 行而头部未声明 → 报错（完全对齐 E 的 L371–377 机制）。
- **F 参与 `rev` 语义**：`_body_key()` 必须把 F 的内容/权重/tag 计入指纹（否则改权重被当 no-op）。
- **（口径类不变量，须写进文档）F 在 `--budget` 与 `prune` 中的去留**：F 是可裁的叶，还是像 E 一样受保护；`--drop-stale` / `--collapse-done` / `--aging` 对 F 的动作必须逐条写明，否则会出现「静默删除」。

---

## 三、现有代码遇到 F 行时：静默丢数据还是显式报错？

**结论：当前代码在两条入口都「显式报错」，不会静默丢数据；真正会静默出问题的，是「只改了一半」的实现路径，以及若干默认按 R 处理的 else 分支。**

### 1) 读取一个含 F 行的 `.ctx` → 显式报错（受控，EXIT 1）

- 函数：`parse()`（L223–379），缩进 4 格分支 L326–356。
- 行为：`    F 依赖外部 API | w:3` 走到 `if indent == INDENT_REQUEST:`；`RE_REQUEST` 不匹配 → `_split_tag()` 得 `text="F 依赖外部 API | w:3"` → `RE_EVENT_META` 不匹配 → `raw[indent:].startswith("E ")` 为假 → 落到 L355 的 `raise CtxError("缩进 4 格的行必须是 `    R 内容`（要求）或 `    E ...`（事件节点）")`。
- 经 `load()`（L467–482）与 `main()`（L1968–1972）包装后：stderr 打 `ERROR …`、退出码 1。**带行号、中文定位，不丢数据**。
- 附带影响：`index` / `find` 走 `_scan_stores()`（L1681–1703），坏库被 `except CtxError` 捕获 → 进 `skipped` → `_report_skipped()`（L1706–1710）报到 stderr 并**整库跳过**（退出码不变）。属于「响亮地跳过」，非静默。

### 2) 用 `add` 写入 F 行 → 显式报错（受控，EXIT 1）

- 函数：`apply_delta()`（L533–684），分派正则 L555–556 `^(T|P|R|E)(?:[ \t]+(.*))?$`。
- 行为：`F 依赖外部 API | w:3` 不匹配任何行首标记 → L564 `raise CtxError("增量行无法识别：…；行首标记只能是 T / P / R / E")`。**显式报错，不写盘、不吞行。**

### 3) 真正危险的「静默」点（实现 F 时必须逐个排查）

这些函数当前就把「非 E 的叶子」**默认当成 R**；一旦 `parse()` 先接受了 `kind:"F"`，而它们没同步改，F 会被**静默当作 R**处理（渲染/计数/导出/删除都错）：

- `_fmt_item()` L406–411 `else` → **F 被渲染成 R 行**（写盘即永久丢语义）。
- `_counts4()` L427–429 `kind != "E"` → **F 被计进 R**（头部计数随之错位/掩盖）。
- `_body_key()` L458–459 `else` → F 改动不计入指纹 → **改权重被判 no-op，不写盘**。
- `_export_item()` L1579、`cmd_tree()` L1352、`cmd_prune()` L1480/L1540 → F 被当 R 导出/显示/老化。
- `cmd_prune() --collapse-done` L1490 `if kind == "E"` → **F 会被直接删掉**（对 F 而言是不可逆的静默丢数据，最严重）。

### 4) 一个应如实指出的既有不一致（影响本次改动面）

事件节点 `E` 在 `ctx.py` 里是**完整实现**（L62–72、L108–110、L337–349、L612–655、L1011–1178 等），但：

- `store-format.md §二 行语法` **没有 E 行**；`§六 不变量` **没有任何 E 相关条目**；头部 ` | E<n>` 段也**没写进 §二 头部语法**；
- `SKILL.md` / `README.md` / `CHANGELOG.md` / 各 references **全文无 E/事件节点**（`grep` 仅命中 `ctx.py`）；
- `ctx.py` L50 注释引用的 `references/event-nodes.md` **在仓库中不存在**。

即 **代码领先文档一整个特性**。这意味着：若照 E 的老路给 F 也只改代码、不改文档，会**复制同一笔文档债**。本次应把 F 的规范、不变量与 `E` 的缺失说明一起补齐。

---

## 四、实际读过 / 检索过的文件清单（相对仓库根）

全部文件（本仓库仅 12 个文件，均已读取）：

- `context-lattice/SKILL.md`
- `context-lattice/README.md`
- `context-lattice/CHANGELOG.md`
- `context-lattice/LICENSE`（列出，未细读内容）
- `context-lattice/scripts/ctx.py`（精读：常量/正则 L24–110、`parse` L223–379、`render`/`_fmt_item`/`_counts4`/`_iter_events`/`_body_key` L386–460、`apply_delta` L533–705、`cmd_init/_fmt_counts/_fmt_stat/cmd_add` L712–809、`cmd_read/_count_lines/_apply_budget` L832–920、事件节点与 fetch 段 L923–1178、`cmd_plan` L1186–1333、`cmd_tree/cmd_validate/cmd_stats` L1336–1410、`_emit_promote/cmd_prune` L1429–1571、`_export_item/cmd_export` L1574–1647、`_iter_stores/_scope_mismatch/_scan_stores/cmd_index/cmd_find` L1654–1840、`build_parser/main` L1847–1985）
- `context-lattice/references/store-format.md`
- `context-lattice/references/split-rules.md`
- `context-lattice/references/compression-strategy.md`
- `context-lattice/references/degradation.md`
- `context-lattice/references/coverage-matrix.md`
- `context-lattice/references/lifecycle.md`（已列出、按内容形态判断无需为 F 改动，未逐行引用）
- `context-lattice/references/host-memory-adapter.md`（同上）
- `context-lattice/templates/skeleton.ctx`
- `context-lattice/templates/delta.txt`
- `context-lattice/templates/start-prompt.md`

检索动作：对全仓 `grep` 了 `SIGIL_TOPIC|SIGIL_PURPOSE|SIGIL_REQUEST|SIGIL_EVENT|FMT_VERSION`、`事件节点|event|src:`、`缩进|行首标记|R 行|E 行` 等，用于确认「E 只存在于代码、文档无记录」以及「所有 `kind != "E"` 的默认-R 分支」清单。
