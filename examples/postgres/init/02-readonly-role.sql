create role dbmap_reader login password 'change-me-demo-only';

grant connect on database dbmap_demo to dbmap_reader;
grant usage on schema crm, billing to dbmap_reader;
grant select on all tables in schema crm, billing to dbmap_reader;
alter default privileges in schema crm grant select on tables to dbmap_reader;
alter default privileges in schema billing grant select on tables to dbmap_reader;
