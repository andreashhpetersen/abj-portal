"""
The shop-rental API. Erhvervsudvalg only, every endpoint, no exceptions.

`IsBusinessCommittee` is set on each view class rather than left to DRF's
default (`IsAuthenticated`), because the default would make this data visible to
every resident with a login — which is precisely what it must not be.

There is no create and no delete for applications: they exist because somebody
filled in the form, and `manage.py sync_applications` is the only thing that
brings them into being. An application nobody wants gets `status=rejected` and
stays on file, so the same applicant reappearing next year is recognisable.
"""

from django.db.models import Count, F, Q
from django.http import Http404
from rest_framework import mixins, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.generics import ListAPIView, RetrieveUpdateAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.accounts.permissions import IsBusinessCommittee

from .models import Application, ApplicationComment, ApplicationDetails, ApplicationStatus
from .serializers import (
    ApplicationCommentSerializer,
    ApplicationDetailsSerializer,
    ApplicationSerializer,
    CommitteeMemberSerializer,
)

#: Sort orders the list accepts. Anything else is ignored rather than rejected —
#: a stale bookmark should still show the applications.
ALLOWED_ORDERING = frozenset(
    {
        "submitted_at",
        "-submitted_at",
        "rating",
        "-rating",
        "status_changed_at",
        "-status_changed_at",
    }
)


def rating_order(ordering):
    """Order by rating with unrated applications last, whichever direction.

    NULLS-last needs an expression rather than a string, and it matters in both
    directions: an unrated application is not a bad one, so it does not belong
    at the top of "worst first" either.
    """
    expression = F(ordering.lstrip("-"))
    if ordering.startswith("-"):
        return expression.desc(nulls_last=True)
    return expression.asc(nulls_last=True)


class ApplicationViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """The applications list, and the workflow fields on a single application."""

    serializer_class = ApplicationSerializer
    permission_classes = [permissions.IsAuthenticated, IsBusinessCommittee]

    def get_queryset(self):
        applications = Application.objects.select_related("assignee", "details").annotate(
            _comment_count=Count("comments", distinct=True)
        )

        # Filters shape the list only. A detail route must reach any
        # application, or rejecting one would hide it from the very request
        # that wants to look at it again or put it back in play.
        if self.action != "list":
            return applications

        params = self.request.query_params

        # Repeatable, so the UI's status chips can be multi-select: the natural
        # question is "show me new and in-progress", not one status at a time.
        if statuses := params.getlist("status"):
            valid = [value for value in statuses if value in ApplicationStatus.values]
            if valid:
                applications = applications.filter(status__in=valid)

        if assignee := params.get("assignee"):
            # "unassigned" is a real thing to filter for — it is the pile nobody
            # has picked up — and cannot be expressed as an id.
            if assignee == "unassigned":
                applications = applications.filter(assignee__isnull=True)
            elif assignee.isdigit():
                applications = applications.filter(assignee_id=int(assignee))

        if (min_rating := params.get("min_rating")) and min_rating.isdigit():
            applications = applications.filter(rating__gte=int(min_rating))

        if search := params.get("q", "").strip():
            # `search_text` is every answer flattened at sync time, so this finds
            # "frisør" in whatever question happened to mention it rather than
            # only in the three mapped columns — which is how the committee
            # actually looks for things. Searching the JSON directly does not
            # work; see `searchable_text` for why.
            applications = applications.filter(
                Q(search_text__icontains=search) | Q(details__company_name__icontains=search)
            )

        ordering = params.get("ordering", "-submitted_at")
        if ordering in ALLOWED_ORDERING:
            applications = applications.order_by(
                rating_order(ordering) if ordering.lstrip("-") == "rating" else ordering
            )
        return applications

    @action(detail=False, methods=["get"])
    def summary(self, request):
        """How many applications sit in each status.

        One query, so the filter chips can carry counts. Answering "how big is
        the pile of new ones" without loading the pile is the whole point.
        """
        counts = dict(
            Application.objects.values_list("status").annotate(total=Count("id")).order_by()
        )
        return Response(
            {
                "total": sum(counts.values()),
                # Every status is present even at zero, so the UI can render a
                # stable row of chips rather than one that appears and vanishes.
                "by_status": {value: counts.get(value, 0) for value in ApplicationStatus.values},
            }
        )

    @action(detail=True, methods=["get", "post"], url_path="comments")
    def comments(self, request, pk=None):
        """Read the thread on an application, or add to it."""
        application = self.get_object()

        if request.method == "POST":
            serializer = ApplicationCommentSerializer(
                data=request.data, context=self.get_serializer_context()
            )
            serializer.is_valid(raise_exception=True)
            serializer.save(application=application, author=request.user)
            return Response(serializer.data, status=status.HTTP_201_CREATED)

        serializer = ApplicationCommentSerializer(
            application.comments.select_related("author"),
            many=True,
            context=self.get_serializer_context(),
        )
        return Response(serializer.data)


class ApplicationDetailsView(RetrieveUpdateAPIView):
    """The contract details for one application.

    Created on first read rather than requiring a POST: the row is a form the
    committee fills in over weeks, so "does it exist yet" is not a distinction
    worth making the client handle.
    """

    serializer_class = ApplicationDetailsSerializer
    permission_classes = [permissions.IsAuthenticated, IsBusinessCommittee]

    def get_object(self):
        application = Application.objects.filter(pk=self.kwargs["pk"]).first()
        if application is None:
            raise Http404("Ansøgningen findes ikke.")
        details, _created = ApplicationDetails.objects.get_or_create(application=application)
        return details


class CommentDeleteView(APIView):
    """Deleting a comment. Its author, or an admin.

    Editing is deliberately absent: a months-long thread that can be rewritten
    after the fact is not a record of what was decided.
    """

    permission_classes = [permissions.IsAuthenticated, IsBusinessCommittee]

    def delete(self, request, pk):
        comment = ApplicationComment.objects.filter(pk=pk).first()
        if comment is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if comment.author_id != request.user.pk and not request.user.is_staff:
            raise PermissionDenied("Du kan kun slette dine egne kommentarer.")
        comment.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class CommitteeMembersView(ListAPIView):
    """Who can be made responsible for an application.

    The list comes from `User.objects.business_committee()` so the rule for who
    counts as a committee member lives in one place — see
    `apps/accounts/models.py`.
    """

    serializer_class = CommitteeMemberSerializer
    permission_classes = [permissions.IsAuthenticated, IsBusinessCommittee]
    pagination_class = None

    def get_queryset(self):
        return User.objects.business_committee().order_by("first_name", "last_name", "email")
