from django.urls import path

from apps.inventory.transport.views.attachment_views import (
    attachment_file_view,
    delete_attachment_view,
    upload_attachments_view,
)
from apps.inventory.transport.views.document_views import (
    approve_document_view,
    cancel_document_view,
    document_detail,
    document_export,
    list_create_documents,
    reject_document_view,
    submit_document_view,
)
from apps.inventory.transport.views.import_views import (
    download_lines_template,
    parse_import_lines,
)
from apps.inventory.transport.views.return_views import (
    adjust_return_view,
    cancel_return_view,
    create_return_entry,
)
from apps.inventory.transport.views.awaiting_return_views import (
    awaiting_return_close,
    awaiting_return_list,
)
from apps.inventory.transport.views.flow_note_views import flow_note_update
from apps.inventory.transport.views.flow_history_views import (
    flow_summary,
    flow_manual_match,
    flow_order_lookup,
    flow_pending_delete,
    flow_pending_mark_invalid,
    flow_pending_note_update,
    flow_pending_replacement_issue,
    flow_pending_unmatch,
    issue_orders,
    issue_pending,
    issue_sku_orders,
    issue_skus,
    return_history,
    return_note_update,
    return_pending,
    return_sku_returns,
    return_skus,
)
from apps.inventory.transport.views.manual_dispatch_views import manual_dispatch
from apps.inventory.transport.views.ledger_views import ledger_documents
from apps.inventory.transport.views.pending_scan_views import (
    create_pending_scans_batch,
    delete_pending_scan,
    list_pending_scans,
    retry_pending_scan,
)
from apps.inventory.transport.views.stock_views import (
    list_movements,
    list_stock,
    reclassify_stock,
    stock_availability,
)

urlpatterns = [
    path("ledger-documents", ledger_documents, name="inventory-ledger-documents"),
    path("import/parse-lines", parse_import_lines, name="inventory-import-parse-lines"),
    path("import/template", download_lines_template, name="inventory-import-template"),
    path("stock", list_stock, name="inventory-stock-list"),
    path("stock/availability", stock_availability, name="inventory-stock-availability"),
    path("stock/movements", list_movements, name="inventory-stock-movements"),
    path("stock/reclassify", reclassify_stock, name="inventory-stock-reclassify"),
    path(
        "dispatch-history/awaiting-return",
        awaiting_return_list,
        name="inventory-awaiting-return",
    ),
    path(
        "orders/dispatch",
        manual_dispatch,
        name="inventory-manual-dispatch",
    ),
    path(
        "dispatch-history/awaiting-return/<int:order_id>/close",
        awaiting_return_close,
        name="inventory-awaiting-return-close",
    ),
    path("dispatch-history/summary", flow_summary, name="inventory-flow-summary"),
    path("dispatch-history/orders", issue_orders, name="inventory-issue-orders"),
    path("dispatch-history/pending", issue_pending, name="inventory-issue-pending"),
    path(
        "dispatch-history/skus/orders",
        issue_sku_orders,
        name="inventory-issue-sku-orders",
    ),
    path("dispatch-history/skus", issue_skus, name="inventory-issue-skus"),
    path("return-history/pending", return_pending, name="inventory-return-pending"),
    path(
        "return-history/skus/returns",
        return_sku_returns,
        name="inventory-return-sku-returns",
    ),
    path("return-history/skus", return_skus, name="inventory-return-skus"),
    path(
        "return-history/<int:return_id>/note",
        return_note_update,
        name="inventory-return-history-note",
    ),
    path("return-history", return_history, name="inventory-return-history"),
    path("flow-order-lookup", flow_order_lookup, name="inventory-flow-order-lookup"),
    # Ghi chú của MỌI loại dòng trên trang đi qua một cửa; `target_type` trong
    # body quyết định nguồn lưu (xem FlowNoteService).
    path("flow-notes", flow_note_update, name="inventory-flow-note-update"),
    path(
        "flow-pending/<int:scan_id>/match",
        flow_manual_match,
        name="inventory-flow-manual-match",
    ),
    path(
        "flow-pending/<int:scan_id>/replacement-issue",
        flow_pending_replacement_issue,
        name="inventory-flow-pending-replacement-issue",
    ),
    path(
        "flow-pending/<int:scan_id>/unmatch",
        flow_pending_unmatch,
        name="inventory-flow-pending-unmatch",
    ),
    path(
        "flow-pending/<int:scan_id>/mark-invalid",
        flow_pending_mark_invalid,
        name="inventory-flow-pending-mark-invalid",
    ),
    path(
        "flow-pending/<int:scan_id>/note",
        flow_pending_note_update,
        name="inventory-flow-pending-note",
    ),
    path(
        "flow-pending/<int:scan_id>",
        flow_pending_delete,
        name="inventory-flow-pending-delete",
    ),
    path("documents", list_create_documents, name="inventory-documents"),
    path(
        "documents/attachments/<int:attachment_id>/file",
        attachment_file_view,
        name="inventory-document-attachment-file",
    ),
    path(
        "documents/attachments/<int:attachment_id>",
        delete_attachment_view,
        name="inventory-document-attachment-delete",
    ),
    path(
        "documents/<int:document_id>", document_detail, name="inventory-document-detail"
    ),
    path(
        "documents/<int:document_id>/export",
        document_export,
        name="inventory-document-export",
    ),
    path(
        "documents/<int:document_id>/attachments",
        upload_attachments_view,
        name="inventory-document-attachments",
    ),
    path(
        "documents/<int:document_id>/submit",
        submit_document_view,
        name="inventory-document-submit",
    ),
    path(
        "documents/<int:document_id>/approve",
        approve_document_view,
        name="inventory-document-approve",
    ),
    path(
        "documents/<int:document_id>/reject",
        reject_document_view,
        name="inventory-document-reject",
    ),
    path(
        "documents/<int:document_id>/cancel",
        cancel_document_view,
        name="inventory-document-cancel",
    ),
    path("returns", create_return_entry, name="inventory-returns"),
    path(
        "returns/<int:entry_id>/cancel",
        cancel_return_view,
        name="inventory-return-cancel",
    ),
    path(
        "returns/<int:entry_id>/adjust",
        adjust_return_view,
        name="inventory-return-adjust",
    ),
    path("pending-scans", list_pending_scans, name="inventory-pending-scans"),
    path(
        "pending-scans/batch",
        create_pending_scans_batch,
        name="inventory-pending-scans-batch",
    ),
    path(
        "pending-scans/<int:scan_id>",
        delete_pending_scan,
        name="inventory-pending-scan-delete",
    ),
    path(
        "pending-scans/<int:scan_id>/retry",
        retry_pending_scan,
        name="inventory-pending-scan-retry",
    ),
]
