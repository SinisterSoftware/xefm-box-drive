from box_drive.box_api import _RE_ITEM, _RE_SHARED

def test_box_url_patterns():
    assert _RE_ITEM.match("https://app.box.com/file/123")
    assert _RE_SHARED.match("https://app.box.com/s/token")
