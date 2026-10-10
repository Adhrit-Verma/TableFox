-- TableFox demo database: a small online shop with real relationships.
-- Run as the database owner (e.g. Neon's SQL editor in a separate project).
-- Then run the "read-only user" block at the bottom with your own password.

drop table if exists order_items, orders, products, customers cascade;

create table customers (
  id serial primary key,
  name text not null,
  email text not null unique,
  country text not null,
  created_at timestamptz not null default now()
);
comment on table customers is 'People who placed at least one order or created an account.';

create table products (
  id serial primary key,
  name text not null,
  category text not null,
  price numeric(10, 2) not null check (price > 0),
  active boolean not null default true
);
comment on table products is 'Catalog of items for sale.';

create table orders (
  id serial primary key,
  customer_id integer not null references customers (id),
  status text not null check (status in ('pending', 'paid', 'shipped', 'delivered', 'cancelled')),
  ordered_at timestamptz not null
);
comment on table orders is 'One checkout by a customer.';

create table order_items (
  order_id integer not null references orders (id),
  product_id integer not null references products (id),
  quantity integer not null check (quantity > 0),
  unit_price numeric(10, 2) not null,
  primary key (order_id, product_id)
);
comment on table order_items is 'Products and quantities inside each order.';

select setseed(0.42);

insert into customers (name, email, country, created_at)
select
  'Customer ' || n,
  'customer' || n || '@example.com',
  (array['India', 'United States', 'Germany', 'United Kingdom', 'Brazil', 'Japan'])[1 + (n % 6)],
  now() - (random() * interval '720 days')
from generate_series(1, 200) as n;

insert into products (name, category, price, active)
select
  (array['Classic', 'Pro', 'Mini', 'Max', 'Eco'])[1 + (n % 5)] || ' ' ||
  (array['Backpack', 'Lamp', 'Kettle', 'Headphones', 'Notebook', 'Mug', 'Chair', 'Watch'])[1 + (n % 8)],
  (array['Bags', 'Home', 'Kitchen', 'Audio', 'Stationery', 'Kitchen', 'Furniture', 'Accessories'])[1 + (n % 8)],
  round((5 + random() * 295)::numeric, 2),
  n % 17 <> 0
from generate_series(1, 40) as n;

insert into orders (customer_id, status, ordered_at)
select
  1 + floor(random() * 200)::int,
  (array['pending', 'paid', 'shipped', 'delivered', 'delivered', 'delivered', 'cancelled'])[1 + floor(random() * 7)::int],
  now() - (random() * interval '365 days')
from generate_series(1, 1500);

insert into order_items (order_id, product_id, quantity, unit_price)
select o.id, p.id, 1 + floor(random() * 4)::int, p.price
from orders o
cross join lateral (
  select id, price from products order by random() + o.id * 0 limit 1 + floor(random() * 3)::int
) p;

-- Read-only user for TableFox. Replace the password with a long random one (letters and digits).
-- create role tablefox_reader login password 'REPLACE_WITH_LONG_RANDOM_PASSWORD';
-- alter role tablefox_reader set default_transaction_read_only = on;
-- grant connect on database neondb to tablefox_reader;
-- grant usage on schema public to tablefox_reader;
-- grant select on all tables in schema public to tablefox_reader;
-- alter default privileges in schema public grant select on tables to tablefox_reader;
