"""
Session-based auth endpoints for the SPA.

Flow: the frontend calls /api/auth/csrf/ once to obtain the CSRF cookie, then
POSTs credentials to /api/auth/login/ with the X-CSRFToken header. After that
the session cookie authenticates every request, so there is no token to store
in JavaScript.
"""

from django.contrib.auth import authenticate, login, logout
from django.db import IntegrityError
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from .emails import send_password_reset_email
from .models import User
from .register import auto_approve
from .serializers import (
    LoginSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    SignupSerializer,
    UserSerializer,
)

#: The only thing signup ever answers. Identical whether an account was created
#: and activated on the spot, created and left for the board, the email was
#: already taken, or the submission looked automated — see `SignupView`.
#:
#: It has to cover both outcomes without saying which one happened, and it does
#: that by sending everybody to the login page. Telling a submitter their number
#: matched would confirm that a given `Beboernr.` belongs to an occupied
#: andelsbolig, which is the one thing signup has always refused to answer.
#: Trying to log in is the safe way to find out, because the login form only
#: names the reason to somebody who already has the password.
SIGNUP_RECEIVED = _(
    "Tak. Prøv at logge ind med det samme — står du i foreningens beboerregister, "
    "er din konto allerede klar. Ellers godkender bestyrelsen den manuelt, "
    "og så kan du logge ind, når den er aktiveret."
)

#: What `PasswordResetRequestView` answers whether or not the address has an
#: account — the same reasoning as `SIGNUP_RECEIVED`: a different response for
#: "sent" versus "no such account" would turn the form into a way to test
#: which emails belong to residents here.
PASSWORD_RESET_SENT = _(
    "Hvis der findes en bruger med den email, er der sendt et link til at "
    "nulstille adgangskoden."
)


@method_decorator(ensure_csrf_cookie, name="get")
class CSRFView(APIView):
    """Sets the csrftoken cookie. Call once before the first unsafe request."""

    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"detail": "CSRF cookie set"})


class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = authenticate(
            request,
            username=serializer.validated_data["email"],
            password=serializer.validated_data["password"],
        )
        if user is None:
            return self._rejected(
                serializer.validated_data["email"],
                serializer.validated_data["password"],
            )
        login(request, user)
        return Response(UserSerializer(user).data)

    def _rejected(self, email, password):
        """Say why, but only to someone who has proved who they are.

        `authenticate()` returns None for a wrong password and for a correct
        password on an inactive account alike, so a resident waiting for the
        board to approve their signup would be told their password was wrong —
        and would go looking for the password reset that does not exist yet.

        Naming the real reason is safe only because it requires the password:
        anyone who can trigger this message could log in the moment the account
        is approved, so it tells them nothing they do not already know. A wrong
        password still gets the vague answer, which is what keeps the login form
        from confirming who has an account here.
        """
        pending = User.objects.filter(email__iexact=email, is_active=False).first()
        if pending is not None and pending.check_password(password):
            return Response(
                {"detail": "Din konto afventer godkendelse fra bestyrelsen."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return Response(
            {"detail": "Forkert email eller adgangskode."},
            status=status.HTTP_401_UNAUTHORIZED,
        )


class SignupView(APIView):
    """Public signup. Checks the claim against the register, or asks the board.

    Four different things happen here and all four look identical from
    outside — same status, same body:

    * A claim whose number resolves to one eligible flat in INNA's register
      creates an active `User` with a `Resident` row and an approved
      `SignupRequest`. Nobody waits: living in an andelsbolig is what entitles
      somebody to the portal, and the register is where that fact lives, so
      there is nothing left for a human to decide. See `register.auto_approve`
      for what that trades away.
    * Anything the register cannot vouch for creates an inactive `User` and a
      pending `SignupRequest`, exactly as before — an unknown number, a mistyped
      one, a shop, a household member of a flat the export has not caught up
      with. The board's queue holds those, and only those.
    * An email that already has an account creates nothing. Saying so would let
      anyone test whether a given address belongs to a resident here, and there
      is no confirmation email to hide behind yet.
    * A filled honeypot creates nothing, and says nothing about why. A bot that
      learns which field gave it away is a bot that stops filling it in.

    `202 Accepted` for all of them, including the one that is now fully created:
    a `201` on a match and a `202` otherwise would turn the form into an oracle
    for testing resident numbers, which is precisely what the uniform answer
    exists to prevent.

    The throttle is per-IP and deliberately tight — a resident signs up once,
    ever, so there is no legitimate traffic to protect. It matters more than it
    used to: with the register deciding, the throttle is what stops the form
    being used to enumerate which numbers are real. There is intentionally no
    global cap: the board announcing the portal means every resident signing up
    the same evening, and a global limit would fail exactly then.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "signup"

    def post(self, request):
        serializer = SignupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if self._should_create(serializer.validated_data):
            try:
                auto_approve(serializer.save())
            except IntegrityError:
                # Two submissions for the same address at once. The loser
                # created nothing, which is also what it is about to be told.
                pass
        return Response({"detail": SIGNUP_RECEIVED}, status=status.HTTP_202_ACCEPTED)

    def _should_create(self, data):
        if data["website"]:
            return False
        # iexact, though the unique constraint is case-sensitive: two accounts
        # differing only in capitalisation are the same person in practice, and
        # the second one could never log in anyway.
        return not User.objects.filter(email__iexact=data["email"]).exists()


class PasswordResetRequestView(APIView):
    """Public: "I forgot my password". Sends a link, or pretends to.

    Answers `PASSWORD_RESET_SENT` whether or not the address matches an
    account — see its docstring — so nothing here may branch on that in a way
    that shows up in the response or its timing. The one thing this endpoint
    does that `SignupView` does not need to is actually send mail on the
    matching path, which is why it exists as its own view rather than folded
    into login.

    An inactive account (still awaiting board approval) does not get a link:
    there is no password to reset into using it yet, and issuing one would
    tell an applicant their account exists before the board has said so.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password_reset"

    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = User.objects.filter(
            email__iexact=serializer.validated_data["email"], is_active=True
        ).first()
        if user is not None:
            send_password_reset_email(user, request)
        return Response({"detail": PASSWORD_RESET_SENT}, status=status.HTTP_202_ACCEPTED)


class PasswordResetConfirmView(APIView):
    """The link from the reset email lands here with a new password attached."""

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password_reset_confirm"

    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(status=status.HTTP_204_NO_CONTENT)


class LogoutView(APIView):
    def post(self, request):
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class CurrentUserView(APIView):
    """Who am I — the SPA calls this on boot to restore its session."""

    def get(self, request):
        return Response(UserSerializer(request.user).data)

    def patch(self, request):
        serializer = UserSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)
