from django.contrib import admin

from apps.sizing.models import MergeRequestSizeResult, SizeLabelPolicy


@admin.register(SizeLabelPolicy)
class SizeLabelPolicyAdmin(admin.ModelAdmin):
    list_display = ("enabled", "xs_max", "s_max", "m_max", "l_max", "revision", "updated_at")


@admin.register(MergeRequestSizeResult)
class MergeRequestSizeResultAdmin(admin.ModelAdmin):
    list_display = ("merge_request", "label", "changed_lines", "status", "labeled_at")
    list_filter = ("status", "label")
