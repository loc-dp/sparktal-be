from typing import TYPE_CHECKING

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.forms import BaseUserCreationForm, UserChangeForm

from apps.accounts.models import User

if TYPE_CHECKING:
    _UserAdminBase = DjangoUserAdmin[User]
else:
    _UserAdminBase = DjangoUserAdmin


class UserCreationForm(BaseUserCreationForm):  # type: ignore[type-arg]
    class Meta:
        model = User
        fields = ("email", "full_name", "kind")


class UserEditForm(UserChangeForm):  # type: ignore[type-arg]
    class Meta:
        model = User
        fields = ("email", "full_name", "kind")


@admin.register(User)
class UserAdmin(_UserAdminBase):
    form = UserEditForm
    add_form = UserCreationForm
    ordering = ("email",)
    list_display = ("email", "full_name", "kind", "is_active", "is_staff", "date_joined")
    list_filter = ("kind", "is_active", "is_staff", "is_superuser")
    search_fields = ("email", "full_name")
    readonly_fields = ("date_joined", "last_login")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Profile", {"fields": ("full_name", "kind")}),
        (
            "Permissions",
            {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")},
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
