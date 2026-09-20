from decimal import Decimal
from typing import Any

from rest_framework import serializers

from apps.companies.access import company_of
from apps.companies.models import Office
from apps.jobs.functions import guess_function
from apps.jobs.models import BOUNTY_PCT_MAX, BOUNTY_PCT_MIN, BountyType, Job, WorkModel

MAX_SKILLS = 20

REQUIRED_MESSAGES = {
    "title": "Enter a job title",
    "why_we_need_you": "Tell recruiters why this role exists",
    "roles": "List the main responsibilities",
    "qualifications": "Add the must-have qualifications",
}


def clean_lines(value: str) -> str:
    """Store one item per line without bullets (prototype `toList`)."""
    lines = [line.strip().lstrip("•-* ").strip() for line in (value or "").splitlines()]
    return "\n".join(line for line in lines if line)


def _blank_message(field: str) -> dict[str, str]:
    message = REQUIRED_MESSAGES[field]
    return {"blank": message, "required": message, "null": message}


class JobSerializer(serializers.ModelSerializer[Job]):
    location_label = serializers.CharField(read_only=True)
    # Declared without the model's range validators so `validate()` can answer with
    # the wording the studio shows instead of DRF's generic bounds message.
    bounty_pct = serializers.IntegerField(required=False, allow_null=True)
    can_edit = serializers.SerializerMethodField()

    class Meta:
        model = Job
        fields = (
            "id",
            "title",
            "function",
            "why_we_need_you",
            "roles",
            "qualifications",
            "nice_to_have",
            "benefits",
            "salary_min",
            "salary_max",
            "pay_basis",
            "work_model",
            "office",
            "location_label",
            "bounty_type",
            "bounty_pct",
            "bounty_amount",
            "skills",
            "status",
            "note_for_recruiters",
            "share_token",
            "created_at",
            "published_at",
            "closed_at",
            "can_edit",
        )
        read_only_fields = (
            "id",
            "function",
            "status",
            "share_token",
            "created_at",
            "published_at",
            "closed_at",
            "location_label",
            "can_edit",
        )
        extra_kwargs = {  # noqa: RUF012
            field: {"error_messages": _blank_message(field)} for field in REQUIRED_MESSAGES
        }

    def get_can_edit(self, job: Job) -> bool:
        from apps.jobs.access import can_change_job

        request = self.context.get("request")
        return bool(request and can_change_job(request.user, job))

    def validate_office(self, office: Office | None) -> Office | None:
        if office is None:
            return office
        request = self.context["request"]
        company = company_of(request.user)
        if company is None or office.company_id != company.pk:
            raise serializers.ValidationError("Select an office that belongs to your company.")
        return office

    def validate_skills(self, skills: list[str]) -> list[str]:
        seen: dict[str, str] = {}
        for skill in skills:
            cleaned = skill.strip()
            if not cleaned:
                continue
            if len(cleaned) > 40:
                raise serializers.ValidationError("Each skill can be at most 40 characters.")
            seen.setdefault(cleaned.lower(), cleaned)
        if len(seen) > MAX_SKILLS:
            raise serializers.ValidationError(f"Up to {MAX_SKILLS} skills.")
        return list(seen.values())

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        instance = self.instance

        def current(field: str, default: Any = None) -> Any:
            return attrs.get(field, getattr(instance, field, default))

        for field in ("roles", "qualifications", "nice_to_have", "benefits"):
            if field in attrs:
                attrs[field] = clean_lines(attrs[field])
        for field, message in REQUIRED_MESSAGES.items():
            if field in attrs and not str(attrs[field]).strip():
                raise serializers.ValidationError({field: [message]})

        salary_min: Decimal | None = current("salary_min")
        salary_max: Decimal | None = current("salary_max")
        if salary_min is not None and salary_max is not None and salary_max < salary_min:
            raise serializers.ValidationError(
                {"salary_max": ["Max salary must be at least the min"]}
            )

        bounty_type = current("bounty_type", BountyType.PCT)
        if bounty_type == BountyType.FIXED:
            if not current("bounty_amount"):
                raise serializers.ValidationError(
                    {"bounty_amount": ["Enter the fixed bounty amount"]}
                )
            attrs["bounty_pct"] = None
        else:
            pct = current("bounty_pct", 15)
            if pct is None or not BOUNTY_PCT_MIN <= int(pct) <= BOUNTY_PCT_MAX:
                raise serializers.ValidationError(
                    {
                        "bounty_pct": [
                            f"Bounty must be between {BOUNTY_PCT_MIN} and {BOUNTY_PCT_MAX}%"
                        ]
                    }
                )
            attrs["bounty_amount"] = None

        work_model = current("work_model", WorkModel.ONSITE)
        if work_model != WorkModel.REMOTE and current("office") is None:
            raise serializers.ValidationError({"office": ["Select an office for this job"]})
        if work_model == WorkModel.REMOTE:
            attrs["office"] = None

        return attrs

    def create(self, validated_data: dict[str, Any]) -> Job:
        from apps.jobs.access import grant_creator_perms

        request = self.context["request"]
        company = company_of(request.user)
        assert company is not None, "CanAddJob already rejected non-members"
        job = Job.objects.create(
            company=company,
            created_by=request.user,
            function=guess_function(validated_data["title"], validated_data.get("skills", [])),
            **validated_data,
        )
        grant_creator_perms(job, request.user)
        return job

    def update(self, instance: Job, validated_data: dict[str, Any]) -> Job:
        job = super().update(instance, validated_data)
        if "title" in validated_data or "skills" in validated_data:
            job.function = guess_function(job.title, job.skills)
            job.save(update_fields=["function", "updated_at"])
        return job


class PublicJobSerializer(serializers.ModelSerializer[Job]):
    """What anyone with the share link may see: no bounty, no internal notes."""

    company = serializers.SerializerMethodField()
    location_label = serializers.CharField(read_only=True)

    class Meta:
        model = Job
        fields = (
            "id",
            "title",
            "function",
            "why_we_need_you",
            "roles",
            "qualifications",
            "nice_to_have",
            "benefits",
            "salary_min",
            "salary_max",
            "pay_basis",
            "work_model",
            "location_label",
            "skills",
            "published_at",
            "company",
        )

    def get_company(self, job: Job) -> dict[str, Any]:
        request = self.context.get("request")
        logo = job.company.logo
        return {
            "name": job.company.name,
            "about": job.company.about,
            "logo_url": request.build_absolute_uri(logo.url) if (logo and request) else None,
        }
