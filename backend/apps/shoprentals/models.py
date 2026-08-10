"""
Shop-rental applications (erhvervslejemål).

No models yet — placeholder, wired into settings and URLs. What is expected
here, from the project brief:

* An application model mirroring the fields of the public Google Form, plus an
  ingestion timestamp and the raw payload, so a form change never loses data.
* A status field with the workflow states the committee uses: new/read,
  in contact, saved for later, accepted, declined.
* Committee comments on an application (separate model, one row per comment,
  attributed to a member).
* Later: correspondence with applicants through a mail service.

Every endpoint in this app must be gated by
`apps.accounts.permissions.IsBusinessCommittee` — this data is visible only to
the erhvervsudvalg, not to members at large.
"""
