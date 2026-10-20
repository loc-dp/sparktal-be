from typing import Any

from rest_framework.permissions import BasePermission
from rest_framework.request import Request

from apps.accounts.models import UserKind


class IsSparktalStaff(BasePermission):
    """Every `/api/v1/ops/*` endpoint: an active staff account, not only the Django flag.

    `permission_classes` replaces the default `IsAuthenticated`, so this runs for an anonymous
    request too and the `is_authenticated` check below is what refuses it. DRF turns the
    resulting `NotAuthenticated` into a 403, not a 401: the API uses session auth, which sends no
    `WWW-Authenticate` challenge.
    """

    message = "Sparktal staff only."

    def has_permission(self, request: Request, view: Any) -> bool:
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and user.is_active
            and user.is_staff
            and getattr(user, "kind", None) == UserKind.STAFF
        )
