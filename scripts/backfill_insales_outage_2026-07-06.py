"""BACKFILL InSales (Tangemshop) — добор заказов, пропущенных из-за простоя токена МС
(токен умер 05.07 21:49, восстановлен 06.07 10:32). Последний синхронизированный
Tangemshop-заказ = 18104; 18105+ в МС отсутствуют.

⚠️ InSales API работает ТОЛЬКО с российского IP (иначе 403). Запускать на сервере
интеграции ИЛИ через VPN с российским IP. С обычного Mac — 403.

Идемпотентно: перед созданием проверяет наличие в МС (как reconciliation).
Без аргумента — DRY-RUN. С аргументом APPLY — пишет в ПРОД МойСклад.

  python scripts/backfill_insales_outage_2026-07-06.py            # dry-run
  python scripts/backfill_insales_outage_2026-07-06.py APPLY      # создать
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from woo_moysklad.config import load_config
from woo_moysklad.ms_client import MoySkladClient
from woo_moysklad.insales.client import InSalesClient
from woo_moysklad.core.counterparty_handler import CounterpartyHandler
from woo_moysklad.core.product_matcher import ProductMatcher
from woo_moysklad.core.order_processor import OrderProcessor
from woo_moysklad.core.reconciliation import Reconciliation
from woo_moysklad.core.source_adapter import InSalesSourceAdapter

# Начало окна простоя (с запасом — последний успех 18104 был 05.07 12:31)
SINCE = "2026-07-05T12:00:00+03:00"
APPLY = len(sys.argv) > 1 and sys.argv[1] == "APPLY"

cfg = load_config()
if not (cfg.INSALES_API_KEY and cfg.INSALES_PASSWORD):
    raise SystemExit("INSALES creds не заданы в .env")

ms = MoySkladClient(cfg)
insales = InSalesClient(cfg)
ok, detail = insales.check_access()
if not ok:
    raise SystemExit(f"InSales API недоступен (нужен российский IP): {detail}")

cp = CounterpartyHandler(ms)
pm = ProductMatcher(ms)
op = OrderProcessor(cfg, ms, cp, pm)
adapter = InSalesSourceAdapter(insales, op)
recon = Reconciliation(cfg, ms, [adapter])

orders = insales.get_orders(updated_since=SINCE)
orders.sort(key=lambda o: int(o.get("number", o.get("id", 0)) or 0))
mode = "APPLY (ПРОД)" if APPLY else "DRY-RUN"
print(f"\n=== InSales backfill {mode} === заказов обновлено с {SINCE}: {len(orders)}\n")

for o in orders:
    num = o.get("number")
    key = adapter.ms_order_number(o)            # "<number> Tangemshop"
    existing = recon._find_order_in_ms(key)
    fin = o.get("financial_status"); paid_at = o.get("paid_at")
    if existing:
        print(f"  {key:<20} есть в МС (МС {existing.get('name')}, payed={existing.get('payedSum',0)/100:.0f})"
              + ("  ⚠ надо оплатить" if adapter.should_mark_paid(o) and existing.get('payedSum',0)==0 else ""))
        if APPLY and adapter.should_mark_paid(o) and existing.get('payedSum',0)==0:
            for p in adapter.mark_paid(o): print(f"     ✓ оплата: {p.get('name')}")
        continue
    print(f"  {key:<20} ❗ НЕТ в МС | fin={fin} paid_at={paid_at} | позиций={len(o.get('order_lines',[]))}")
    if APPLY:
        for r in adapter.process(o): print(f"     ✓ создан в МС: {r.get('name')}")
        if adapter.should_mark_paid(o):
            for p in adapter.mark_paid(o): print(f"     ✓ оплата: {p.get('name')}")

print("\n=== готово ===" + ("" if APPLY else "  (dry-run)"))
