from django.db import migrations

SHARED_TEAM = "Team chung"


def backfill(apps, schema_editor):
    Company = apps.get_model("companies", "Company")
    Team = apps.get_model("companies", "Team")
    Membership = apps.get_model("companies", "CompanyMembership")
    Job = apps.get_model("jobs", "Job")
    ContentType = apps.get_model("contenttypes", "ContentType")
    UserObjectPermission = apps.get_model("guardian", "UserObjectPermission")

    shared = {}
    for company in Company.objects.filter(
        memberships__role__in=["hiring_manager", "talent_acquisition"]
    ).distinct():
        shared[company.pk], _ = Team.objects.get_or_create(company=company, name=SHARED_TEAM)

    for membership in Membership.objects.filter(role__in=["hiring_manager", "talent_acquisition"]):
        membership.team = shared[membership.company_id]
        if membership.role == "talent_acquisition":
            membership.can_publish = True
        membership.save(update_fields=["team", "can_publish"])

    for job in Job.objects.filter(team__isnull=True):
        creator = (
            Membership.objects.filter(user_id=job.created_by_id).first()
            if job.created_by_id
            else None
        )
        team_id = creator.team_id if creator and creator.company_id == job.company_id else None
        if team_id is None and job.company_id in shared:
            team_id = shared[job.company_id].pk
        if team_id is not None:
            Job.objects.filter(pk=job.pk).update(team_id=team_id)

    job_type = ContentType.objects.filter(app_label="jobs", model="job").first()
    if job_type is not None:
        UserObjectPermission.objects.filter(content_type=job_type).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("companies", "0004_teams"),
        ("jobs", "0005_job_teams_and_change_requests"),
        ("contenttypes", "0002_remove_content_type_name"),
        ("guardian", "0001_initial"),
    ]

    operations = [migrations.RunPython(backfill, reverse_code=migrations.RunPython.noop)]
