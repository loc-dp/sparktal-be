"""Demo hiring activity: rounds, feedback, notes and approvals in every decision state.

Most of it sits on Senior Backend Engineer; the other jobs get enough to show activity."""

from datetime import timedelta

from django.utils import timezone
from guardian.shortcuts import assign_perm

from apps.accounts.models import User
from apps.candidates.models import Application, Stage
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
MANAGER = "khoa.nguyen@velapay.vn"
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
    ("Vũ Khánh Linh", 1, -24, 11, InterviewFormat.ONLINE),
    ("Nguyễn Thanh Hằng", 1, -5, 16, InterviewFormat.ONLINE),
    ("Lý Hoàng Yến", 1, -3, 10, InterviewFormat.ONLINE),
    ("Phan Nhật Minh", 1, -80, 9, InterviewFormat.IN_PERSON),
    ("Phan Nhật Minh", 2, -76, 14, InterviewFormat.IN_PERSON),
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
    (
        "Vũ Khánh Linh",
        1,
        TA,
        Verdict.STRONG_NO,
        "No payments domain exposure and the salary ask is well above the band.",
    ),
    ("Nguyễn Thanh Hằng", 1, TA, Verdict.YES, "Walked through a cohort model she built in dbt."),
    ("Lý Hoàng Yến", 1, ADMIN, Verdict.YES, "Portfolio shows real design-system ownership."),
    ("Phan Nhật Minh", 1, TA, Verdict.STRONG_YES, "Built a Playwright suite from scratch at VNG."),
    ("Phan Nhật Minh", 2, ADMIN, Verdict.YES, "Good fit for the checkout and webhook suites."),
]

# candidate, author, hours ago, body
NOTES = [
    ("Phạm Minh Quân", TA, 140, "Notice period is 30 days; open to hybrid three days a week."),
    ("Phạm Minh Quân", ADMIN, 20, "Check his expectation against the L4 band before round 2."),
    ("Lê Thu Trang", TA, 30, "Screen call went well — clear on Go concurrency and testing."),
    (
        "Nguyễn Thanh Hằng",
        TA,
        50,
        "Wants to own merchant pricing analytics; asks about dbt tooling.",
    ),
    ("Lý Hoàng Yến", ADMIN, 70, "Ask for the merchant-portal case study before the panel."),
    ("Phan Nhật Minh", TA, 1800, "Offer accepted; start date confirmed with HR."),
]

APPROVAL = (
    "Lê Thu Trang",
    "Screen went well. OK to move her straight to the panel interview?",
)

# Decided requests, so every status shows up in a candidate's history.
# candidate, status, stage asked from, days ago, request note, decision note
DECIDED_APPROVALS = [
    (
        "Đặng Khánh Vy",
        ApprovalStatus.APPROVED,
        Stage.SCREEN,
        20,
        "Strong platform background. OK to schedule round 1?",
        "Approved. Keep the panel to two people.",
    ),
    (
        "Trịnh Quốc Việt",
        ApprovalStatus.DECLINED,
        Stage.SCREEN,
        8,
        "Java-heavy profile. Move him to interview?",
        "Hold for now: we need Go production experience at this level.",
    ),
    (
        "Vũ Thảo Nguyên",
        ApprovalStatus.CANCELLED,
        Stage.SHORTLISTED,
        2,
        "Shortlisted, ask to move to screen?",
        "",
    ),
]


@register_seeder(order=80)
def seed(ctx: SeedContext) -> None:
    job = ctx.refs["jobs"][JOB]
    users = {email: User.objects.get(email=email) for email in (TA, ADMIN, APPROVER)}
    applications = list(Application.objects.filter(job=job).select_related("candidate"))
    every_application = Application.objects.filter(job__company=job.company).select_related(
        "candidate"
    )
    by_name = {app.candidate.full_name: app for app in every_application if not app.is_duplicate}

    # Hiring Managers who can see these jobs: Huy so the approval has someone to wait on, Khoa
    # because the chat quotes his round-1 note and the Data Analyst brief names him.
    assign_perm("jobs.view_job", users[APPROVER], job)
    manager = User.objects.get(email=MANAGER)
    for title in (JOB, "Data Analyst"):
        assign_perm("jobs.view_job", manager, ctx.refs["jobs"][title])

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
        interview = Interview.objects.get(application=by_name[name], round=number)
        feedback, _ = Feedback.objects.update_or_create(
            interview=interview,
            author=users[email],
            defaults={"verdict": verdict, "body": body},
        )
        # Written a couple of hours after the interview ended, not when the seeder ran.
        written = interview.ends_at + timedelta(hours=2)
        Feedback.objects.filter(pk=feedback.pk).update(created_at=written, updated_at=written)

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

    for name, status, stage, days_ago, request_note, decision_note in DECIDED_APPROVALS:
        decided_at = now - timedelta(days=days_ago)
        approval, _ = ApprovalRequest.objects.update_or_create(
            application=by_name[name],
            status=status,
            defaults={
                "requested_by": users[TA],
                "approver": users[APPROVER],
                "stage": stage,
                "note": request_note,
                "decision_note": decision_note,
                "decided_at": decided_at,
            },
        )
        ApprovalRequest.objects.filter(pk=approval.pk).update(
            created_at=decided_at - timedelta(hours=3)
        )

    # Chat lines that name a candidate become part of that candidate's Discussion.
    for application in applications:
        Message.objects.filter(
            channel__job=job,
            channel__recruiter=application.recruiter,
            application__isnull=True,
            body__contains=application.candidate.full_name,
        ).update(application=application)
