from django.contrib import admin

from apps.companies.models import Company, CompanyMembership, Office, Team


class OfficeInline(admin.TabularInline):  # type: ignore[type-arg]
    model = Office
    extra = 0


class MembershipInline(admin.TabularInline):  # type: ignore[type-arg]
    model = CompanyMembership
    extra = 0
    autocomplete_fields = ("user", "team")


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("name", "industry", "company_type", "located", "profile_completed_at")
    list_filter = ("industry", "company_type", "located")
    search_fields = ("name",)
    inlines = (OfficeInline, MembershipInline)


@admin.register(Office)
class OfficeAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("address", "company", "city", "place", "position")
    list_filter = ("city",)
    search_fields = ("address", "company__name")
    autocomplete_fields = ("company",)


@admin.register(CompanyMembership)
class CompanyMembershipAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("user", "company", "role", "team", "can_publish", "created_at")
    list_filter = ("role", "can_publish")
    search_fields = ("user__email", "user__full_name", "company__name")
    autocomplete_fields = ("user", "company", "team")


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("name", "company")
    search_fields = ("name",)
    autocomplete_fields = ("company",)
