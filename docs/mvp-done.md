# MVP Done Criteria

The MVP is considered ready when all of the following are true:

- the application installs with one command and starts on SQLite;
- CI runs linting and tests;
- the trade/warehouse scenario works end-to-end;
- document posting is idempotent;
- unposting and reposting do not corrupt balances;
- closed periods block old document changes;
- reports read through Register API;
- document journals use keyset pagination;
- CSV/XLSX export is available;
- HTML print forms are available;
- roles, organizations, audit log, and operation log exist in the schema;
- demo data can be seeded;
- SQLite backup exists;
- developer documentation explains trusted plugin extensions.

The first automated test suite covers the most important accounting invariants:

- posting creates movements;
- repeated posting does not duplicate movements;
- unposting removes movements;
- reposting restores movements;
- negative stock is blocked by default;
- closed periods block posting;
- turnover and balance APIs return expected values;
- register totals can be rebuilt;
- document journals page by keyset cursor;
- report rows can be exported to CSV and XLSX.
