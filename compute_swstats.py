#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""计算强弱板块「当月/当年」统计：
- 板块在强势/弱势榜出现次数（按天计数，取 (日期,口径) 维度）
- 领涨股/领跌股当月/当年领涨/领跌次数（同一天同股只计一次）
结果写入 workbench_data.json 的 swStats[caliber][scope] = {strong:{sector,stock}, weak:{sector,stock}}。
scope: 'YYYY-MM' (月) 或 'YYYY' (年)。
"""
import json, collections, os

P = os.path.join(os.path.dirname(__file__), "workbench_data.json")
d = json.load(open(P, encoding="utf-8"))
SW = d.get("strongWeak", {})


def compute(sw):
    stats = {}
    for date, calmap in sw.items():
        y = date[:4]
        m = date[:7]  # YYYY-MM
        for cal, entry in calmap.items():
            stats.setdefault(cal, {})
            for scope in (m, y):  # 月桶与年桶各累计一次（年桶自然累加所有月）
                st = stats[cal].setdefault(scope, {
                    "strong": {"sector": collections.defaultdict(int), "stock": collections.defaultdict(int)},
                    "weak": {"sector": collections.defaultdict(int), "stock": collections.defaultdict(int)},
                })
                # 强势榜：板块按天计数；领涨股按天+去重同股
                strong = entry.get("strong") or []
                sset = set()
                for b in strong:
                    nm = b.get("name")
                    if nm:
                        st["strong"]["sector"][nm] += 1
                    ld = b.get("leader") or {}
                    if ld.get("name"):
                        sset.add(ld["name"])
                for s in sset:
                    st["strong"]["stock"][s] += 1
                # 弱势榜：板块按天计数；领跌股按天+去重同股
                weak = entry.get("weak") or []
                wset = set()
                for b in weak:
                    nm = b.get("name")
                    if nm:
                        st["weak"]["sector"][nm] += 1
                    lg = b.get("laggard") or {}
                    if lg.get("name"):
                        wset.add(lg["name"])
                for s in wset:
                    st["weak"]["stock"][s] += 1

    def conv(o):
        if isinstance(o, collections.defaultdict):
            return {k: conv(v) for k, v in o.items()}
        return o

    return conv(stats)


stats = compute(SW)
d["swStats"] = stats
json.dump(d, open(P, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))

# ---- 校验报告 ----
print("calibers:", list(stats.keys()))
for cal in stats:
    scopes = list(stats[cal].keys())
    month_scopes = sorted(s for s in scopes if len(s) > 4)
    year_scopes = sorted(s for s in scopes if len(s) == 4)
    print(f"  口径 {cal}: 月桶 {len(month_scopes)} 个, 年桶 {len(year_scopes)} 个 -> {year_scopes}")
    if month_scopes:
        sc = month_scopes[0]
        s = stats[cal][sc]["strong"]
        w = stats[cal][sc]["weak"]
        print(f"    样例月 {sc}: 强板块{len(s['sector'])} 强领涨股{len(s['stock'])} | 弱板块{len(w['sector'])} 弱领跌股{len(w['stock'])}")
# 抽样核对：某板块当月强榜次数应 <= 当月交易日数
import random
cal0 = list(stats.keys())[0]
msc = sorted(s for s in stats[cal0] if len(s) > 4)[0]
top_sector = max(stats[cal0][msc]["strong"]["sector"].items(), key=lambda kv: kv[1])
print("当月强榜出现最多板块:", top_sector, "(应 <= 该月交易日数)")
print("OK")
