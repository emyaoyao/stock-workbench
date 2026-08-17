# -*- coding: utf-8 -*-
"""
发布「股票板块数据工作台」到 GitHub Pages（固定链接，每日更新）：
1) 由模板生成轻量外壳 publish/index.html（把 __DATA__ 占位符替换为 undefined，运行时 fetch('data.json')）
2) 导出 publish/data.json（完整数据集）
3) 经 GitHub Contents API 把 index.html + data.json 推送到 emyaoyao/stock-workbench（main 分支）
   链接永久固定：https://emyaoyao.github.io/stock-workbench/
"""
import base64, json, os, pathlib, urllib.request, urllib.error, datetime

HERE = pathlib.Path(__file__).resolve().parent
ROOT = pathlib.Path(os.environ.get("WB_ROOT", HERE))
TPL = HERE / "workbench_template2.html"
DATA = HERE / "workbench_data.json"
PUB = pathlib.Path(os.environ.get("WB_PUBLISH_DIR", HERE / "publish"))
PUB.mkdir(exist_ok=True)

# ---------- 1) 外壳版 index.html ----------
tpl = TPL.read_text(encoding="utf-8")
n = tpl.count("__DATA__")
assert n == 1, "模板占位符数量异常(%d)，请检查是否又在注释里写了 __DATA__" % n
shell = tpl.replace("__DATA__", "undefined")
(PUB / "index.html").write_text(shell, encoding="utf-8")

# ---------- 2) data.json ----------
dat = json.loads(DATA.read_text(encoding="utf-8"))
(PUB / "data.json").write_text(json.dumps(dat, ensure_ascii=False), encoding="utf-8")
# .nojekyll：禁用 Jekyll，让 GitHub Pages 直接以静态文件服务（避免大 data.json 触发构建报错）
(PUB / ".nojekyll").write_text("", encoding="utf-8")
print("built shell index.html =", (PUB / "index.html").stat().st_size,
      "bytes | data.json =", (PUB / "data.json").stat().st_size, "bytes")

# ---------- 3) 推送 ----------
def put(path, sha=None):
    b64 = base64.b64encode(path.read_bytes()).decode()
    url = "https://api.github.com/repos/emyaoyao/stock-workbench/contents/" + path.name
    msg = {"message": "daily update " + datetime.date.today().isoformat(), "content": b64}
    if sha:
        msg["sha"] = sha

    def do(m):
        req = urllib.request.Request(url, data=json.dumps(m).encode(), method="PUT")
        req.add_header("Authorization", "Bearer " + TOK)
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("Content-Type", "application/json")
        return urllib.request.urlopen(req, timeout=240)

    try:
        return do(msg).status
    except urllib.error.HTTPError as e:
        if e.code == 422:  # 已存在，需带 sha 更新
            g = urllib.request.Request(url, method="GET")
            g.add_header("Authorization", "Bearer " + TOK)
            cur = json.load(urllib.request.urlopen(g, timeout=30))
            msg["sha"] = cur["sha"]
            return do(msg).status
        raise

if os.environ.get("WB_SKIP_PUSH"):
    print("WB_SKIP_PUSH 已设置：跳过 Contents API 推送，产物位于", PUB)
else:
    # Token：优先环境变量（CI/云端），回退本地 publish/.ghtoken
    TOK = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not TOK:
        TOK = (HERE / "publish" / ".ghtoken").read_text(encoding="utf-8").strip()
    for f in (PUB / "index.html", PUB / "data.json", PUB / ".nojekyll"):
        st = put(f)
        print("pushed", f.name, "-> HTTP", st)
    print("DONE fixed link: https://emyaoyao.github.io/stock-workbench/")
