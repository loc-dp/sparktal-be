from typing import TYPE_CHECKING, Any

from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.forms import BaseUserCreationForm, UserChangeForm
from django.db.models import QuerySet
from django.http import HttpRequest

from apps.accounts.models import User
from apps.companies.members import unlock_users as unlock_users_in

if TYPE_CHECKING:
    _UserAdminBase = DjangoUserAdmin[User]
else:
    _UserAdminBase = DjangoUserAdmin


class UserCreationForm(BaseUserCreationForm):  # type: ignore[type-arg]
    class Meta:
        model = User
        fields = ("email", "full_name", "kind")


LOCKED_STAYS_OFF = (
    "Sparktal has locked this account. Use the “Unlock selected users” action to unlock it."
)


class UserEditForm(UserChangeForm):  # type: ignore[type-arg]
    class Meta:
        model = User
        fields = ("email", "full_name", "kind")

    def clean(self) -> dict[str, Any] | None:
        cleaned = super().clean()
        # The database refuses it too (`accounts_user_locked_is_inactive`); say so in the form.
        if self.instance.locked_at is not None and self.cleaned_data.get("is_active"):
            self.add_error("is_active", LOCKED_STAYS_OFF)
        return cleaned


@admin.register(User)
class UserAdmin(_UserAdminBase):
    form = UserEditForm
    add_form = UserCreationForm
    ordering = ("email",)
    list_display = (
        "email",
        "full_name",
        "kind",
        "is_active",
        "locked_at",
        "is_staff",
        "date_joined",
    )
    list_filter = (
        "kind",
        "is_active",
        ("locked_at", admin.EmptyFieldListFilter),
        "is_staff",
        "is_superuser",
    )
    search_fields = ("email", "full_name")
    readonly_fields = ("date_joined", "last_login", "locked_at")
    actions = ("unlock_users",)
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Profile", {"fields": ("full_name", "kind")}),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "locked_at",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        ("Dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "full_name", "kind", "password1", "password2"),
            },
        ),
    )

    @admin.action(description="Unlock selected users", permissions=["change"])
    def unlock_users(self, request: HttpRequest, queryset: QuerySet[User]) -> None:
        # Sparktal's unlock for someone the ops portal no longer lists: removed while locked.
        restored, left_off = unlock_users_in(queryset)
        self.message_user(
            request,
            f"Unlocked {restored + left_off}: {restored} can sign in again, {left_off} have no"
            " company seat and stay switched off until a company invites them again.",
            messages.SUCCESS,
        )
