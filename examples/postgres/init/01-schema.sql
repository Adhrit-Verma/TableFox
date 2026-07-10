create schema crm;
create schema billing;

create table crm.customers (
  id uuid primary key,
  email text not null unique,
  full_name text not null,
  lifecycle_stage text not null default 'lead',
  created_at timestamptz not null default now()
);

create table crm.accounts (
  id uuid primary key,
  customer_id uuid not null references crm.customers(id),
  account_name text not null,
  plan_code text not null,
  created_at timestamptz not null default now()
);

create table billing.invoices (
  id uuid primary key,
  account_id uuid not null references crm.accounts(id),
  invoice_number text not null unique,
  total_cents integer not null,
  status text not null,
  issued_at date not null
);

create table billing.payments (
  id uuid primary key,
  invoice_id uuid not null references billing.invoices(id),
  amount_cents integer not null,
  provider text not null,
  paid_at timestamptz not null
);

create index accounts_customer_id_idx on crm.accounts(customer_id);
create index invoices_account_id_idx on billing.invoices(account_id);
create index payments_invoice_id_idx on billing.payments(invoice_id);

create view billing.open_invoices as
select
  i.id,
  i.account_id,
  i.invoice_number,
  i.total_cents,
  i.issued_at
from billing.invoices i
where i.status in ('open', 'past_due');

comment on schema crm is 'Customer relationship and account ownership data.';
comment on schema billing is 'Invoice and payment data linked back to CRM accounts.';
comment on table crm.customers is 'People or organizations that can own accounts.';
comment on table crm.accounts is 'Commercial accounts owned by customers.';
comment on table billing.invoices is 'Invoices issued to accounts.';
comment on table billing.payments is 'Payments collected against invoices.';
comment on column crm.customers.email is 'Primary customer contact email.';
comment on column billing.invoices.total_cents is 'Invoice total stored in cents.';
