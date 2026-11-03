from unittest.mock import MagicMock, patch

from apps.exports.infrastructure import export_storage


def test_upload_file_creates_bucket_when_missing(tmp_path):
    path = tmp_path / "a.xlsx"
    path.write_bytes(b"xx")
    client = MagicMock()
    client.head_bucket.side_effect = Exception("missing")

    with patch.object(export_storage, "_client", return_value=client):
        with patch.object(export_storage, "_ClientError", Exception):
            export_storage.upload_file("exports/1/a.xlsx", str(path), "application/xlsx")

    client.create_bucket.assert_called_once()
    client.upload_file.assert_called_once()


def test_delete_object_calls_client():
    client = MagicMock()
    with patch.object(export_storage, "_client", return_value=client):
        export_storage.delete_object("exports/1/a.xlsx")
    client.delete_object.assert_called_once()
