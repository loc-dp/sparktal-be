"""Requests validated before a competing account/team change must recheck at write time."""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from typing import Any

import pytest
from django.db import connection, connections, transaction
from rest_framework.exceptions import ValidationError

from apps.companies.access import lock_company
from apps.companies.members import (
    LAST_ADMIN,
    MemberRuleError,
    invite_member,
    remove_member,
    set_locked,
    update_member,
)
from apps.companies.models import CompanyMembership, CompanyRole, Team
from apps.companies.teams import delete_team
from apps.companies.tests.factories import CompanyFactory, CompanyMembershipFactory, TeamFactory
from apps.jobs.models import Job
from apps.jobs.serializers import JobSerializer

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("action", ["demote", "remove"])
def test_last_admin_guard_uses_current_lock_state(action: str) -> None:
    company = CompanyFactory()
    admin = CompanyMembershipFactory(company=company, role=CompanyRole.ADMIN)
    other = CompanyMembershipFactory(company=company, role=CompanyRole.ADMIN)
    set_locked(admin, locked=True)
    stale = CompanyMembership.objects.select_related("user").get(pk=admin.pk)
    set_locked(admin, locked=False)
    set_locked(other, locked=True)
    team = TeamFactory(company=company)

    def write() -> None:
        if action == "demote":
            update_member(stale, role=CompanyRole.HIRING_MANAGER, team=team)
        else:
            remove_member(stale, actor=other.user)

    with pytest.raises(MemberRuleError, match=LAST_ADMIN):
        write()


@pytest.mark.parametrize("action", ["invite", "move", "create_job", "move_job"])
def test_team_deleted_after_validation_is_a_field_error(action: str) -> None:
    company = CompanyFactory()
    admin = CompanyMembershipFactory(company=company, role=CompanyRole.ADMIN)
    team = TeamFactory(company=company)
    member = CompanyMembershipFactory(company=company, role=CompanyRole.HIRING_MANAGER)
    serializer = JobSerializer(
        data={"title": "Engineer", "team": str(team.pk)},
        context={"request": SimpleNamespace(user=admin.user)},
    )
    if action == "move_job":
        from apps.jobs.tests.factories import JobFactory

        serializer = JobSerializer(
            JobFactory(company=company),
            data={"team": str(team.pk)},
            partial=True,
            context={"request": SimpleNamespace(user=admin.user)},
        )
    if action.endswith("job"):
        assert serializer.is_valid(), serializer.errors
    Team.objects.filter(pk=team.pk).delete()
    before = Job.objects.count()

    def write() -> None:
        if action == "invite":
            invite_member(
                company,
                email="race@example.com",
                full_name="Race",
                role=CompanyRole.HIRING_MANAGER,
                team=team,
            )
        elif action == "move":
            update_member(member, team=team)
        else:
            serializer.save()

    with pytest.raises(ValidationError) as exc:
        write()
    assert "team" in exc.value.detail
    assert Job.objects.count() == before


@pytest.mark.parametrize("action", ["demote", "invite", "create_job"])
def test_writer_waits_for_competing_ops_lock_or_team_delete(action: str) -> None:
    company = CompanyFactory()
    admin = CompanyMembershipFactory(company=company, role=CompanyRole.ADMIN)
    other = CompanyMembershipFactory(company=company, role=CompanyRole.ADMIN)
    team = TeamFactory(company=company)
    attempting = Event()

    def write() -> None:
        # A separate database connection, as for a concurrent HTTP request.
        def observe(
            execute: Callable[..., Any], sql: str, params: Any, many: bool, context: dict[str, Any]
        ) -> Any:
            if 'FROM "companies_company"' in sql and "FOR UPDATE" in sql:
                attempting.set()
            return execute(sql, params, many, context)

        try:
            stale = CompanyMembership.objects.select_related("user").get(pk=admin.pk)
            with connection.execute_wrapper(observe):
                if action == "demote":
                    update_member(stale, role=CompanyRole.HIRING_MANAGER, team=team)
                elif action == "create_job":
                    serializer = JobSerializer(
                        data={"title": "Engineer", "team": str(team.pk)},
                        context={"request": SimpleNamespace(user=stale.user)},
                    )
                    assert serializer.is_valid(), serializer.errors
                    serializer.save()
                else:
                    invite_member(
                        company,
                        email="concurrent@example.com",
                        full_name="New",
                        role=CompanyRole.HIRING_MANAGER,
                        team=team,
                    )
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as executor:
        with transaction.atomic():
            lock_company(company.pk)
            future = executor.submit(write)
            assert attempting.wait(5), "Writer never reached the shared lock"
            assert not future.done(), "Writer bypassed the competing company write"
            if action == "demote":
                set_locked(other, locked=True)
            else:
                delete_team(team)
        error = MemberRuleError if action == "demote" else ValidationError
        with pytest.raises(error):
            future.result(timeout=5)
