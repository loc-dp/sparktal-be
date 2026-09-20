import uuid

from django.db import models
from django.utils import timezone
from simple_history.models import HistoricalRecords

from apps.accounts.models import User


class Industry(models.TextChoices):
    """The ten sectors from the UI (prototypes/assets/data.js `industries`)."""

    BANKING = "banking_finance", "Banking & Finance"
    FINTECH = "fintech", "Fintech"
    TECHNOLOGY = "technology_software", "Technology & Software"
    ECOMMERCE = "ecommerce_retail", "E-commerce & Retail"
    MANUFACTURING = "manufacturing", "Manufacturing"
    LOGISTICS = "logistics_supply_chain", "Logistics & Supply Chain"
    HEALTHCARE = "healthcare_pharma", "Healthcare & Pharma"
    REAL_ESTATE = "real_estate_construction", "Real Estate & Construction"
    FMCG = "fmcg", "FMCG"
    ENERGY = "energy_utilities", "Energy & Utilities"


class CompanyType(models.TextChoices):
    FDI = "fdi", "FDI"
    VIETNAMESE = "vn", "Vietnamese company"


class SizeRange(models.TextChoices):
    LT_50 = "lt50", "Under 50 employees"
    R50_100 = "r50_100", "50–100 employees"
    R100_500 = "r100_500", "100–500 employees"
    R500_1000 = "r500_1000", "500–1,000 employees"
    GT_1000 = "gt1000", "1,000+ employees"


class Location(models.TextChoices):
    HCM = "hcm", "Ho Chi Minh City"
    HANOI = "hn", "Ha Noi"
    DA_NANG = "dn", "Da Nang"
    HAI_PHONG = "hp", "Hai Phong"
    CAN_THO = "ct", "Can Tho"
    BINH_DUONG = "bd", "Binh Duong"
    DONG_NAI = "dnai", "Dong Nai"
    BAC_NINH = "bn", "Bac Ninh"
    OTHER = "other", "Other province"


class CompanyRole(models.TextChoices):
    ADMIN = "admin", "Company Admin"
    HIRING_MANAGER = "hiring_manager", "Hiring Manager"
    TALENT_ACQUISITION = "talent_acquisition", "Talent Acquisition"


class Company(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)
    industry = models.CharField(max_length=32, choices=Industry.choices, blank=True)
    company_type = models.CharField(max_length=8, choices=CompanyType.choices, blank=True)
    year_incorporated = models.PositiveSmallIntegerField(null=True, blank=True)
    size_range = models.CharField(max_length=16, choices=SizeRange.choices, blank=True)
    bd_headcount = models.PositiveIntegerField(null=True, blank=True)
    located = models.CharField(max_length=8, choices=Location.choices, blank=True)
    about = models.TextField(blank=True)
    website = models.URLField(blank=True)
    linkedin = models.URLField(blank=True)
    facebook = models.URLField(blank=True)
    logo = models.ImageField(upload_to="companies/logos/", null=True, blank=True)
    banner = models.ImageField(upload_to="companies/banners/", null=True, blank=True)
    profile_completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    history = HistoricalRecords(excluded_fields=["updated_at"])

    class Meta:
        verbose_name_plural = "companies"
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name

    @property
    def profile_completed(self) -> bool:
        return self.profile_completed_at is not None

    def mark_profile_completed(self) -> None:
        if self.profile_completed_at is None:
            self.profile_completed_at = timezone.now()
            self.save(update_fields=["profile_completed_at", "updated_at"])


class Office(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="offices")
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=8, choices=Location.choices, default=Location.HCM)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("position",)

    def __str__(self) -> str:
        return f"{self.company.name} — {self.address}"

    @property
    def is_head_office(self) -> bool:
        return self.position == 0


class CompanyMembership(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="membership")
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=24, choices=CompanyRole.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("user__full_name",)

    def __str__(self) -> str:
        return f"{self.user.email} @ {self.company.name} ({self.role})"
