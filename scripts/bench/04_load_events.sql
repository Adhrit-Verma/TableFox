-- 04_load_events.sql: support tickets, marketing/web events, logins, API request log, audit log, change history.
\set ON_ERROR_STOP on
SET client_min_messages = warning;
SET session_replication_role = replica;
SET max_parallel_workers_per_gather = 0;
SET timezone = 'UTC';
SET work_mem = '256MB';
SELECT setseed(0.4444);

-- ---------------------------------------------------------------- support tickets (~300k)
CREATE TEMP TABLE agent_pool AS SELECT count(*)::int n FROM support.agents;
INSERT INTO support.tickets
SELECT x.g, 'TCK-' || lpad(x.g::text, 7, '0'), x.acc, CASE WHEN random() < 0.8 THEN x.acc END, CASE WHEN random() < 0.7 THEN x.acc END,
       CASE WHEN random() < 0.4 THEN bench_gen.skew(25000, 2.0) END,
       a.support_tier_id, x.prio, 'new', x.cat,
       CASE WHEN random() < 0.6 THEN x.cat || '/' || bench_gen.pick(ARRAY['general','urgent','how_to','bug','request']) END,
       bench_gen.pick(ARRAY['email','email','portal','portal','chat','phone']),
       bench_gen.pick(bench_gen.lorem()),
       1 + floor(random() * ap.n)::int,
       x.opened,
       x.opened + (-ln(1 - random()) * frs.h) * interval '1 hour',
       x.opened + (-ln(1 - random()) * frs.r * CASE x.prio WHEN 'p1' THEN 0.3 WHEN 'p2' THEN 0.6 WHEN 'p3' THEN 1 ELSE 1.5 END + frs.h) * interval '1 hour',
       NULL, x.opened + t.resolution_sla_h * interval '1 hour',
       x.prio IN ('p1','p2') AND random() < 0.3, CASE WHEN random() < 0.06 THEN bench_gen.rint(1, 3) ELSE 0 END, NULL,
       CASE WHEN random() < 0.25 THEN ARRAY[bench_gen.pick(ARRAY['vip','regression','billing','outage','feature-request'])] END
FROM (SELECT g, bench_gen.skew(20000, 1.6) acc,
             bench_gen.t1() - power(random(), 1.3) * (bench_gen.t1() - bench_gen.t0()) opened,
             CASE WHEN r < 0.03 THEN 'p1' WHEN r < 0.15 THEN 'p2' WHEN r < 0.65 THEN 'p3' ELSE 'p4' END::support.ticket_priority prio,
             bench_gen.pick(ARRAY['billing','billing','technical','technical','technical','shipping','account','returns','api','feature_request']) cat
      FROM (SELECT g, random() r FROM generate_series(1, 300000) g) a0) x
JOIN crm.accounts a ON a.account_id = x.acc
JOIN support.tiers t ON t.tier_id = a.support_tier_id
CROSS JOIN agent_pool ap
CROSS JOIN LATERAL (SELECT CASE a.support_tier_id WHEN 4 THEN 0.6 WHEN 3 THEN 2.5 WHEN 2 THEN 6 ELSE 16 END + x.g * 0 h,
                           CASE a.support_tier_id WHEN 4 THEN 10 WHEN 3 THEN 20 WHEN 2 THEN 40 ELSE 70 END r) frs;

UPDATE support.tickets SET
  first_response_at = CASE WHEN first_response_at > bench_gen.t1() THEN NULL ELSE first_response_at END,
  resolved_at = CASE WHEN resolved_at > bench_gen.t1() OR ticket_id % 61 = 0 THEN NULL ELSE resolved_at END;
UPDATE support.tickets SET
  status = CASE WHEN resolved_at IS NULL THEN
                  CASE WHEN first_response_at IS NULL THEN 'new' WHEN escalated THEN 'escalated' WHEN ticket_id % 3 = 0 THEN 'pending_customer' ELSE 'open' END
                WHEN resolved_at + interval '3 days' < bench_gen.t1() THEN 'closed' ELSE 'resolved' END,
  closed_at = CASE WHEN resolved_at + interval '3 days' < bench_gen.t1() THEN resolved_at + interval '3 days' END,
  assigned_agent_id = CASE WHEN first_response_at IS NULL THEN NULL ELSE assigned_agent_id END,
  csat_score = CASE WHEN resolved_at IS NOT NULL AND random() < 0.45 THEN
                 greatest(1, least(5, 5 - floor(extract(epoch FROM resolved_at - opened_at) / 3600 / 48)::int - (random() < 0.15)::int)) END;

INSERT INTO support.ticket_messages
SELECT row_number() OVER (ORDER BY t.ticket_id, k), t.ticket_id, au.author_type,
       CASE WHEN au.author_type = 'agent' THEN t.assigned_agent_id END,
       CASE WHEN au.author_type = 'customer' THEN t.contact_id END,
       t.opened_at + (k - 1)::float8 / n * (coalesce(t.resolved_at, bench_gen.t1()) - t.opened_at),
       bench_gen.pick(bench_gen.lorem()), au.author_type = 'agent' AND random() < 0.1,
       CASE WHEN random() < 0.12 THEN bench_gen.rint(1, 4) ELSE 0 END
FROM support.tickets t
CROSS JOIN LATERAL (SELECT 1 + (t.ticket_id * 7919 % 11)::int / 2 n) nn
CROSS JOIN LATERAL generate_series(1, nn.n) k
CROSS JOIN LATERAL (SELECT CASE WHEN k = 1 THEN 'customer' WHEN (t.ticket_id + k) % 19 = 0 THEN 'system'
                                WHEN k % 2 = 0 AND t.assigned_agent_id IS NOT NULL THEN 'agent' ELSE 'customer' END author_type) au;

INSERT INTO support.sla_breaches
SELECT row_number() OVER (ORDER BY ticket_id, breach_type), ticket_id, breach_type, breached_at, minutes_over FROM (
  SELECT t.ticket_id, 'first_response' breach_type, t.opened_at + tr.first_response_sla_h * interval '1 hour' breached_at,
         (extract(epoch FROM coalesce(t.first_response_at, bench_gen.t1()) - t.opened_at) / 60 - tr.first_response_sla_h * 60)::int minutes_over
  FROM support.tickets t JOIN support.tiers tr ON tr.tier_id = t.tier_id
  WHERE coalesce(t.first_response_at, bench_gen.t1()) > t.opened_at + tr.first_response_sla_h * interval '1 hour'
  UNION ALL
  SELECT t.ticket_id, 'resolution', t.sla_due_at, (extract(epoch FROM coalesce(t.resolved_at, bench_gen.t1()) - t.sla_due_at) / 60)::int
  FROM support.tickets t WHERE coalesce(t.resolved_at, bench_gen.t1()) > t.sla_due_at) b;

-- ---------------------------------------------------------------- marketing email events (~1.5M)
CREATE TEMP TABLE camp AS SELECT campaign_id, start_dt, coalesce(end_dt, start_dt + 30) end_dt, row_number() OVER (ORDER BY campaign_id) rn FROM marketing.campaigns;
INSERT INTO marketing.email_events
SELECT x.g, c.campaign_id, x.cid, coalesce(ct.email, 'unknown' || x.cid || '@client.example'), x.et,
       least(c.start_dt + random() * (c.end_dt - c.start_dt) * interval '1 day' + random() * interval '1 day', bench_gen.t1()),
       CASE WHEN x.et = 'click' THEN 'https://northwind.example/' || bench_gen.pick(ARRAY['pricing','webinar','blog/post-' || (x.g % 50),'demo','promo']) END,
       CASE WHEN x.et IN ('open','click') THEN bench_gen.pick(ARRAY['Outlook','Gmail','Apple Mail','Thunderbird']) END,
       md5('msg' || x.cid || '-' || c.campaign_id)::uuid
FROM (SELECT g, bench_gen.rint(1, 400) crn, bench_gen.skew(60000, 1.3) cid,
             CASE WHEN r < 0.40 THEN 'sent' WHEN r < 0.75 THEN 'delivered' WHEN r < 0.90 THEN 'open' WHEN r < 0.96 THEN 'click'
                  WHEN r < 0.985 THEN 'bounce' WHEN r < 0.995 THEN 'unsubscribe' ELSE 'spam_report' END et
      FROM (SELECT g, random() r FROM generate_series(1, 1500000) g) a) x
JOIN camp c ON c.rn = x.crn
JOIN crm.contacts ct ON ct.contact_id = x.cid;

-- ---------------------------------------------------------------- web analytics: sessions (~600k) and page views (~2M)
INSERT INTO analytics.web_sessions
SELECT g, CASE WHEN random() < 0.35 THEN bench_gen.skew(50000, 2.0) END, md5('anon' || g)::uuid, st, NULL,
       bench_gen.pick(ARRAY['US','US','US','GB','DE','FR','IN','AU','BR','CA','JP','NL','SG','MX']::bpchar[]),
       bench_gen.pick(ARRAY['desktop','desktop','mobile','mobile','tablet']), bench_gen.pick(ARRAY['chrome','chrome','safari','edge','firefox']),
       bench_gen.pick(ARRAY['google','google','direct','newsletter','linkedin','bing',NULL]),
       bench_gen.pick(ARRAY['organic','cpc','email','social',NULL]),
       CASE WHEN random() < 0.3 THEN 'cmp_' || bench_gen.rint(1, 400) END,
       bench_gen.pick(ARRAY['/','/','/pricing','/products','/blog','/docs','/login']),
       (1 + floor(power(random(), 2) * 8))::smallint, false
FROM (SELECT g, bench_gen.t1() - power(random(), 1.2) * (bench_gen.t1() - bench_gen.t0()) st FROM generate_series(1, 600000) g) x;
UPDATE analytics.web_sessions SET ended_at = started_at + page_count * (20 + random() * 200) * interval '1 second',
  converted = random() < CASE WHEN cust_id IS NOT NULL THEN 0.08 ELSE 0.015 END;

INSERT INTO analytics.page_views
SELECT row_number() OVER (ORDER BY s.session_id, k), s.session_id,
       bench_gen.pick(ARRAY['/','/pricing','/products','/products/' || (s.session_id % 25000 + 1),'/cart','/checkout','/blog','/docs/api','/account','/support']),
       CASE WHEN k = 1 THEN bench_gen.pick(ARRAY['https://google.example/','https://linkedin.example/',NULL]) END,
       s.started_at + (k - 1) * (s.ended_at - s.started_at) / s.page_count,
       (500 + power(random(), 2) * 180000)::int
FROM analytics.web_sessions s CROSS JOIN LATERAL generate_series(1, s.page_count) k;

INSERT INTO analytics.feature_usage
SELECT g, bench_gen.skew(20000, 1.4),
       bench_gen.pick(ARRAY['dashboard','reports','api','exports','alerts','sso','bulk_import','mobile_app','integrations','ai_assistant']),
       (bench_gen.t1() - power(random(), 1.5) * (bench_gen.t1() - bench_gen.t0()))::date,
       (1 + power(random(), 3) * 5000)::int, bench_gen.rint(1, 80)
FROM generate_series(1, 300000) g;

-- ---------------------------------------------------------------- logins (~1M)
INSERT INTO ops.login_events
SELECT g, u, ts, ok, CASE WHEN NOT ok THEN bench_gen.pick(ARRAY['bad_password','bad_password','bad_password','mfa_failed','account_locked','unknown_user']) END,
       (CASE WHEN u % 211 = 0 AND NOT ok THEN '198.51.100.' ELSE bench_gen.pick(ARRAY['203.0.113.','192.0.2.','198.18.0.','198.19.1.']) END || bench_gen.rint(1, 254))::inet,
       bench_gen.pick(ARRAY['Mozilla/5.0 (Windows NT 10.0)','Mozilla/5.0 (Macintosh)','Mozilla/5.0 (iPhone)','okhttp/4.9','python-requests/2.31']),
       bench_gen.pick(ARRAY['US','US','GB','DE','IN','BR','AU','JP']::bpchar[]), CASE WHEN ok THEN random() < 0.6 END
FROM (SELECT g, CASE WHEN random() < 0.4 THEN bench_gen.rint(1, 4000) ELSE 4000 + bench_gen.skew(26000, 1.5) END u,
             bench_gen.t1() - power(random(), 1.2) * (bench_gen.t1() - bench_gen.t0()) ts
      FROM generate_series(1, 1000000) g) x
CROSS JOIN LATERAL (SELECT random() >= CASE WHEN x.u % 211 = 0 THEN 0.6 ELSE 0.06 END ok) o;
UPDATE ops.app_users u SET last_login_at = l.m FROM (SELECT user_id, max(event_ts) m FROM ops.login_events WHERE success GROUP BY user_id) l
WHERE l.user_id = u.user_id;

-- ---------------------------------------------------------------- API request log (~2.5M, recent-heavy; some keys fail a lot)
INSERT INTO ops.api_request_log
SELECT x.g, x.kid, x.ts, x.meth, x.ep, sc.code,
       (15 + power(random(), 4) * CASE WHEN sc.code >= 500 THEN 30000 ELSE 2500 END)::int,
       CASE WHEN sc.code < 400 THEN bench_gen.rint(200, 250000) ELSE bench_gen.rint(80, 600) END,
       ('203.0.113.' || (x.kid % 254 + 1))::inet,
       CASE sc.code WHEN 400 THEN 'invalid_request' WHEN 401 THEN 'unauthorized' WHEN 403 THEN 'forbidden' WHEN 404 THEN 'not_found'
                    WHEN 429 THEN 'rate_limited' WHEN 500 THEN 'internal_error' WHEN 503 THEN 'unavailable' END
FROM (SELECT g, bench_gen.skew(3000, 2.0) kid,
             bench_gen.t1() - power(random(), 1.4) * (bench_gen.t1() - bench_gen.t0()) ts,
             bench_gen.pick(ARRAY['GET','GET','GET','GET','POST','POST','PUT','DELETE']) meth,
             bench_gen.pick(ARRAY['/v2/orders','/v2/orders','/v2/orders/{id}','/v2/products','/v2/products/{id}','/v2/invoices','/v2/customers','/v2/shipments','/v2/webhooks','/v1/legacy/sync']) ep,
             random() r
      FROM generate_series(1, 2500000) g) x
JOIN ops.api_keys k ON k.api_key_id = x.kid
CROSS JOIN LATERAL (SELECT CASE
    WHEN k.revoked_at IS NOT NULL AND x.ts > k.revoked_at THEN 401
    WHEN x.r < CASE WHEN k.api_key_id % 37 = 0 THEN 0.45 WHEN x.ep = '/v1/legacy/sync' THEN 0.2 ELSE 0.03 END THEN
         (ARRAY[400,401,403,404,404,429,429,429,500,503])[1 + floor(random() * 10)::int]
    WHEN x.meth = 'POST' THEN 201 WHEN x.meth = 'DELETE' THEN 204 ELSE 200 END::smallint code) sc;
UPDATE ops.api_keys k SET last_used_at = l.m FROM (SELECT api_key_id, max(requested_at) m FROM ops.api_request_log GROUP BY api_key_id) l
WHERE l.api_key_id = k.api_key_id;

-- ---------------------------------------------------------------- audit log (~1.2M) and change history (~800k)
INSERT INTO public.audit_log
SELECT g, ts, CASE WHEN at = 'system' THEN NULL WHEN at = 'service' THEN 30000 + bench_gen.rint(1, 40)
                   WHEN random() < 0.5 THEN bench_gen.skew(4000, 1.5) ELSE 4000 + bench_gen.skew(26000, 1.5) END,
       at, act, split_part(ent, '.', 1), split_part(ent, '.', 2), bench_gen.rint(1, 1000000),
       (bench_gen.pick(ARRAY['203.0.113.','192.0.2.','10.20.0.']) || bench_gen.rint(1, 254))::inet,
       md5('req' || g)::uuid,
       CASE WHEN act IN ('delete','permission_change','api_key_create','export') THEN CASE WHEN random() < 0.3 THEN 'critical' ELSE 'warning' END ELSE 'info' END,
       jsonb_build_object('rows', bench_gen.rint(1, 5000), 'via', bench_gen.pick(ARRAY['ui','api','cli']), 'reason', bench_gen.pick(ARRAY['routine','support case','audit request',NULL]))
FROM (SELECT g, bench_gen.t1() - power(random(), 1.2) * (bench_gen.t1() - bench_gen.t0()) ts,
             bench_gen.pick(ARRAY['user','user','user','user','service','system']) at,
             bench_gen.pick(ARRAY['view','view','view','view','update','update','create','export','delete','login','permission_change','api_key_create']) act,
             bench_gen.pick(ARRAY['crm.accounts','crm.contacts','sales.orders','sales.customers','finance.invoices','finance.payments','hr.employees','hr.payroll','ops.api_keys','inventory.products']) ent
      FROM generate_series(1, 1200000) g) x;
UPDATE public.audit_log SET ip_address = NULL WHERE actor_type = 'system';

INSERT INTO public.change_history
SELECT g, tbl, (bench_gen.rint(1, CASE tbl WHEN 'hr.employees' THEN 4000 WHEN 'inventory.products' THEN 25000 WHEN 'crm.accounts' THEN 20000 ELSE 50000 END))::text,
       col, ov, nv, CASE WHEN src = 'ui' THEN bench_gen.rint(1, 4000) WHEN src = 'api' THEN 4000 + bench_gen.rint(1, 26000) END, src, ts
FROM (SELECT g, t.tbl, t.cols[1 + floor(random() * array_length(t.cols, 1))::int] col,
             bench_gen.pick(ARRAY['ui','ui','api','import','sync']) src,
             bench_gen.t1() - power(random(), 1.1) * (bench_gen.t1() - bench_gen.t0()) ts
      FROM generate_series(1, 800000) g
      CROSS JOIN LATERAL (SELECT (ARRAY['crm.accounts','sales.customers','inventory.products','hr.employees'])[1 + (g * 7 % 4)] tbl) tt
      JOIN (VALUES ('crm.accounts', ARRAY['tier','owner_employee_id','industry','health_score','is_active']),
                   ('sales.customers', ARRAY['credit_limit','segment','territory_id','email']),
                   ('inventory.products', ARRAY['list_price','unit_cost','is_active','category_id']),
                   ('hr.employees', ARRAY['salary_amt','department_id','manager_id','job_title','is_active'])) t(tbl, cols) ON t.tbl = tt.tbl) x
CROSS JOIN LATERAL (SELECT CASE WHEN x.col IN ('is_active') THEN 'true' WHEN x.col LIKE '%_id' THEN bench_gen.rint(1, 4000)::text
                                WHEN x.col IN ('tier','segment','industry','job_title','email') THEN bench_gen.pick(ARRAY['smb','enterprise','Retail','Analyst','old@client.example'])
                                ELSE round((random() * 100000)::numeric, 2)::text END ov,
                           CASE WHEN x.col IN ('is_active') THEN 'false' WHEN x.col LIKE '%_id' THEN bench_gen.rint(1, 4000)::text
                                WHEN x.col IN ('tier','segment','industry','job_title','email') THEN bench_gen.pick(ARRAY['mid_market','strategic','Healthcare','Manager','new@client.example'])
                                ELSE round((random() * 100000)::numeric, 2)::text END nv) v;

ANALYZE;
