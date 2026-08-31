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

from .models import User
from .serializers import LoginSerializer, SignupSerializer, UserSerializer

#: The only thing signup ever answers. Identical whether an account was created,
#: the email was already taken, or the submission looked automated — see
#: `SignupView`.
SIGNUP_RECEIVED = _(
    "Tak. Din anmodning er sendt til bestyrelsen, som godkender nye brugere manuelt. "
    "Du kan logge ind, når din konto er aktiveret."
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
    """Public signup. Creates an inactive account for the board to approve.

    Three different things happen here and all three look identical from
    outside — same status, same body:

    * A real signup creates an inactive `User` and a pending `SignupRequest`.
    * An email that already has an account creates nothing. Saying so would let
      anyone test whether a given address belongs to a resident here, and there
      is no confirmation email to hide behind yet.
    * A filled honeypot creates nothing, and says nothing about why. A bot that
      learns which field gave it away is a bot that stops filling it in.

    `202 Accepted` rather than `201 Created`: from the applicant's point of view
    nothing usable has been created, and something still has to happen before it
    is. The throttle is per-IP and deliberately tight — a resident signs up once,
    ever, so there is no legitimate traffic to protect. There is intentionally no
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
                serializer.save()
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
