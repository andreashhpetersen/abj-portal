"""
The portal's transactional email, in one place.

Everything here is plain text and sent synchronously from the request/response
cycle — there is no task queue yet, so a slow SMTP round-trip is a slow
response rather than a dropped message. That is an acceptable trade at this
volume (see INFRASTRUCTURE.md, *Email*); revisit it if sending ever needs to
survive a provider outage without blocking the caller.
"""

from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.utils.translation import gettext as _


def send_password_reset_email(user, request):
    """Mail a one-time reset link to `user`.

    The link points at the SPA's own route, not an API endpoint — the
    frontend collects the new password and posts it back to
    `/api/auth/password-reset/confirm/`. `request.build_absolute_uri()` is
    what makes the link correct in both development and production without a
    separate "frontend URL" setting: the project is same-origin in both (see
    CLAUDE.md), so the host the API was reached on is also the SPA's host.

    The token is `default_token_generator`'s — Django's own password-reset
    machinery, HMAC-derived from the user's pk, password hash and a
    timestamp. Nothing is stored: the token is self-verifying and expires on
    its own (`PASSWORD_RESET_TIMEOUT`, 3 days by default), and changing the
    password — the reset itself, or a login elsewhere — invalidates every
    token issued before it, because the hash it is derived from just changed.
    """
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    link = request.build_absolute_uri(f"/reset-password/{uid}/{token}/")

    send_mail(
        subject=_("Nulstil din adgangskode"),
        message=_(
            "Der er anmodet om at nulstille adgangskoden til din bruger på "
            "Beboerportalen.\n\n"
            "Klik på linket for at vælge en ny adgangskode:\n"
            "%(link)s\n\n"
            "Linket virker i 3 dage. Har du ikke selv bedt om dette, kan du "
            "roligt ignorere denne email — der sker intet, før linket bruges."
        )
        % {"link": link},
        from_email=None,  # DEFAULT_FROM_EMAIL
        recipient_list=[user.email],
    )
