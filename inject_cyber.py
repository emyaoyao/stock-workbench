# -*- coding: utf-8 -*-
# 仅用于赛博朋克工作台：将 workbench_data.json 注入 workbench_template2.html -> 股票板块数据工作台.html
import json, os
d = os.path.dirname(__file__)
tpl = open(os.path.join(d, "workbench_template2.html"), encoding="utf-8").read()
data = json.load(open(os.path.join(d, "workbench_data.json"), encoding="utf-8"))
js = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
js = js.replace("</", "<\\/")  # 防止 </script> 截断
out = tpl.replace("__DATA__", js)
dst = os.environ.get("WB_HTML_OUT")
if not dst:
    _ws = r"C:/Users/麋鹿/WorkBuddy/2026-08-10-14-37-27"
    dst = os.path.join(_ws, "股票板块数据工作台.html") if os.path.isdir(_ws) else os.path.join(d, "股票板块数据工作台.html")
open(dst, "w", encoding="utf-8").write(out)
print("written:", dst, "size MB:", round(os.path.getsize(dst) / 1e6, 2))
