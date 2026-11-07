import json

from box_drive import box_api
from box_drive.box_api import _RE_ITEM, _RE_SHARED


def test_box_url_patterns():
    assert _RE_ITEM.match("https://app.box.com/file/123")
    assert _RE_SHARED.match("https://app.box.com/s/token")


def test_api_request_uses_saved_access_token(tmp_path, monkeypatch):
    token_path = tmp_path / "box_token.json"
    token_path.write_text(json.dumps({
        "access_token": "test-access-token",
        "refresh_token": "test-refresh-token",
    }), encoding="utf-8")
    monkeypatch.setattr(box_api, "TOKEN_PATH", token_path)
    monkeypatch.setattr(box_api, "load_credentials", lambda: ("client-id", "client-secret", 8080))
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return (
                b'{"id":"123","name":"report.pdf","type":"file",'
                b'"path_collection":{"entries":[{"id":"0","name":"All Files"}]}}'
            )

    def fake_urlopen(request, timeout):
        captured["authorization"] = request.get_header("Authorization")
        return Response()

    monkeypatch.setattr(box_api, "urlopen", fake_urlopen)

    converter = box_api.BoxPathConverter(tmp_path)
    assert converter.url_to_drive_path("https://app.box.com/file/123") == tmp_path / "report.pdf"
    assert captured["authorization"] == "Bearer test-access-token"
