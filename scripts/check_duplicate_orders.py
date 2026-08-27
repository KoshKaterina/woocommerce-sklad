import json
from woo_moysklad.config import load_config
from woo_moysklad.ms_client import MoySkladClient

cfg = load_config()
ms = MoySkladClient(cfg)
attr_id = cfg.MS_ATTR_ORDER_NUMBER_ID
res = {}
offset = 0
while True:
    r = ms.get("entity/customerorder", {
        "limit": 1000, "offset": offset,
        "filter": "moment>=2026-06-01 00:00:00",
        "order": "moment,asc",
    })
    rows = r.get("rows", [])
    for o in rows:
        num = None
        for a in o.get("attributes", []) or []:
            if a.get("id") == attr_id:
                num = a.get("value")
        if num:
            res.setdefault(str(num), []).append([o.get("name"), o.get("moment")])
    if len(rows) < 1000:
        break
    offset += 1000
dups = {k: v for k, v in res.items() if len(v) > 1}
print("ВСЕГО ЗАКАЗОВ С НОМЕРОМ САЙТА:", sum(len(v) for v in res.values()))
print("ДУБЛЕЙ:", len(dups))
print(json.dumps(dups, ensure_ascii=False, indent=1))
