from typing import TYPE_CHECKING, Any

from django.contrib.auth.base_user import BaseUserManager

if TYPE_CHECKING:
    from apps.accounts.models import User


class UserManager(BaseUserManager["User"]):
    use_in_migrations = True

    @classmethod
    def normalize_email(cls, email: str | None) -> str:
        return (email or "").strip().lower()

    def _create_user(self, email: str, password: str | None, **extra: Any) -> "User":
        if not email:
            raise ValueError("Users must have an email address.")
        user = self.model(email=self.normalize_email(email), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email: str, password: str | None = None, **extra: Any) -> "User":
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra)

    def create_superuser(self, email: str, password: str | None = None, **extra: Any) -> "User":
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("kind", "staff")
        if extra["is_staff"] is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra["is_superuser"] is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self._create_user(email, password, **extra)
