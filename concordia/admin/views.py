import re

from bittersweet.models import validated_get_or_create
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import permission_required
from django.core.exceptions import ValidationError
from django.shortcuts import render
from django.utils.text import slugify
from django.views.decorators.cache import never_cache

from importer.tasks import import_items_into_project_from_url
from importer.utils.excel import slurp_excel

from ..forms import AdminProjectBulkImportForm
from ..models import Campaign, Project


@never_cache
@staff_member_required
@permission_required("concordia.add_campaign")
@permission_required("concordia.change_campaign")
@permission_required("concordia.add_project")
@permission_required("concordia.change_project")
@permission_required("concordia.add_item")
@permission_required("concordia.change_item")
def admin_bulk_import_view(request):
    # TODO: when we upgrade to Django 2.1 we can use the admin site override
    # mechanism (the old one is broken in 2.0): see
    # https://code.djangoproject.com/ticket/27887 in the meantime, this will
    # simply be a regular Django view using forms and just enough context to
    # reuse the Django admin template

    request.current_app = "admin"

    context = {"title": "Bulk Import"}

    if request.method == "POST":
        form = AdminProjectBulkImportForm(request.POST, request.FILES)

        if form.is_valid():
            context["import_jobs"] = import_jobs = []

            rows = slurp_excel(request.FILES["spreadsheet_file"])
            required_fields = [
                "Campaign",
                "Campaign Short Description",
                "Campaign Long Description",
                "Project",
                "Project Description",
                "Import URLs",
            ]
            for idx, row in enumerate(rows):
                missing_fields = [i for i in required_fields if i not in row]
                if missing_fields:
                    messages.warning(
                        request, f"Skipping row {idx}: missing fields {missing_fields}"
                    )
                    continue

                campaign_title = row["Campaign"]
                project_title = row["Project"]
                import_url_blob = row["Import URLs"]

                if not all((campaign_title, project_title, import_url_blob)):
                    if not any(row.values()):
                        # No messages for completely blank rows
                        continue

                    warning_message = (
                        f"Skipping row {idx}: at least one required field "
                        "(Campaign, Project, Import URLs) is empty"
                    )
                    messages.warning(request, warning_message)
                    continue

                try:
                    campaign, created = validated_get_or_create(
                        Campaign,
                        title=campaign_title,
                        defaults={
                            "slug": slugify(campaign_title, allow_unicode=True),
                            "description": row["Campaign Long Description"] or "",
                            "short_description": row["Campaign Short Description"]
                            or "",
                        },
                    )
                except ValidationError as exc:
                    messages.error(
                        request, f"Unable to create campaign {campaign_title}: {exc}"
                    )
                    continue

                if created:
                    messages.info(request, f"Created new campaign {campaign_title}")
                else:
                    messages.info(
                        request,
                        f"Reusing campaign {campaign_title} without modification",
                    )

                try:
                    project, created = validated_get_or_create(
                        Project,
                        title=project_title,
                        campaign=campaign,
                        defaults={
                            "slug": slugify(project_title, allow_unicode=True),
                            "description": row["Project Description"] or "",
                            "campaign": campaign,
                        },
                    )
                except ValidationError as exc:
                    messages.error(
                        request,
                        request,
                        f"Unable to create project {project_title}: {exc}",
                    )
                    continue

                if created:
                    messages.info(request, f"Created new project {project_title}")
                else:
                    messages.info(
                        request, f"Reusing project {project_title} without modification"
                    )

                potential_urls = filter(None, re.split(r"[\s]+", import_url_blob))
                for url in potential_urls:
                    if not url.startswith("http"):
                        messages.warning(
                            request, f"Skipping unrecognized URL value: {url}"
                        )
                        continue

                    try:
                        import_jobs.append(
                            import_items_into_project_from_url(
                                request.user, project, url
                            )
                        )

                        messages.info(
                            request,
                            f"Queued {campaign_title} {project_title} import for {url}",
                        )
                    except Exception as exc:
                        messages.error(
                            request,
                            f"Unhandled error attempting to import {url}: {exc}",
                        )
    else:
        form = AdminProjectBulkImportForm()

    context["form"] = form

    return render(request, "admin/bulk_import.html", context)
