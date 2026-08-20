# -*- coding: utf-8 -*-
"""取数层格式预检（防数据源格式漂移静默变形）。

在 analyze.py 之前【独立】运行：读取 fetch_indices.py 生成的 *_raw.md（K线 markdown，数据源为 eastmoney HTTP 接口，格式兼容 westock），
断言其结构/字段完整、行数充足、数值可解析、日期单调。任一项不达标 → 打印详情并 sys.exit(1)，
迫使每日自动化在此中止、绝不进入 analyze/build/部署，避免用错位/空数据污染看板。

EXIT 码约定（与 validate.py 守门纪律一致）：0=通过(警告除外), 1=主指数取数层变形/异常。

设计意图（R148）：
  _raw.md 格式一旦漂移（列改名、删列、返回错误文本而非表、取数截断），
  下游 analyze.read_kline_md 的容错逻辑会把缺失列静默回退为 close，不报错但让数据无声失真——
  这是取数层唯一未被下游闸口完全兜住的盲区。本脚本在变形传入 build_data 之前就拦截。

真实 feed 列集（务必对齐，勿凭空假设）：
  当前数据源已统一为 eastmoney HTTP 接口（fetch_indices.py 与 build_data._ensure_raw 均按
  `| date | open | high | low | last | volume |` 六列写入全部指数，含上证 sh000001），
  不再依赖旧 westock-data CLI。
  → 因此 low/volume/amount 虽目前齐全，仍保留为【可选列】以维持对数据源格式漂移的容错；
    date / last(close) / open / high 为【必含列】，缺失即判失败（会静默回退为 close 而失真）。

主/副分级（匹配现有优雅降级哲学）：
  主指数 sh000001 变形 → 硬性阻断（EXIT=1，无它整个看板作废）。
  副指数 sh000300/sz399006 变形/取数失败 → 仅告警、不阻断（共振 breadth 已有→0 兜底，
    且实测 sh000300 偶发 rate-limit，过度阻断会误伤整次更新）。
"""
import os
import sys
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))

# 主指数：驱动浪型/目标，缺失即全盘作废 → 硬性阻断。
CRITICAL_FILES = ["sh000001_raw.md"]
# 副指数：跨指数共振 / 跨市场联动用，缺失时 breadth→0 兜底 → 仅告警不阻断。
SECONDARY_FILES = ["sh000300_raw.md", "sz399006_raw.md",
                   "sh000016_raw.md", "sh000905_raw.md", "sh000688_raw.md",
                   "hkHSI_raw.md", "hkHSTECH_raw.md", "usINX_raw.md", "usIXIC_raw.md"]

DATE_COL = "date"
REQUIRED_CLOSE = ("close", "last")      # 任一存在即可（westock 收盘列名为 last）
EXPECTED_COLS = ("open", "high")        # 真实 feed 两指数均含；缺失会静默回退为 close，必须断言
OPTIONAL_COLS = ("low", "volume", "amount", "exchange")  # 上证历来缺失，不断言

# 行数下限：--limit 1300 正常产出 ~1250 根日K；留足余量，低于此视为取数截断/失败。
MIN_ROWS = int(os.environ.get("PREFLIGHT_MIN_ROWS", "600"))
# 允许的坏行比例（非数字/缺日期/乱序）。westock 偶发坏行可容忍，整体大量缺失则失败。
MAX_BAD_ROW_RATIO = 0.05

blocking = []   # 主指数致命失败 → 阻断
warnings = []   # 副指数失败 → 仅告警


def fail(blocking_list, msg):
    blocking_list.append(msg)
    print("  !! " + msg)


def parts_of(line):
    return [p.strip() for p in line.strip().strip("|").split("|")]


def first_present(header, names):
    for n in names:
        if n in header:
            return header[n]
    return None


def parse_date(s):
    if s is None:
        return None
    s = str(s).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def check_file(fname, critical):
    target = blocking if critical else warnings
    before = len(target)
    tag = "主指数" if critical else "副指数"
    print("[预检:%s] %s" % (tag, fname))
    path = os.path.join(BASE, "data", fname)
    if not os.path.exists(path):
        fail(target, "文件缺失: %s（eastmoney/fetch_indices 未生成？路径/命令错误？）" % fname)
        return
    size = os.path.getsize(path)
    if size <= 200:
        fail(target, "文件过小(%d B): %s（eastmoney/fetch_indices 可能报错/返回空，非 K 线表）" % (size, fname))
        return

    with open(path, encoding="utf-8") as f:
        text = f.read()

    # 1) 必须含 markdown 表（表头行 + 分隔行 + 数据行）
    lines = [ln.strip() for ln in text.splitlines()
             if ln.strip().startswith("|") and "---" not in ln]
    if len(lines) < 2:
        fail(target, "无有效 markdown 表(仅 %d 行): %s（eastmoney/fetch_indices 可能返回错误文本/JSON，而非 K 线表）"
             % (len(lines), fname))
        return

    # 2) 表头列名映射
    header = {h.lower(): i for i, h in enumerate(parts_of(lines[0]))}
    close_idx = first_present(header, REQUIRED_CLOSE)
    date_idx = header.get(DATE_COL)
    if close_idx is None:
        fail(target, "缺失收盘列(%s): %s（eastmoney 列改名！read_kline_md 将无法取收盘）"
             % ("/".join(REQUIRED_CLOSE), fname))
    if date_idx is None:
        fail(target, "缺失日期列(%s): %s（eastmoney 列改名！）" % (DATE_COL, fname))
    missing_expected = [c for c in EXPECTED_COLS if c not in header]
    if missing_expected:
        fail(target, "缺失必含列 %s: %s（eastmoney 列改名！将被静默回退为 close，高低点/开盘失真）"
             % (missing_expected, fname))
    # 数值校验所需列索引（open/high 必含、low 可选；缺失已在上方判失败/告警）
    open_idx = header.get("open")
    high_idx = header.get("high")
    low_idx = header.get("low")

    def _oknum(x):
        if x is None:
            return False
        try:
            float(str(x).replace(",", "").strip())
            return True
        except (ValueError, TypeError):
            return False

    # 3) 解析数据行：行数、坏行比例、日期单调
    #    可选列(low/volume 等)不要求；主/必含列已在列名层断言过存在性。
    n_total = 0
    n_bad = 0
    n_ok = 0
    prev_date = None
    direction = 0   # 0=未知, 1=升序, -1=降序（westock 输出为最新在前=降序，两种均合法）
    for ln in lines[1:]:
        parts = parts_of(ln)
        if len(parts) < 4:          # 至少 date + close + open + high
            n_bad += 1
            n_total += 1
            continue
        n_total += 1
        d = parts[date_idx] if (date_idx is not None and date_idx < len(parts)) else None
        dt = parse_date(d)
        if dt is None:
            n_bad += 1
            continue
        # 日期单调性：允许整体升序或整体降序，仅当方向中途反转才判坏（防 westock 偶发乱序/重复）
        if prev_date is not None and direction != 0:
            if (direction == 1 and dt < prev_date) or (direction == -1 and dt > prev_date):
                n_bad += 1
                prev_date = dt
                continue
        elif prev_date is not None and direction == 0:
            if dt > prev_date:
                direction = 1
            elif dt < prev_date:
                direction = -1
        prev_date = dt
        # 必含/可选列数值可解析性：close 已查；open/high 为必含列，非数字会静默回退为
        # close 造成高低点/开盘失真（docstring 设计意图），须拦；low 可选但非数字亦计坏行。
        bad_val = False
        if close_idx is not None and close_idx < len(parts) and not _oknum(parts[close_idx]):
            bad_val = True
        for _name, _idx in (("open", open_idx), ("high", high_idx), ("low", low_idx)):
            if _idx is not None and _idx < len(parts) and not _oknum(parts[_idx]):
                bad_val = True
        if bad_val:
            n_bad += 1
            continue
        n_ok += 1

    # 用有效行数 n_ok 做门槛，与 analyze.load_data 的 len(rows)（有效行）口径一致；
    # 否则含坏行的 n_total 通过时，下游 analyze 用有效行数判定可能 <600 而崩溃。
    if n_ok < MIN_ROWS:
        fail(target, "数据行数不足(%d < %d): %s（取数截断/部分失败）" % (n_ok, MIN_ROWS, fname))
    ratio = (n_bad / n_total) if n_total else 1.0
    if ratio > MAX_BAD_ROW_RATIO:
        fail(target, "坏行比例过高(%.1f%% > %.0f%%): %s（大量非数字/乱序行，eastmoney 格式可能已变）"
             % (ratio * 100, MAX_BAD_ROW_RATIO * 100, fname))
    added = len(target) > before
    if not added:
        print("    OK: %d 行有效, 坏行 %d (%.1f%%)" % (n_ok, n_bad, ratio * 100))
    return added


def main():
    print("=== 取数层格式预检 (preflight) ===")
    for fn in CRITICAL_FILES:
        check_file(fn, critical=True)
    for fn in SECONDARY_FILES:
        check_file(fn, critical=False)
    # 汇总告警（副指数）已在 check_file 内登记到 warnings

    if blocking:
        print("\n[预检失败·阻断] %d 项主指数致命问题，中止更新（不进入 analyze/build/部署）：" % len(blocking))
        for m in blocking:
            print("  - " + m)
        _write_report(blocking, warnings)
        sys.exit(1)

    print("\n[预检通过] 主指数取数层格式/字段完整，可继续 analyze/build。")
    if warnings:
        print("[预检告警] 副指数存在问题（不阻断，共振 breadth 将走→0 兜底）：")
        for m in warnings:
            print("  * " + m)
    _write_report(blocking, warnings)
    sys.exit(0)


def _write_report(blocking_list, warning_list):
    try:
        with open(os.path.join(BASE, "data", "_preflight_report.txt"), "w", encoding="utf-8") as f:
            f.write("preflight @ %s\n" % datetime.now().isoformat(timespec="seconds"))
            f.write("blocking(%d):\n" % len(blocking_list))
            f.write("\n".join(blocking_list) + "\n")
            f.write("warnings(%d):\n" % len(warning_list))
            f.write("\n".join(warning_list) + "\n")
    except OSError:
        pass


if __name__ == "__main__":
    main()
