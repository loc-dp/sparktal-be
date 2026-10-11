"""Demo hiring activity on Senior Backend Engineer: rounds, feedback, notes and one approval."""

from datetime import timedelta

from django.utils import timezone
from guardian.shortcuts import assign_perm

from apps.accounts.models import User
from apps.candidates.models import Application
from apps.chat.models import Message
from apps.core.seed import SeedContext, register_seeder
from apps.hiring.models import (
    ApprovalRequest,
    ApprovalStatus,
    CandidateNote,
    Feedback,
    Interview,
    InterviewFormat,
    Verdict,
)

JOB = "Senior Backend Engineer"
TA = "maianh.tran@velapay.vn"
ADMIN = "nhung.le@velapay.vn"
APPROVER = "huy.phan@velapay.vn"
MEET = "https://meet.google.com/vel-apay-int"
OFFICE = "Vela Pay · 12 Tôn Đản, District 4, Ho Chi Minh City"

# candidate, round, days from today, start hour (local), format
ROUNDS = [
    ("Phạm Minh Quân", 1, -6, 9, InterviewFormat.ONLINE),
    ("Phạm Minh Quân", 2, 3, 9, InterviewFormat.ONLINE),
    ("Nguyễn Hoàng Anh", 1, -14, 10, InterviewFormat.ONLINE),
    ("Nguyễn Hoàng Anh", 2, -9, 15, InterviewFormat.IN_PERSON),
    ("Đặng Khánh Vy", 1, -2, 14, InterviewFormat.IN_PERSON),
    ("Tô Minh Châu", 1, 1, 10, InterviewFormat.ONLINE),
]

# candidate, round, author, verdict, body
FEEDBACK = [
    (
        "Phạm Minh Quân",
        1,
        TA,
        Verdict.YES,
        "Solid on Kafka and Postgres; explained idempotency with real numbers.",
    ),
    (
        "Phạm Minh Quân",
        1,
        ADMIN,
        Verdict.STRONG_YES,
        "Best system-design answer this quarter. Probe ledger design in round 2.",
    ),
    ("Nguyễn Hoàng Anh", 1, TA, Verdict.YES, "Calm under pressure, good on Go concurrency."),
    (
        "Nguyễn Hoàng Anh",
        2,
        ADMIN,
        Verdict.STRONG_YES,
        "Led a payments migration at MoMo end to end. Make the offer.",
    ),
    (
        "Đặng Khánh Vy",
        1,
        TA,
        Verdict.NO,
        "Strong platform skills, but little product backend work for this level.",
    ),
]

# candidate, author, hours ago, body
NOTES = [
    ("Phạm Minh Quân", TA, 140, "Notice period is 30 days; open to hybrid three days a week."),
    ("Phạm Minh Quân", ADMIN, 20, "Check his expectation against the L4 band before round 2."),
    ("Lê Thu Trang", TA, 30, "Screen call went well — clear on Go concurrency and testing."),
]

APPROVAL = (
    "Lê Thu Trang",
    "Screen went well. OK to move her straight to the panel interview?",
)


@register_seeder(order=80)
def seed(ctx: SeedContext) -> None:
    job = ctx.refs["jobs"][JOB]
    users = {email: User.objects.get(email=email) for email in (TA, ADMIN, APPROVER)}
    applications = list(Application.objects.filter(job=job).select_related("candidate"))
    by_name = {app.candidate.full_name: app for app in applications if not app.is_duplicate}

    # A Hiring Manager who can see this job, so the approval has someone to wait on.
    assign_perm("jobs.view_job", users[APPROVER], job)

    today = timezone.localtime().replace(minute=0, second=0, microsecond=0)
    for name, number, days, hour, meeting in ROUNDS:
        starts = today.replace(hour=hour) + timedelta(days=days)
        Interview.objects.update_or_create(
            application=by_name[name],
            round=number,
            defaults={
                "starts_at": starts,
                "ends_at": starts + timedelta(hours=1),
                "format": meeting,
                "location": MEET if meeting == InterviewFormat.ONLINE else OFFICE,
                "created_by": users[TA],
            },
        )

    for name, number, email, verdict, body in FEEDBACK:
        Feedback.objects.update_or_create(
            interview=Interview.objects.get(application=by_name[name], round=number),
            author=users[email],
            defaults={"verdict": verdict, "body": body},
        )

    now = timezone.now()
    for name, email, hours_ago, body in NOTES:
        note, created = CandidateNote.objects.get_or_create(
            application=by_name[name], author=users[email], body=body
        )
        if created:
            CandidateNote.objects.filter(pk=note.pk).update(
                created_at=now - timedelta(hours=hours_ago)
            )

    name, approval_note = APPROVAL
    ApprovalRequest.objects.update_or_create(
        application=by_name[name],
        status=ApprovalStatus.PENDING,
        defaults={
            "requested_by": users[TA],
            "approver": users[APPROVER],
            "stage": by_name[name].stage,
            "note": approval_note,
        },
    )

    # Chat lines that name a candidate become part of that candidate's Discussion.
    for application in applications:
        Message.objects.filter(
            channel__job=job,
            channel__recruiter=application.recruiter,
            application__isnull=True,
            body__contains=application.candidate.full_name,
        ).update(application=application)
