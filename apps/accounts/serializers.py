from typing import Any

from rest_framework import serializers

from apps.accounts.models import User


class MeCompanySerializer(serializers.Serializer[dict[str, Any]]):
    id = serializers.UUIDField()
    name = serializers.CharField()
    role = serializers.CharField()
    profile_completed = serializers.BooleanField()


class MeRecruiterSerializer(serializers.Serializer[dict[str, Any]]):
    id = serializers.UUIDField()
    nickname = serializers.CharField()


class MeSerializer(serializers.ModelSerializer[User]):
    company = serializers.SerializerMethodField()
    recruiter = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ("id", "email", "full_name", "kind", "company", "recruiter")

    @staticmethod
    def get_company(user: User) -> dict[str, Any] | None:
        """Plan 03 adds the models; until then nobody has a membership."""
        membership = getattr(user, "membership", None)
        if membership is None:
            return None
        company = membership.company
        return dict(
            MeCompanySerializer(
                {
                    "id": company.id,
                    "name": company.name,
                    "role": membership.role,
                    "profile_completed": company.profile_completed,
                }
            ).data
        )

    @staticmethod
    def get_recruiter(user: User) -> dict[str, Any] | None:
        """Plan 03 fills this from `user.recruiter`."""
        profile = getattr(user, "recruiter", None)
        if profile is None:
            return None
        return dict(MeRecruiterSerializer({"id": profile.id, "nickname": profile.nickname}).data)
