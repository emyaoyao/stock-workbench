#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
同花顺每日板块及个股行情分析 —— 数据管道
输出 pipeline_data.json（HTML 工作台消费的结构化数据）。

板块口径（经用户确认）：
  一级板块      = 一级行业(881) + 地区(882) + 概念(886) 合并排名（不含884细分行业）
  一级行业板块  = 仅 881 一级行业
  概念板块      = 仅 886 概念
涨停规则（按代码前缀 + ST 名称）：
  北交所 8/4/920 -> 30%   科创板 688/689 -> 20%   创业板 300/301 -> 20%
  主板 ST/*ST -> 5%       其余主板 -> 10%
停牌(prev_price 为 null 或 pct 为 None)不计入任何统计；平盘(pct==0)不计涨/跌但参与领涨/最弱判定。
区间 [2%,6%] 含端点。
"""
import json, os, sys, time, datetime
import urllib.request, urllib.parse

BASE = "https://fuyao.aicubes.cn"
API = BASE + "/api"
TMP = os.environ.get("WB_TMP", os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(TMP, "pipeline_cache.json")
os.makedirs(TMP, exist_ok=True)

# ---- 读取 API Key：优先环境变量（CI/云端），回退本地 credentials.env ----
_key = os.environ.get("HITHINK_FINANCE_API_KEY", "")
if not _key:
    _cred = r"C:/Users/麋鹿/AppData/Roaming/hithink-finance/credentials.env"
    try:
        with open(_cred, encoding="utf-8") as f:
            for line in f:
                if line.upper().startswith("HITHINK_FINANCE_API_KEY"):
                    _key = line.split("=", 1)[1].strip()
                    break
    except FileNotFoundError:
        pass
assert _key, "未找到 HITHINK_FINANCE_API_KEY（CI 请设置环境变量，本地请确认 credentials.env）"

FRESH = "--fresh" in sys.argv


def get(path, params=None, retries=4):
    url = API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"X-api-key": _key, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read().decode("utf-8")
            return json.loads(raw)
        except Exception as e:
            last = e
            if i < retries - 1:
                time.sleep(1.2 * (i + 1))
    raise RuntimeError(f"API fail {path} {params}: {last}")


def snap_index(thscodes):
    out = {}
    items = list(thscodes)
    for i in range(0, len(items), 80):
        chunk = items[i:i + 80]
        d = get("/a-share-index/prices/snapshot", {"thscodes": ",".join(chunk)})
        for it in d.get("data", {}).get("item", []):
            out[it["thscode"]] = it
    return out


def snap_stock(thscodes):
    out = {}
    items = list(thscodes)
    for i in range(0, len(items), 80):
        chunk = items[i:i + 80]
        d = get("/a-share/prices/snapshot", {"thscodes": ",".join(chunk)})
        for it in d.get("data", {}).get("item", []):
            out[it["thscode"]] = it
    return out


def limit_pct(ticker, name):
    if ticker[:1] in ("8", "4") or ticker.startswith("920"):
        return 30.0
    if ticker.startswith("688") or ticker.startswith("689"):
        return 20.0
    if ticker.startswith("300") or ticker.startswith("301"):
        return 20.0
    if name.startswith("*ST") or name.startswith("ST"):
        return 5.0
    return 10.0


def is_limit_up(pct, ticker, name):
    # 涨停 = 涨跌幅落在「当日限幅 ±0.06」区间内（向上且触板），避免异常值误判
    if pct is None or pct <= 0:
        return False
    L = limit_pct(ticker, name)
    return (L - 0.06) <= pct <= (L + 0.06)


def load_catalog(tag):
    d = get("/a-share-index/catalog/ths-index-list", {"tag": tag})
    return d.get("data", {}).get("item", [])


def get_constituents(thscode):
    d = get("/a-share-index/constituents/ths-stock-list", {"thscode": thscode})
    return d.get("data", {}).get("item", [])


def data_date():
    # 全市场分页模式 timestamp 为最新有效时间
    d = get("/a-share/prices/snapshot", {"limit": 1, "offset": 0})
    ts = d.get("data", {}).get("timestamp")
    if not ts:
        return None
    return datetime.datetime.fromtimestamp(ts / 1000, tz=datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y-%m-%d")


def pct_of(it):
    if not it:
        return None
    p = it.get("price_change_ratio_pct")
    if p is None:
        return None
    try:
        return float(p)
    except Exception:
        return None


def build_board_entry(thscode, name, board_snap, cons, stock_snap):
    bpct = pct_of(board_snap)
    valid = []  # (name, pct, ticker)
    missing = 0
    for c in cons:
        st = c["thscode"]
        s = stock_snap.get(st)
        if s is None:
            missing += 1
            continue
        p = pct_of(s)
        if p is None:
            # 停牌（无有效涨跌幅）
            continue
        valid.append((c["name"], p, c.get("ticker", st.split(".")[0])))
    n = len(valid)
    up = sum(1 for _, p, _ in valid if p > 0)
    down = sum(1 for _, p, _ in valid if p < 0)
    limit_up = sum(1 for nm, p, tk in valid if is_limit_up(p, tk, nm))
    if n == 0:
        leader = None
        laggard = None
    else:
        leader = max(valid, key=lambda x: x[1])
        laggard = min(valid, key=lambda x: x[1])
    band = sorted([v for v in valid if 2.0 <= v[1] <= 6.0], key=lambda x: x[1])
    return {
        "thscode": thscode,
        "name": name,
        "board_pct": round(bpct, 3) if bpct is not None else None,
        "up": up,
        "down": down,
        "limit_up": limit_up,
        "constituents_valid": n,
        "constituents_total": len(cons),
        "constituents_missing": missing,
        "leader": {"name": leader[0], "pct": round(leader[1], 3)} if leader else None,
        "laggard": {"name": laggard[0], "pct": round(laggard[1], 3)} if laggard else None,
        "band": [{"name": v[0], "pct": round(v[1], 3)} for v in band],
    }


def main():
    if (not FRESH) and os.path.exists(CACHE):
        print("[cache] 使用已缓存 pipeline_cache.json")
        DATA = json.load(open(CACHE, encoding="utf-8"))
    else:
        try:
            print("[fetch] 加载目录 ...")
            industry = load_catalog("industry")   # 881 + 884
            region = load_catalog("region")        # 882
            concept = load_catalog("cn_concept")    # 886

            l1_industry = [it for it in industry if it["thscode"].startswith("881")]
            all_boards = l1_industry + region + concept  # 一级板块 = 一级行业(881)+地区(882)+概念(886)，不含884细分行业

            cats = {
                "level1": ("同花顺一级板块", all_boards),
                "industry1": ("同花顺一级行业板块", l1_industry),
                "concept": ("同花顺概念板块", concept),
            }

            # 排名快照
            print("[fetch] 板块快照排名 ...")
            board_snaps = {}
            for key, (label, items) in cats.items():
                codes = [it["thscode"] for it in items]
                snap = snap_index(codes)
                board_snaps[key] = snap
                print(f"  {label}: {len(items)} 板块, 命中快照 {len(snap)}")

            # 选 ±TOP10
            selected = {}  # key -> list of (thscode, name)
            for key, (label, items) in cats.items():
                snap = board_snaps[key]
                ranked = []
                for it in items:
                    p = pct_of(snap.get(it["thscode"]))
                    if p is None:
                        continue
                    ranked.append((p, it["thscode"], it["name"]))
                ranked.sort(key=lambda x: x[0], reverse=True)
                top = ranked[:10]
                bottom = sorted(ranked, key=lambda x: x[0])[:10]
                selected[key] = (top, bottom)
                print(f"  {label}: TOP1={top[0][2]} {top[0][0]:+.2f}%  BOTTOM1={bottom[0][2]} {bottom[0][0]:+.2f}%")

            # 收集需统计的板块（60个）及成分股
            need = {}  # thscode -> name
            for key, (top, bottom) in selected.items():
                for _, ts, nm in list(top) + list(bottom):
                    need[ts] = nm
            print(f"[fetch] 拉取 {len(need)} 个板块成分股 ...")
            cons_map = {}
            stock_name = {}
            all_stocks = set()
            for ts, nm in need.items():
                cons = get_constituents(ts)
                cons_map[ts] = cons
                for c in cons:
                    stock_name[c["thscode"]] = c["name"]
                    all_stocks.add(c["thscode"])
            print(f"[fetch] 拉取 {len(all_stocks)} 只个股快照 ...")
            stock_snap = snap_stock(all_stocks)

            # 组装
            out = {}
            for key, (label, _) in cats.items():
                top, bottom = selected[key]
                top_entries = []
                for rank, (p, ts, nm) in enumerate(top, 1):
                    e = build_board_entry(ts, nm, board_snaps[key].get(ts), cons_map.get(ts, []), stock_snap)
                    e["rank"] = rank
                    top_entries.append(e)
                bottom_entries = []
                for rank, (p, ts, nm) in enumerate(bottom, 1):
                    e = build_board_entry(ts, nm, board_snaps[key].get(ts), cons_map.get(ts, []), stock_snap)
                    e["rank"] = rank
                    bottom_entries.append(e)
                out[key] = {"label": label, "top": top_entries, "bottom": bottom_entries}

            dd = data_date()
            now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
            DATA = {
                "meta": {
                    "data_date": dd or now.strftime("%Y-%m-%d"),
                    "updated_at": now.strftime("%Y-%m-%d %H:%M"),
                    "source": "同花顺 (Hithink)",
                    "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
                    "status": "ok",
                    "last_success_at": now.strftime("%Y-%m-%d %H:%M"),
                    "note": "盘中快照" if dd == now.strftime("%Y-%m-%d") and now.hour < 15 else "收盘数据",
                },
                "categories": out,
            }
            json.dump(DATA, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            print(f"[done] dataDate={DATA['meta']['data_date']} update={DATA['meta']['updated_at']}")
        except Exception as e:
            # 数据源获取失败：不要用旧数据伪装成当天数据
            import traceback
            traceback.print_exc()
            prev = None
            if os.path.exists(CACHE):
                try:
                    prev = json.load(open(CACHE, encoding="utf-8"))
                except Exception:
                    prev = None
            now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
            DATA = {
                "meta": {
                    "data_date": (prev or {}).get("meta", {}).get("data_date", now.strftime("%Y-%m-%d")),
                    "updated_at": now.strftime("%Y-%m-%d %H:%M"),
                    "source": "同花顺 (Hithink)",
                    "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
                    "status": "failed",
                    "last_success_at": (prev or {}).get("meta", {}).get("last_success_at")
                                      or (prev or {}).get("meta", {}).get("updated_at") or "无",
                    "note": f"获取失败：{e}",
                },
                "categories": (prev or {}).get("categories", {}) if prev else {},
            }
            print(f"[FAILED] 今日数据更新失败：{e}")

    # 写出供 HTML 读取的最终数据
    json.dump(DATA, open(os.path.join(TMP, "pipeline_data.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # 生成单文件 HTML 工作台（注入 SEED_DATA）
    try:
        tpl = open(os.path.join(TMP, "workbench_template.html"), encoding="utf-8").read()
        seed = json.dumps(DATA, ensure_ascii=False)
        html = tpl.replace("__SEED_JSON__", seed)
        out_html = os.path.join(os.path.dirname(TMP), "同花顺每日行情分析工作台.html")
        open(out_html, "w", encoding="utf-8").write(html)
        print(f"[html] 已生成 {out_html}")
    except Exception as e:
        print(f"[html] 生成失败：{e}")
    # 完整性自检
    print("\n=== 完整性自检 ===")
    for key, c in DATA["categories"].items():
        for kind, lst in (("TOP", c["top"]), ("BOTTOM", c["bottom"])):
            for e in lst:
                miss = e.get("constituents_missing", 0)
                if miss:
                    print(f"  [warn] {c['label']}/{kind} {e['name']}: {miss} 只成分股快照缺失")
    print("OK")


if __name__ == "__main__":
    main()
