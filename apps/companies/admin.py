from django.contrib import admin

from apps.companies.models import Company, CompanyMembership, Office

# django-stubs types these as generic, but neither is subscriptable at runtime,
# so the classes stay bare and the type-arg check is silenced per class.


class OfficeInline(admin.TabularInline):  # type: ignore[type-arg]
    model = Office
    extra = 0


class MembershipInline(admin.TabularInline):  # type: ignore[type-arg]
    model = CompanyMembership
    extra = 0
    autocomplete_fields = ("user",)


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("name", "industry", "company_type", "located", "profile_completed_at")
    list_filter = ("industry", "company_type", "located")
    search_fields = ("name",)
    inlines = (OfficeInline, MembershipInline)


@admin.register(CompanyMembership)
class CompanyMembershipAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("user", "company", "role", "created_at")
    list_filter = ("role",)
    search_fields = ("user__email", "user__full_name", "company__name")
    autocomplete_fields = ("user", "company")
