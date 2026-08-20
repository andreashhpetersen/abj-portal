"""
The shop-rental API: who may see it, and what the committee can change.

The permission tests are the important ones. This is the only data in the portal
that most residents must not see, so "a logged-in neighbour gets 403" is worth
asserting on every route rather than trusting the default.
"""

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import ERHVERVSUDVALG_GROUP
from apps.shoprentals.models import (
    Application,
    ApplicationComment,
    ApplicationDetails,
    ApplicationStatus,
)

User = get_user_model()


@pytest.fixture
def committee_group(db):
    group, _created = Group.objects.get_or_create(name=ERHVERVSUDVALG_GROUP)
    return group


@pytest.fixture
def member(db, committee_group):
    user = User.objects.create_user(
        email="udvalg@ab-jaeger.dk", password="hemmeligt123", first_name="Jens"
    )
    user.groups.add(committee_group)
    return user


@pytest.fixture
def other_member(db, committee_group):
    user = User.objects.create_user(email="andet@ab-jaeger.dk", password="hemmeligt123")
    user.groups.add(committee_group)
    return user


@pytest.fixture
def neighbour(db):
    """A perfectly ordinary resident. Must see none of this."""
    return User.objects.create_user(email="nabo@example.dk", password="hemmeligt123")


@pytest.fixture
def application(db):
    return Application.objects.create(
        source_key="a" * 64,
        submitted_at=timezone.now(),
        applicant_name="Mette Sørensen",
        email="mette@blomster.dk",
        phone="12345678",
        answers=[{"question": "Formål", "value": "Blomsterbutik"}],
    )


def as_user(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


# --- access ---------------------------------------------------------------


def test_a_resident_outside_the_committee_cannot_list_applications(neighbour, application):
    response = as_user(neighbour).get(reverse("shoprentals:application-list"))
    assert response.status_code == 403


def test_a_resident_outside_the_committee_cannot_read_one_application(neighbour, application):
    url = reverse("shoprentals:application-detail", args=[application.pk])
    assert as_user(neighbour).get(url).status_code == 403


@pytest.mark.parametrize(
    "route",
    ["shoprentals:application-list", "shoprentals:application-summary", "shoprentals:members"],
)
def test_every_collection_route_is_closed_to_outsiders(neighbour, route):
    assert as_user(neighbour).get(reverse(route)).status_code == 403


def test_the_committees_other_routes_are_closed_to_outsiders(neighbour, application):
    client = as_user(neighbour)
    details = reverse("shoprentals:application-details", args=[application.pk])
    comments = reverse("shoprentals:application-comments", args=[application.pk])
    assert client.get(details).status_code == 403
    assert client.patch(details, {"cvr": "12345678"}, format="json").status_code == 403
    assert client.get(comments).status_code == 403


def test_an_anonymous_visitor_is_refused(application):
    assert APIClient().get(reverse("shoprentals:application-list")).status_code in (401, 403)


def test_a_committee_member_sees_the_applications(member, application):
    response = as_user(member).get(reverse("shoprentals:application-list"))
    assert response.status_code == 200
    assert response.data["results"][0]["applicant_name"] == "Mette Sørensen"


def test_a_superuser_outside_the_group_still_gets_in(db, application):
    """`is_business_committee` grants superusers access, and the permission
    class must agree with the property."""
    root = User.objects.create_superuser(email="root@ab-jaeger.dk", password="hemmeligt123")
    assert as_user(root).get(reverse("shoprentals:application-list")).status_code == 200


# --- the workflow ---------------------------------------------------------


def test_the_committee_can_move_an_application_through_the_statuses(member, application):
    url = reverse("shoprentals:application-detail", args=[application.pk])
    response = as_user(member).patch(url, {"status": ApplicationStatus.IN_PROGRESS}, format="json")
    assert response.status_code == 200
    assert response.data["status"] == "in_progress"
    assert response.data["status_display"] == "I gang"


def test_changing_status_records_when_it_happened(member, application):
    """So that "in progress since March" is answerable — the committee's own
    stated problem is that this stage drags on."""
    url = reverse("shoprentals:application-detail", args=[application.pk])
    as_user(member).patch(url, {"status": ApplicationStatus.SAVED}, format="json")
    application.refresh_from_db()
    assert application.status_changed_at is not None


def test_setting_the_same_status_again_does_not_reset_the_clock(member, application):
    application.set_status(ApplicationStatus.IN_PROGRESS)
    first = Application.objects.get(pk=application.pk).status_changed_at
    url = reverse("shoprentals:application-detail", args=[application.pk])
    as_user(member).patch(url, {"status": ApplicationStatus.IN_PROGRESS}, format="json")
    assert Application.objects.get(pk=application.pk).status_changed_at == first


def test_an_application_can_be_rated_and_unrated(member, application):
    url = reverse("shoprentals:application-detail", args=[application.pk])
    client = as_user(member)
    assert client.patch(url, {"rating": 4}, format="json").data["rating"] == 4
    # Clearing it matters: unrated is not the same as rated 1.
    assert client.patch(url, {"rating": None}, format="json").data["rating"] is None


@pytest.mark.parametrize("rating", [0, 6, 99])
def test_a_rating_outside_one_to_five_is_a_field_error(member, application, rating):
    url = reverse("shoprentals:application-detail", args=[application.pk])
    response = as_user(member).patch(url, {"rating": rating}, format="json")
    assert response.status_code == 400
    assert "rating" in response.data


def test_an_application_can_be_assigned_to_a_committee_member(member, other_member, application):
    url = reverse("shoprentals:application-detail", args=[application.pk])
    response = as_user(member).patch(url, {"assignee_id": other_member.pk}, format="json")
    assert response.status_code == 200
    assert response.data["assignee"]["email"] == "andet@ab-jaeger.dk"


def test_it_cannot_be_assigned_to_someone_outside_the_committee(member, neighbour, application):
    """Assigning a neighbour would hand them a task on a page they cannot open."""
    url = reverse("shoprentals:application-detail", args=[application.pk])
    response = as_user(member).patch(url, {"assignee_id": neighbour.pk}, format="json")
    assert response.status_code == 400
    assert "assignee_id" in response.data


def test_an_assignment_can_be_cleared(member, other_member, application):
    application.assignee = other_member
    application.save()
    url = reverse("shoprentals:application-detail", args=[application.pk])
    response = as_user(member).patch(url, {"assignee_id": None}, format="json")
    assert response.data["assignee"] is None


def test_the_applicants_own_answers_cannot_be_edited_through_the_api(member, application):
    """They are a record of what was submitted. Corrections belong in the
    contract details, and the sync would overwrite them anyway."""
    url = reverse("shoprentals:application-detail", args=[application.pk])
    as_user(member).patch(
        url, {"applicant_name": "Noget andet", "email": "andet@dk"}, format="json"
    )
    application.refresh_from_db()
    assert application.applicant_name == "Mette Sørensen"
    assert application.email == "mette@blomster.dk"


def test_applications_cannot_be_created_or_deleted_through_the_api(member, application):
    """They exist because someone filled in the form. Rubbish gets rejected,
    not deleted, so a reapplying applicant is recognisable."""
    client = as_user(member)
    created = client.post(reverse("shoprentals:application-list"), {}, format="json")
    assert created.status_code == 405
    url = reverse("shoprentals:application-detail", args=[application.pk])
    assert client.delete(url).status_code == 405


# --- filtering and the summary -------------------------------------------


@pytest.fixture
def a_pile_of_applications(db, member, other_member):
    """One application in each status, with a rating or two."""
    made = {}
    for index, status_value in enumerate(ApplicationStatus.values):
        application = Application.objects.create(
            source_key=f"{index:064d}",
            submitted_at=timezone.now() - timezone.timedelta(days=index),
            applicant_name=f"Ansøger {index}",
            email=f"ansoeger{index}@example.dk",
            answers=[{"question": "Formål", "value": "Bodega" if index else "Frisør"}],
            status=status_value,
            rating=index or None,
        )
        made[status_value] = application
    made[ApplicationStatus.NEW].assignee = member
    made[ApplicationStatus.NEW].save()
    return made


def test_the_list_can_be_filtered_to_one_status(member, a_pile_of_applications):
    response = as_user(member).get(
        reverse("shoprentals:application-list"), {"status": ApplicationStatus.SAVED}
    )
    assert [item["status"] for item in response.data["results"]] == ["saved"]


def test_the_status_filter_is_repeatable(member, a_pile_of_applications):
    """ "Show me new and in-progress" is the natural question, not one at a time."""
    response = as_user(member).get(
        reverse("shoprentals:application-list"),
        {"status": [ApplicationStatus.NEW, ApplicationStatus.IN_PROGRESS]},
    )
    assert {item["status"] for item in response.data["results"]} == {"new", "in_progress"}


def test_an_unknown_status_is_ignored_rather_than_emptying_the_list(member, a_pile_of_applications):
    response = as_user(member).get(reverse("shoprentals:application-list"), {"status": "vrøvl"})
    assert len(response.data["results"]) == len(ApplicationStatus.values)


def test_the_list_can_be_filtered_by_assignee(member, a_pile_of_applications):
    response = as_user(member).get(
        reverse("shoprentals:application-list"), {"assignee": str(member.pk)}
    )
    assert len(response.data["results"]) == 1


def test_the_unassigned_pile_can_be_asked_for(member, a_pile_of_applications):
    """The pile nobody has picked up cannot be expressed as an id."""
    response = as_user(member).get(
        reverse("shoprentals:application-list"), {"assignee": "unassigned"}
    )
    assert len(response.data["results"]) == len(ApplicationStatus.values) - 1


def test_the_list_can_be_filtered_by_minimum_rating(member, a_pile_of_applications):
    response = as_user(member).get(reverse("shoprentals:application-list"), {"min_rating": "3"})
    assert all(item["rating"] >= 3 for item in response.data["results"])


def test_search_reaches_into_the_forms_answers(member, a_pile_of_applications):
    """The interesting words are in whatever question happened to ask for them,
    so searching only the mapped columns would miss the point."""
    response = as_user(member).get(reverse("shoprentals:application-list"), {"q": "Frisør"})
    assert len(response.data["results"]) == 1


def test_search_matches_the_applicants_name(member, a_pile_of_applications):
    response = as_user(member).get(reverse("shoprentals:application-list"), {"q": "Ansøger 2"})
    assert len(response.data["results"]) == 1


def test_sorting_by_rating_puts_unrated_applications_last(member, a_pile_of_applications):
    """Unrated is not the same as bad, but it is not a five either."""
    response = as_user(member).get(reverse("shoprentals:application-list"), {"ordering": "-rating"})
    ratings = [item["rating"] for item in response.data["results"]]
    assert ratings[0] == 4
    assert ratings[-1] is None


def test_the_summary_counts_every_status_including_the_empty_ones(member, a_pile_of_applications):
    """A stable row of chips beats one that appears and vanishes."""
    response = as_user(member).get(reverse("shoprentals:application-summary"))
    assert response.data["total"] == len(ApplicationStatus.values)
    assert set(response.data["by_status"]) == set(ApplicationStatus.values)
    assert response.data["by_status"]["new"] == 1


def test_a_rejected_application_is_still_reachable_on_its_own_url(member, a_pile_of_applications):
    """Filters shape the list only. Hiding a rejected one from its own detail
    route would make it impossible to put back in play."""
    rejected = a_pile_of_applications[ApplicationStatus.REJECTED]
    url = reverse("shoprentals:application-detail", args=[rejected.pk])
    assert as_user(member).get(url).status_code == 200


# --- comments -------------------------------------------------------------


def test_a_member_can_comment_on_an_application(member, application):
    url = reverse("shoprentals:application-comments", args=[application.pk])
    response = as_user(member).post(url, {"body": "Ringet 20/8, vender tilbage."}, format="json")
    assert response.status_code == 201
    assert response.data["author"]["email"] == "udvalg@ab-jaeger.dk"


def test_comments_come_back_oldest_first(member, other_member, application):
    for author, body in ((member, "først"), (other_member, "så")):
        ApplicationComment.objects.create(application=application, author=author, body=body)
    url = reverse("shoprentals:application-comments", args=[application.pk])
    response = as_user(member).get(url)
    assert [item["body"] for item in response.data] == ["først", "så"]


def test_an_empty_comment_is_refused(member, application):
    url = reverse("shoprentals:application-comments", args=[application.pk])
    response = as_user(member).post(url, {"body": "   "}, format="json")
    assert response.status_code == 400


def test_the_comment_count_is_on_the_application(member, application):
    ApplicationComment.objects.create(application=application, author=member, body="note")
    url = reverse("shoprentals:application-detail", args=[application.pk])
    assert as_user(member).get(url).data["comment_count"] == 1


def test_a_member_can_delete_their_own_comment(member, application):
    comment = ApplicationComment.objects.create(application=application, author=member, body="note")
    url = reverse("shoprentals:comment-detail", args=[comment.pk])
    assert as_user(member).delete(url).status_code == 204


def test_a_member_cannot_delete_someone_elses_comment(member, other_member, application):
    """A months-long thread that anyone can rewrite is not a record."""
    comment = ApplicationComment.objects.create(
        application=application, author=other_member, body="note"
    )
    url = reverse("shoprentals:comment-detail", args=[comment.pk])
    assert as_user(member).delete(url).status_code == 403


def test_an_admin_can_delete_any_comment(db, committee_group, member, application):
    admin = User.objects.create_user(
        email="formand@ab-jaeger.dk", password="hemmeligt123", is_staff=True
    )
    admin.groups.add(committee_group)
    comment = ApplicationComment.objects.create(application=application, author=member, body="note")
    url = reverse("shoprentals:comment-detail", args=[comment.pk])
    assert as_user(admin).delete(url).status_code == 204


# --- contract details ----------------------------------------------------


def test_the_details_are_created_on_first_read(member, application):
    """The committee fills this in over weeks; "does it exist yet" is not a
    distinction the client should have to handle."""
    url = reverse("shoprentals:application-details", args=[application.pk])
    response = as_user(member).get(url)
    assert response.status_code == 200
    assert ApplicationDetails.objects.filter(application=application).exists()


def test_details_can_be_filled_in_a_field_at_a_time(member, application):
    url = reverse("shoprentals:application-details", args=[application.pk])
    client = as_user(member)
    client.patch(url, {"company_name": "Blomster ApS"}, format="json")
    response = client.patch(url, {"cvr": "12345678"}, format="json")
    assert response.data["company_name"] == "Blomster ApS"
    assert response.data["cvr"] == "12345678"


def test_a_malformed_cvr_is_a_field_error_not_a_crash(member, application):
    url = reverse("shoprentals:application-details", args=[application.pk])
    response = as_user(member).patch(url, {"cvr": "1234"}, format="json")
    assert response.status_code == 400
    assert "cvr" in response.data


def test_the_details_report_what_the_lawyer_is_still_missing(member, application):
    url = reverse("shoprentals:application-details", args=[application.pk])
    client = as_user(member)
    empty = client.get(url).data
    assert empty["is_complete"] is False
    assert "CVR-nummer" in empty["missing_fields"]

    filled = client.patch(url, {"cvr": "12345678"}, format="json").data
    assert "CVR-nummer" not in filled["missing_fields"]


def test_details_are_complete_once_every_contract_field_is_filled(member, application):
    url = reverse("shoprentals:application-details", args=[application.pk])
    response = as_user(member).patch(
        url,
        {
            "company_name": "Blomster ApS",
            "cvr": "12345678",
            "contact_name": "Mette Sørensen",
            "contact_email": "mette@blomster.dk",
            "unit_label": "Jægergade 4, kld.",
            "purpose": "Detailhandel med blomster",
            "area_sqm": "62.50",
            "annual_rent_dkk": "120000.00",
            "lease_start": "2026-11-01",
        },
        format="json",
    )
    assert response.data["missing_fields"] == []
    assert response.data["is_complete"] is True


def test_the_application_reports_whether_details_have_been_started(member, application):
    url = reverse("shoprentals:application-detail", args=[application.pk])
    client = as_user(member)
    assert client.get(url).data["has_details"] is False
    client.get(reverse("shoprentals:application-details", args=[application.pk]))
    assert client.get(url).data["has_details"] is True


# --- assignable members --------------------------------------------------


def test_the_assignable_members_are_the_committee(member, other_member, neighbour):
    response = as_user(member).get(reverse("shoprentals:members"))
    emails = {item["email"] for item in response.data}
    assert emails == {"udvalg@ab-jaeger.dk", "andet@ab-jaeger.dk"}
    assert "nabo@example.dk" not in emails


def test_a_deactivated_member_is_not_offered_as_an_assignee(member, other_member):
    """They cannot log in, so offering them only misleads."""
    other_member.is_active = False
    other_member.save()
    response = as_user(member).get(reverse("shoprentals:members"))
    assert {item["email"] for item in response.data} == {"udvalg@ab-jaeger.dk"}


def test_a_member_without_a_name_is_listed_by_email(member):
    """A dropdown of blanks is useless."""
    member.first_name = ""
    member.save()
    response = as_user(member).get(reverse("shoprentals:members"))
    assert response.data[0]["name"] == "udvalg@ab-jaeger.dk"
