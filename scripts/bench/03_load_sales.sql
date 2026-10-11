-- 03_load_sales.sql: order-to-cash facts (orders, items, invoices, payments, shipments, returns),
-- purchasing, stock movements, general ledger, subscriptions.
\set ON_ERROR_STOP on
SET client_min_messages = warning;
SET session_replication_role = replica;
SET max_parallel_workers_per_gather = 0;
SET timezone = 'UTC';
SET work_mem = '256MB';
SELECT setseed(0.4343);

-- ---------------------------------------------------------------- sales orders (~1M) with growth, seasonality and weekday dips
CREATE TEMP TABLE day_counts AS
SELECT d::date d,
       round((680 + 560 * (d::date - date '2023-10-01') / 1106.0)
             * CASE WHEN extract(month FROM d) IN (11, 12) THEN 1.35 WHEN extract(month FROM d) IN (7, 8) THEN 0.85 ELSE 1 END
             * CASE WHEN extract(isodow FROM d) IN (6, 7) THEN 0.6 ELSE 1 END
             * (0.9 + 0.2 * random()))::int n
FROM generate_series(date '2023-10-01', date '2026-10-10', interval '1 day') d;

CREATE TEMP TABLE o_raw AS
SELECT row_number() OVER (ORDER BY dc.d, k)::bigint order_id,
       (dc.d + random() * interval '1 day')::timestamptz order_dt,
       bench_gen.skew(50000, 2.2) cust_id, random() rs, random() rc,
       (1 + floor(power(random(), 1.8) * 6))::smallint item_count
FROM day_counts dc CROSS JOIN LATERAL generate_series(1, dc.n) k;

CREATE TEMP TABLE region_wh AS
SELECT * FROM (VALUES (1, ARRAY[1,1,2,3,3,4,5,6]), (2, ARRAY[7,8,8,9]), (3, ARRAY[10,10,11,12,13,13,14,15,16,17,18]),
                      (4, ARRAY[19,19,20,21,22,23]), (5, ARRAY[1,3])) v(region_id, whs);
CREATE TEMP TABLE promo_pool AS SELECT array_agg(promo_code ORDER BY promo_code) codes FROM sales.promo_codes;

CREATE TEMP TABLE o (LIKE sales.orders);
INSERT INTO o (order_id, order_no, cust_id, order_dt, status, channel, sales_rep_id, territory_id, warehouse_id, promo_code,
               currency_code, fx_rate_to_usd, item_count, payment_terms, ship_to_country, ship_to_city, ship_to_postal, bill_to_country,
               requested_ship_dt, promised_dt, is_gift, gift_message, priority_flag, source_system, external_ref, notes, metadata,
               created_at, created_by)
SELECT r.order_id, 'SO-' || to_char(r.order_dt, 'YYMM') || '-' || lpad(r.order_id::text, 8, '0'), r.cust_id, r.order_dt,
       (CASE WHEN r.rs < 0.003 THEN 'draft'
             WHEN age < 2 THEN CASE WHEN r.rs < 0.5 THEN 'pending' ELSE 'confirmed' END
             WHEN age < 6 THEN CASE WHEN r.rs < 0.3 THEN 'confirmed' WHEN r.rs < 0.97 THEN 'shipped' ELSE 'cancelled' END
             WHEN age < 14 THEN CASE WHEN r.rs < 0.35 THEN 'shipped' WHEN r.rs < 0.96 THEN 'delivered' ELSE 'cancelled' END
             WHEN r.rs < 0.043 THEN 'cancelled' WHEN r.rs < 0.065 THEN 'returned' WHEN r.rs < 0.25 THEN 'delivered' ELSE 'completed' END)::sales.order_status,
       ch, CASE WHEN ch IN ('sales_rep', 'partner') THEN a.owner_employee_id END,
       c.territory_id, wh.whs[1 + floor(random() * array_length(wh.whs, 1))::int],
       CASE WHEN ch = 'web' AND random() < 0.08 THEN pp.codes[1 + floor(random() * array_length(pp.codes, 1))::int] END,
       c.default_currency, fx.usd_rate, r.item_count,
       CASE WHEN c.segment IN ('enterprise', 'commercial') THEN 'NET' || a.payment_terms_days ELSE 'PREPAID' END,
       c.shipping_country, a.city, a.postal_code, c.billing_country,
       (r.order_dt + interval '2 days')::date, (r.order_dt + bench_gen.rint(3, 10) * interval '1 day')::date,
       is_gift, CASE WHEN is_gift AND random() < 0.6 THEN 'Happy holidays from the team!' END,
       random() < 0.05, CASE ch WHEN 'edi' THEN 'edi_gateway' WHEN 'marketplace' THEN 'amazon' WHEN 'web' THEN 'webshop' ELSE 'crm' END,
       CASE WHEN ch IN ('edi', 'marketplace') THEN 'EXT-' || md5(r.order_id::text) END,
       CASE WHEN random() < 0.03 THEN bench_gen.pick(ARRAY['Deliver to loading dock B','Call before delivery','Split shipment OK','Use customer PO 4411']) END,
       CASE WHEN ch = 'web' THEN jsonb_build_object('device', bench_gen.pick(ARRAY['desktop','mobile','tablet']), 'utm_source', bench_gen.pick(ARRAY['google','newsletter','direct','linkedin',NULL]))
            WHEN random() < 0.2 THEN jsonb_build_object('po_number', 'PO' || (r.order_id * 13 % 999999)) END,
       r.order_dt, CASE WHEN ch IN ('sales_rep', 'partner') THEN 'crm-sync' ELSE 'system' END
FROM o_raw r
CROSS JOIN LATERAL (SELECT extract(epoch FROM bench_gen.t1() - r.order_dt) / 86400 age,
                           CASE WHEN r.rc < 0.50 THEN 'web' WHEN r.rc < 0.74 THEN 'sales_rep' WHEN r.rc < 0.88 THEN 'partner'
                                WHEN r.rc < 0.97 THEN 'marketplace' ELSE 'edi' END ch,
                           (extract(month FROM r.order_dt) = 12 AND r.rc < 0.5 AND r.rs > 0.85) is_gift) x
JOIN sales.customers c ON c.cust_id = r.cust_id
JOIN crm.accounts a ON a.account_id = c.account_id
JOIN region_wh wh ON wh.region_id = a.region_id
JOIN public.fx_rates fx ON fx.currency_code = c.default_currency AND fx.rate_dt = r.order_dt::date
CROSS JOIN promo_pool pp;

UPDATE o SET
  shipped_dt   = CASE WHEN status IN ('shipped','delivered','completed','returned') THEN order_dt + (6 + random() * 60) * interval '1 hour' END,
  cancelled_dt = CASE WHEN status = 'cancelled' THEN order_dt + random() * interval '3 days' END,
  cancel_reason = CASE WHEN status = 'cancelled' THEN bench_gen.pick(ARRAY['customer_request','out_of_stock','payment_failed','duplicate','fraud_check']) END;
UPDATE o SET delivered_dt = shipped_dt + (12 + random() * 200) * interval '1 hour' WHERE status IN ('delivered','completed','returned');
UPDATE o SET delivered_dt = NULL WHERE delivered_dt > bench_gen.t1();
UPDATE o SET status = 'shipped' WHERE delivered_dt IS NULL AND status IN ('delivered','completed');
-- link accepted quotes to the customer's first order after the quote
UPDATE o SET quote_id = x.quote_id
FROM (SELECT DISTINCT ON (order_id) order_id, quote_id FROM (
        SELECT DISTINCT ON (q.quote_id) q.quote_id, oo.order_id
        FROM sales.quotes q JOIN o oo ON oo.cust_id = q.cust_id AND oo.order_dt::date BETWEEN q.quote_dt AND q.quote_dt + 60
        WHERE q.status = 'accepted' ORDER BY q.quote_id, oo.order_dt) y ORDER BY order_id, quote_id) x
WHERE o.order_id = x.order_id;

-- ---------------------------------------------------------------- order items (~2.7M)
INSERT INTO sales.order_items
SELECT row_number() OVER (ORDER BY x.order_id, x.ln), x.order_id, x.ln, p.product_id, x.qty,
       round(p.list_price / x.fx_rate_to_usd, 2), x.disc,
       round(x.qty * round(p.list_price / x.fx_rate_to_usd, 2) * (1 - x.disc / 100), 2),
       round(x.qty * round(p.list_price / x.fx_rate_to_usd, 2) * (1 - x.disc / 100) * coalesce(co.vat_rate_pct, 7) / 100, 2),
       CASE x.status WHEN 'cancelled' THEN 'cancelled' WHEN 'returned' THEN 'returned' WHEN 'draft' THEN 'open' WHEN 'pending' THEN 'open'
                     WHEN 'confirmed' THEN 'allocated' ELSE 'fulfilled' END
FROM (SELECT o.order_id, ln, o.fx_rate_to_usd, o.status, o.ship_to_country,
             bench_gen.skew(25000, 2.5) pid,
             (1 + floor(power(random(), 3) * 24))::int qty,
             CASE WHEN random() < 0.7 THEN 0 ELSE bench_gen.pick(ARRAY[5,10,10,15,20])::numeric END disc
      FROM o CROSS JOIN LATERAL generate_series(1, o.item_count) ln) x
JOIN inventory.products p ON p.product_id = x.pid
LEFT JOIN public.countries co ON co.country_code = x.ship_to_country;

INSERT INTO sales.orders
SELECT o.order_id, o.order_no, o.cust_id, o.order_dt, o.status, o.channel, o.sales_rep_id, o.territory_id, o.warehouse_id, o.quote_id, o.promo_code,
       o.currency_code, o.fx_rate_to_usd, o.item_count,
       t.sub, t.disc + coalesce(round(t.sub * pc.discount_pct / 100, 2), 0), t.tax, s.ship,
       t.sub - t.disc - coalesce(round(t.sub * pc.discount_pct / 100, 2), 0) + t.tax + s.ship,
       round((t.sub - t.disc - coalesce(round(t.sub * pc.discount_pct / 100, 2), 0) + t.tax + s.ship) * o.fx_rate_to_usd, 2),
       o.payment_terms, o.ship_to_country, o.ship_to_city, o.ship_to_postal, o.bill_to_country, o.requested_ship_dt, o.promised_dt,
       o.shipped_dt, o.delivered_dt, o.cancelled_dt, o.cancel_reason, o.is_gift, o.gift_message, o.priority_flag, t.wt,
       o.source_system, o.external_ref, o.notes, o.metadata, o.created_at,
       coalesce(o.delivered_dt, o.cancelled_dt, o.shipped_dt, o.created_at), o.created_by, CASE WHEN o.shipped_dt IS NOT NULL THEN 'wms' END
FROM o
JOIN (SELECT i.order_id, sum(i.qty * i.unit_price) sub, sum(i.qty * i.unit_price) - sum(i.line_amt) disc, sum(i.tax_amt) tax,
             sum(i.qty * p.weight_kg) wt
      FROM sales.order_items i JOIN inventory.products p ON p.product_id = i.product_id GROUP BY i.order_id) t ON t.order_id = o.order_id
CROSS JOIN LATERAL (SELECT CASE WHEN t.sub > 500 THEN 0 ELSE round(9.95 / o.fx_rate_to_usd, 2) END ship) s
LEFT JOIN sales.promo_codes pc ON pc.promo_code = o.promo_code;

UPDATE sales.customers c SET first_order_dt = f.d
FROM (SELECT cust_id, min(order_dt)::date d FROM sales.orders GROUP BY cust_id) f WHERE f.cust_id = c.cust_id;
DROP TABLE o, o_raw;

-- ---------------------------------------------------------------- returns, shipments
INSERT INTO sales.returns
SELECT row_number() OVER (ORDER BY i.order_item_id), i.order_item_id, (o.delivered_dt + bench_gen.rint(2, 30) * interval '1 day')::date,
       greatest(1, floor(i.qty * (0.3 + random() * 0.7))::int),
       bench_gen.pick(ARRAY['damaged','wrong_item','not_as_described','no_longer_needed','defective','late_delivery']),
       bench_gen.pick(ARRAY['new','opened','damaged',NULL]), round(i.line_amt * (0.5 + random() * 0.5)::numeric, 2), random() < 0.6
FROM sales.order_items i JOIN sales.orders o ON o.order_id = i.order_id
WHERE o.status = 'returned' AND o.delivered_dt IS NOT NULL AND (i.line_no = 1 OR random() < 0.4);

INSERT INTO ops.shipments
SELECT row_number() OVER (ORDER BY o.order_id), o.order_id, o.warehouse_id,
       CASE WHEN o.ship_to_country IN ('US') THEN bench_gen.pick(ARRAY[1,1,2,2,4]) WHEN o.ship_to_country = 'GB' THEN bench_gen.pick(ARRAY[5,3,6])
            WHEN o.ship_to_country = 'BR' THEN 7 WHEN o.ship_to_country = 'JP' THEN 8 WHEN o.ship_to_country = 'AU' THEN 9
            WHEN o.ship_to_country = 'IN' THEN 10 ELSE bench_gen.pick(ARRAY[3,3,6,1,11]) END,
       '1Z' || upper(left(md5('t' || o.order_id), 14)), bench_gen.pick(ARRAY['ground','ground','ground','express','overnight']),
       o.shipped_dt, o.delivered_dt,
       CASE WHEN o.delivered_dt IS NOT NULL THEN 'delivered' WHEN random() < 0.03 THEN 'exception' ELSE 'in_transit' END,
       round((5 + random() * 60)::numeric, 2), (1 + floor(power(random(), 3) * 5))::smallint
FROM sales.orders o WHERE o.shipped_dt IS NOT NULL;

-- ---------------------------------------------------------------- invoices, invoice lines, payments
INSERT INTO finance.invoices
SELECT row_number() OVER (ORDER BY o.order_id), 'INV-' || to_char(o.shipped_dt, 'YYYY') || '-' || lpad(o.order_id::text, 8, '0'),
       o.order_id, o.cust_id, o.shipped_dt::date, o.shipped_dt::date + terms, o.currency_code,
       o.subtotal_amt - o.discount_amt, o.tax_amt, o.total_amt - o.shipping_amt,
       CASE WHEN o.status = 'returned' AND random() < 0.5 THEN 'void'
            WHEN o.shipped_dt::date + terms > date '2026-10-10' THEN CASE WHEN random() < 0.35 THEN 'paid' ELSE 'open' END
            WHEN random() < 0.93 THEN 'paid' WHEN random() < 0.75 THEN 'overdue' ELSE 'open' END,
       NULL, CASE WHEN terms = 0 THEN 'Due on receipt' ELSE 'Net ' || terms END, o.shipped_dt
FROM sales.orders o
CROSS JOIN LATERAL (SELECT CASE WHEN o.payment_terms LIKE 'NET%' THEN substr(o.payment_terms, 4)::int ELSE 0 END terms) t
WHERE o.shipped_dt IS NOT NULL;
-- shipping is not invoiced per line; total_amt = subtotal + tax
UPDATE finance.invoices SET total_amt = subtotal_amt + tax_amt;
UPDATE finance.invoices SET paid_at = least(issued_dt + bench_gen.rint(0, greatest(1, due_dt - issued_dt + 10)) * interval '1 day' + random() * interval '1 day', bench_gen.t1())
WHERE status = 'paid';

INSERT INTO finance.invoice_lines
SELECT row_number() OVER (ORDER BY i.order_item_id), inv.invoice_id, i.order_item_id, i.line_no, i.qty,
       i.unit_price, i.line_amt, i.tax_amt
FROM finance.invoices inv JOIN sales.order_items i ON i.order_id = inv.order_id;

INSERT INTO finance.payments
SELECT row_number() OVER (ORDER BY x.invoice_id, x.k), x.invoice_id, x.paid, x.amt, x.currency_code, x.method,
       CASE WHEN x.method = 'card' THEN lpad((x.invoice_id * 7 % 10000)::text, 4, '0') END,
       CASE WHEN x.method IN ('wire','ach') THEN 'NL00TEST' || lpad((x.cust_id * 7727 % 1000000000)::text, 10, '0') END,
       x.st, 'ch_' || left(md5(x.invoice_id::text || x.k), 20),
       CASE WHEN x.method = 'card' THEN round(x.amt * 0.029 + 0.30, 2) WHEN x.method = 'paypal' THEN round(x.amt * 0.034, 2) ELSE 0 END
FROM (
  SELECT inv.invoice_id, inv.cust_id, 1 k, inv.paid_at paid, inv.total_amt amt, inv.currency_code, m.method, 'settled' st
  FROM finance.invoices inv CROSS JOIN LATERAL (SELECT CASE WHEN inv.total_amt < 2000 AND random() < 0.7 + inv.invoice_id * 0 THEN 'card'
                                                         ELSE bench_gen.pick(ARRAY['wire','ach','ach','paypal','check']) END method) m
  WHERE inv.status = 'paid'
  UNION ALL
  SELECT inv.invoice_id, inv.cust_id, 0, inv.issued_dt + interval '2 days', inv.total_amt, inv.currency_code, 'card', 'failed'
  FROM finance.invoices inv WHERE inv.status IN ('paid','overdue') AND random() < 0.04
) x;

-- ---------------------------------------------------------------- purchasing (ops.orders = purchase orders)
INSERT INTO ops.orders
SELECT g, 'PO-' || lpad(g::text, 7, '0'), 1 + floor(power(random(), 1.5) * 800)::int, bench_gen.rint(1, 23),
       (SELECT employee_id FROM hr.employees WHERE department_id = 27 ORDER BY employee_id LIMIT 1 OFFSET g % 20),
       od, od + bench_gen.rint(5, 45), NULL,
       CASE WHEN od > date '2026-09-20' THEN bench_gen.pick(ARRAY['draft','sent']) WHEN random() < 0.05 THEN 'cancelled' WHEN random() < 0.06 THEN 'partial' ELSE 'received' END,
       bench_gen.pick(ARRAY['USD','USD','USD','EUR','JPY']::bpchar[]), NULL
FROM (SELECT g, date '2023-10-01' + bench_gen.rint(0, 1105) od FROM generate_series(1, 60000) g) x;
UPDATE ops.orders SET received_dt = least(expected_dt + bench_gen.rint(-3, 12), date '2026-10-10') WHERE status IN ('received','partial');

INSERT INTO ops.order_lines
SELECT row_number() OVER (ORDER BY po.order_id, k), po.order_id, bench_gen.skew(25000, 1.7), q,
       CASE po.status WHEN 'received' THEN q WHEN 'partial' THEN floor(q * random())::int ELSE 0 END,
       0
FROM ops.orders po CROSS JOIN LATERAL generate_series(1, 1 + (po.order_id * 7 % 9)) k
CROSS JOIN LATERAL (SELECT (10 * bench_gen.rint(1, 50) + k * 0) q) qq;
UPDATE ops.order_lines l SET unit_cost = round(p.unit_cost * CASE po.currency_code WHEN 'EUR' THEN 0.93 WHEN 'JPY' THEN 150 ELSE 1 END, 2)
FROM inventory.products p, ops.orders po WHERE p.product_id = l.product_id AND po.order_id = l.order_id;
UPDATE ops.orders po SET total_amt = t.s FROM (SELECT order_id, sum(qty_ordered * unit_cost) s FROM ops.order_lines GROUP BY order_id) t WHERE t.order_id = po.order_id;

-- ---------------------------------------------------------------- stock movements (~1.2M)
CREATE TEMP TABLE wh_staff AS SELECT array_agg(employee_id ORDER BY employee_id) ids FROM hr.employees WHERE department_id IN (25, 8);
INSERT INTO inventory.stock_movements
SELECT g, wh, pid, mt,
       CASE WHEN mt IN ('pick','transfer_out') THEN -bench_gen.rint(1, 40) WHEN mt = 'adjustment' THEN bench_gen.rint(-15, 15) ELSE bench_gen.rint(1, 400) END,
       CASE mt WHEN 'receipt' THEN 'ops.orders' WHEN 'pick' THEN 'sales.orders' WHEN 'return' THEN 'sales.returns' ELSE NULL END,
       CASE WHEN mt IN ('receipt','pick') THEN bench_gen.rint(1, 900000) END,
       bench_gen.rts(bench_gen.t0(), bench_gen.t1()),
       CASE WHEN g % 5 <> 0 THEN s.ids[1 + g % array_length(s.ids, 1)] END
FROM (SELECT g, bench_gen.rint(1, 23) wh, bench_gen.skew(25000, 2.0) pid,
             CASE WHEN r < 0.55 THEN 'pick' WHEN r < 0.80 THEN 'receipt' WHEN r < 0.88 THEN 'adjustment' WHEN r < 0.93 THEN 'transfer_in'
                  WHEN r < 0.98 THEN 'transfer_out' ELSE 'return' END mt
      FROM (SELECT g, random() r FROM generate_series(1, 1200000) g) a) x
CROSS JOIN wh_staff s;

-- ---------------------------------------------------------------- general ledger (~1.3M lines, balanced journals)
INSERT INTO finance.gl_entries
SELECT row_number() OVER (ORDER BY j, side), j, CASE side WHEN 0 THEN dr ELSE cr END, cc, d, d + interval '1 day' + random() * interval '5 days',
       CASE side WHEN 0 THEN amt ELSE 0 END, CASE side WHEN 1 THEN amt ELSE 0 END, 'USD', st,
       CASE WHEN st = 'invoice' THEN bench_gen.rint(1, 900000) END,
       CASE WHEN random() < 0.4 THEN bench_gen.pick(ARRAY['Monthly accrual','Vendor bill','Customer invoice','Payroll run','FX revaluation','Reclass']) END,
       bench_gen.rint(1, 4000)
FROM (SELECT j, date '2023-10-01' + floor(random() * 1106)::int d, bench_gen.rint(1, 60) cc,
             round((10 + power(random(), 3) * 50000)::numeric, 2) amt,
             bench_gen.pick(ARRAY['invoice','invoice','invoice','bill','payroll','manual','fx']) st,
             -- debit an asset/expense account, credit a liability/revenue account (type = (id - 7) % 6)
             7 + 6 * floor(random() * 24)::int + bench_gen.pick(ARRAY[0,4,5]) dr,
             7 + 6 * floor(random() * 24)::int + bench_gen.pick(ARRAY[1,3]) cr
      FROM generate_series(1, 650000) j) x
CROSS JOIN generate_series(0, 1) side;

-- ---------------------------------------------------------------- subscriptions (~24k) and their events
INSERT INTO finance.subscriptions
SELECT g, acc, pl, 'active', CASE WHEN random() < 0.35 THEN 'annual' ELSE 'monthly' END,
       seats, 0, 'USD', st, NULL, NULL, NULL, NULL, random() < 0.85, st
FROM (SELECT g, CASE WHEN g <= 15000 THEN g ELSE bench_gen.skew(20000, 1.5) END acc,
             CASE WHEN r < 0.10 THEN 1 WHEN r < 0.35 THEN 2 WHEN r < 0.62 THEN 3 WHEN r < 0.80 THEN 4 WHEN r < 0.88 THEN 5
                  WHEN r < 0.93 THEN 6 WHEN r < 0.97 THEN 7 ELSE 8 END pl,
             bench_gen.rint(1, 120) seats, bench_gen.rts(timestamptz '2022-01-01', timestamptz '2026-10-01') st
      FROM (SELECT g, random() r FROM generate_series(1, 24000) g) a) x;
UPDATE finance.subscriptions s SET
  seats = least(s.seats, coalesce(p.max_seats, s.seats)),
  mrr_amt = round(CASE WHEN s.billing_period = 'annual' THEN p.annual_price / 12 ELSE p.monthly_price END
                  * CASE WHEN p.tier IN ('team','business') THEN greatest(1, least(s.seats, coalesce(p.max_seats, s.seats))) / 5.0 ELSE 1 END, 2),
  status = CASE WHEN s.started_at > timestamptz '2026-09-15' AND random() < 0.5 THEN 'trialing'
                WHEN random() < 0.22 THEN 'cancelled' WHEN random() < 0.04 THEN 'past_due' WHEN random() < 0.02 THEN 'paused' ELSE 'active' END
FROM finance.plans p WHERE p.plan_id = s.plan_id;
UPDATE finance.subscriptions SET mrr_amt = 0 WHERE plan_id = 1;
UPDATE finance.subscriptions s SET cancelled_at = bench_gen.rts(s.started_at, bench_gen.t1()),
  cancel_reason = bench_gen.pick(ARRAY['too_expensive','switched_competitor','no_longer_needed','missing_feature','company_closed',NULL])
WHERE s.status = 'cancelled';
UPDATE finance.subscriptions s SET current_period_end = CASE WHEN s.status = 'cancelled' THEN s.cancelled_at
       ELSE s.started_at + ceil(extract(epoch FROM bench_gen.t1() - s.started_at) / CASE s.billing_period WHEN 'annual' THEN 31536000 ELSE 2592000 END)
            * CASE s.billing_period WHEN 'annual' THEN interval '1 year' ELSE interval '30 days' END END,
  sales_rep_id = a.owner_employee_id
FROM crm.accounts a WHERE a.account_id = s.account_id;

INSERT INTO finance.subscription_events
SELECT row_number() OVER (ORDER BY ev_dt, subscription_id), subscription_id, event_type, ev_dt, old_plan, new_plan, old_mrr, new_mrr
FROM (
  SELECT subscription_id, 'created' event_type, started_at ev_dt, NULL::int old_plan, plan_id new_plan, NULL::numeric old_mrr, mrr_amt new_mrr
  FROM finance.subscriptions
  UNION ALL
  SELECT subscription_id, 'cancel', cancelled_at, plan_id, NULL, mrr_amt, 0 FROM finance.subscriptions WHERE status = 'cancelled'
  UNION ALL
  SELECT s.subscription_id, et, bench_gen.rts(s.started_at, coalesce(s.cancelled_at, bench_gen.t1())), s.plan_id, s.plan_id,
         round(s.mrr_amt * CASE et WHEN 'upgrade' THEN 0.7 WHEN 'downgrade' THEN 1.4 ELSE 0.9 END, 2), s.mrr_amt
  FROM finance.subscriptions s
  CROSS JOIN LATERAL generate_series(1, 1 + (s.subscription_id * 7 % 4)) k
  CROSS JOIN LATERAL (SELECT bench_gen.pick(ARRAY['upgrade','downgrade','seat_change','seat_change','renewal','renewal']) et, k k2) e
) x;
ANALYZE;
