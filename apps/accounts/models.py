import uuid
from typing import ClassVar

from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.utils import timezone

from apps.accounts.managers import UserManager


class UserKind(models.TextChoices):
    COMPANY = "company", "Company"
    RECRUITER = "recruiter", "Recruiter"
    STAFF = "staff", "Staff"


class User(AbstractBaseUser, PermissionsMixin):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField("email address", unique=True)
    full_name = models.CharField(max_length=120)
    kind = models.CharField(max_length=16, choices=UserKind.choices, default=UserKind.COMPANY)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)

    objects: ClassVar[UserManager] = UserManager()

    EMAIL_FIELD = "email"
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: ClassVar[list[str]] = ["full_name"]

    class Meta:
        verbose_name = "user"
        verbose_name_plural = "users"

    def __str__(self) -> str:
        return self.email

    def clean(self) -> None:
        super().clean()
        self.email = UserManager.normalize_email(self.email)

    def get_full_name(self) -> str:
        return self.full_name

    def get_short_name(self) -> str:
        return self.full_name.split(" ")[-1] if self.full_name else self.email
