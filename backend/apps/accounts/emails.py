"""
The portal's transactional email, in one place.

Everything is sent synchronously from the request/response cycle or the
admin action that triggered it — there is no task queue yet, so a slow SMTP
round-trip is a slow response rather than a dropped message. That is an
acceptable trade at this volume (see INFRASTRUCTURE.md, *Email*); revisit it
if sending ever needs to survive a provider outage without blocking the
caller.

Every message is HTML with a plain-text alternative, built from the templates
under `accounts/emails/` — `base.html`/`base.txt` hold the shared shell (see
`base.html`'s comment for why it is a table with every style inlined), so a
new notification is a content template, not a new layout.
"""

from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.utils.translation import gettext as _


def _send(*, subject, template, context, to):
    text_body = render_to_string(f"accounts/emails/{template}.txt", context)
    html_body = render_to_string(f"accounts/emails/{template}.html", context)
    message = EmailMultiAlternatives(subject=subject, body=text_body, to=[to])
    message.attach_alternative(html_body, "text/html")
    message.send()


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

    _send(
        subject=_("Nulstil din adgangskode"),
        template="password_reset",
        context={"link": link},
        to=user.email,
    )


def send_signup_pending_notification(signup_request, request):
    """Tell the board a signup needs a human, because nothing else will.

    Only called for a request the register could not resolve on its own —
    see `SignupView`, which checks `is_pending` after `auto_approve` before
    calling this. Without it, a pending request is invisible until a board
    member happens to open `/admin/`; the queue existed before this email did
    and worked, but only as fast as someone remembered to check.

    `SIGNUP_NOTIFICATION_EMAIL` may be unset — a bare checkout should not
    require it to run signup at all — in which case this quietly does
    nothing rather than raising, matching how the shop-rental sync's Google
    settings degrade when left blank.
    """
    if not settings.SIGNUP_NOTIFICATION_EMAIL:
        return
    admin_link = request.build_absolute_uri(
        reverse("admin:accounts_signuprequest_change", args=[signup_request.pk])
    )
    _send(
        subject=_("Ny anmodning om oprettelse"),
        template="signup_pending",
        context={
            "signup_request": signup_request,
            "name": signup_request.user.get_full_name() if signup_request.user else "",
            "admin_link": admin_link,
        },
        to=settings.SIGNUP_NOTIFICATION_EMAIL,
    )


def send_signup_approved_email(signup_request, request):
    """Tell an applicant their account is live, once a board member says so.

    Only for a request that was actually sitting in the queue: an
    auto-approved signup is told on the spot, in the page it just submitted
    (`SIGNUP_RECEIVED`), so sending this there too would just be a second
    copy of the same news. `SignupRequestAdmin._apply` is the one caller,
    and only for `decision == "approve"` — see its docstring.
    """
    login_link = request.build_absolute_uri("/login")
    _send(
        subject=_("Din bruger er godkendt"),
        template="signup_approved",
        context={"login_link": login_link},
        to=signup_request.email,
    )
