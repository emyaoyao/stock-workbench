# -*- coding: utf-8 -*-
"""
回填 DATA.boardLeading：
每天 × 三口径(综合/一级行业/概念) × 当天最强十板块 → 该板块成分股当日涨幅 Top10 作为龙头股代表。

设计为可自包含运行（GitHub Actions 也能跑，无需改 daily.yml）：
  - duckdb 缺失时自动 pip install；
  - 成分股映射 / 全市场日K 缺失时，用 hithink key 自动下载与重新生成（落到 .blcache 目录）；
  - 任何素材获取失败仅告警跳过，绝不破坏已有的 boardLeading（merge_and_save 只写当天字段，历史保留）。
数据来源：hithink 全市场日K(parquet) + 板块成分股映射。
"""
import json, os, sys, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
PARQUET_DUMP = "https://fuyao.aicubes.cn/api/dump/market-dumps/daily-k/download-url"


def _ensure_duckdb():
    try:
        import duckdb  # noqa
        return True
    except Exception:
        subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "duckdb"], check=False)
        try:
            import duckdb  # noqa
            return True
        except Exception:
            return False


def board_of(code):
    if code.startswith("688"):
        return "科"
    if code.startswith("8") or code.startswith("4") or code.endswith(".BJ") or code.startswith("920"):
        return "北"
    if code.startswith("300") or code.startswith("301"):
        return "创"
    return "主"


def _hithink_get(path, key):
    import urllib.request, urllib.error
    url = "https://fuyao.aicubes.cn" + path
    req = urllib.request.Request(url, headers={"X-api-key": key, "Accept": "application/json"})
    for _ in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                import time
                time.sleep(2)
                continue
            return None
        except Exception:
            import time
            time.sleep(1.5)
            continue
    return None


def _download_parquet(cache, key):
    import urllib.request
    d = _hithink_get("/api/dump/market-dumps/daily-k/download-url", key)
    if not d or d.get("code") != 0:
        return None
    pu = d.get("data", {}).get("presigned_url")
    if not pu:
        return None
    out = os.path.join(cache, "daily_k_10y.parquet")
    try:
        urllib.request.urlretrieve(pu, out)
        return out
    except Exception:
        return None


def _ensure_maps(cache, key):
    """返回 (board_name_ts, board_cons_name) 字典；缺失则自动生成。"""
    bn_path = os.path.join(cache, "board_name_ts.json")
    bc_path = os.path.join(cache, "board_cons_name.json")
    if os.path.exists(bn_path) and os.path.exists(bc_path):
        return json.load(open(bn_path, encoding="utf-8")), json.load(open(bc_path, encoding="utf-8"))

    from pipeline import load_catalog, get_constituents
    bn = {}
    for tag in ("cn_concept", "industry"):
        for it in load_catalog(tag):
            bn[it["name"]] = it["thscode"]
    json.dump(bn, open(bn_path, "w", encoding="utf-8"), ensure_ascii=False)

    # 从 workbench_data 收集所有历史强板块名 → thscode → 成分股
    wd = json.load(open(os.path.join(HERE, "workbench_data.json"), encoding="utf-8"))
    sw = wd["strongWeak"]
    target_ts = {}
    for dt, blk in sw.items():
        for cal in ("综合", "一级行业", "概念"):
            for e in blk.get(cal, {}).get("strong", []):
                if e["name"] in bn:
                    target_ts[e["name"]] = bn[e["name"]]
    bcn = {}
    import time
    for name, ts in target_ts.items():
        d = _hithink_get(f"/api/a-share-index/constituents/ths-stock-list?thscode={ts}", key)
        if d and d.get("code") == 0:
            bcn[ts] = [{"code": it["thscode"], "name": it["name"]} for it in d["data"]["item"]]
        time.sleep(0.1)
    json.dump(bcn, open(bc_path, "w", encoding="utf-8"), ensure_ascii=False)
    return bn, bcn


def main(parquet_path=None, rec_dir=None, data_path=None):
    if not _ensure_duckdb():
        print("[warn] boardLeading 跳过：duckdb 不可用（pip install 失败）。")
        return
    import duckdb

    proj = HERE
    # 缓存目录：优先 rec_dir，其次项目内 .blcache；并兼容本地 recover_828
    cache = rec_dir or os.path.join(proj, ".blcache")
    os.makedirs(cache, exist_ok=True)
    fallbacks = [cache, os.path.join(os.path.dirname(HERE), "recover_828")]
    bn_file = bc_file = None
    for d in fallbacks:
        if os.path.exists(os.path.join(d, "board_name_ts.json")) and os.path.exists(os.path.join(d, "board_cons_name.json")):
            bn_file = os.path.join(d, "board_name_ts.json")
            bc_file = os.path.join(d, "board_cons_name.json")
            cache = d
            break

    from hithink_auth import load_key
    key = load_key()

    if not bn_file:
        if not key:
            print("[warn] boardLeading 跳过：缺成分股映射且无 hithink key 重新生成。")
            return
        bn, bcn = _ensure_maps(cache, key)
        bn_file = os.path.join(cache, "board_name_ts.json")
        bc_file = os.path.join(cache, "board_cons_name.json")
    else:
        bn = json.load(open(bn_file, encoding="utf-8"))
        bcn = json.load(open(bc_file, encoding="utf-8"))

    p = parquet_path or os.path.join(cache, "daily_k_10y.parquet")
    if not os.path.exists(p):
        if not key:
            print("[warn] boardLeading 跳过：全市场日K 不存在且无 key 下载。")
            return
        print("[boardLeading] 下载全市场日K ...", flush=True)
        p = _download_parquet(cache, key) or p
    if not os.path.exists(p):
        print("[warn] boardLeading 跳过：全市场日K 下载失败。")
        return

    if data_path is None:
        data_path = os.path.join(proj, "workbench_data.json")
    wd = json.load(open(data_path, encoding="utf-8"))
    sw = wd["strongWeak"]

    CALIBERS = ["综合", "一级行业", "概念"]
    targets, missing_ts = [], 0
    for dt, blk in sw.items():
        for cal in CALIBERS:
            for e in blk.get(cal, {}).get("strong", [])[:10]:
                ts = bn.get(e["name"])
                if not ts:
                    missing_ts += 1
                    continue
                targets.append((dt, cal, e["name"], ts))

    cons_rows, code_name = [], {}
    for ts, members in bcn.items():
        for m in members:
            cons_rows.append((ts, m["code"]))
            code_name[m["code"]] = m["name"]

    print(f"[boardLeading] targets={len(targets)} missing_ts={missing_ts} cons_rows={len(cons_rows)} codes={len(code_name)}")

    con = duckdb.connect()
    con.execute("CREATE TABLE targets(dt VARCHAR, caliber VARCHAR, board_name VARCHAR, board_ts VARCHAR)")
    con.executemany("INSERT INTO targets VALUES (?,?,?,?)", targets)
    con.execute("CREATE TABLE cons(board_ts VARCHAR, code VARCHAR)")
    con.executemany("INSERT INTO cons VALUES (?,?)", cons_rows)

    print("[boardLeading] computing daily returns ...", flush=True)
    con.execute(f"""
    CREATE TABLE ret AS
    SELECT thscode,
           CAST(to_timestamp(CAST(CAST(date_ms AS BIGINT)/1000 AS BIGINT)) AS DATE) AS dt,
           (close_price / LAG(close_price) OVER (PARTITION BY thscode ORDER BY date_ms) - 1) * 100 AS pct
    FROM read_parquet('{p}')
    WHERE interval='1d' AND thscode IN (SELECT code FROM cons)
    """)
    rows = con.execute("""
    SELECT t.dt, t.caliber, t.board_name, c.code, r.pct
    FROM targets t
    JOIN cons c ON c.board_ts = t.board_ts
    JOIN ret r ON r.thscode = c.code AND r.dt = t.dt
    WHERE r.pct IS NOT NULL
    """).fetchall()

    from collections import defaultdict
    buckets = defaultdict(list)
    for dt, cal, bname, code, pct in rows:
        buckets[(dt, cal, bname)].append((pct, code))

    boardLeading = {}
    for (dt, cal, bname), lst in buckets.items():
        # 确定性排序：涨幅降序；涨幅相同按代码升序（避免 duckdb JOIN 行序不定导致的并列随机）
        lst.sort(key=lambda x: (-x[0], x[1]))
        boardLeading.setdefault(dt, {}).setdefault(cal, {})[bname] = [
            {"name": code_name.get(c, c), "code": c, "board": board_of(c), "pct": round(float(pc), 2)}
            for pc, c in lst[:10]
        ]

    wd["boardLeading"] = boardLeading
    wd.pop("leading", None)
    json.dump(wd, open(data_path, "w", encoding="utf-8"), ensure_ascii=False)

    n = len(boardLeading)
    tot = sum(len(cd) for d in boardLeading.values() for cd in d.values())
    print(f"[boardLeading] 写入完成: 日期数={n}  (日期×口径×板块)={tot}")


if __name__ == "__main__":
    main()
