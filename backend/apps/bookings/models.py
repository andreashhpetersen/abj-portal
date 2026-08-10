"""
Community-room bookings (beboerlokale).

No models yet — this app is a placeholder that is already wired into settings
and URLs so the first feature commit is purely additive. What is expected here,
from the project brief:

* An event/booking model with a start/end datetime, a `created_by` member, and
  a category of PRIVATE or PUBLIC.
* PRIVATE bookings: no title/description needed, and may only be created up to
  a limited horizon (2 weeks) in advance. Admins can disable private booking
  entirely, which implies a settings/toggle model rather than a constant.
* PUBLIC events: title + description, optional recurrence at a regular
  interval, and members can register attendance (a through-model between member
  and event).
* Cancellation by the creator or an admin — see
  `apps.accounts.permissions.IsOwnerOrAdmin`.
"""
