# Infrastructure

Where the portal is hosted, who sends its email, and why. `OPERATIONS.md`
describes how a release reaches the server; this file records what was chosen,
what was rejected, and what is actually running.

The database and application server exist — see *As provisioned* below. The
prices quoted further down are indicative, checked August 2026, and were
gathered before those plans were picked, so they read low. Confirm the real
figure against the first invoice.

## Conclusion

| Concern              | Choice                          | Indicative cost |
| -------------------- | ------------------------------- | --------------- |
| App + database       | UpCloud, Copenhagen (`dk-cph1`) | ~€15–20/month   |
| Association mailboxes| Proton Business                 | per seat        |
| Email sent by the app| Scaleway Transactional Email    | low, usage-based|

All three are European-owned with no US parent company. Each is
independently replaceable.

The launch scope is the booking system plus shop-rental applications, and it
is sized accordingly — one small server, one single-node database, no load
balancer:

| Item                                   | Indicative cost   |
| -------------------------------------- | ----------------- |
| Cloud server (1–2 vCPU)                | ~€5–7/month       |
| Managed PostgreSQL, smallest single node| ~€8–12/month     |
| Backup storage (see *Where backups go*)| nothing, for now  |
| Scaleway Transactional Email           | low, usage-based  |
| TLS certificates (Let's Encrypt)       | free              |

Deliberately not bought at launch: a Managed Load Balancer (unnecessary for
a single app node), a second database node (see *Sizing* below), and a
separate staging database — local development runs on SQLite, which the
default settings already do.

## As provisioned

Both live in `dk-cph1` — Ballerup, greater Copenhagen — so the application and
its database sit in the same zone and talk over UpCloud's private network
rather than the public internet.

**Database — `abj-portal-db`**

- Managed PostgreSQL 18, 1 core, 2 GiB memory, 50 GiB storage
- Single node, so three days of point-in-time recovery. `deploy/backup.sh`
  covers anything older; see *Sizing* for why that trade was made
- Public access disabled. Nothing outside UpCloud can reach it, which is
  sufficient because everything that touches it runs on the server: the app,
  the nightly backup, and `migrate`, which the deploy workflow runs over SSH
  rather than from the CI runner

**Application server — `abj-portal-app`**

- 1 vCPU, 2 GiB memory, 20 GB storage
- Storage encrypted at rest, AES-256. Chosen at creation because UpCloud cannot
  enable it afterwards — retrofitting means cloning to a new encrypted device

The encryption is worth stating precisely, because it is the kind of control
that gets over-claimed. It protects data at rest: decommissioned disks,
snapshots, physical access to the hardware. The volume is transparently
decrypted while the server runs, so it is no defence against a stolen SSH key
or a root compromise. It earns its place because the server holds
`/opt/abj-portal/.env` — database credentials, `DJANGO_SECRET_KEY`, the SMTP
password — along with Caddy's TLS private keys and the nightly dump for the few
seconds before `age` encrypts it.

Two properties of this setup that are easy to forget later:

- **Neither disk can be shrunk.** Growing either is a hot resize; shrinking
  means a clone or a rebuild. Both were therefore sized with room to spare.
- **`deploy/backup.sh` pins a `pg_dump` image matching the database's major
  version.** `pg_dump` refuses to run against a newer server, so a database
  upgrade means raising that pin first — otherwise the nightly backup stops
  and says so only in cron mail.

## The requirement

The brief asks for "DigitalOcean or another reliable and socially
responsible hosting provider (ie. NO Amazon and that kind)". In discussion
this was sharpened to a stronger criterion: European ownership, not merely
European data residency, so that no non-EU jurisdiction can compel access
to resident data.

That distinction is what drove every choice below. The portal stores names,
addresses, resident numbers and each resident's identifier in the
association's other database — personal data for which the association is
the data controller.

## Why not DigitalOcean

The original plan was DigitalOcean App Platform with managed Postgres, and
as a product it fits this project well: push-to-deploy, a release phase for
`migrate`, managed backups, no server to administer.

It fails the sharpened requirement. DigitalOcean is US-owned, so even with
everything hosted in the Frankfurt region the company is subject to the US
CLOUD Act. The databehandleraftale then needs a third-country transfer
assessment attached. Data residency in the EU is not the same thing as data
sovereignty, and it was the second that turned out to be the point.

## Why not Hetzner

Hetzner was the first alternative considered: German-owned, EU datacentres,
renewable power, and roughly a fifth of the cost.

It was rejected on operations rather than principle. Hetzner Cloud is IaaS
only — there is no managed PostgreSQL offering at all. That would leave the
association self-hosting Postgres, and self-hosted database backups are
exactly the thing that quietly stops working in a project maintained by
volunteers whose composition changes every few years. Managed Postgres with
verified backups is worth more here than the money it saves.

OVHcloud was also considered and set aside. Its Web PaaS is the closest
European equivalent to push-to-deploy, but its console and support are hard
going for an occasional maintainer.

## Why UpCloud rather than Scaleway

Both are genuinely European-owned with EU-only infrastructure, so both
satisfy the requirement equally and neither carries CLOUD Act exposure.
Scaleway is French (Iliad group); UpCloud is Finnish, ISO 27001 certified
and a CISPE member. The decision came down to operational detail.

**Managed Postgres pricing at small scale.** Scaleway's ladder is steep:
`DB-DEV-S` is about €11/month but is a dev-grade single node, `DB-DEV-M`
about €28/month with 7-day PITR, and anything with real failover jumps to
€80–123/month. UpCloud's managed PostgreSQL starts around €8–10/month for a
10 GB single node. Neither is expensive in absolute terms, but UpCloud's
entry tier is an honest fit for one association's booking calendar.

**A Danish region.** UpCloud opened `dk-cph1` on 16 December 2025, in
Ballerup, roughly 15 km from central Copenhagen. It runs on 100% renewable
power and feeds its waste heat into the local district heating network. For
AB Jæger, storing its own residents' data, that is easier to justify to a
general assembly than a datacentre in Paris, and it reads as a direct hit on
the brief's "socially responsible" wording.

**No lock-in.** Plain VMs, standard PostgreSQL, S3-compatible object
storage. If UpCloud were ever acquired by a US company — a real risk for a
mid-size Finnish firm, and one that would undermine the whole reason for
choosing it — migrating away is mechanical rather than a rewrite. This is
where plain infrastructure beats a proprietary platform, including
Scaleway's Serverless Containers.

**Fixed pricing with no data-transfer charges,** which matters once
residents are uploading and downloading documents and photos.

### What choosing UpCloud costs

UpCloud has no PaaS, so there is a virtual machine to look after. Scaleway
Serverless Containers would have removed that entirely, and that is the one
real argument the other way.

The mitigation is that the managed database holds all the state, which
leaves the VM effectively stateless: a container running gunicorn and
WhiteNoise, rebuildable from scratch by CI in minutes, with
`unattended-upgrades` handling patching. That is a very different
maintenance posture from also hand-rolling Postgres backups.

One genuine gap: UpCloud has no managed observability product — no metrics,
logs or APM service. Self-hosting Grafana and Loki on a small VM, or buying
an EU-hosted service, is extra work that App Platform would partly have
provided.

### Sizing: start on one node

Backup retention on UpCloud depends on node count, not on a setting: 1-node
plans give 3 days of point-in-time recovery, 2-node plans 15 days, 3-node
plans 31 days. The 2-node Standard plan is around €60/month, which is far
more than the launch scope — the booking system plus shop-rental
applications — can justify.

Launch on the **smallest single-node plan** (around €8–12/month) and cover
the retention gap with an application-level backup: a nightly `pg_dump`,
encrypted, pushed to object storage with 90-day retention. See
`deploy/backup.sh`.

That split matches the actual risks. What a second node buys is automatic
failover, and the portal does not need it: if the database node fails,
nobody is harmed by being unable to book the community room for an hour.
What the nightly dump buys is *long retention*, and that covers the failure
that is actually likely — data deleted or corrupted and nobody noticing for
a week, which three days of PITR does not reach. The cheap option is
therefore the better fit for this risk profile as well as the cheaper one.

Two conditions attached:

- **The dump must be restore-tested,** not merely scheduled. An untested
  backup is not a backup. Restore into a scratch database and check row
  counts at least once a quarter.
- **Dumps contain resident personal data,** so they are encrypted before
  upload, stored in the EU, and expire on the same retention schedule the
  association adopts for the data itself.

Scaling up later is a console operation, not a migration: UpCloud does
zero-downtime plan and node-count changes, and discounts the second node by
10% and the third by 30%. Moving to 2-node when membership or the value of
the data justifies it costs an afternoon's attention, not a project.

Self-hosting Postgres on the app VM would save the remaining €8–12/month
and is not worth it — it buys back responsibility for major-version
upgrades, which is the one piece of database administration most likely to
go wrong in a project maintained by volunteers.

Scaleway's Serverless SQL Database was considered as a cheaper
scale-to-zero option and rejected: it offers PostgreSQL 14, which reaches
end of life in November 2026. That is not a foundation to start on.

## Email

These are two separate problems, and Proton only solves one of them.

### Mailboxes — Proton Business

Moving the association's own mailboxes (`bestyrelse@ab-jaeger.dk` and
similar) from Google Workspace to Proton Business is a good fit. Swiss
jurisdiction with an EU adequacy decision, zero-access encryption at rest,
and a privacy posture that matches the reason for leaving Google. No
concerns.

### Email sent by the app — Scaleway, not Proton

Proton is the wrong tool for the portal's own email: booking confirmations,
password resets, notifications.

Proton Business does offer SMTP submission tokens, so it is not impossible,
but the limits show what it is built for — roughly 100–200 messages per
hour, send-only with no IMAP, and no bounce or complaint webhooks, no
suppression list, and no deliverability visibility. It would cope with
today's handful of booking confirmations and then fail precisely when the
portal grows, which is the worst time to migrate.

**Scaleway Transactional Email** is the choice instead: French, Paris
region, and its DPA claims no US infrastructure dependency. Using Scaleway
for email while hosting on UpCloud is fine — it is a small, cheap,
independent piece.

Brevo and Mailjet are the more obvious European picks and were rejected for
a specific reason: both are EU-headquartered but run their EU
infrastructure on Google Cloud (Brevo in Belgium, Mailjet in Frankfurt and
Saint-Ghislain). If getting off Google is part of the point, they undercut
it. MailerSend (Lithuanian) is a reasonable fallback.

### Deliverability

Two senders will share `ab-jaeger.dk`. Send the app's mail from a
subdomain — `varsel.ab-jaeger.dk` or similar — so automated sending
reputation stays isolated from the board's human correspondence, and set up
SPF, DKIM and DMARC for both. Getting this wrong is the usual reason portal
email lands in spam.

There is no production email configuration yet: `config/settings/dev.py`
sets the console backend and nothing else exists. When the provider is
live, the SMTP settings and `DEFAULT_FROM_EMAIL` belong in
`config/settings/base.py`, read from environment variables.

## Google, for the shop-rental form

This one goes the other way from every other decision here, so it is worth being
explicit about what was traded.

Candidates for erhvervslejemål apply through a **Google Form**, and the portal
reads its responses sheet via the Sheets API using a read-only service account
(see `OPERATIONS.md`). That means a Google credential now sits on the portal's server
and the server makes outbound calls to Google on a timer — in a project whose
stated criterion is European *ownership*, not merely residency.

The reasoning for accepting it:

* **The exposure already exists and is not the portal's to remove.** The
  applicants' data is typed into a Google Form. Whether the portal reads that
  sheet or a committee member reads it in a browser, the data is in Google either
  way. Refusing to integrate would not have moved a single byte out of Google; it
  would only have kept the committee working in a spreadsheet.
* **The credential is narrow.** A service account with
  `spreadsheets.readonly`, and access granted solely by sharing that one
  spreadsheet with its address. It cannot read anything else in the Drive, and it
  cannot write.
* **The alternative was worse in the way that matters.** An Apps Script webhook
  would have put no credential on the server, but its failure mode — a
  submission arriving while the portal is down, or a trigger that silently
  stops — is indistinguishable from "nobody applied", in a volunteer-run system
  where nobody is watching. Polling reconciles the whole sheet every run, so
  every such failure heals itself.

What would actually resolve it is **replacing the Google Form** with a form in
the portal, writing applications directly to the database. That is the honest
long-term answer and it is not currently planned. If it happens,
`apps/shoprentals/sheets.py` is the only file that has to go: `ingest.py`
deliberately takes a header and rows from anywhere.

Worth noting for anyone reconsidering: applicants for a commercial lease are
businesses rather than residents, so this is not the association's
resident register. That does not make it public — it is committee-only, and
includes people's names and phone numbers — but the sensitivity is lower than the
data the rest of this document is written to protect.

## Room to grow

The load ceiling for a single andelsboligforening sits far below the point
where the choice of provider starts to matter — even with every AB Jæger
household active daily, this is one mid-size VM and a managed database.
Compute is not the constraint, so growth is a question of which services are
available rather than whether the platform can keep up.

UpCloud has the pieces the portal would reach for:

- **Managed Object Storage**, S3-compatible — document archives, meeting
  minutes, photos, attachments. Needed as soon as the portal accepts
  uploads; the current stack has no answer for this.
- **Managed Valkey** — cache, Celery broker for notification fan-out, and
  channel layer if real-time features arrive.
- **Managed Load Balancer** and **Managed Kubernetes**, for multi-node.
- **Managed OpenSearch**, if Postgres full-text search is outgrown.
- Multi-node PostgreSQL with 31-day retention.

Two things to keep in mind rather than act on now. If the portal ever grows
into a general communication platform, the significant new cost is GDPR
rather than infrastructure — storing message content raises retention
policy, an Article 30 processing record and possibly a DPIA, and that lands
on the board rather than the server. And `Event` still has no room FK; the
single-room assumption is deliberately isolated to `_clashing_events()`, so
adding more bookable spaces stays cheap as long as it is not duplicated
elsewhere.

## Where backups go

`deploy/backup.sh` needs somewhere to put its nightly encrypted dump. The
destination is deliberately still open, and the reasoning matters more than the
answer, because the obvious choice is the wrong one.

**UpCloud Managed Object Storage was rejected.** Its minimum invoicing unit is
250 GB and every instance is deployed at that size, so it would cost roughly
€4–5/month to hold perhaps 100 MB of dumps. More importantly, backups sitting
in the same account as the database are not off-site in any meaningful sense: a
lost account — billing dispute, stolen credentials, a mistaken deletion — takes
the database and its only copy together, which is precisely the failure the
dump exists to survive.

There is no security cost to storing them elsewhere. `backup.sh` encrypts to an
age public key before uploading, so whoever holds the bytes holds ciphertext.
Where they live is purely a resilience question.

**Until real resident data exists, none of this is urgent.** A backup of an
empty schema is theatre. The trigger is the first import of households — data
nobody can re-derive, and which GDPR Article 32 explicitly requires be
restorable.

Options, cheapest first:

- **A local directory on the server.** `BACKUP_REMOTE=/var/backups/abj-portal`
  works with no change to the script, because rclone treats a plain path as a
  local remote — same encryption, same 90-day pruning. It does not survive
  losing the server, but it does cover the likelier disaster of data quietly
  corrupted weeks ago, and moving off-site later is one variable.
- **Proton Drive**, included in the Proton Business subscription bought for the
  mailboxes. rclone has a Proton Drive backend, so this is off-provider at no
  additional cost.
- **Scaleway Object Storage**, on the account that exists for Transactional
  Email. Verify whether the advertised 75 GB free allowance is a standing tier
  or a three-month trial before relying on it.

Note that *Room to grow* still names UpCloud Managed Object Storage, for
document archives and resident uploads if the portal grows. That is not a
contradiction: files the app serves want to be near the app, and backups want
to be far from it.

## Before committing

1. ~~Confirm Managed Databases are available in `dk-cph1`.~~ **Confirmed** —
   Managed PostgreSQL is offered in Copenhagen, so the Danish-residency
   argument holds and both the database and the server live in `dk-cph1`.
   Provisioned as PostgreSQL 18, 1 core, 2 GiB memory, 50 GiB storage, single
   node, with public access disabled.
2. **Obtain the databehandleraftale and sub-processor list** from both UpCloud
   and Scaleway before any real resident data is migrated.
3. **Verify current pricing.** The figures above are indicative.

## Still to do

Where provisioning actually stands, so this can be picked up cold.

Working already: the database and server exist in `dk-cph1`; Docker, `age` and
`rclone` are installed; `deploy/` is at `/opt/abj-portal/` with a real `.env`;
and the stack is proven end-to-end — registry pull, `migrate` against the
managed database, `compose up`, and every `deploy/smoke.sh` assertion passing.

**The portal is live at `https://portal.ab-jaeger.dk` as of 27 August 2026.**
DNS resolves, Caddy holds a Let's Encrypt certificate and redirects `:80`, and
Django is seeing `X-Forwarded-Proto` correctly — the HSTS header is served, so
the `SECURE_PROXY_SSL_HEADER` chain works. The interim plain-HTTP arrangement
is retired and its section is gone from `OPERATIONS.md`.

A push to `main` now deploys by itself: the four `DEPLOY_*` secrets are set, the
CI key is in the server's `authorized_keys`, and `main` is protected by the
three CI jobs. The `PORTAL_DOMAIN` variable is set too, so each release now
runs `deploy/smoke.sh` against the live site before reporting success. The shop-rental sync is live too — the service account, its key
under `/opt/abj-portal/secrets/`, and a five-minute systemd timer, with the
association's back catalogue of applications imported.

Remaining, in dependency order:

1. **Confirm the interim HTTP configuration is fully retired.**
   `DJANGO_SECURE_SSL_REDIRECT=False` must be gone from `/opt/abj-portal/.env`,
   and this is the one item that cannot be checked from outside: Caddy answers
   `:80` itself, so Django never sees an insecure request and a lingering
   override looks exactly like a correct configuration. Check it on the server.
   `DJANGO_ALLOWED_HOSTS` should be the domain rather than the server's IP, and
   the server's `Caddyfile` should match this repository's rather than having
   been edited back by hand.
2. **Create the first superuser** — nobody can reach `/admin/` until one exists.
   `OPERATIONS.md`, *Administrative commands*.
3. **Tighten the database allowlist** to the server's utility-network IP,
   testing connectivity before and after so a failure is unambiguous.
4. **Harden the server**: swap, firewall limited to 22/80/443, confirm
   `unattended-upgrades` is active, SSH keys only.
5. **Choose a backup destination** (see *Where backups go*), fill the
   `BACKUP_*` values, run `backup.sh` by hand once, then schedule it — and
   restore-test it. Needed before the first real resident data, not before
   launch.
6. **Set up Scaleway Transactional Email** and the sending subdomain's SPF,
   DKIM and DMARC records, before anything in the app sends mail. The
   `ab-jaeger.dk` zone access that DNS needed covers the Proton mailbox
   migration (MX, SPF, DKIM, DMARC) too.
7. **Rotate the database password** if it has been copied anywhere off the
   server, and consider a dedicated application role rather than `upadmin`,
   which is the cluster administrator.

## Portability

Nothing in the code is provider-specific: the database comes from
`DATABASE_URL` through django-environ, serving is gunicorn plus WhiteNoise,
and there are no media uploads yet to require object storage. The hosting
decision is cheap to revisit, and the only artefact that needs updating is
`OPERATIONS.md`.
