from django.urls import path

from apps.contact_flow.transport.views import config_views, runtime_views

urlpatterns = [
    path("stages", config_views.stages_list_create),
    path("stages/<int:pk>", config_views.stage_update),
    path("results", config_views.results_list_create),
    path("results/<int:pk>", config_views.result_update),
    path("transitions", config_views.transitions_list_create),
    path("transitions/<int:pk>", config_views.transition_update_delete),
    path("import", config_views.import_workbook),
    path("customers/<int:customer_id>/result", runtime_views.apply_result),
    path("customers/<int:customer_id>/due", runtime_views.edit_due),
    path("customers/<int:customer_id>/stage", runtime_views.set_stage),
]
