"""真实任务的行为层机械判据（在派执行者**之前**冻结）。

任务：给存储格式新增 `F` 行类型（缩进 4 格、与 R 同级、带 `w:` 字段），回答四个问题。
判据只看产物文本，逐条打印命中证据；不看模型自述的可信度。

每条判据 = 若干「关键词组」，组内取 or、组间取 and。
所有关键词都对大小写不敏感，同时接受中英文写法（执行者可能混用）。
"""
import json
import os
import re
import sys

# (id, 说明, [[组内 or 的关键词...], [下一组...]])
CHECKS = [
    ("P1-行分派", "命中解析侧的行分派（parse / 缩进 4 格分支）",
     [["parse"], ["indent", "分派", "缩进 4", "缩进4", "indent_request"]]),
    ("P2-渲染", "命中 render（写出 .ctx 文本的那一处）",
     [["render"]]),
    ("P3-计数", "命中头部计数 / 计数函数（T P R 计数与新段声明）",
     [["_counts", "计数", "计数段", "头部声明", "re_header", "T%d P%d R%d", "T P R"]]),
    ("P4-内容指纹", "命中内容指纹（no-op 判定依据）",
     [["_body_key", "body_key", "指纹", "内容指纹"]]),
    ("P5-增量消费", "命中增量归并路径（apply_delta）",
     [["apply_delta", "applydelta", "增量归并", "增量行"]]),
    ("P6-格式文档", "命中 store-format.md 的语法与不变量表",
     [["store-format", "存储格式"], ["ebnf", "语法", "不变量", "grammar"]]),
    ("P7-报错而非静默", "第 3 问主结论答对：既不认识的行会**显式报错**",
     [["显式", "报错", "拒绝", "无法识别", "抛", "exit 1", "退出码 1"]]),
    ("P8-边界区分", "第 3 问是否区分了「入口显式报错」与「其他路径仍有静默风险」两件事",
     [["显式", "exit 1", "退出码 1", "报错"],
      ["静默", "漏掉", "跳过", "降级", "当作 r", "当成 r"],
      ["_fmt_item", "_body_key", "_export_item", "_counts4", "collapse", "prune",
       "apply_delta", "index", "find"]]),
]

# 说明（P8 的口径修正）：
# 第一版 P8 写成「出现『静默』即判为编造」，结果两臂都被判 FAIL —— 但那两处
# 「静默」说的不是本题主结论，而是它们各自额外发现的**其他**静默路径（渲染兜底、
# 折叠删除、多库扫描跳过）。那是正确答案里最有价值的部分，被错判成编造。
# 新口径改成正面判据：既要给出主结论（显式报错），又要指出至少一处其他静默风险。
# —— 这是本项目第 4 处裁判自身误判，记在此处以免再犯。

# 明确错误的表述：把主结论说成「会静默丢弃」而**没有**否定语境。
# 否定语境必须排除：实测第一版漏了这层，把「不会静默丢数据」误报成告警。
BAD_SILENT = re.compile(
    r"(结论|答案|综上|因此|所以)[^\n]{0,24}?"
    r"(?<!不会)(?<!没有)(?<!不是)(?<!未曾)(?<!从不)"
    r"(静默|悄悄|默默地)(地)?(丢|丢弃|丢数据|丢失|忽略)")



def norm(text):
    return text.lower()


def score(text):
    out = []
    low = norm(text)
    for cid, desc, groups in CHECKS:
        evs = []
        ok = True
        for g in groups:
            hit = [k for k in g if k.lower() in low]
            if not hit:
                ok = False
                evs.append("缺[%s]" % "/".join(g[:3]))
            else:
                evs.append("有[%s]" % hit[0])
        out.append((cid, desc, ok, " ".join(evs)))
    # 附加告警（不计分）：把主结论本身说成「静默丢弃」
    m = BAD_SILENT.search(text)
    if m:
        out.append(("告警", "主结论疑似被写成「会静默丢数据」", False,
                    "出现：「%s」" % m.group(0)))
    return out


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else None
    if not path:
        raise SystemExit("用法：rw-behavior-rubric.py <产物文件>")
    text = open(path, encoding="utf-8").read()
    rows = score(text)
    judged = [r for r in rows if r[0] != "告警"]
    n_ok = sum(1 for _c, _d, ok, _e in judged if ok)
    print("产物 = %s（%d 字符）" % (path, len(text)))
    print("-" * 74)
    for cid, desc, ok, ev in rows:
        tag = "WARN" if cid == "告警" else ("PASS" if ok else "FAIL")
        print("%-4s %-16s %s" % (tag, cid, desc))
        print("      证据：%s" % ev)
    print("-" * 74)
    print("得分 %d / %d" % (n_ok, len(judged)))
    print(json.dumps({"score": n_ok, "total": len(judged)}, ensure_ascii=False))
    return 0 if n_ok == len(judged) else 1


if __name__ == "__main__":
    sys.exit(main())
