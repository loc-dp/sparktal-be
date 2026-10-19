"""What a browser posts to the Django Admin change form of a job."""

from typing import Any

from django.test import Client

from apps.jobs.models import Job


def job_change_form(client: Client, job: Job, **overrides: Any) -> tuple[str, dict[str, Any]]:
    """The change URL and what the browser would post for `job`, with `overrides` on top.

    That is every field as the form shows it, and the management data of each inline (the
    invites), which Django refuses the post without.
    """
    url = f"/admin/jobs/job/{job.pk}/change/"
    context = client.get(url).context
    form = context["adminform"].form
    data: dict[str, Any] = {}
    for name in form.fields:
        value = form[name].value()
        if value is None or value is False:
            continue
        data[name] = ",".join(map(str, value)) if isinstance(value, list | tuple) else value
    for inline in context["inline_admin_formsets"]:
        management = inline.formset.management_form
        for name in management.fields:
            data[management.add_prefix(name)] = management[name].value()
    data.update(overrides)
    return url, {key: "" if value is None else value for key, value in data.items()}
