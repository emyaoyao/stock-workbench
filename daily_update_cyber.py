#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
赛博朋克「股票板块数据工作台」每日数据更新（替代原 同花顺每日行情分析工作台）。
数据来源：
  - 同花顺 fuyao API  → 板块强弱(strongWeak) + 2%-6% 区间(band)  [综合/一级行业/概念]
  - 同花顺数据中心爬虫 → 板块资金流向(fund)                            [概念/行业]
流程：拉取 → 合并进 workbench_data.json（原子写，失败保留旧数据）→ compute_swstats → inject_cyber。
注意：环境存在失效本地代理 HTTPS_PROXY，需绕过直连。
"""
import json, os, sys, time, datetime, subprocess, shutil, urllib.request

# ---- 网络：先探测当前代理是否可用，不可用再绕过直连 ----
# 注意：只探根域名会被"假连通"的失效代理骗过（根域名能通，但带 /api 的真实请求被代理丢弃成空响应）。
# 因此改探真实 API 接口，确保代理确实能取到数据才沿用。
PROXY_KEYS = ("HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy")

from hithink_auth import load_key

def _proxy_alive():
    k = load_key()
    if not k:
        return False
    try:
        url = "https://fuyao.aicubes.cn/api/a-share/prices/snapshot?thscodes=600519.SH"
        req = urllib.request.Request(url, headers={"X-api-key": k, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=8) as r:
            d = json.loads(r.read().decode("utf-8"))
        return isinstance(d, dict) and d.get("data") is not None
    except Exception:
        return False

if not _proxy_alive():
    for kk in PROXY_KEYS:
        os.environ.pop(kk, None)
    # 关键：urlopen 首次调用会缓存带代理的默认 opener，仅清 env 不够，必须显式安装"无代理"opener
    urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))
    print("[net] 本地代理不可用/取数失败，已绕过直连。", flush=True)
else:
    print("[net] 代理可用，沿用。", flush=True)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pipeline as P
import crawl_fund_flow as cf

# 云函数适配：WB_DATA_FILE 指向可写位置(如 /tmp)；未设置时回退本地目录(与旧行为一致)
DATA = os.environ.get("WB_DATA_FILE") or os.path.join(HERE, "workbench_data.json")
BAK = DATA + ".bak"
TZ = datetime.timezone(datetime.timedelta(hours=8))


def now():
    return datetime.datetime.now(TZ)


def build_daily():
    """拉取 fuyao(强弱/区间) + 爬虫(资金流向)，返回 (date, payload)。任一失败抛异常。"""
    # ---- fuyao: 目录 ----
    industry = P.load_catalog("industry")
    region = P.load_catalog("region")
    concept = P.load_catalog("cn_concept")
    l1 = [it for it in industry if it["thscode"].startswith("881")]
    all_boards = l1 + region + concept
    cats = {
        "综合": ("同花顺一级板块", all_boards),
        "一级行业": ("同花顺一级行业板块", l1),
        "概念": ("同花顺概念板块", concept),
    }
    # 快照
    board_snaps = {}
    for key, (label, items) in cats.items():
        board_snaps[key] = P.snap_index([it["thscode"] for it in items])
    # 选 ±TOP10
    selected = {}
    ranked_all = {}
    need = {}
    for key, (label, items) in cats.items():
        snap = board_snaps[key]
        ranked = [(P.pct_of(snap.get(it["thscode"])), it["thscode"], it["name"])
                  for it in items]
        ranked = [r for r in ranked if r[0] is not None]
        ranked.sort(key=lambda x: x[0], reverse=True)
        top = ranked[:10]
        bottom = sorted(ranked, key=lambda x: x[0])[:10]
        selected[key] = (top, bottom)
        ranked_all[key] = ranked
        for _, ts, nm in list(top) + list(bottom):
            need[ts] = nm
    # 全口径板块整体涨跌计数（综合=881+882+886 全部板块，不局限于 Top10）
    board_stats = {}
    for key, ranked in ranked_all.items():
        up = sum(1 for r in ranked if r[0] > 0)
        down = sum(1 for r in ranked if r[0] < 0)
        flat = sum(1 for r in ranked if r[0] == 0)
        board_stats[key] = {"up": up, "down": down, "flat": flat, "total": len(ranked)}
    # 成分股 + 个股快照
    cons_map = {}
    all_stocks = set()
    stock_name = {}
    for ts, nm in need.items():
        cons = P.get_constituents(ts)
        cons_map[ts] = cons
        for c in cons:
            stock_name[c["thscode"]] = c["name"]
            all_stocks.add(c["thscode"])
    stock_snap = P.snap_stock(all_stocks)

    def to_sw_entry(e, rank):
        return {
            "rank": rank, "name": e["name"], "pct": e["board_pct"],
            "up": e.get("up", 0), "limit_up": e.get("limit_up", 0),
            "leader": e.get("leader"), "down": e.get("down", 0),
            "laggard": e.get("laggard"),
        }

    sw = {}
    band = {}
    # 预计算每个板块的 entry 一次，供 strong/weak 与 band 两段复用（避免重复调用 build_board_entry）
    for key, (label, _) in cats.items():
        top, bottom = selected[key]
        entries = {}
        for p, ts, nm in list(top) + list(bottom):
            entries[ts] = P.build_board_entry(ts, nm, board_snaps[key].get(ts),
                                              cons_map.get(ts, []), stock_snap)
        strong = [to_sw_entry(entries[ts], i + 1) for i, (p, ts, nm) in enumerate(top)]
        weak = [to_sw_entry(entries[ts], i + 1) for i, (p, ts, nm) in enumerate(bottom)]
        sw[key] = {"strong": strong, "weak": weak}
        boards = []
        for i, (p, ts, nm) in enumerate(list(top) + list(bottom), 1):
            e = entries[ts]
            boards.append({"rank": i, "name": nm, "pct": e["board_pct"],
                           "stocks": [s["name"] for s in e.get("band", [])]})
        band[key] = boards

    dd = P.data_date() or now().strftime("%Y-%m-%d")

    # ---- 爬虫: 资金流向 ----
    fund = {}
    for t in ("概念", "行业"):
        fund[t] = cf.crawl_one(t, cf.TYPES[t])

    payload = {"date": dd, "strongWeak": sw, "band": band, "fund": fund, "boardStats": board_stats}
    return dd, payload


def merge_and_save(dd, payload):
    # 备份
    if os.path.exists(DATA):
        shutil.copyfile(DATA, BAK)
    data = json.load(open(DATA, encoding="utf-8"))
    data.setdefault("strongWeak", {})
    data.setdefault("band", {})
    data.setdefault("fund", {})
    data.setdefault("boardStats", {})
    data["strongWeak"][dd] = payload["strongWeak"]
    data["band"][dd] = payload["band"]
    data["fund"][dd] = payload["fund"]
    data["boardStats"][dd] = payload["boardStats"]
    alld = sorted(set(list(data["strongWeak"].keys()) +
                     list(data["band"].keys()) + list(data["fund"].keys())))
    data["meta"]["date_min"] = alld[0]
    data["meta"]["date_max"] = alld[-1]
    data["meta"]["days_count"] = len(alld)
    data["meta"]["generated_at"] = now().strftime("%Y-%m-%d %H:%M")
    data["meta"]["source"] = "同花顺 (Hithink fuyao API + 数据中心爬虫)"
    json.dump(data, open(DATA, "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    return len(alld)


def already_updated_today():
    """若 workbench_data.json 的 meta.date_max 已是今天(北京时间)，视为已更新，可安全跳过。"""
    if not os.path.exists(DATA):
        return False
    try:
        data = json.load(open(DATA, encoding="utf-8"))
        dm = data.get("meta", {}).get("date_max", "")
        return dm == now().strftime("%Y-%m-%d")
    except Exception:
        return False


def main():
    force = "--force" in sys.argv
    no_inject = "--no-inject" in sys.argv
    if not force and already_updated_today():
        print(f"[skip] meta.date_max 已是今天（{now().strftime('%Y-%m-%d')}），无需重复更新，退出。")
        return
    t0 = time.time()
    print("[build] 拉取 fuyao + 爬虫 ...", flush=True)
    try:
        dd, payload = build_daily()
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"[FAILED] 今日数据更新失败：{e}；保留旧数据，未覆盖。")
        sys.exit(2)
    n = merge_and_save(dd, payload)
    bs = payload["boardStats"]["综合"]
    print(f"[merge] date={dd} days={n} 强弱={len(payload['strongWeak']['综合']['strong'])} "
          f"区间板={len(payload['band']['综合'])} 概念资金={len(payload['fund']['概念'])} "
          f"行业资金={len(payload['fund']['行业'])} "
          f"综合涨跌 涨{bs['up']}/跌{bs['down']}/平{bs['flat']} (共{bs['total']})", flush=True)
    # 附加龙头股(boardLeading)：每天三口径最强十板块 → 成分股当日涨幅 Top10。
    # 依赖全市场日K(parquet) 与板块成分股映射；CI 未准备素材时仅告警跳过，保留历史 boardLeading。
    try:
        import build_board_leading_v2 as _bl2
        _bl2.main()
    except Exception as e:
        print(f"[warn] 龙头股(boardLeading)构建跳过：{e}")
    if no_inject:
        print("[skip] --no-inject：未重新生成 HTML")
        return
    print("[swstats] ...", flush=True)
    subprocess.run([sys.executable, os.path.join(HERE, "compute_swstats.py")], check=False)
    print("[inject] ...", flush=True)
    subprocess.run([sys.executable, os.path.join(HERE, "inject_cyber.py")], check=False)
    print(f"[done] 用时 {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
