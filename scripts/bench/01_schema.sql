-- 01_schema.sql: drop and recreate every schema, type and table of the bench database.
-- Run through create_big_db.sql (see header there). Foreign keys are declared inline;
-- the load steps run with session_replication_role = replica so they are not checked row by row,
-- and 04_finish.sql verifies every foreign key afterwards.
\set ON_ERROR_STOP on
SET client_min_messages = warning;

DROP SCHEMA IF EXISTS sales, crm, inventory, finance, hr, support, marketing, analytics, ops, bench_gen CASCADE;
DROP TABLE IF EXISTS public.regions, public.currencies, public.countries, public.fx_rates,
  public.tbl_cust_legacy, public.app_settings, public.audit_log, public.change_history CASCADE;

CREATE SCHEMA hr;
CREATE SCHEMA finance;
CREATE SCHEMA crm;
CREATE SCHEMA support;
CREATE SCHEMA marketing;
CREATE SCHEMA inventory;
CREATE SCHEMA sales;
CREATE SCHEMA ops;
CREATE SCHEMA analytics;

CREATE TYPE sales.order_status AS ENUM ('draft','pending','confirmed','shipped','delivered','completed','cancelled','returned');
CREATE TYPE crm.account_tier AS ENUM ('strategic','enterprise','mid_market','smb');
CREATE TYPE support.ticket_priority AS ENUM ('p1','p2','p3','p4');

-- ---------------------------------------------------------------- public reference data
CREATE TABLE public.regions (
  region_id    smallint PRIMARY KEY,
  region_code  text NOT NULL UNIQUE,
  region_name  text NOT NULL,
  hq_city      text,
  created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE public.currencies (
  currency_code char(3) PRIMARY KEY,
  currency_name text NOT NULL,
  symbol        text,
  minor_units   smallint NOT NULL DEFAULT 2
);

CREATE TABLE public.countries (
  country_code  char(2) PRIMARY KEY,
  country_name  text NOT NULL,
  iso3          char(3),
  region_id     smallint NOT NULL REFERENCES public.regions,
  currency_code char(3) NOT NULL REFERENCES public.currencies,
  phone_prefix  text,
  is_eu         boolean NOT NULL DEFAULT false,
  vat_rate_pct  numeric(5,2)
);

CREATE TABLE public.fx_rates (
  currency_code char(3) NOT NULL REFERENCES public.currencies,
  rate_dt       date NOT NULL,
  usd_rate      numeric(14,6) NOT NULL,
  source        text NOT NULL DEFAULT 'ecb',
  PRIMARY KEY (currency_code, rate_dt)
);

-- legacy dump from the 2018 CRM migration: no primary key, dates stored as text, no foreign keys
CREATE TABLE public.tbl_cust_legacy (
  custno              integer,
  custname            varchar(120),
  addr1               varchar(200),
  city                varchar(80),
  cntry               varchar(40),
  tel                 varchar(40),
  email_addr          varchar(120),
  crlimit             numeric(12,2),
  last_ord_dt         varchar(10),
  status_flg          char(1),
  sls_rep             varchar(10),
  migrated_to_cust_id integer
);

CREATE TABLE public.app_settings (
  setting_key   text PRIMARY KEY,
  setting_value jsonb NOT NULL,
  updated_at    timestamptz NOT NULL,
  updated_by    text
);

-- ---------------------------------------------------------------- hr (part 1)
CREATE TABLE hr.departments (
  department_id        integer PRIMARY KEY,
  dept_code            text NOT NULL UNIQUE,
  dept_name            text NOT NULL,
  parent_department_id integer REFERENCES hr.departments,
  head_employee_id     integer,           -- FK added at the end (cycle with hr.employees)
  location_country     char(2) REFERENCES public.countries,
  cost_center_code     text,
  is_active            boolean NOT NULL DEFAULT true,
  created_at           timestamptz NOT NULL
);

CREATE TABLE finance.cost_centers (
  cost_center_id integer PRIMARY KEY,
  cc_code        text NOT NULL UNIQUE,
  cc_name        text NOT NULL,
  department_id  integer NOT NULL REFERENCES hr.departments,
  is_active      boolean NOT NULL DEFAULT true,
  opened_dt      date
);

CREATE TABLE hr.employees (
  employee_id             integer PRIMARY KEY,
  employee_number         text NOT NULL UNIQUE,
  first_name              text NOT NULL,
  last_name               text NOT NULL,
  preferred_name          text,
  email                   text NOT NULL,
  personal_email          text,
  phone                   text,
  mobile_phone            text,
  ssn                     text,
  national_id             text,
  date_of_birth           date,
  gender                  char(1),
  hire_dt                 date NOT NULL,
  term_dt                 date,
  termination_reason      text,
  department_id           integer NOT NULL REFERENCES hr.departments,
  manager_id              integer REFERENCES hr.employees,
  job_title               text NOT NULL,
  job_level               smallint CHECK (job_level BETWEEN 1 AND 10),
  employment_type         text NOT NULL CHECK (employment_type IN ('full_time','part_time','contractor','intern')),
  salary_amt              numeric(12,2),
  salary_currency         char(3) REFERENCES public.currencies,
  bonus_target_pct        numeric(5,2),
  location_country        char(2) REFERENCES public.countries,
  office_city             text,
  work_mode               text CHECK (work_mode IN ('remote','hybrid','onsite')),
  is_active               boolean NOT NULL,
  home_address            text,
  postal_code             text,
  emergency_contact_name  text,
  emergency_contact_phone text,
  iban                    text,
  tax_code                text,
  badge_id                text,
  cost_center_id          integer REFERENCES finance.cost_centers,
  last_review_rating      smallint,
  pto_balance_days        numeric(5,1),
  created_at              timestamptz NOT NULL,
  updated_at              timestamptz,
  extra                   jsonb
);

CREATE TABLE hr.teams (
  team_id          integer PRIMARY KEY,
  team_name        text NOT NULL,
  department_id    integer NOT NULL REFERENCES hr.departments,
  lead_employee_id integer REFERENCES hr.employees,
  created_at       timestamptz NOT NULL
);

CREATE TABLE hr.team_members (
  team_id      integer NOT NULL REFERENCES hr.teams,
  employee_id  integer NOT NULL REFERENCES hr.employees,
  role_in_team text,
  joined_dt    date NOT NULL,
  left_dt      date,
  PRIMARY KEY (team_id, employee_id)
);

CREATE TABLE hr.payroll (
  payroll_id    bigint PRIMARY KEY,
  employee_id   integer NOT NULL REFERENCES hr.employees,
  pay_period    date NOT NULL,
  gross_amt     numeric(12,2) NOT NULL,
  tax_amt       numeric(12,2) NOT NULL,
  benefits_amt  numeric(12,2) NOT NULL,
  net_amt       numeric(12,2) NOT NULL,
  currency_code char(3) NOT NULL REFERENCES public.currencies,
  paid_dt       date,
  bank_iban     text,
  status        text NOT NULL CHECK (status IN ('scheduled','paid','reversed'))
);

CREATE TABLE hr.time_off (
  request_id   integer PRIMARY KEY,
  employee_id  integer NOT NULL REFERENCES hr.employees,
  approver_id  integer REFERENCES hr.employees,
  leave_type   text NOT NULL,
  start_dt     date NOT NULL,
  end_dt       date NOT NULL,
  days         numeric(4,1) NOT NULL,
  status       text NOT NULL CHECK (status IN ('requested','approved','rejected','cancelled')),
  requested_at timestamptz NOT NULL
);

CREATE TABLE hr.performance_reviews (
  review_id     integer PRIMARY KEY,
  employee_id   integer NOT NULL REFERENCES hr.employees,
  reviewer_id   integer REFERENCES hr.employees,
  review_period text NOT NULL,
  rating        smallint CHECK (rating BETWEEN 1 AND 5),
  comments      text,
  submitted_at  timestamptz
);

-- ---------------------------------------------------------------- support (reference) + marketing campaigns
CREATE TABLE support.tiers (
  tier_id               smallint PRIMARY KEY,
  tier_name             text NOT NULL UNIQUE,
  first_response_sla_h  integer NOT NULL,
  resolution_sla_h      integer NOT NULL,
  price_per_seat_amt    numeric(10,2),
  has_phone_support     boolean NOT NULL
);

CREATE TABLE marketing.campaigns (
  campaign_id       integer PRIMARY KEY,
  campaign_name     text NOT NULL,
  channel           text NOT NULL,
  region_id         smallint REFERENCES public.regions,
  owner_employee_id integer REFERENCES hr.employees,
  start_dt          date NOT NULL,
  end_dt            date,
  budget_amt        numeric(12,2),
  actual_spend_amt  numeric(12,2),
  target_audience   text,
  utm_campaign      text,
  status            text NOT NULL,
  created_at        timestamptz NOT NULL
);

-- ---------------------------------------------------------------- crm
CREATE TABLE crm.accounts (
  account_id          integer PRIMARY KEY,
  account_number      text NOT NULL UNIQUE,
  account_name        text NOT NULL,
  legal_name          text,
  parent_account_id   integer REFERENCES crm.accounts,
  industry            text,
  sub_industry        text,
  tier                crm.account_tier NOT NULL,
  region_id           smallint NOT NULL REFERENCES public.regions,
  country_code        char(2) NOT NULL REFERENCES public.countries,
  city                text,
  state_province      text,
  postal_code         text,
  billing_address     text,
  website             text,
  phone               text,
  employee_count      integer,
  annual_revenue_amt  numeric(16,2),
  owner_employee_id   integer REFERENCES hr.employees,
  support_tier_id     smallint REFERENCES support.tiers,
  preferred_currency  char(3) REFERENCES public.currencies,
  is_active           boolean NOT NULL,
  churned_at          timestamptz,
  lifecycle_stage     text,
  acquisition_source  text,
  tax_id              text,
  duns_number         text,
  credit_limit_amt    numeric(14,2),
  payment_terms_days  smallint,
  nps_score           smallint,
  health_score        numeric(5,2),
  last_activity_at    timestamptz,
  tags                text[],
  custom_fields       jsonb,
  created_at          timestamptz NOT NULL,
  updated_at          timestamptz
);

CREATE TABLE crm.contacts (
  contact_id         integer PRIMARY KEY,
  account_id         integer NOT NULL REFERENCES crm.accounts,
  first_name         text NOT NULL,
  last_name          text NOT NULL,
  email              text,
  phone              text,
  mobile             text,
  job_title          text,
  department         text,
  seniority          text,
  date_of_birth      date,
  linkedin_url       text,
  is_primary         boolean NOT NULL DEFAULT false,
  opt_in_marketing   boolean NOT NULL,
  preferred_language text,
  status             text NOT NULL,
  last_contacted_at  timestamptz,
  created_at         timestamptz NOT NULL
);

CREATE TABLE crm.leads (
  lead_id              integer PRIMARY KEY,
  first_name           text,
  last_name            text,
  email                text,
  phone                text,
  company_name         text,
  country_code         char(2) REFERENCES public.countries,
  lead_source          text NOT NULL,
  status               text NOT NULL CHECK (status IN ('new','working','nurture','qualified','disqualified','converted')),
  score                smallint,
  campaign_id          integer REFERENCES marketing.campaigns,
  owner_employee_id    integer REFERENCES hr.employees,
  converted_account_id integer REFERENCES crm.accounts,
  converted_at         timestamptz,
  created_at           timestamptz NOT NULL
);

CREATE TABLE crm.opportunities (
  opp_id             integer PRIMARY KEY,
  account_id         integer NOT NULL REFERENCES crm.accounts,
  lead_id            integer REFERENCES crm.leads,
  opp_name           text NOT NULL,
  opp_type           text NOT NULL CHECK (opp_type IN ('new_business','renewal','upsell','cross_sell')),
  stage              text NOT NULL CHECK (stage IN ('prospecting','qualification','proposal','negotiation','closed_won','closed_lost')),
  amt                numeric(14,2),
  probability_pct    smallint,
  forecast_category  text,
  expected_close_dt  date,
  closed_dt          date,
  is_won             boolean,
  lost_reason        text,
  owner_employee_id  integer REFERENCES hr.employees,
  created_at         timestamptz NOT NULL
);

CREATE TABLE crm.activities (
  activity_id  bigint PRIMARY KEY,
  account_id   integer NOT NULL REFERENCES crm.accounts,
  contact_id   integer REFERENCES crm.contacts,
  opp_id       integer REFERENCES crm.opportunities,
  employee_id  integer NOT NULL REFERENCES hr.employees,
  activity_type text NOT NULL,
  subject      text,
  activity_dt  timestamptz NOT NULL,
  duration_min integer,
  outcome      text
);

CREATE TABLE crm.account_team (
  account_id  integer NOT NULL REFERENCES crm.accounts,
  employee_id integer NOT NULL REFERENCES hr.employees,
  team_role   text NOT NULL,
  assigned_dt date NOT NULL,
  PRIMARY KEY (account_id, employee_id)
);

-- ---------------------------------------------------------------- support staff + knowledge base
CREATE TABLE support.agents (
  agent_id         integer PRIMARY KEY,
  employee_id      integer NOT NULL UNIQUE REFERENCES hr.employees,
  tier_id          smallint REFERENCES support.tiers,
  team_name        text,
  languages        text[],
  skills           jsonb,
  max_concurrent   smallint,
  is_active        boolean NOT NULL,
  joined_support_dt date
);

CREATE TABLE support.kb_articles (
  article_id         integer PRIMARY KEY,
  title              text NOT NULL,
  category           text,
  author_employee_id integer REFERENCES hr.employees,
  published_at       timestamptz,
  view_count         integer NOT NULL DEFAULT 0,
  helpful_votes      integer NOT NULL DEFAULT 0,
  is_published       boolean NOT NULL,
  body_md            text
);

-- ---------------------------------------------------------------- inventory
CREATE TABLE inventory.product_categories (
  category_id   integer PRIMARY KEY,
  parent_id     integer REFERENCES inventory.product_categories,
  category_name text NOT NULL,
  path          text,
  depth         smallint NOT NULL
);

CREATE TABLE inventory.suppliers (
  supplier_id     integer PRIMARY KEY,
  supplier_name   text NOT NULL,
  country_code    char(2) REFERENCES public.countries,
  contact_name    text,
  contact_email   text,
  phone           text,
  iban            text,
  payment_terms   text,
  rating          numeric(3,2),
  is_preferred    boolean NOT NULL,
  created_at      timestamptz NOT NULL
);

CREATE TABLE inventory.products (
  product_id         integer PRIMARY KEY,
  sku                text NOT NULL UNIQUE,
  product_name       text NOT NULL,
  short_desc         text,
  category_id        integer NOT NULL REFERENCES inventory.product_categories,
  supplier_id        integer REFERENCES inventory.suppliers,
  brand              text,
  list_price         numeric(12,2) NOT NULL,
  unit_cost          numeric(12,2) NOT NULL,
  msrp               numeric(12,2),
  currency_code      char(3) NOT NULL REFERENCES public.currencies,
  uom                text NOT NULL,
  pack_size          integer,
  weight_kg          numeric(8,3),
  length_cm          numeric(8,2),
  width_cm           numeric(8,2),
  height_cm          numeric(8,2),
  color              text,
  material           text,
  is_active          boolean NOT NULL,
  is_hazmat          boolean NOT NULL DEFAULT false,
  is_serialized      boolean NOT NULL DEFAULT false,
  launched_dt        date,
  discontinued_at    timestamptz,
  warranty_months    smallint,
  hs_code            text,
  country_of_origin  char(2) REFERENCES public.countries,
  barcode_ean        text,
  min_order_qty      integer,
  lead_time_days     integer,
  avg_rating         numeric(3,2),
  review_count       integer,
  tax_class          text,
  attrs              jsonb,
  created_at         timestamptz NOT NULL,
  updated_at         timestamptz
);

CREATE TABLE inventory.warehouses (
  warehouse_id        integer PRIMARY KEY,
  warehouse_code      text NOT NULL UNIQUE,
  warehouse_name      text NOT NULL,
  region_id           smallint NOT NULL REFERENCES public.regions,
  country_code        char(2) NOT NULL REFERENCES public.countries,
  city                text,
  manager_employee_id integer REFERENCES hr.employees,
  capacity_pallets    integer,
  is_3pl              boolean NOT NULL,
  opened_dt           date
);

CREATE TABLE inventory.stock_levels (
  warehouse_id    integer NOT NULL REFERENCES inventory.warehouses,
  product_id      integer NOT NULL REFERENCES inventory.products,
  qty_on_hand     integer NOT NULL,
  qty_reserved    integer NOT NULL,
  qty_on_order    integer NOT NULL,
  reorder_point   integer NOT NULL,
  reorder_qty     integer NOT NULL,
  bin_location    text,
  last_counted_at timestamptz,
  updated_at      timestamptz NOT NULL,
  PRIMARY KEY (warehouse_id, product_id)
);

CREATE TABLE inventory.item_aliases (
  alias_id        integer PRIMARY KEY,
  item_code       text NOT NULL,
  product_id      integer NOT NULL REFERENCES inventory.products,
  external_system text NOT NULL,
  is_primary      boolean NOT NULL,
  created_at      timestamptz NOT NULL
);

CREATE TABLE inventory.product_suppliers (
  product_id     integer NOT NULL REFERENCES inventory.products,
  supplier_id    integer NOT NULL REFERENCES inventory.suppliers,
  supplier_sku   text,
  cost_amt       numeric(12,2),
  lead_time_days integer,
  is_primary     boolean NOT NULL,
  PRIMARY KEY (product_id, supplier_id)
);

-- ---------------------------------------------------------------- sales
CREATE TABLE sales.sales_territories (
  territory_id        integer PRIMARY KEY,
  territory_name      text NOT NULL,
  region_id           smallint NOT NULL REFERENCES public.regions,
  manager_employee_id integer REFERENCES hr.employees,
  quota_amt           numeric(14,2)
);

CREATE TABLE sales.territory_reps (
  territory_id integer NOT NULL REFERENCES sales.sales_territories,
  employee_id  integer NOT NULL REFERENCES hr.employees,
  start_dt     date NOT NULL,
  end_dt       date,
  PRIMARY KEY (territory_id, employee_id)
);

CREATE TABLE sales.price_lists (
  price_list_id integer PRIMARY KEY,
  price_list_name text NOT NULL,
  currency_code char(3) NOT NULL REFERENCES public.currencies,
  valid_from    date NOT NULL,
  valid_to      date,
  is_default    boolean NOT NULL
);

CREATE TABLE sales.price_list_items (
  price_list_id integer NOT NULL REFERENCES sales.price_lists,
  product_id    integer NOT NULL REFERENCES inventory.products,
  price         numeric(12,2) NOT NULL,
  min_qty       integer NOT NULL DEFAULT 1,
  PRIMARY KEY (price_list_id, product_id)
);

CREATE TABLE sales.promo_codes (
  promo_code   text PRIMARY KEY,
  description  text,
  discount_pct numeric(5,2) NOT NULL CHECK (discount_pct > 0 AND discount_pct <= 60),
  campaign_id  integer REFERENCES marketing.campaigns,
  valid_from   date NOT NULL,
  valid_to     date,
  max_uses     integer,
  is_active    boolean NOT NULL
);

CREATE TABLE sales.customers (
  cust_id          integer PRIMARY KEY,
  account_id       integer NOT NULL REFERENCES crm.accounts,
  customer_code    text NOT NULL UNIQUE,
  customer_name    text NOT NULL,
  segment          text NOT NULL,
  email            text,
  phone            text,
  billing_country  char(2) REFERENCES public.countries,
  shipping_country char(2) REFERENCES public.countries,
  default_currency char(3) NOT NULL REFERENCES public.currencies,
  price_list_id    integer REFERENCES sales.price_lists,
  territory_id     integer REFERENCES sales.sales_territories,
  credit_limit     numeric(14,2),
  loyalty_points   integer,
  is_active        boolean NOT NULL,
  first_order_dt   date,
  notes            text,
  created_at       timestamptz NOT NULL
);

CREATE TABLE sales.quotes (
  quote_id    integer PRIMARY KEY,
  opp_id      integer REFERENCES crm.opportunities,
  cust_id     integer NOT NULL REFERENCES sales.customers,
  quote_dt    date NOT NULL,
  valid_until date,
  total_amt   numeric(14,2),
  status      text NOT NULL CHECK (status IN ('draft','sent','accepted','rejected','expired')),
  created_by  integer REFERENCES hr.employees
);

CREATE TABLE sales.orders (
  order_id          bigint PRIMARY KEY,
  order_no          text NOT NULL UNIQUE,
  cust_id           integer NOT NULL REFERENCES sales.customers,
  order_dt          timestamptz NOT NULL,
  status            sales.order_status NOT NULL,
  channel           text NOT NULL CHECK (channel IN ('web','sales_rep','partner','marketplace','edi')),
  sales_rep_id      integer REFERENCES hr.employees,
  territory_id      integer REFERENCES sales.sales_territories,
  warehouse_id      integer REFERENCES inventory.warehouses,
  quote_id          integer REFERENCES sales.quotes,
  promo_code        text REFERENCES sales.promo_codes,
  currency_code     char(3) NOT NULL REFERENCES public.currencies,
  fx_rate_to_usd    numeric(14,6),
  item_count        smallint NOT NULL,
  subtotal_amt      numeric(14,2),
  discount_amt      numeric(14,2),
  tax_amt           numeric(14,2),
  shipping_amt      numeric(14,2),
  total_amt         numeric(14,2),
  total_usd         numeric(14,2),
  payment_terms     text,
  ship_to_country   char(2) REFERENCES public.countries,
  ship_to_city      text,
  ship_to_postal    text,
  bill_to_country   char(2) REFERENCES public.countries,
  requested_ship_dt date,
  promised_dt       date,
  shipped_dt        timestamptz,
  delivered_dt      timestamptz,
  cancelled_dt      timestamptz,
  cancel_reason     text,
  is_gift           boolean NOT NULL DEFAULT false,
  gift_message      text,
  priority_flag     boolean NOT NULL DEFAULT false,
  weight_kg         numeric(10,3),
  source_system     text,
  external_ref      text,
  notes             text,
  metadata          jsonb,
  created_at        timestamptz NOT NULL,
  updated_at        timestamptz,
  created_by        text,
  updated_by        text
);

CREATE TABLE sales.order_items (
  order_item_id bigint PRIMARY KEY,
  order_id      bigint NOT NULL REFERENCES sales.orders,
  line_no       smallint NOT NULL,
  product_id    integer NOT NULL REFERENCES inventory.products,
  qty           integer NOT NULL CHECK (qty > 0),
  unit_price    numeric(12,2) NOT NULL,
  discount_pct  numeric(5,2) NOT NULL DEFAULT 0,
  line_amt      numeric(14,2) NOT NULL,
  tax_amt       numeric(12,2),
  line_status   text NOT NULL
);

CREATE TABLE sales.returns (
  return_id     integer PRIMARY KEY,
  order_item_id bigint NOT NULL REFERENCES sales.order_items,
  return_dt     date NOT NULL,
  qty           integer NOT NULL,
  reason        text NOT NULL,
  item_condition text,
  refund_amt    numeric(12,2),
  restocked     boolean NOT NULL
);

-- archive copied out of the old ERP in 2020: different column names, no keys, no foreign keys
CREATE TABLE sales.orders_archive_2019 (
  ord_no       varchar(20),
  cust_ref     varchar(20),
  ord_date     date,
  ord_status   char(1),
  gross        numeric(14,2),
  net          numeric(14,2),
  ccy          char(3),
  rep_initials varchar(4),
  region_txt   varchar(20),
  archived_at  timestamp
);

-- ---------------------------------------------------------------- support tickets
CREATE TABLE support.tickets (
  ticket_id          bigint PRIMARY KEY,
  ticket_ref         text NOT NULL UNIQUE,
  account_id         integer NOT NULL REFERENCES crm.accounts,
  contact_id         integer REFERENCES crm.contacts,
  cust_id            integer REFERENCES sales.customers,
  product_id         integer REFERENCES inventory.products,
  tier_id            smallint NOT NULL REFERENCES support.tiers,
  priority           support.ticket_priority NOT NULL,
  status             text NOT NULL CHECK (status IN ('new','open','pending_customer','escalated','resolved','closed')),
  category           text NOT NULL,
  subcategory        text,
  channel            text NOT NULL,
  subject            text NOT NULL,
  assigned_agent_id  integer REFERENCES support.agents,
  opened_at          timestamptz NOT NULL,
  first_response_at  timestamptz,
  resolved_at        timestamptz,
  closed_at          timestamptz,
  sla_due_at         timestamptz,
  escalated          boolean NOT NULL,
  reopened_count     smallint NOT NULL DEFAULT 0,
  csat_score         smallint CHECK (csat_score BETWEEN 1 AND 5),
  tags               text[]
);

CREATE TABLE support.ticket_messages (
  message_id        bigint PRIMARY KEY,
  ticket_id         bigint NOT NULL REFERENCES support.tickets,
  author_type       text NOT NULL CHECK (author_type IN ('customer','agent','system')),
  author_agent_id   integer REFERENCES support.agents,
  author_contact_id integer REFERENCES crm.contacts,
  sent_at           timestamptz NOT NULL,
  body              text,
  is_internal       boolean NOT NULL,
  attachment_count  smallint NOT NULL DEFAULT 0
);

CREATE TABLE support.sla_breaches (
  breach_id    integer PRIMARY KEY,
  ticket_id    bigint NOT NULL REFERENCES support.tickets,
  breach_type  text NOT NULL CHECK (breach_type IN ('first_response','resolution')),
  breached_at  timestamptz NOT NULL,
  minutes_over integer NOT NULL
);

-- ---------------------------------------------------------------- finance
CREATE TABLE finance.chart_of_accounts (
  gl_account_id        integer PRIMARY KEY,
  account_code         text NOT NULL UNIQUE,
  account_name         text NOT NULL,
  account_type         text NOT NULL CHECK (account_type IN ('asset','liability','equity','revenue','expense')),
  parent_gl_account_id integer REFERENCES finance.chart_of_accounts,
  is_active            boolean NOT NULL
);

CREATE TABLE finance.plans (
  plan_id       integer PRIMARY KEY,
  plan_code     text NOT NULL UNIQUE,
  plan_name     text NOT NULL,
  tier          text NOT NULL,
  monthly_price numeric(10,2) NOT NULL,
  annual_price  numeric(10,2),
  max_seats     integer,
  is_legacy     boolean NOT NULL
);

CREATE TABLE finance.subscriptions (
  subscription_id    integer PRIMARY KEY,
  account_id         integer NOT NULL REFERENCES crm.accounts,
  plan_id            integer NOT NULL REFERENCES finance.plans,
  status             text NOT NULL CHECK (status IN ('trialing','active','past_due','cancelled','paused')),
  billing_period     text NOT NULL CHECK (billing_period IN ('monthly','annual')),
  seats              integer NOT NULL,
  mrr_amt            numeric(12,2) NOT NULL,
  currency_code      char(3) NOT NULL REFERENCES public.currencies,
  started_at         timestamptz NOT NULL,
  current_period_end timestamptz,
  cancelled_at       timestamptz,
  cancel_reason      text,
  sales_rep_id       integer REFERENCES hr.employees,
  auto_renew         boolean NOT NULL,
  created_at         timestamptz NOT NULL
);

CREATE TABLE finance.subscription_events (
  event_id        integer PRIMARY KEY,
  subscription_id integer NOT NULL REFERENCES finance.subscriptions,
  event_type      text NOT NULL CHECK (event_type IN ('created','upgrade','downgrade','seat_change','renewal','cancel','reactivate')),
  event_dt        timestamptz NOT NULL,
  old_plan_id     integer REFERENCES finance.plans,
  new_plan_id     integer REFERENCES finance.plans,
  old_mrr         numeric(12,2),
  new_mrr         numeric(12,2)
);

CREATE TABLE finance.invoices (
  invoice_id    bigint PRIMARY KEY,
  invoice_no    text NOT NULL UNIQUE,
  order_id      bigint NOT NULL REFERENCES sales.orders,
  cust_id       integer NOT NULL REFERENCES sales.customers,
  issued_dt     date NOT NULL,
  due_dt        date NOT NULL,
  currency_code char(3) NOT NULL REFERENCES public.currencies,
  subtotal_amt  numeric(14,2) NOT NULL,
  tax_amt       numeric(14,2) NOT NULL,
  total_amt     numeric(14,2) NOT NULL,
  status        text NOT NULL CHECK (status IN ('draft','open','paid','overdue','void')),
  paid_at       timestamptz,
  terms         text,
  created_at    timestamptz NOT NULL
);

CREATE TABLE finance.invoice_lines (
  invoice_line_id bigint PRIMARY KEY,
  invoice_id      bigint NOT NULL REFERENCES finance.invoices,
  order_item_id   bigint NOT NULL REFERENCES sales.order_items,
  line_no         smallint NOT NULL,
  qty             integer NOT NULL,
  unit_price      numeric(12,2) NOT NULL,
  line_amt        numeric(14,2) NOT NULL,
  tax_amt         numeric(12,2) NOT NULL
);

CREATE TABLE finance.payments (
  payment_id    bigint PRIMARY KEY,
  invoice_id    bigint NOT NULL REFERENCES finance.invoices,
  paid_dt       timestamptz NOT NULL,
  amt           numeric(14,2) NOT NULL,
  currency_code char(3) NOT NULL REFERENCES public.currencies,
  method        text NOT NULL CHECK (method IN ('card','wire','ach','paypal','check')),
  card_last4    char(4),
  payer_iban    text,
  status        text NOT NULL CHECK (status IN ('settled','pending','failed','refunded')),
  processor_ref text,
  fee_amt       numeric(10,2)
);

CREATE TABLE finance.gl_entries (
  entry_id       bigint PRIMARY KEY,
  journal_id     bigint NOT NULL,
  gl_account_id  integer NOT NULL REFERENCES finance.chart_of_accounts,
  cost_center_id integer REFERENCES finance.cost_centers,
  entry_dt       date NOT NULL,
  posted_at      timestamptz,
  debit_amt      numeric(14,2) NOT NULL DEFAULT 0,
  credit_amt     numeric(14,2) NOT NULL DEFAULT 0,
  currency_code  char(3) NOT NULL REFERENCES public.currencies,
  source_type    text NOT NULL,
  source_id      bigint,
  memo           text,
  created_by     integer
);

CREATE TABLE finance.budgets (
  cost_center_id integer NOT NULL REFERENCES finance.cost_centers,
  gl_account_id  integer NOT NULL REFERENCES finance.chart_of_accounts,
  fiscal_month   date NOT NULL,
  budget_amt     numeric(14,2) NOT NULL,
  PRIMARY KEY (cost_center_id, gl_account_id, fiscal_month)
);

-- ---------------------------------------------------------------- ops (purchasing, logistics, platform)
CREATE TABLE ops.carriers (
  carrier_id   integer PRIMARY KEY,
  carrier_name text NOT NULL,
  scac         char(4),
  is_active    boolean NOT NULL
);

-- NOTE: ops.orders are PURCHASE orders to suppliers, not customer orders (see sales.orders)
CREATE TABLE ops.orders (
  order_id      integer PRIMARY KEY,
  po_number     text NOT NULL UNIQUE,
  supplier_id   integer NOT NULL REFERENCES inventory.suppliers,
  warehouse_id  integer NOT NULL REFERENCES inventory.warehouses,
  buyer_id      integer REFERENCES hr.employees,
  ordered_dt    date NOT NULL,
  expected_dt   date,
  received_dt   date,
  status        text NOT NULL CHECK (status IN ('draft','sent','partial','received','cancelled')),
  currency_code char(3) NOT NULL REFERENCES public.currencies,
  total_amt     numeric(14,2)
);

CREATE TABLE ops.order_lines (
  order_line_id integer PRIMARY KEY,
  order_id      integer NOT NULL REFERENCES ops.orders,
  product_id    integer NOT NULL REFERENCES inventory.products,
  qty_ordered   integer NOT NULL,
  qty_received  integer NOT NULL,
  unit_cost     numeric(12,2) NOT NULL
);

CREATE TABLE ops.shipments (
  shipment_id    bigint PRIMARY KEY,
  sales_order_id bigint NOT NULL REFERENCES sales.orders,
  warehouse_id   integer NOT NULL REFERENCES inventory.warehouses,
  carrier_id     integer NOT NULL REFERENCES ops.carriers,
  tracking_no    text,
  service_level  text,
  shipped_at     timestamptz NOT NULL,
  delivered_at   timestamptz,
  status         text NOT NULL,
  freight_cost   numeric(10,2),
  package_count  smallint
);

CREATE TABLE ops.app_users (
  user_id         integer PRIMARY KEY,
  username        text NOT NULL UNIQUE,
  email           text NOT NULL,
  password_hash   text NOT NULL,
  user_type       text NOT NULL CHECK (user_type IN ('staff','customer','service')),
  employee_id     integer REFERENCES hr.employees,
  account_id      integer REFERENCES crm.accounts,
  mfa_enabled     boolean NOT NULL,
  is_locked       boolean NOT NULL,
  last_login_at   timestamptz,
  created_at      timestamptz NOT NULL
);

CREATE TABLE ops.login_events (
  event_id       bigint PRIMARY KEY,
  user_id        integer NOT NULL REFERENCES ops.app_users,
  event_ts       timestamptz NOT NULL,
  success        boolean NOT NULL,
  failure_reason text,
  ip_address     inet,
  user_agent     text,
  geo_country    char(2),
  mfa_used       boolean
);

CREATE TABLE ops.api_keys (
  api_key_id  integer PRIMARY KEY,
  account_id  integer NOT NULL REFERENCES crm.accounts,
  key_name    text,
  key_prefix  text NOT NULL,
  api_key     text NOT NULL,
  scopes      text[] NOT NULL,
  created_by  integer REFERENCES ops.app_users,
  created_at  timestamptz NOT NULL,
  last_used_at timestamptz,
  revoked_at  timestamptz,
  rate_limit_per_min integer NOT NULL
);

CREATE TABLE ops.api_request_log (
  request_id   bigint PRIMARY KEY,
  api_key_id   integer NOT NULL REFERENCES ops.api_keys,
  requested_at timestamptz NOT NULL,
  http_method  text NOT NULL,
  endpoint     text NOT NULL,
  status_code  smallint NOT NULL,
  latency_ms   integer NOT NULL,
  bytes_out    integer,
  client_ip    inet,
  error_code   text
);

CREATE TABLE inventory.stock_movements (
  movement_id   bigint PRIMARY KEY,
  warehouse_id  integer NOT NULL REFERENCES inventory.warehouses,
  product_id    integer NOT NULL REFERENCES inventory.products,
  movement_type text NOT NULL CHECK (movement_type IN ('receipt','pick','adjustment','transfer_in','transfer_out','return')),
  qty           integer NOT NULL,
  ref_type      text,
  ref_id        bigint,
  moved_at      timestamptz NOT NULL,
  employee_id   integer REFERENCES hr.employees
);

-- ---------------------------------------------------------------- marketing
CREATE TABLE marketing.campaign_members (
  campaign_id integer NOT NULL REFERENCES marketing.campaigns,
  lead_id     integer NOT NULL REFERENCES crm.leads,
  member_status text NOT NULL,
  added_at    timestamptz NOT NULL,
  responded   boolean NOT NULL,
  PRIMARY KEY (campaign_id, lead_id)
);

CREATE TABLE marketing.email_events (
  event_id    bigint PRIMARY KEY,
  campaign_id integer NOT NULL REFERENCES marketing.campaigns,
  contact_id  integer REFERENCES crm.contacts,
  email       text NOT NULL,
  event_type  text NOT NULL CHECK (event_type IN ('sent','delivered','open','click','bounce','unsubscribe','spam_report')),
  event_ts    timestamptz NOT NULL,
  link_url    text,
  user_agent  text,
  message_id  uuid
);

-- standalone: no foreign keys, emails are not linked to crm.contacts
CREATE TABLE marketing.newsletter_subscribers (
  subscriber_id   integer PRIMARY KEY,
  email           text NOT NULL,
  full_name       text,
  source          text,
  country         text,
  subscribed_at   timestamptz NOT NULL,
  unsubscribed_at timestamptz,
  double_opt_in   boolean NOT NULL
);

-- ---------------------------------------------------------------- analytics
CREATE TABLE analytics.web_sessions (
  session_id    bigint PRIMARY KEY,
  cust_id       integer REFERENCES sales.customers,
  anonymous_id  uuid,
  started_at    timestamptz NOT NULL,
  ended_at      timestamptz,
  country_code  char(2) REFERENCES public.countries,
  device_type   text,
  browser       text,
  utm_source    text,
  utm_medium    text,
  utm_campaign  text,
  landing_page  text,
  page_count    smallint NOT NULL,
  converted     boolean NOT NULL
);

CREATE TABLE analytics.page_views (
  view_id     bigint PRIMARY KEY,
  session_id  bigint NOT NULL REFERENCES analytics.web_sessions,
  url_path    text NOT NULL,
  referrer    text,
  viewed_at   timestamptz NOT NULL,
  duration_ms integer
);

CREATE TABLE analytics.feature_usage (
  usage_id     bigint PRIMARY KEY,
  account_id   integer NOT NULL REFERENCES crm.accounts,
  feature_name text NOT NULL,
  usage_dt     date NOT NULL,
  event_count  integer NOT NULL,
  active_users integer NOT NULL
);

-- ---------------------------------------------------------------- audit / history (public)
CREATE TABLE public.audit_log (
  audit_id      bigint PRIMARY KEY,
  occurred_at   timestamptz NOT NULL,
  actor_user_id integer REFERENCES ops.app_users,
  actor_type    text NOT NULL,
  action        text NOT NULL,
  entity_schema text NOT NULL,
  entity_table  text NOT NULL,
  entity_id     bigint,
  ip_address    inet,
  request_id    uuid,
  severity      text NOT NULL,
  details       jsonb
);

-- no foreign keys: changed_by is an hr.employees id OR an ops.app_users id depending on change_source
CREATE TABLE public.change_history (
  change_id     bigint PRIMARY KEY,
  table_name    text NOT NULL,
  record_pk     text NOT NULL,
  column_name   text NOT NULL,
  old_value     text,
  new_value     text,
  changed_by    integer,
  change_source text NOT NULL,
  changed_at    timestamptz NOT NULL
);

ALTER TABLE hr.departments ADD FOREIGN KEY (head_employee_id) REFERENCES hr.employees;

-- ---------------------------------------------------------------- comments (deliberately on only ~40% of tables)
COMMENT ON SCHEMA sales IS 'Customer-facing order-to-cash: customers, sales orders, line items, quotes, returns.';
COMMENT ON SCHEMA ops IS 'Operations: purchasing (ops.orders = purchase orders), shipping, platform users, API access.';
COMMENT ON TABLE public.regions IS 'Sales regions: NA, LATAM, EMEA, APAC (+ UNASSIGNED).';
COMMENT ON TABLE public.fx_rates IS 'Daily FX rate, 1 unit of currency_code expressed in USD.';
COMMENT ON TABLE public.tbl_cust_legacy IS 'Legacy customer dump from old CRM (2018). Do not use for reporting.';
COMMENT ON TABLE public.audit_log IS 'Security and data-access audit trail written by the application for every sensitive action.';
COMMENT ON TABLE public.change_history IS 'Field-level change history (old/new values) for master data tables.';
COMMENT ON TABLE hr.employees IS 'All staff, current and former. manager_id points at another employee.';
COMMENT ON COLUMN hr.employees.manager_id IS 'Direct manager (self reference). NULL for the CEO.';
COMMENT ON COLUMN hr.employees.term_dt IS 'Termination date; NULL while employed.';
COMMENT ON TABLE hr.payroll IS 'Monthly payroll run per employee.';
COMMENT ON TABLE crm.accounts IS 'Companies we sell to (client organisations). Billing entities live in sales.customers.';
COMMENT ON COLUMN crm.accounts.tier IS 'Commercial segment, not the support tier.';
COMMENT ON COLUMN crm.accounts.support_tier_id IS 'Contracted support tier (support.tiers).';
COMMENT ON TABLE crm.contacts IS 'People at client accounts.';
COMMENT ON TABLE crm.opportunities IS 'Sales pipeline deals.';
COMMENT ON COLUMN crm.opportunities.amt IS 'Deal value in USD.';
COMMENT ON TABLE sales.customers IS 'Billing customer (cust_id); each belongs to one crm account.';
COMMENT ON TABLE sales.orders IS 'Customer sales orders.';
COMMENT ON COLUMN sales.orders.total_amt IS 'Order total in the order currency (currency_code). Use total_usd for cross-currency sums.';
COMMENT ON COLUMN sales.orders.total_usd IS 'Order total converted to USD at order date.';
COMMENT ON COLUMN sales.order_items.qty IS 'Units ordered';
COMMENT ON TABLE sales.orders_archive_2019 IS 'Pre-2020 orders copied from the old ERP. cust_ref = sales.customers.customer_code (not enforced).';
COMMENT ON TABLE inventory.products IS 'Product master (SKU catalogue).';
COMMENT ON TABLE inventory.stock_levels IS 'Current stock per warehouse and product; reorder when qty_on_hand - qty_reserved < reorder_point.';
COMMENT ON TABLE inventory.item_aliases IS 'Alternative item codes for products in external systems (ERP, marketplace, EDI).';
COMMENT ON TABLE ops.orders IS 'Purchase orders to suppliers (NOT customer orders).';
COMMENT ON TABLE ops.api_request_log IS 'One row per public API request. status_code >= 400 is a failed request.';
COMMENT ON TABLE ops.login_events IS 'Login attempts for ops.app_users (portal and staff).';
COMMENT ON COLUMN ops.api_keys.api_key IS 'Secret key value. Never expose.';
COMMENT ON TABLE finance.invoices IS 'Customer invoices, one per shipped sales order.';
COMMENT ON TABLE finance.subscriptions IS 'SaaS subscriptions per account; mrr_amt is monthly recurring revenue in USD.';
COMMENT ON TABLE finance.gl_entries IS 'General ledger lines; each journal_id balances (debits = credits).';
COMMENT ON TABLE finance.chart_of_accounts IS 'GL accounts (not customer accounts).';
COMMENT ON TABLE support.tickets IS 'Customer support tickets.';
COMMENT ON COLUMN support.tickets.resolved_at IS 'When the ticket was resolved; NULL if still open.';
COMMENT ON TABLE support.tiers IS 'Support plans with SLA targets in hours.';
COMMENT ON TABLE marketing.email_events IS 'Email delivery / engagement events per campaign send.';
COMMENT ON TABLE analytics.page_views IS 'Web page views; join to web_sessions for attribution.';
