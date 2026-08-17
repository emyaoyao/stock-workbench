#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
同花顺数据中心「板块资金流向」爬虫（静态分页，无需浏览器/Cookie）
- 概念板块: https://data.10jqka.com.cn/funds/gnzjl/field/code/order/asc/page/N/
- 行业板块: https://data.10jqka.com.cn/funds/hyzjl/field/code/order/asc/page/N/
- 地域板块: https://data.10jqka.com.cn/funds/dyzjl/field/code/order/asc/page/N/
每个分页均为服务端渲染（GBK），直接用 urllib 抓取即可，翻页不受 chameleon 反爬影响。

输出 schema（与 workbench_data.json 的 fund[date][caliber] 对齐）:
  [{"rank":序号,"name":板块名称,"pct":涨跌幅,"inflow":流入资金(亿),
    "outflow":流出资金(亿),"net":净额(亿),"count":公司家数,
    "leader":领涨股,"leader_pct":领涨股涨跌幅,"leader_price":当前价}, ...]
"""
import json, sys, re, argparse, urllib.request, urllib.parse, datetime, time

TYPES = {
    "概念": "gnzjl",
    "行业": "hyzjl",
    "地域": "dyzjl",
}
BASE = "https://data.10jqka.com.cn/funds/{code}/field/code/order/asc/page/{page}/"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}


def fetch(url, retries=4):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("gbk", "ignore")
        except Exception as e:
            last = e
            if i < retries - 1:
                time.sleep(1.0 * (i + 1))
    raise RuntimeError(f"fetch fail {url}: {last}")


def num(s):
    s = (s or "").strip().replace("%", "").replace(",", "")
    if s in ("", "-", "--"):
        return None
    try:
        return float(s)
    except Exception:
        return None


def parse_page(html):
    tb = re.search(r"<tbody>(.*?)</tbody>", html, re.S)
    if not tb:
        return []
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", tb.group(1), re.S)
    out = []
    for row in rows:
        tds = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        if len(tds) < 7:
            continue
        txt = [re.sub(r"<[^>]+>", "", t).strip() for t in tds]
        name = txt[1]
        if not name:
            continue
        out.append({
            "rank": num(txt[0]),
            "name": name,
            "pct": num(txt[3]),
            "inflow": num(txt[4]),
            "outflow": num(txt[5]),
            "net": num(txt[6]),
            "count": num(txt[7]) if len(txt) > 7 else None,
            "leader": txt[8] if len(txt) > 8 else None,
            "leader_pct": num(txt[9]) if len(txt) > 9 else None,
            "leader_price": num(txt[10]) if len(txt) > 10 else None,
        })
    return out


def crawl_one(caliber, code, max_pages=15):
    allrows = []
    seen_pages = 0
    for page in range(1, max_pages + 1):
        url = BASE.format(code=code, page=page)
        try:
            html = fetch(url)
        except Exception as e:
            print(f"  [{caliber}] page {page} 失败: {e}", file=sys.stderr)
            break
        rows = parse_page(html)
        if not rows:
            # 空页 → 到达末页
            if page > 1:
                break
            # 第一页就空，可能路径不对
            print(f"  [{caliber}] page 1 为空，跳过", file=sys.stderr)
            break
        allrows.extend(rows)
        seen_pages += 1
        if len(rows) < 50:
            # 末页不足 50 行
            break
    print(f"  [{caliber}] 共抓取 {seen_pages} 页, {len(allrows)} 条", file=sys.stderr)
    return allrows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--types", default="概念,行业,地域",
                    help="逗号分隔: 概念/行业/地域; all=全部")
    ap.add_argument("--out", default=None, help="输出 JSON 路径")
    ap.add_argument("--merge", default=None,
                    help="将结果合并进该 workbench_data.json 的 fund[date]")
    args = ap.parse_args()

    types = args.types.split(",") if args.types != "all" else list(TYPES.keys())
    result = {}
    for t in types:
        t = t.strip()
        if t not in TYPES:
            print(f"  未知类型 {t}，跳过", file=sys.stderr)
            continue
        print(f"[crawl] {t} ...", file=sys.stderr)
        result[t] = crawl_one(t, TYPES[t])

    payload = {args.date: result}
    out_json = json.dumps(payload, ensure_ascii=False, indent=1)

    if args.merge:
        import os
        data = json.load(open(args.merge, encoding="utf-8"))
        data.setdefault("fund", {})
        data["fund"].setdefault(args.date, {})
        for t, lst in result.items():
            data["fund"][args.date][t] = lst
        json.dump(data, open(args.merge, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print(f"[merge] 已合并 fund[{args.date}] 到 {args.merge}", file=sys.stderr)
    elif args.out:
        open(args.out, "w", encoding="utf-8").write(out_json)
        print(f"[out] 已写出 {args.out}", file=sys.stderr)
    else:
        print(out_json)


if __name__ == "__main__":
    main()
