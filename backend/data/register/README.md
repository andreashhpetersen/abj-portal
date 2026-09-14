# Beboerregister-dumps — kun til udvikling

**På serveren lægges udtrækket ikke nogen steder.** Bestyrelsen lægger det op i
admin under *Beboerregister → Importér beboerregister*; filen læses direkte ud af
requestet og gemmes aldrig. Se `OPERATIONS.md`.

Denne mappe findes til udvikling på egen maskine, hvor det er nemmere at have
filen liggende end at klikke den op hver gang:

    python manage.py import_residents data/register/users-2026-09-08.csv --dry-run
    python manage.py import_residents data/register/users-2026-09-08.csv

**Intet i mappen bliver committet**, og den er også i `.dockerignore`, så en fil
her ikke kan ende i et image — et udtræk er navn, email, telefonnummer og
hjemmeadresse på alle i foreningen. Navngiv en download efter den dag, den er
hentet, og slet den, når du er færdig.

Det anonymiserede eksempel, testene bruger, ligger i
`backend/apps/accounts/tests/data/register_sample.csv`; det er opdigtet og er de
eneste registerdata i repoet.
