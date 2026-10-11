-- 02_load_master.sql: helper functions + reference and master data (regions .. accounts, products, users).
\set ON_ERROR_STOP on
SET client_min_messages = warning;
SET session_replication_role = replica;   -- skip FK triggers during bulk load; 05_finish verifies all FKs
SET max_parallel_workers_per_gather = 0;  -- keep random() consumption order stable
SET timezone = 'UTC';
SELECT setseed(0.42);

-- ---------------------------------------------------------------- generator helpers (dropped in 05_finish)
DROP SCHEMA IF EXISTS bench_gen CASCADE;
CREATE SCHEMA bench_gen;
CREATE FUNCTION bench_gen.pick(a anyarray) RETURNS anyelement VOLATILE LANGUAGE sql
  AS $$ SELECT a[1 + floor(random() * array_length(a, 1))::int] $$;
CREATE FUNCTION bench_gen.rint(lo int, hi int) RETURNS int VOLATILE LANGUAGE sql
  AS $$ SELECT lo + floor(random() * (hi - lo + 1))::int $$;
CREATE FUNCTION bench_gen.rts(a timestamptz, b timestamptz) RETURNS timestamptz VOLATILE LANGUAGE sql
  AS $$ SELECT a + random() * (b - a) $$;
-- skewed id in 1..n: low ids are picked far more often (power-law-ish)
CREATE FUNCTION bench_gen.skew(n int, p float8) RETURNS int VOLATILE LANGUAGE sql
  AS $$ SELECT 1 + floor(n * power(random(), p))::int $$;
CREATE FUNCTION bench_gen.t0() RETURNS timestamptz IMMUTABLE LANGUAGE sql AS $$ SELECT timestamptz '2023-10-01 00:00:00+00' $$;
CREATE FUNCTION bench_gen.t1() RETURNS timestamptz IMMUTABLE LANGUAGE sql AS $$ SELECT timestamptz '2026-10-10 23:59:59+00' $$;
CREATE FUNCTION bench_gen.first_names() RETURNS text[] IMMUTABLE LANGUAGE sql AS $$ SELECT ARRAY[
  'James','Mary','Robert','Patricia','John','Jennifer','Michael','Linda','David','Elizabeth','William','Barbara',
  'Richard','Susan','Joseph','Jessica','Thomas','Sarah','Carlos','Maria','Wei','Li','Aarav','Priya','Hiroshi','Yuki',
  'Lukas','Anna','Pierre','Camille','Ahmed','Fatima','Olivia','Noah','Emma','Liam','Sofia','Mateo','Isabella','Lucas',
  'Chen','Arjun','Ingrid','Sven','Giulia','Marco','Zara','Omar'] $$;
CREATE FUNCTION bench_gen.last_names() RETURNS text[] IMMUTABLE LANGUAGE sql AS $$ SELECT ARRAY[
  'Smith','Johnson','Williams','Brown','Jones','Garcia','Miller','Davis','Rodriguez','Martinez','Hernandez','Lopez',
  'Wilson','Anderson','Thomas','Taylor','Moore','Jackson','Martin','Lee','Thompson','White','Harris','Clark',
  'Mueller','Schmidt','Dubois','Rossi','Kowalski','Nakamura','Tanaka','Wang','Zhang','Patel','Sharma','Singh',
  'Silva','Santos','Andersson','Nielsen','OBrien','Murphy','Khan','Ali','Novak','Fischer','Costa','Kim'] $$;
CREATE FUNCTION bench_gen.lorem() RETURNS text[] IMMUTABLE LANGUAGE sql AS $$ SELECT ARRAY[
  'Customer reports the export job fails after the latest update.',
  'Requested a refund for a duplicate charge on the last invoice.',
  'Cannot log in after resetting the password.',
  'Shipment arrived damaged, photos attached.',
  'Asked about volume pricing for next quarter.',
  'API returns 429 during nightly sync.',
  'Integration with the ERP stopped posting orders.',
  'Following up on the previous message, any update?',
  'Escalated to tier 2 for investigation.',
  'Workaround provided, waiting for customer confirmation.',
  'Issue resolved after cache clear.',
  'Requested invoice copy in PDF.',
  'Dashboard loads slowly for large accounts.',
  'Wrong item delivered, return label sent.',
  'Customer wants to add 15 seats to the subscription.'] $$;

-- ---------------------------------------------------------------- reference data
INSERT INTO public.regions VALUES
 (1,'NA','North America','Chicago','2019-01-01'),
 (2,'LATAM','Latin America','Sao Paulo','2019-01-01'),
 (3,'EMEA','Europe, Middle East & Africa','Amsterdam','2019-01-01'),
 (4,'APAC','Asia Pacific','Singapore','2019-06-01'),
 (5,'UNASSIGNED','Unassigned / data cleanup',NULL,'2021-03-15');

INSERT INTO public.currencies VALUES
 ('USD','US Dollar','$',2),('CAD','Canadian Dollar','C$',2),('MXN','Mexican Peso','$',2),('BRL','Brazilian Real','R$',2),
 ('EUR','Euro','€',2),('GBP','Pound Sterling','£',2),('CHF','Swiss Franc','CHF',2),('SEK','Swedish Krona','kr',2),
 ('JPY','Japanese Yen','¥',0),('AUD','Australian Dollar','A$',2),('INR','Indian Rupee','₹',2),('SGD','Singapore Dollar','S$',2),
 ('ZAR','South African Rand','R',2),('AED','UAE Dirham','AED',2);

INSERT INTO public.countries VALUES
 ('US','United States','USA',1,'USD','+1',false,NULL),('CA','Canada','CAN',1,'CAD','+1',false,5),
 ('MX','Mexico','MEX',2,'MXN','+52',false,16),('BR','Brazil','BRA',2,'BRL','+55',false,17),
 ('AR','Argentina','ARG',2,'USD','+54',false,21),('CL','Chile','CHL',2,'USD','+56',false,19),('CO','Colombia','COL',2,'USD','+57',false,19),
 ('GB','United Kingdom','GBR',3,'GBP','+44',false,20),('DE','Germany','DEU',3,'EUR','+49',true,19),('FR','France','FRA',3,'EUR','+33',true,20),
 ('NL','Netherlands','NLD',3,'EUR','+31',true,21),('ES','Spain','ESP',3,'EUR','+34',true,21),('IT','Italy','ITA',3,'EUR','+39',true,22),
 ('IE','Ireland','IRL',3,'EUR','+353',true,23),('SE','Sweden','SWE',3,'SEK','+46',true,25),('CH','Switzerland','CHE',3,'CHF','+41',false,8.1),
 ('BE','Belgium','BEL',3,'EUR','+32',true,21),('AT','Austria','AUT',3,'EUR','+43',true,20),('ZA','South Africa','ZAF',3,'ZAR','+27',false,15),
 ('AE','United Arab Emirates','ARE',3,'AED','+971',false,5),('JP','Japan','JPN',4,'JPY','+81',false,10),('AU','Australia','AUS',4,'AUD','+61',false,10),
 ('IN','India','IND',4,'INR','+91',false,18),('SG','Singapore','SGP',4,'SGD','+65',false,9);

-- country choice per region, weighted by repetition
CREATE TEMP TABLE region_countries AS
SELECT * FROM (VALUES
 (1, ARRAY['US','US','US','US','US','US','CA','CA']::text[]),
 (2, ARRAY['BR','BR','BR','MX','MX','AR','CL','CO']),
 (3, ARRAY['GB','GB','GB','DE','DE','DE','FR','FR','NL','NL','ES','IT','IE','SE','CH','BE','AT','ZA','AE']),
 (4, ARRAY['JP','JP','AU','AU','AU','IN','IN','IN','SG','SG']),
 (5, ARRAY['US','GB','DE','IN'])) v(region_id, codes);

INSERT INTO public.fx_rates (currency_code, rate_dt, usd_rate)
SELECT c.code, d::date,
       round((c.base * (1 + 0.04 * sin(extract(epoch FROM d) / 86400 / 97.0 + c.phase) + 0.006 * (random() - 0.5)))::numeric, 6)
FROM (VALUES ('USD',1.0,0.0),('CAD',0.735,1.0),('MXN',0.055,2.0),('BRL',0.19,0.5),('EUR',1.08,1.5),('GBP',1.26,2.5),
             ('CHF',1.12,3.0),('SEK',0.094,0.7),('JPY',0.0068,1.2),('AUD',0.66,2.2),('INR',0.012,0.3),('SGD',0.74,1.8),
             ('ZAR',0.054,2.8),('AED',0.2723,0.0)) c(code, base, phase)
CROSS JOIN generate_series(date '2023-09-01', date '2026-10-10', interval '1 day') d;
UPDATE public.fx_rates SET usd_rate = 1 WHERE currency_code = 'USD';
UPDATE public.fx_rates SET usd_rate = 0.272300 WHERE currency_code = 'AED';

INSERT INTO public.app_settings
SELECT 'setting.' || g, jsonb_build_object('enabled', g % 3 <> 0, 'value', g * 7, 'owner', bench_gen.pick(ARRAY['platform','billing','growth'])),
       bench_gen.rts(bench_gen.t0(), bench_gen.t1()), bench_gen.pick(ARRAY['admin','deploy-bot','j.smith',NULL])
FROM generate_series(1, 60) g;

-- ---------------------------------------------------------------- hr / org
INSERT INTO hr.departments (department_id, dept_code, dept_name, parent_department_id, location_country, cost_center_code, created_at) VALUES
 (1,'EXEC','Executive',NULL,'US','CC-100','2015-01-01'),(2,'SALES','Sales',1,'US','CC-200','2015-01-01'),
 (3,'MKT','Marketing',1,'US','CC-300','2015-01-01'),(4,'ENG','Engineering',1,'US','CC-400','2015-01-01'),
 (5,'SUP','Customer Support',1,'IE','CC-500','2015-06-01'),(6,'FIN','Finance',1,'US','CC-600','2015-01-01'),
 (7,'PPL','People',1,'US','CC-700','2015-01-01'),(8,'OPS','Operations',1,'NL','CC-800','2016-01-01'),
 (9,'PROD','Product',1,'US','CC-900','2017-01-01'),(10,'IT','IT & Security',1,'US','CC-1000','2016-01-01'),
 (11,'SALES-NA','Sales North America',2,'US','CC-210','2016-01-01'),(12,'SALES-EMEA','Sales EMEA',2,'GB','CC-220','2016-01-01'),
 (13,'SALES-APAC','Sales APAC',2,'SG','CC-230','2018-01-01'),(14,'SALES-LATAM','Sales LATAM',2,'BR','CC-240','2019-01-01'),
 (15,'MKT-DG','Demand Generation',3,'US','CC-310','2017-01-01'),(16,'MKT-BRAND','Brand & Content',3,'GB','CC-320','2017-01-01'),
 (17,'ENG-PLAT','Platform Engineering',4,'US','CC-410','2016-01-01'),(18,'ENG-APPS','Application Engineering',4,'IN','CC-420','2016-01-01'),
 (19,'ENG-DATA','Data Engineering',4,'DE','CC-430','2019-01-01'),(20,'SUP-T1','Support Tier 1',5,'IE','CC-510','2016-01-01'),
 (21,'SUP-T2','Support Tier 2',5,'IN','CC-520','2016-01-01'),(22,'FIN-AR','Accounts Receivable',6,'US','CC-610','2016-01-01'),
 (23,'FIN-FPA','FP&A',6,'US','CC-620','2018-01-01'),(24,'PPL-TA','Talent Acquisition',7,'US','CC-710','2018-01-01'),
 (25,'OPS-WH','Warehousing',8,'NL','CC-810','2016-01-01'),(26,'OPS-LOG','Logistics',8,'US','CC-820','2016-01-01'),
 (27,'OPS-PROC','Procurement',8,'DE','CC-830','2017-01-01'),(28,'PROD-DES','Product Design',9,'US','CC-910','2019-01-01'),
 (29,'IT-SEC','Security Office',10,'US','CC-1010','2020-01-01'),(30,'LEGAL','Legal',1,'US','CC-110','2015-01-01');

INSERT INTO finance.cost_centers
SELECT d.department_id * 2 - 1 + k, 'CC-' || d.dept_code || '-' || k, d.dept_name || CASE k WHEN 0 THEN ' - Opex' ELSE ' - Programs' END,
       d.department_id, NOT (k = 1 AND d.department_id % 7 = 0), date '2015-01-01' + d.department_id * 30
FROM hr.departments d CROSS JOIN generate_series(0, 1) k;

-- employees: 1..30 are department heads (dept = id); the rest are spread with weights
INSERT INTO hr.employees (employee_id, employee_number, first_name, last_name, email, hire_dt, department_id, job_title,
  employment_type, is_active, created_at)
SELECT g, 'E' || lpad(g::text, 6, '0'), '', '', '', date '2015-01-01', CASE WHEN g <= 30 THEN g ELSE
         CASE WHEN r < 0.25 THEN 11 + floor(random() * 4)::int
              WHEN r < 0.40 THEN 20 + floor(random() * 2)::int
              WHEN r < 0.55 THEN 25 + floor(random() * 3)::int
              WHEN r < 0.73 THEN 17 + floor(random() * 3)::int
              ELSE (ARRAY[2,3,4,5,6,7,8,9,10,15,16,22,23,24,28,29,30])[1 + floor(random() * 17)::int] END END,
       '', 'full_time', true, now()
FROM (SELECT g, random() r FROM generate_series(1, 4000) g) s;

UPDATE hr.employees e SET
  first_name = f, last_name = l, preferred_name = CASE WHEN random() < 0.1 THEN left(f, 3) END,
  email = lower(f || '.' || l || e.employee_id) || '@northwind-corp.example',
  personal_email = CASE WHEN random() < 0.7 THEN lower(left(f,1) || l || (e.employee_id * 7 % 997)) || '@mail.example' END,
  phone = '+1-555-' || lpad((e.employee_id * 37 % 10000)::text, 4, '0'),
  mobile_phone = CASE WHEN random() < 0.8 THEN '+1-555-' || lpad((e.employee_id * 91 % 10000)::text, 4, '0') END,
  ssn = CASE WHEN loc = 'US' THEN '9' || lpad((e.employee_id % 100)::text, 2, '0') || '-' || lpad((e.employee_id * 13 % 100)::text, 2, '0') || '-' || lpad((e.employee_id * 7919 % 10000)::text, 4, '0') END,
  national_id = CASE WHEN loc <> 'US' THEN loc || '-ID-' || lpad((e.employee_id * 104729 % 100000000)::text, 8, '0') END,
  date_of_birth = date '1960-01-01' + floor(random() * 15000)::int,
  gender = bench_gen.pick(ARRAY['F','M','F','M','X',NULL]),
  hire_dt = hd,
  term_dt = CASE WHEN e.employee_id > 30 AND random() < 0.16 THEN nullif(least(hd + 60 + floor(random() * 1500)::int, date '2026-10-11'), date '2026-10-11') END,
  job_level = CASE WHEN e.employee_id = 1 THEN 10 WHEN e.employee_id <= 30 THEN 8 ELSE bench_gen.rint(1, 7) END,
  employment_type = CASE WHEN random() < 0.85 THEN 'full_time' WHEN random() < 0.5 THEN 'contractor' WHEN random() < 0.6 THEN 'part_time' ELSE 'intern' END,
  location_country = loc,
  office_city = CASE loc WHEN 'US' THEN bench_gen.pick(ARRAY['Chicago','Austin','New York','Seattle']) WHEN 'GB' THEN 'London'
                 WHEN 'DE' THEN 'Berlin' WHEN 'IN' THEN 'Bengaluru' WHEN 'NL' THEN 'Amsterdam' WHEN 'IE' THEN 'Dublin'
                 WHEN 'SG' THEN 'Singapore' WHEN 'BR' THEN 'Sao Paulo' ELSE NULL END,
  work_mode = bench_gen.pick(ARRAY['remote','hybrid','hybrid','onsite']),
  home_address = (e.employee_id * 17 % 900 + 10) || ' ' || bench_gen.pick(ARRAY['Oak St','Maple Ave','High St','Main St','Park Rd']),
  postal_code = lpad((e.employee_id * 389 % 99999)::text, 5, '0'),
  emergency_contact_name = CASE WHEN random() < 0.6 THEN bench_gen.pick(bench_gen.first_names()) || ' ' || l END,
  emergency_contact_phone = CASE WHEN random() < 0.6 THEN '+1-555-' || lpad((e.employee_id * 53 % 10000)::text, 4, '0') END,
  iban = CASE WHEN loc IN ('DE','NL','IE','GB') THEN loc || '00TEST' || lpad((e.employee_id * 7727 % 1000000000)::text, 12, '0') END,
  tax_code = 'TX' || (e.employee_id * 31 % 99991),
  badge_id = CASE WHEN random() < 0.9 THEN 'B-' || (100000 + e.employee_id) END,
  cost_center_id = e.department_id * 2 - (random() < 0.7)::int,
  last_review_rating = CASE WHEN random() < 0.85 THEN bench_gen.rint(1, 5) END,
  pto_balance_days = round((random() * 30)::numeric, 1),
  salary_currency = CASE loc WHEN 'US' THEN 'USD' WHEN 'GB' THEN 'GBP' WHEN 'IN' THEN 'INR' WHEN 'SG' THEN 'SGD' WHEN 'BR' THEN 'BRL' ELSE 'EUR' END,
  bonus_target_pct = CASE WHEN random() < 0.6 THEN (ARRAY[5,10,15,20,30])[1 + floor(random() * 5)::int] END,
  extra = CASE WHEN random() < 0.3 THEN jsonb_build_object('skills', ARRAY[bench_gen.pick(ARRAY['sql','python','negotiation','excel','go','forklift'])], 'remote_stipend', random() < 0.5) END,
  created_at = hd::timestamptz + interval '9 hours',
  updated_at = CASE WHEN random() < 0.7 THEN bench_gen.rts(bench_gen.t0(), bench_gen.t1()) END
FROM (SELECT employee_id,
             bench_gen.pick(bench_gen.first_names()) f, bench_gen.pick(bench_gen.last_names()) l,
             CASE WHEN employee_id <= 2 THEN date '2015-01-01' + employee_id * 10
                  ELSE date '2015-01-01' + floor(power(random(), 0.6) * 4250)::int END hd,
             bench_gen.pick(ARRAY['US','US','US','US','US','US','GB','GB','DE','DE','IN','IN','IN','NL','IE','IE','SG','BR','CA','AU']) loc
      FROM hr.employees) x
WHERE x.employee_id = e.employee_id;

UPDATE hr.employees SET is_active = (term_dt IS NULL),
  termination_reason = CASE WHEN term_dt IS NOT NULL THEN bench_gen.pick(ARRAY['resigned','resigned','laid_off','terminated','retired','contract_end']) END;

UPDATE hr.employees e SET job_title = CASE
    WHEN e.employee_id = 1 THEN 'Chief Executive Officer'
    WHEN e.employee_id <= 30 THEN 'Head of ' || d.dept_name
    WHEN d.department_id IN (2,11,12,13,14) THEN bench_gen.pick(ARRAY['Account Executive','Account Executive','Senior Account Executive','Sales Development Rep','Sales Manager','Solutions Engineer'])
    WHEN d.department_id IN (5,20,21) THEN bench_gen.pick(ARRAY['Support Specialist','Support Specialist','Senior Support Engineer','Support Team Lead'])
    WHEN d.department_id IN (4,17,18,19) THEN bench_gen.pick(ARRAY['Software Engineer','Senior Software Engineer','Staff Engineer','Engineering Manager','Data Engineer','SRE'])
    WHEN d.department_id IN (8,25,26,27) THEN bench_gen.pick(ARRAY['Warehouse Associate','Warehouse Associate','Logistics Coordinator','Buyer','Operations Manager'])
    ELSE bench_gen.pick(ARRAY['Analyst','Senior Analyst','Specialist','Manager','Coordinator']) END,
  salary_amt = round((CASE WHEN e.employee_id = 1 THEN 420000 ELSE 38000 + coalesce(e.job_level, 3) * 14000 * (0.8 + random() * 0.5) END
                      * CASE e.salary_currency WHEN 'INR' THEN 40 WHEN 'GBP' THEN 0.78 WHEN 'EUR' THEN 0.9 WHEN 'BRL' THEN 3.2 WHEN 'SGD' THEN 1.3 ELSE 1 END)::numeric, -2)
FROM hr.departments d WHERE d.department_id = e.department_id;

UPDATE hr.departments SET head_employee_id = department_id;
UPDATE hr.employees e SET manager_id = CASE WHEN e.employee_id = 1 THEN NULL ELSE coalesce(d.parent_department_id, 1) END
FROM hr.departments d WHERE e.employee_id <= 30 AND d.department_id = e.employee_id;

-- the rest report to an earlier member of their own department; ~8% report across departments
CREATE TEMP TABLE dept_members AS
SELECT employee_id, department_id, row_number() OVER (PARTITION BY department_id ORDER BY employee_id) rn FROM hr.employees;
WITH pick AS MATERIALIZED (
  SELECT employee_id, department_id, 1 + floor(random() * least(rn - 1, 25))::int mrn,
         employee_id > 60 AND random() < 0.08 is_cross,
         31 + floor(random() * (least(employee_id - 1, 800) - 30))::int cross_mgr
  FROM dept_members WHERE employee_id > 30)
UPDATE hr.employees e SET manager_id = CASE WHEN p.is_cross THEN p.cross_mgr ELSE m.employee_id END
FROM pick p JOIN dept_members m ON m.department_id = p.department_id AND m.rn = p.mrn
WHERE e.employee_id = p.employee_id;

INSERT INTO hr.teams
SELECT g, d.dept_name || ' Squad ' || (g % 4 + 1), d.department_id,
       (SELECT min(employee_id) FROM hr.employees e WHERE e.department_id = d.department_id AND e.employee_id > 30 AND e.employee_id % 4 = g % 4),
       timestamptz '2018-01-01' + g * interval '9 days'
FROM generate_series(1, 120) g JOIN hr.departments d ON d.department_id = 1 + (g - 1) % 30;

INSERT INTO hr.team_members
SELECT DISTINCT ON (t.team_id, e.employee_id) t.team_id, e.employee_id,
       bench_gen.pick(ARRAY['member','member','member','lead','contributor']), e.hire_dt + 30, e.term_dt
FROM hr.employees e JOIN hr.teams t ON t.department_id = e.department_id AND t.team_id % 4 = e.employee_id % 4
WHERE random() < 0.85;

INSERT INTO hr.payroll
SELECT row_number() OVER (ORDER BY m, e.employee_id), e.employee_id, m::date,
       g, round(g * 0.24, 2), round(g * 0.06, 2), round(g * 0.70, 2), e.salary_currency,
       (m + interval '1 month' - interval '2 days')::date, e.iban,
       CASE WHEN m >= date '2026-10-01' THEN 'scheduled' WHEN random() < 0.002 THEN 'reversed' ELSE 'paid' END
FROM hr.employees e
CROSS JOIN generate_series(date '2023-10-01', date '2026-10-01', interval '1 month') m
CROSS JOIN LATERAL (SELECT round(e.salary_amt / 12 * (1 + CASE WHEN extract(month FROM m) = 12 THEN coalesce(e.bonus_target_pct, 0) / 100 ELSE 0 END), 2) g) gg
WHERE m >= date_trunc('month', e.hire_dt) AND (e.term_dt IS NULL OR m <= e.term_dt) AND e.employment_type <> 'contractor';

INSERT INTO hr.time_off
SELECT g, e.employee_id, e.manager_id, bench_gen.pick(ARRAY['vacation','vacation','vacation','sick','parental','unpaid','bereavement']),
       s, s + d, d + 1, bench_gen.pick(ARRAY['approved','approved','approved','approved','requested','rejected','cancelled']),
       (s - 20)::timestamptz + interval '10 hours'
FROM (SELECT g, bench_gen.rint(2, 4000) emp, date '2023-10-01' + bench_gen.rint(0, 1120) s, bench_gen.rint(0, 9) d FROM generate_series(1, 30000) g) x
JOIN hr.employees e ON e.employee_id = x.emp;

INSERT INTO hr.performance_reviews
SELECT row_number() OVER (), e.employee_id, e.manager_id, p, CASE WHEN random() < 0.95 THEN bench_gen.rint(1, 5) END,
       CASE WHEN random() < 0.4 THEN bench_gen.pick(ARRAY['Exceeds expectations','Solid quarter','Needs improvement on delivery','Great collaborator']) END,
       CASE WHEN random() < 0.9 THEN (CASE right(p, 2) WHEN 'H1' THEN (left(p, 4) || '-07-15')::date ELSE (left(p, 4)::int + 1 || '-01-15')::date END)::timestamptz END
FROM hr.employees e CROSS JOIN unnest(ARRAY['2023-H2','2024-H1','2024-H2','2025-H1','2025-H2','2026-H1']) p
WHERE e.manager_id IS NOT NULL AND e.hire_dt < (left(p, 4) || '-06-01')::date AND (e.term_dt IS NULL OR e.term_dt > (left(p, 4) || '-06-01')::date)
  AND random() < 0.55;

-- ---------------------------------------------------------------- support tiers, campaigns
INSERT INTO support.tiers VALUES
 (1,'Bronze',24,120,0,false),(2,'Silver',8,72,12.00,false),(3,'Gold',4,48,25.00,true),(4,'Platinum',1,24,45.00,true);

INSERT INTO marketing.campaigns
SELECT g, bench_gen.pick(ARRAY['Spring','Summer','Autumn','Winter','Q1','Q2','Q3','Q4','Black Friday','Launch']) || ' ' ||
          bench_gen.pick(ARRAY['Webinar','Promo','Newsletter','Trade Show','Retargeting','ABM Push','Nurture']) || ' ' || (2023 + g % 4),
       ch, 1 + g % 5, (SELECT employee_id FROM hr.employees WHERE department_id IN (3,15,16) ORDER BY employee_id OFFSET (g * 7) % 60 LIMIT 1),
       sd, sd + bench_gen.rint(7, 90), round((2000 + random() * 120000)::numeric, 2), NULL,
       bench_gen.pick(ARRAY['SMB','Enterprise','Developers','Existing customers','Lapsed customers']),
       'cmp_' || g, CASE WHEN sd + 90 < date '2026-10-10' THEN 'completed' WHEN sd > date '2026-10-10' THEN 'planned' ELSE 'running' END,
       sd::timestamptz - interval '14 days'
FROM (SELECT g, date '2023-09-01' + bench_gen.rint(0, 1130) sd, bench_gen.pick(ARRAY['email','email','email','paid_search','social','events','webinar','display']) ch
      FROM generate_series(1, 400) g) x;
UPDATE marketing.campaigns SET actual_spend_amt = round(budget_amt * (0.6 + random() * 0.6)::numeric, 2) WHERE status = 'completed';

-- ---------------------------------------------------------------- crm accounts
CREATE TEMP TABLE region_reps AS
SELECT r.region_id, array_agg(e.employee_id ORDER BY e.employee_id) reps
FROM public.regions r JOIN hr.employees e ON e.department_id = CASE r.region_id WHEN 1 THEN 11 WHEN 2 THEN 14 WHEN 3 THEN 12 WHEN 4 THEN 13 ELSE 11 END
GROUP BY r.region_id;

INSERT INTO crm.accounts (account_id, account_number, account_name, tier, region_id, country_code, is_active, created_at)
SELECT g, 'ACC-' || lpad(g::text, 6, '0'), '', CASE WHEN g <= 400 THEN 'strategic' WHEN g <= 2400 THEN 'enterprise' WHEN g <= 8000 THEN 'mid_market' ELSE 'smb' END::crm.account_tier,
       reg, 'US', true, now()
FROM (SELECT g, CASE WHEN r < 0.40 THEN 1 WHEN r < 0.70 THEN 3 WHEN r < 0.88 THEN 4 WHEN r < 0.995 THEN 2 ELSE 5 END reg
      FROM (SELECT g, random() r FROM generate_series(1, 20000) g) a) b;

UPDATE crm.accounts a SET
  account_name = nm,
  legal_name = CASE WHEN random() < 0.7 THEN nm || bench_gen.pick(ARRAY[' Inc.',' Ltd',' GmbH',' S.A.',' LLC',' Pty Ltd',' BV']) END,
  country_code = bench_gen.pick(rc.codes),
  industry = ind,
  sub_industry = CASE WHEN random() < 0.6 THEN ind || ' / ' || bench_gen.pick(ARRAY['B2B','B2C','Public','Mid-market']) END,
  city = bench_gen.pick(ARRAY['Springfield','Riverton','Lakeside','Fairview','Georgetown','Madison','Franklin','Clinton','Arlington','Salem']),
  state_province = CASE WHEN random() < 0.5 THEN bench_gen.pick(ARRAY['CA','TX','NY','IL','ON','NSW','BY','IDF']) END,
  postal_code = lpad((a.account_id * 71 % 99999)::text, 5, '0'),
  billing_address = (a.account_id % 900 + 1) || ' Commerce Way',
  website = 'https://www.' || lower(regexp_replace(nm, '[^A-Za-z]', '', 'g')) || a.account_id || '.example',
  phone = '+1-555-' || lpad((a.account_id * 17 % 10000)::text, 4, '0'),
  employee_count = CASE a.tier WHEN 'strategic' THEN bench_gen.rint(5000, 200000) WHEN 'enterprise' THEN bench_gen.rint(1000, 20000)
                    WHEN 'mid_market' THEN bench_gen.rint(100, 1500) ELSE bench_gen.rint(1, 120) END,
  owner_employee_id = rr.reps[1 + floor(random() * array_length(rr.reps, 1))::int],
  support_tier_id = CASE WHEN random() < 0.15 THEN bench_gen.rint(1, 4)
                    ELSE CASE a.tier WHEN 'strategic' THEN 4 WHEN 'enterprise' THEN 3 WHEN 'mid_market' THEN 2 ELSE 1 END END,
  parent_account_id = CASE WHEN a.account_id > 400 AND random() < 0.04 THEN bench_gen.rint(1, 400) END,
  lifecycle_stage = bench_gen.pick(ARRAY['customer','customer','customer','customer','expansion','at_risk']),
  acquisition_source = bench_gen.pick(ARRAY['inbound','outbound','partner','event','referral',NULL]),
  tax_id = CASE WHEN random() < 0.8 THEN 'TAX' || lpad((a.account_id * 7919 % 10000000)::text, 7, '0') END,
  duns_number = CASE WHEN a.tier IN ('strategic','enterprise') THEN lpad((a.account_id * 104723 % 1000000000)::text, 9, '0') END,
  payment_terms_days = bench_gen.pick(ARRAY[30,30,30,45,60,15]::smallint[]),
  nps_score = CASE WHEN random() < 0.5 THEN bench_gen.rint(-100, 100) END,
  health_score = round((random() * 100)::numeric, 2),
  tags = CASE WHEN random() < 0.3 THEN ARRAY[bench_gen.pick(ARRAY['beta','reference','strategic-2025','renewal-risk','partner'])] END,
  custom_fields = CASE WHEN random() < 0.4 THEN jsonb_build_object('erp_id', 'SAP-' || a.account_id, 'segment_override', bench_gen.pick(ARRAY['none','gov','edu']), 'csm', bench_gen.pick(bench_gen.last_names())) END,
  created_at = bench_gen.rts(timestamptz '2019-01-01', timestamptz '2026-06-30'),
  updated_at = bench_gen.rts(bench_gen.t0(), bench_gen.t1())
FROM (SELECT account_id,
             bench_gen.pick(ARRAY['Acme','Globex','Initech','Umbrella','Stark','Wayne','Wonka','Tyrell','Cyberdyne','Soylent','Hooli','Vandelay',
                                  'Pied Piper','Aperture','Gringotts','Oscorp','Nakatomi','Duff','Monarch','Blue Sun','Zenith','Apex','Summit',
                                  'Northwind','Contoso','Fabrikam','Tailspin','Adventure Works','Litware','Proseware']) || ' ' ||
             bench_gen.pick(ARRAY['Industries','Systems','Logistics','Foods','Health','Labs','Retail','Energy','Capital','Motors','Media','Networks','Holdings','Analytics','Pharma']) nm,
             bench_gen.pick(ARRAY['Manufacturing','Retail','Healthcare','Financial Services','Technology','Logistics','Education','Government','Energy','Media']) ind
      FROM crm.accounts) x, region_countries rc, region_reps rr
WHERE x.account_id = a.account_id AND rc.region_id = a.region_id AND rr.region_id = a.region_id;

UPDATE crm.accounts SET
  annual_revenue_amt = round((employee_count * (80000 + random() * 300000))::numeric, 2),
  credit_limit_amt = CASE tier WHEN 'strategic' THEN 2000000 WHEN 'enterprise' THEN 500000 WHEN 'mid_market' THEN 100000 ELSE 20000 END,
  preferred_currency = (SELECT currency_code FROM public.countries c WHERE c.country_code = crm.accounts.country_code),
  is_active = NOT (tier = 'smb' AND random() < 0.15 OR random() < 0.03);
UPDATE crm.accounts SET churned_at = bench_gen.rts(greatest(created_at, bench_gen.t0()), bench_gen.t1()), lifecycle_stage = 'churned' WHERE NOT is_active;

INSERT INTO crm.contacts
SELECT g, acc, f, l,
       CASE WHEN random() < 0.95 THEN lower(left(f, 1) || '.' || l || g) || '@client' || acc || '.example' END,
       CASE WHEN random() < 0.7 THEN '+1-555-' || lpad((g * 29 % 10000)::text, 4, '0') END,
       CASE WHEN random() < 0.4 THEN '+44-7700-' || lpad((g * 13 % 1000000)::text, 6, '0') END,
       bench_gen.pick(ARRAY['CFO','CTO','VP Operations','IT Manager','Procurement Lead','Developer','Analyst','Office Manager','CEO','Head of Support']),
       bench_gen.pick(ARRAY['Finance','IT','Operations','Procurement','Engineering','Executive',NULL]),
       bench_gen.pick(ARRAY['c_level','vp','director','manager','individual']),
       CASE WHEN random() < 0.3 THEN date '1960-01-01' + bench_gen.rint(0, 15000) END,
       CASE WHEN random() < 0.5 THEN 'https://linkedin.example/in/' || lower(f || l || g) END,
       g <= 20000, random() < 0.6, bench_gen.pick(ARRAY['en','en','en','de','fr','es','pt','ja']),
       bench_gen.pick(ARRAY['active','active','active','active','left_company','bounced']),
       CASE WHEN random() < 0.8 THEN bench_gen.rts(bench_gen.t0(), bench_gen.t1()) END,
       bench_gen.rts(timestamptz '2019-01-01', bench_gen.t1())
FROM (SELECT g, CASE WHEN g <= 20000 THEN g ELSE bench_gen.skew(20000, 1.8) END acc,
             bench_gen.pick(bench_gen.first_names()) f, bench_gen.pick(bench_gen.last_names()) l
      FROM generate_series(1, 60000) g) x;

INSERT INTO crm.leads
SELECT g, f, l, CASE WHEN random() < 0.9 THEN lower(f || '.' || l || g) || '@prospect.example' END,
       CASE WHEN random() < 0.5 THEN '+1-555-' || lpad((g * 41 % 10000)::text, 4, '0') END,
       bench_gen.pick(ARRAY['Acme','Globex','Initech','Hooli','Zenith','Apex','Contoso','Fabrikam']) || ' ' || bench_gen.pick(ARRAY['Co','Group','Partners','Labs']),
       bench_gen.pick(ARRAY['US','US','US','GB','DE','FR','IN','AU','BR','CA','JP','NL']),
       src, st, bench_gen.rint(0, 100),
       CASE WHEN src IN ('campaign','webinar','event') THEN bench_gen.rint(1, 400) END,
       CASE WHEN random() < 0.85 THEN 1 END, NULL, NULL, ts
FROM (SELECT g, bench_gen.pick(bench_gen.first_names()) f, bench_gen.pick(bench_gen.last_names()) l,
             bench_gen.pick(ARRAY['web_form','campaign','campaign','webinar','event','referral','cold_call','partner']) src,
             CASE WHEN r < 0.25 THEN 'new' WHEN r < 0.45 THEN 'working' WHEN r < 0.65 THEN 'nurture' WHEN r < 0.75 THEN 'qualified'
                  WHEN r < 0.92 THEN 'disqualified' ELSE 'converted' END st,
             bench_gen.rts(bench_gen.t0(), bench_gen.t1()) ts
      FROM (SELECT g, random() r FROM generate_series(1, 150000) g) a) x;
-- leads are owned by SDRs/AEs in the sales sub-departments
UPDATE crm.leads l SET owner_employee_id = rr.reps[1 + l.lead_id * 7 % array_length(rr.reps, 1)]
FROM region_reps rr WHERE rr.region_id = 1 AND l.owner_employee_id IS NOT NULL;
UPDATE crm.leads SET converted_account_id = bench_gen.rint(2400, 20000), converted_at = created_at + bench_gen.rint(3, 120) * interval '1 day'
WHERE status = 'converted';

INSERT INTO crm.opportunities
SELECT g, acc, CASE WHEN random() < 0.3 THEN bench_gen.rint(1, 150000) END,
       'Deal ' || g || ' - ' || bench_gen.pick(ARRAY['Platform','Expansion','Renewal','Pilot','Enterprise License','Add-on']),
       bench_gen.pick(ARRAY['new_business','new_business','renewal','renewal','upsell','cross_sell']),
       stg, round((CASE WHEN acc <= 400 THEN 50000 + random() * 900000 WHEN acc <= 2400 THEN 10000 + random() * 200000 ELSE 1000 + random() * 40000 END)::numeric, 2),
       CASE stg WHEN 'prospecting' THEN 10 WHEN 'qualification' THEN 20 WHEN 'proposal' THEN 50 WHEN 'negotiation' THEN 75 WHEN 'closed_won' THEN 100 ELSE 0 END,
       CASE WHEN stg IN ('closed_won') THEN 'closed' WHEN stg = 'negotiation' THEN 'commit' WHEN stg = 'proposal' THEN 'best_case' WHEN stg = 'closed_lost' THEN 'omitted' ELSE 'pipeline' END,
       (cr + bench_gen.rint(20, 150) * interval '1 day')::date,
       CASE WHEN stg LIKE 'closed%' THEN least((cr + bench_gen.rint(10, 180) * interval '1 day')::date, date '2026-10-10') END,
       CASE WHEN stg = 'closed_won' THEN true WHEN stg = 'closed_lost' THEN false END,
       CASE WHEN stg = 'closed_lost' THEN bench_gen.pick(ARRAY['price','competitor','no_decision','timing','feature_gap']) END,
       NULL, cr
FROM (SELECT g, bench_gen.skew(20000, 2.0) acc, bench_gen.rts(bench_gen.t0(), bench_gen.t1()) cr,
             CASE WHEN r < 0.10 THEN 'prospecting' WHEN r < 0.20 THEN 'qualification' WHEN r < 0.30 THEN 'proposal' WHEN r < 0.37 THEN 'negotiation'
                  WHEN r < 0.70 THEN 'closed_won' ELSE 'closed_lost' END stg
      FROM (SELECT g, random() r FROM generate_series(1, 40000) g) a) x;
UPDATE crm.opportunities o SET owner_employee_id = a.owner_employee_id FROM crm.accounts a WHERE a.account_id = o.account_id;
-- keep open deals open in the future, closed deals in the past
UPDATE crm.opportunities SET stage = 'negotiation', closed_dt = NULL, is_won = NULL, probability_pct = 75, forecast_category = 'commit', lost_reason = NULL
WHERE closed_dt IS NOT NULL AND closed_dt < created_at::date;

INSERT INTO crm.account_team
SELECT DISTINCT ON (acc, emp) acc, emp, bench_gen.pick(ARRAY['account_exec','csm','solutions_engineer','exec_sponsor','support_lead']),
       date '2020-01-01' + bench_gen.rint(0, 2400)
FROM (SELECT bench_gen.skew(20000, 1.5) acc, bench_gen.rint(31, 4000) emp FROM generate_series(1, 32000)) x;

INSERT INTO crm.activities
SELECT g, acc, CASE WHEN random() < 0.85 THEN acc END,
       CASE WHEN random() < 0.25 THEN bench_gen.rint(1, 40000) END, bench_gen.rint(31, 4000),
       bench_gen.pick(ARRAY['call','call','email','email','email','meeting','demo','note','task']),
       bench_gen.pick(ARRAY['Intro call','Follow-up','QBR','Pricing discussion','Renewal check-in','Technical deep dive','Escalation review']),
       bench_gen.rts(bench_gen.t0(), bench_gen.t1()), CASE WHEN random() < 0.6 THEN bench_gen.rint(5, 90) END,
       bench_gen.pick(ARRAY['completed','completed','no_show','rescheduled',NULL])
FROM (SELECT g, bench_gen.skew(20000, 1.7) acc FROM generate_series(1, 400000) g) x;
-- contact = the account's primary contact (contact_id = account_id for 1..20000)
UPDATE crm.activities SET employee_id = (SELECT owner_employee_id FROM crm.accounts WHERE account_id = crm.activities.account_id) WHERE activity_id % 3 = 0;

-- ---------------------------------------------------------------- support staff, knowledge base
INSERT INTO support.agents
SELECT row_number() OVER (ORDER BY e.employee_id), e.employee_id, CASE e.department_id WHEN 21 THEN bench_gen.rint(3, 4) ELSE bench_gen.rint(1, 3) END,
       CASE e.department_id WHEN 20 THEN 'Frontline' WHEN 21 THEN 'Escalations' ELSE 'Leads' END,
       ARRAY['en'] || CASE WHEN random() < 0.3 THEN ARRAY[bench_gen.pick(ARRAY['de','fr','es','pt','ja','hi'])] ELSE ARRAY[]::text[] END,
       jsonb_build_object('billing', random() < 0.5, 'api', random() < 0.4, 'shipping', random() < 0.6),
       bench_gen.rint(3, 12), e.is_active, e.hire_dt
FROM hr.employees e WHERE e.department_id IN (5, 20, 21);

INSERT INTO support.kb_articles
SELECT g, bench_gen.pick(ARRAY['How to','Troubleshooting','FAQ:','Guide:']) || ' ' || bench_gen.pick(ARRAY['reset your password','configure SSO','export orders','read your invoice','rotate API keys','track a shipment','request a return','add seats']) || ' #' || g,
       bench_gen.pick(ARRAY['account','billing','api','shipping','returns','security']),
       (SELECT employee_id FROM support.agents ORDER BY agent_id OFFSET g % 50 LIMIT 1),
       CASE WHEN random() < 0.9 THEN bench_gen.rts(timestamptz '2020-01-01', bench_gen.t1()) END,
       floor(power(random(), 3) * 50000)::int, floor(random() * 400)::int, random() < 0.9, repeat('Lorem ipsum. ', bench_gen.rint(5, 40))
FROM generate_series(1, 1500) g;

-- ---------------------------------------------------------------- inventory master data
INSERT INTO inventory.product_categories
SELECT g, NULL, (ARRAY['Electronics','Office Supplies','Furniture','Industrial','Software','Networking','Safety','Cleaning','Packaging','Food Service','Medical','Tools'])[g], NULL, 0
FROM generate_series(1, 12) g;
INSERT INTO inventory.product_categories
SELECT g, 1 + (g - 13) % 12, p.category_name || ' - ' || (ARRAY['Basic','Pro','Accessories','Consumables','Spare Parts','Bulk','Premium','Refurbished','Kits'])[1 + (g - 13) / 12], NULL, 1
FROM generate_series(13, 120) g JOIN inventory.product_categories p ON p.category_id = 1 + (g - 13) % 12;
UPDATE inventory.product_categories c SET path = coalesce((SELECT p.category_name || ' > ' FROM inventory.product_categories p WHERE p.category_id = c.parent_id), '') || c.category_name;

INSERT INTO inventory.suppliers
SELECT g, bench_gen.pick(ARRAY['Allied','United','Pacific','Nordic','Atlas','Pioneer','Eastern','Global','Prime','Metro']) || ' ' ||
          bench_gen.pick(ARRAY['Supply','Components','Trading','Manufacturing','Wholesale','Distribution']) || ' ' || g,
       bench_gen.pick(ARRAY['US','US','DE','JP','IN','MX','GB','NL','BR','SG']),
       bench_gen.pick(bench_gen.first_names()) || ' ' || bench_gen.pick(bench_gen.last_names()),
       'orders' || g || '@supplier.example', '+49-30-' || lpad((g * 97 % 1000000)::text, 6, '0'),
       'DE00TEST' || lpad((g * 7919 % 100000000)::text, 14, '0'), bench_gen.pick(ARRAY['NET30','NET45','NET60','PREPAID']),
       round((2 + random() * 3)::numeric, 2), random() < 0.2, bench_gen.rts(timestamptz '2016-01-01', timestamptz '2025-12-31')
FROM generate_series(1, 800) g;

INSERT INTO inventory.products
SELECT g, 'SKU-' || lpad(g::text, 6, '0'),
       bench_gen.pick(ARRAY['Ultra','Pro','Eco','Smart','Heavy-Duty','Compact','Classic','Max','Lite','Prime']) || ' ' ||
       bench_gen.pick(ARRAY['Widget','Sensor','Chair','Monitor','Cable','Router','Glove','Cleaner','Box','Drill','Label','Scanner','Desk','Battery','Switch']) || ' ' || g,
       CASE WHEN random() < 0.7 THEN 'Standard product description ' || g END,
       cat, 1 + floor(power(random(), 1.5) * 800)::int,
       bench_gen.pick(ARRAY['Northwind','Contoso','Fabrikam','Litware','Proseware','Generic',NULL]),
       lp, round(lp * (0.45 + random() * 0.3)::numeric, 2), CASE WHEN random() < 0.6 THEN round(lp * 1.15, 2) END, 'USD',
       bench_gen.pick(ARRAY['each','each','each','box','case','kg','license']), bench_gen.pick(ARRAY[1,1,1,6,12,24,100]),
       round((random() * 25)::numeric, 3), round((random() * 120)::numeric, 2), round((random() * 80)::numeric, 2), round((random() * 60)::numeric, 2),
       CASE WHEN random() < 0.5 THEN bench_gen.pick(ARRAY['black','white','grey','blue','red']) END,
       CASE WHEN random() < 0.4 THEN bench_gen.pick(ARRAY['steel','plastic','aluminium','wood','fabric']) END,
       random() < 0.93, random() < 0.03, random() < 0.1,
       date '2016-01-01' + bench_gen.rint(0, 3600), NULL, bench_gen.pick(ARRAY[0,12,12,24,36]::smallint[]),
       lpad((g * 7 % 100000000)::text, 8, '0'), bench_gen.pick(ARRAY['CN','US','DE','JP','IN','MX']::bpchar[]),
       lpad((g::bigint * 104729 % 10000000000000)::text, 13, '0'), bench_gen.pick(ARRAY[1,1,1,5,10,50]),
       bench_gen.rint(2, 60), CASE WHEN random() < 0.7 THEN round((2.5 + random() * 2.5)::numeric, 2) END, floor(power(random(), 4) * 3000)::int,
       bench_gen.pick(ARRAY['standard','standard','reduced','exempt']),
       CASE WHEN random() < 0.6 THEN jsonb_build_object('voltage', bench_gen.pick(ARRAY['110V','230V','n/a']), 'certifications', ARRAY[bench_gen.pick(ARRAY['CE','UL','RoHS'])], 'legacy_code', 'L' || g) END,
       bench_gen.rts(timestamptz '2016-01-01', timestamptz '2025-12-31'), bench_gen.rts(bench_gen.t0(), bench_gen.t1())
FROM (SELECT g, 13 + floor(random() * 108)::int cat,
             round((CASE WHEN random() < 0.6 THEN 5 + random() * 95 WHEN random() < 0.8 THEN 100 + random() * 900 ELSE 1000 + random() * 4000 END)::numeric, 2) lp
      FROM generate_series(1, 25000) g) x;
-- countries 'CN' is not in public.countries: map to JP to keep the FK valid
UPDATE inventory.products SET country_of_origin = 'JP' WHERE country_of_origin NOT IN (SELECT country_code FROM public.countries);
UPDATE inventory.products SET discontinued_at = bench_gen.rts(bench_gen.t0(), bench_gen.t1()) WHERE NOT is_active;

INSERT INTO inventory.warehouses VALUES
 (1,'WH-CHI','Chicago DC',1,'US','Chicago',NULL,42000,false,'2016-03-01'),(2,'WH-DAL','Dallas DC',1,'US','Dallas',NULL,38000,false,'2017-05-01'),
 (3,'WH-NJ','Newark DC',1,'US','Newark',NULL,50000,false,'2016-03-01'),(4,'WH-LA','Los Angeles DC',1,'US','Los Angeles',NULL,45000,false,'2018-01-15'),
 (5,'WH-SEA','Seattle Overflow',1,'US','Seattle',NULL,9000,true,'2022-09-01'),(6,'WH-TOR','Toronto DC',1,'CA','Toronto',NULL,20000,false,'2019-04-01'),
 (7,'WH-MEX','Monterrey DC',2,'MX','Monterrey',NULL,15000,true,'2020-02-01'),(8,'WH-SAO','Sao Paulo DC',2,'BR','Sao Paulo',NULL,22000,false,'2019-08-01'),
 (9,'WH-SCL','Santiago 3PL',2,'CL','Santiago',NULL,6000,true,'2023-01-10'),(10,'WH-AMS','Amsterdam Hub',3,'NL','Amsterdam',NULL,60000,false,'2016-01-01'),
 (11,'WH-LON','London DC',3,'GB','London',NULL,30000,false,'2016-09-01'),(12,'WH-MAN','Manchester DC',3,'GB','Manchester',NULL,18000,false,'2020-06-01'),
 (13,'WH-FRA','Frankfurt DC',3,'DE','Frankfurt',NULL,35000,false,'2017-02-01'),(14,'WH-PAR','Paris DC',3,'FR','Paris',NULL,25000,false,'2018-03-01'),
 (15,'WH-MAD','Madrid 3PL',3,'ES','Madrid',NULL,12000,true,'2021-05-01'),(16,'WH-STO','Stockholm 3PL',3,'SE','Stockholm',NULL,8000,true,'2022-01-01'),
 (17,'WH-DXB','Dubai DC',3,'AE','Dubai',NULL,14000,false,'2021-11-01'),(18,'WH-JNB','Johannesburg 3PL',3,'ZA','Johannesburg',NULL,7000,true,'2022-04-01'),
 (19,'WH-SIN','Singapore Hub',4,'SG','Singapore',NULL,40000,false,'2017-07-01'),(20,'WH-TYO','Tokyo DC',4,'JP','Tokyo',NULL,26000,false,'2018-10-01'),
 (21,'WH-SYD','Sydney DC',4,'AU','Sydney',NULL,24000,false,'2018-02-01'),(22,'WH-MEL','Melbourne 3PL',4,'AU','Melbourne',NULL,9000,true,'2023-03-01'),
 (23,'WH-BLR','Bengaluru DC',4,'IN','Bengaluru',NULL,28000,false,'2019-01-01'),(24,'WH-OLD','Legacy Reno (closed)',5,'US','Reno',NULL,0,false,'2014-01-01');
UPDATE inventory.warehouses w SET manager_employee_id = (SELECT employee_id FROM hr.employees e WHERE e.department_id = 25 ORDER BY employee_id OFFSET w.warehouse_id LIMIT 1);

INSERT INTO inventory.stock_levels
SELECT w.warehouse_id, p.product_id, oh, floor(oh * random() * 0.3)::int, CASE WHEN random() < 0.2 THEN bench_gen.rint(10, 500) ELSE 0 END,
       rp, rp * bench_gen.rint(2, 5), chr(65 + bench_gen.rint(0, 19)) || '-' || bench_gen.rint(1, 40) || '-' || bench_gen.rint(1, 8),
       CASE WHEN random() < 0.9 THEN bench_gen.rts(timestamptz '2026-01-01', bench_gen.t1()) END,
       bench_gen.rts(timestamptz '2026-09-01', bench_gen.t1())
FROM inventory.warehouses w
CROSS JOIN inventory.products p
CROSS JOIN LATERAL (SELECT bench_gen.rint(10, 200) + p.product_id * 0 + w.warehouse_id * 0 rp) r
CROSS JOIN LATERAL (SELECT CASE WHEN random() < 0.14 + p.product_id * 0 THEN floor(r.rp * random() * 0.9)::int ELSE r.rp + bench_gen.rint(0, 2000) END oh) o
WHERE w.warehouse_id < 24 AND random() < CASE WHEN w.warehouse_id IN (1,3,10,19) THEN 0.6 ELSE 0.28 END;

INSERT INTO inventory.item_aliases
SELECT g, bench_gen.pick(ARRAY['ERP-','MKT-','EDI-','OLD-']) || lpad((g * 31 % 1000000)::text, 7, '0'), 1 + (g * 7 % 25000),
       bench_gen.pick(ARRAY['sap','amazon','edi','legacy_erp']), random() < 0.3, bench_gen.rts(timestamptz '2018-01-01', bench_gen.t1())
FROM generate_series(1, 40000) g;

INSERT INTO inventory.product_suppliers
SELECT DISTINCT ON (pid, sid) pid, sid, 'S' || sid || '-' || pid, NULL, bench_gen.rint(3, 60), false
FROM (SELECT 1 + (g % 25000) pid, bench_gen.rint(1, 800) sid FROM generate_series(1, 52000) g) x;
UPDATE inventory.product_suppliers ps SET cost_amt = round(p.unit_cost * (0.9 + random() * 0.2)::numeric, 2), is_primary = (ps.supplier_id = p.supplier_id)
FROM inventory.products p WHERE p.product_id = ps.product_id;
INSERT INTO inventory.product_suppliers
SELECT product_id, supplier_id, 'S' || supplier_id || '-' || product_id, unit_cost, lead_time_days, true FROM inventory.products
ON CONFLICT DO NOTHING;

-- ---------------------------------------------------------------- sales master data
INSERT INTO sales.sales_territories
SELECT (r - 1) * 4 + k, rg.region_code || ' ' || (ARRAY['North','South','East','West'])[k], r,
       (SELECT reps[k] FROM region_reps WHERE region_id = r), round((2000000 + random() * 8000000)::numeric, -3)
FROM generate_series(1, 4) r CROSS JOIN generate_series(1, 4) k JOIN public.regions rg ON rg.region_id = r;

INSERT INTO sales.territory_reps
SELECT DISTINCT t.territory_id, rep, date '2020-01-01' + bench_gen.rint(0, 1500), NULL::date
FROM sales.sales_territories t JOIN region_reps rr ON rr.region_id = t.region_id CROSS JOIN LATERAL unnest(rr.reps) rep
WHERE (rep + t.territory_id) % 4 = 0;

INSERT INTO sales.price_lists VALUES
 (1,'List USD','USD','2020-01-01',NULL,true),(2,'List EUR','EUR','2020-01-01',NULL,false),(3,'List GBP','GBP','2020-01-01',NULL,false),
 (4,'Partner USD','USD','2021-01-01',NULL,false),(5,'Enterprise 2024','USD','2024-01-01','2024-12-31',false),(6,'APAC AUD','AUD','2022-01-01',NULL,false);
INSERT INTO sales.price_list_items
SELECT pl.price_list_id, p.product_id,
       round(p.list_price * CASE pl.currency_code WHEN 'EUR' THEN 0.93 WHEN 'GBP' THEN 0.8 WHEN 'AUD' THEN 1.5 ELSE 1 END * CASE pl.price_list_id WHEN 4 THEN 0.8 WHEN 5 THEN 0.85 ELSE 1 END, 2),
       bench_gen.pick(ARRAY[1,1,1,10,50])
FROM sales.price_lists pl CROSS JOIN inventory.products p
WHERE pl.price_list_id = 1 OR random() < 0.35;

INSERT INTO sales.promo_codes
SELECT upper(bench_gen.pick(ARRAY['SAVE','WELCOME','BF','SPRING','VIP','PARTNER'])) || g, 'Promo ' || g,
       bench_gen.pick(ARRAY[5,10,10,15,20,25,40]), CASE WHEN random() < 0.6 THEN bench_gen.rint(1, 400) END,
       vf, vf + bench_gen.rint(7, 120), CASE WHEN random() < 0.5 THEN bench_gen.rint(100, 10000) END, vf + 120 > date '2026-10-10'
FROM (SELECT g, date '2023-06-01' + bench_gen.rint(0, 1200) vf FROM generate_series(1, 500) g) x;

-- customers: first 20000 map 1:1 to accounts, the rest are extra billing entities of (skewed) accounts
INSERT INTO sales.customers
SELECT g, a.account_id, 'C' || lpad(g::text, 7, '0'),
       a.account_name || CASE WHEN g > 20000 THEN ' - ' || bench_gen.pick(ARRAY['EU Branch','Billing','Subsidiary','Plant ' || (g % 9 + 1),'Retail Div']) ELSE '' END,
       CASE a.tier WHEN 'strategic' THEN 'enterprise' WHEN 'enterprise' THEN 'enterprise' WHEN 'mid_market' THEN 'commercial' ELSE bench_gen.pick(ARRAY['smb','smb','consumer']) END,
       CASE WHEN random() < 0.9 THEN 'ap' || g || '@client' || a.account_id || '.example' END,
       CASE WHEN random() < 0.6 THEN '+1-555-' || lpad((g * 59 % 10000)::text, 4, '0') END,
       a.country_code, CASE WHEN random() < 0.9 THEN a.country_code ELSE bench_gen.pick(ARRAY['US','GB','DE']) END,
       c.currency_code,
       CASE c.currency_code WHEN 'EUR' THEN 2 WHEN 'GBP' THEN 3 WHEN 'AUD' THEN 6 ELSE CASE WHEN a.tier = 'strategic' THEN 5 ELSE 1 END END,
       (greatest(a.region_id, 1) - 1) % 4 * 4 + bench_gen.rint(1, 4),
       a.credit_limit_amt, CASE WHEN random() < 0.3 THEN bench_gen.rint(0, 50000) END,
       a.is_active, NULL, CASE WHEN random() < 0.05 THEN 'Requires PO number on invoice' END,
       a.created_at + bench_gen.rint(0, 60) * interval '1 day'
FROM (SELECT g, CASE WHEN g <= 20000 THEN g ELSE bench_gen.skew(20000, 2.2) END acc FROM generate_series(1, 50000) g) x
JOIN crm.accounts a ON a.account_id = x.acc
JOIN public.countries c ON c.country_code = a.country_code;
-- currencies without a price list fall back to USD price list; territory region 5 (UNASSIGNED) already folded into NA

INSERT INTO sales.quotes
SELECT g, o.opp_id, (SELECT min(cust_id) FROM sales.customers c WHERE c.account_id = o.account_id),
       o.created_at::date + 5, o.created_at::date + 35, round(o.amt * (0.9 + random() * 0.2)::numeric, 2),
       CASE WHEN o.stage = 'closed_won' THEN 'accepted' WHEN o.stage = 'closed_lost' THEN bench_gen.pick(ARRAY['rejected','expired'])
            WHEN o.stage IN ('proposal','negotiation') THEN 'sent' ELSE 'draft' END,
       o.owner_employee_id
FROM generate_series(1, 30000) g JOIN crm.opportunities o ON o.opp_id = g;

-- ---------------------------------------------------------------- finance reference
INSERT INTO finance.chart_of_accounts VALUES
 (1,'1000','Assets','asset',NULL,true),(2,'2000','Liabilities','liability',NULL,true),(3,'3000','Equity','equity',NULL,true),
 (4,'4000','Revenue','revenue',NULL,true),(5,'5000','Cost of Goods Sold','expense',NULL,true),(6,'6000','Operating Expenses','expense',NULL,true);
INSERT INTO finance.chart_of_accounts
SELECT 6 + g, (p * 1000 + g)::text, '',
       (ARRAY['asset','liability','equity','revenue','expense','expense'])[p], p, g % 37 <> 0
FROM generate_series(1, 144) g CROSS JOIN LATERAL (SELECT 1 + (g - 1) % 6 p) pp;
UPDATE finance.chart_of_accounts SET account_name = CASE account_type
   WHEN 'asset' THEN (ARRAY['Cash - Operating','Accounts Receivable','Inventory','Prepaid Expenses','Fixed Assets','Intercompany Receivable'])[1 + gl_account_id % 6]
   WHEN 'liability' THEN (ARRAY['Accounts Payable','Accrued Payroll','Deferred Revenue','VAT Payable','Credit Card Payable'])[1 + gl_account_id % 5]
   WHEN 'equity' THEN (ARRAY['Retained Earnings','Common Stock','APIC'])[1 + gl_account_id % 3]
   WHEN 'revenue' THEN (ARRAY['Product Revenue','Subscription Revenue','Services Revenue','Shipping Revenue','Discounts'])[1 + gl_account_id % 5]
   ELSE (ARRAY['Salaries','Rent','Software','Travel','Marketing Programs','Freight','COGS - Materials','Bank Fees','Depreciation'])[1 + gl_account_id % 9] END
   || ' ' || account_code
WHERE gl_account_id > 6;

INSERT INTO finance.plans VALUES
 (1,'FREE','Free','free',0,0,3,false),(2,'START','Starter','starter',49,490,10,false),(3,'TEAM','Team','team',199,1990,50,false),
 (4,'BUS','Business','business',599,5990,250,false),(5,'ENT','Enterprise','enterprise',2499,24990,NULL,false),
 (6,'TEAM-2019','Team (2019 pricing)','team',149,1490,50,true),(7,'PRO-OLD','Professional (legacy)','business',399,3990,100,true),
 (8,'ENT-PLUS','Enterprise Plus','enterprise',4999,49990,NULL,false);

INSERT INTO finance.budgets
SELECT cc.cost_center_id, gl.gl_account_id, m::date, round((5000 + random() * 120000)::numeric, 2)
FROM finance.cost_centers cc
CROSS JOIN (SELECT gl_account_id FROM finance.chart_of_accounts WHERE account_type = 'expense' AND gl_account_id > 6 ORDER BY gl_account_id LIMIT 6) gl
CROSS JOIN generate_series(date '2023-10-01', date '2026-09-01', interval '1 month') m
WHERE random() < 0.8;

-- ---------------------------------------------------------------- ops reference, platform users, API keys
INSERT INTO ops.carriers VALUES
 (1,'UPS','UPSN',true),(2,'FedEx','FDXE',true),(3,'DHL Express','DHLX',true),(4,'USPS','USPS',true),(5,'Royal Mail','RMLX',true),
 (6,'DPD','DPDX',true),(7,'Correios','CORR',true),(8,'Japan Post','JPPO',true),(9,'Australia Post','AUSP',true),
 (10,'Blue Dart','BDRT',true),(11,'Local Courier','LOCL',true),(12,'Old Freight Co','OLDF',false);

INSERT INTO ops.app_users
SELECT e.employee_id, 'staff.' || lower(e.first_name || e.last_name) || e.employee_id, e.email,
       '$2b$12$' || md5('pw' || e.employee_id) || md5('salt' || e.employee_id), 'staff', e.employee_id, NULL,
       random() < 0.9, NOT e.is_active, NULL, e.hire_dt::timestamptz + interval '1 day'
FROM hr.employees e;
INSERT INTO ops.app_users
SELECT 4000 + g, 'user' || g || '.' || c.account_id, coalesce(c.email, 'user' || g || '@client.example'),
       '$2b$12$' || md5('pw' || g) || md5('salt' || g), 'customer', NULL, c.account_id,
       random() < 0.35, random() < 0.01, NULL, greatest(c.created_at, timestamptz '2020-01-01')
FROM generate_series(1, 26000) g JOIN crm.contacts c ON c.contact_id = g * 2;
INSERT INTO ops.app_users
SELECT 30000 + g, 'svc-' || bench_gen.pick(ARRAY['etl','billing','sync','backup','report']) || '-' || g, 'svc' || g || '@northwind-corp.example',
       '$2b$12$' || md5('svc' || g), 'service', NULL, NULL, false, false, NULL, timestamptz '2021-01-01' + g * interval '10 days'
FROM generate_series(1, 40) g;

INSERT INTO ops.api_keys
SELECT g, acc, bench_gen.pick(ARRAY['prod','staging','zapier','erp-sync','reporting',NULL]), 'nwk_' || left(md5('k' || g), 8),
       'nwk_live_' || md5('key' || g) || md5('secret' || g),
       CASE WHEN random() < 0.6 THEN ARRAY['orders:read','products:read'] WHEN random() < 0.7 THEN ARRAY['orders:read','orders:write','invoices:read'] ELSE ARRAY['admin'] END,
       NULL, ca, CASE WHEN random() < 0.85 THEN bench_gen.rts(ca, bench_gen.t1()) END,
       CASE WHEN random() < 0.12 THEN bench_gen.rts(ca, bench_gen.t1()) END, bench_gen.pick(ARRAY[60,120,600,1200])
FROM (SELECT g, bench_gen.skew(20000, 2.5) acc, bench_gen.rts(timestamptz '2021-01-01', timestamptz '2026-08-01') ca FROM generate_series(1, 3000) g) x;
UPDATE ops.api_keys k SET created_by = (SELECT min(user_id) FROM ops.app_users u WHERE u.account_id = k.account_id);

-- ---------------------------------------------------------------- legacy / standalone tables
INSERT INTO public.tbl_cust_legacy
SELECT 100000 + g, upper(bench_gen.pick(ARRAY['ACME','GLOBEX','INITECH','HOOLI','ZENITH','APEX','CONTOSO']) || ' ' || bench_gen.pick(ARRAY['CORP','LTD','INC','GMBH',''])),
       (g % 999) || ' MAIN ST', upper(bench_gen.pick(ARRAY['chicago','london','berlin','paris','sydney','tokyo','toronto'])),
       bench_gen.pick(ARRAY['USA','U.S.','United States','UK','GB','Germany','DE','France',NULL]),
       CASE WHEN random() < 0.8 THEN '(555) ' || lpad((g * 7 % 1000)::text, 3, '0') || '-' || lpad((g * 13 % 10000)::text, 4, '0') END,
       CASE WHEN random() < 0.6 THEN 'legacy' || g || '@oldmail.example' END,
       round((random() * 50000)::numeric, 2),
       CASE WHEN random() < 0.7 THEN to_char(date '2012-01-01' + bench_gen.rint(0, 2500), bench_gen.pick(ARRAY['YYYY-MM-DD','MM/DD/YYYY','DD.MM.YYYY'])) END,
       bench_gen.pick(ARRAY['A','A','I','D',NULL]), upper(left(bench_gen.pick(bench_gen.first_names()), 1) || left(bench_gen.pick(bench_gen.last_names()), 2)),
       CASE WHEN random() < 0.4 THEN bench_gen.rint(1, 50000) END
FROM generate_series(1, 40000) g;

INSERT INTO sales.orders_archive_2019
SELECT 'SO19-' || lpad(g::text, 7, '0'), 'C' || lpad(bench_gen.skew(20000, 2)::text, 7, '0'), d,
       bench_gen.pick(ARRAY['C','C','C','C','X','R']::bpchar[]), gr, round(gr * 0.92, 2), bench_gen.pick(ARRAY['USD','USD','EUR','GBP']::bpchar[]),
       upper(left(bench_gen.pick(bench_gen.first_names()), 1) || left(bench_gen.pick(bench_gen.last_names()), 1)),
       bench_gen.pick(ARRAY['N. America','NA','EMEA','Europe','APAC','LatAm',NULL]), timestamp '2020-02-15 03:00:00'
FROM (SELECT g, date '2019-01-01' + bench_gen.rint(0, 364) d, round((20 + power(random(), 3) * 20000)::numeric, 2) gr FROM generate_series(1, 200000) g) x;

INSERT INTO marketing.newsletter_subscribers
SELECT g, lower(bench_gen.pick(bench_gen.first_names())) || '.' || g || '@' || bench_gen.pick(ARRAY['mail.example','inbox.example','corp.example']),
       CASE WHEN random() < 0.5 THEN bench_gen.pick(bench_gen.first_names()) || ' ' || bench_gen.pick(bench_gen.last_names()) END,
       bench_gen.pick(ARRAY['footer','blog','checkout','webinar','import']), bench_gen.pick(ARRAY['United States','USA','UK','Germany','India','Brasil',NULL]),
       s, CASE WHEN random() < 0.18 THEN s + bench_gen.rint(1, 600) * interval '1 day' END, random() < 0.7
FROM (SELECT g, bench_gen.rts(timestamptz '2020-01-01', bench_gen.t1()) s FROM generate_series(1, 80000) g) x;

INSERT INTO marketing.campaign_members
SELECT DISTINCT ON (cid, lid) cid, lid, bench_gen.pick(ARRAY['sent','sent','responded','registered','attended']),
       bench_gen.rts(bench_gen.t0(), bench_gen.t1()), random() < 0.25
FROM (SELECT bench_gen.rint(1, 400) cid, bench_gen.rint(1, 150000) lid FROM generate_series(1, 205000)) x;

ANALYZE;
