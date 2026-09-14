# Beboerregister-dumps

Manually downloaded exports of the resident register from INNA (the
administration company, formerly Cobblestone). `manage.py import_residents`
reads one of these:

    python manage.py import_residents data/register/users-2026-09-08.csv --dry-run
    python manage.py import_residents data/register/users-2026-09-08.csv

**Nothing in this directory is committed.** A dump is the name, email, phone
and home address of every person in the association — see the `.gitignore`
entry next to this file. Name a download after the day it was taken, so the
one the database was last reconciled against can be identified.

The anonymised sample used by the tests lives in
`backend/apps/accounts/tests/data/register_sample.csv` instead; it is made up
and is the only register data in the repository.
