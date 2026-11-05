"""Box API による Drive パス ⇔ Box URL 変換 (~/.xefm/extensions/box_api.py)

box.py の sync.db 方式で解決できない時 (共有リンク等) のフォールバック。標準ライブラリのみ。

初回だけ認証が必要。XeFM の中ではブラウザ待ちで固まるので、ターミナルで一度実行する:

    python box_api.py auth

認証情報 (Box Developer Console の Custom App / OAuth 2.0) は次のどちらかに置く:
  - 環境変数  BOX_CLIENT_ID / BOX_CLIENT_SECRET
  - ~/.xefm/box_config.json   {"client_id": "...", "client_secret": "...", "redirect_port": 8080}
トークンは ~/.xefm/box_token.json に保存される (中身は秘密情報。共有・コミットしないこと)。
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Literal
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

ItemType = Literal["file", "folder"]

XEFM_DIR = Path.home() / ".xefm"
CONFIG_PATH = XEFM_DIR / "box_config.json"
TOKEN_PATH = XEFM_DIR / "box_token.json"

_API_BASE = "https://api.box.com/2.0"
_TOKEN_URL = "https://api.box.com/oauth2/token"
_AUTH_URL = "https://account.box.com/api/oauth2/authorize"
_FIELDS_ITEM = "id,name,type,path_collection"
_BOXNOTE_EXT = ".boxnote"
_TIMEOUT = 30
_AUTH_TIMEOUT = 120

_HTTP_ERRORS = {
    400: "リクエストの形式が正しくありません。",
    401: "認証に失敗しました。`python box_api.py auth` で再認証してください。",
    403: "このアイテムへのアクセス権がありません。",
    404: "指定されたアイテムが見つかりませんでした。IDやURLを確認してください。",
    429: "APIのリクエスト制限に達しました。しばらく待ってから再試行してください。",
    500: "Box サーバーでエラーが発生しました。時間をおいて再試行してください。",
}

# 通常 URL: https://<domain>.box.com/{file|folder|notes}/<id>  (末尾の ?query や / は許す)
_RE_ITEM = re.compile(r"https://[^/]+\.box\.com/(file|folder|notes)/(\d+)")
_RE_SHARED = re.compile(r"https://[^/]+\.box\.com/s/\S+$")


# ================================================================
# 例外
# ================================================================

class BoxConverterError(Exception):
    """このモジュールの基底例外。"""


class BoxUrlError(BoxConverterError):
    """未対応・不正な URL 形式。"""


class BoxApiError(BoxConverterError):
    """Box API / 認証のエラー。"""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class BoxNotFoundError(BoxConverterError):
    """Box 上にアイテムが見つからない。"""


class BoxPathError(BoxConverterError):
    """Drive ルート配下でないパス。"""


class BoxNotSyncedError(BoxConverterError):
    """Box 上には在るが Box Drive に同期されていない。"""


# ================================================================
# HTTP
# ================================================================

def _request(url: str, *, headers: dict | None = None, params: dict | None = None,
             form: dict | None = None) -> Any:
    """GET (form 無し) / POST (form あり) して JSON を返す。"""
    if params:
        url = f"{url}?{urlencode(params)}"
    hdrs = dict(headers or {})
    body = None
    if form is not None:
        body = urlencode(form).encode("utf-8")
        hdrs["Content-Type"] = "application/x-www-form-urlencoded"
    with urlopen(Request(url, data=body, headers=hdrs), timeout=_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _api_error(e: HTTPError, context: str = "") -> BoxApiError:
    msg = _HTTP_ERRORS.get(e.code, f"Box API からエラーが返されました（HTTP {e.code}）。")
    return BoxApiError(f"{context}\n{msg}" if context else msg, status_code=e.code)


# ================================================================
# 認証
# ================================================================

def load_credentials() -> tuple[str, str, int]:
    """(client_id, client_secret, redirect_port)。環境変数 → box_config.json の順。"""
    cfg: dict = {}
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise BoxApiError(f"{CONFIG_PATH} の JSON が不正です: {e}")
    cid = os.environ.get("BOX_CLIENT_ID") or cfg.get("client_id")
    secret = os.environ.get("BOX_CLIENT_SECRET") or cfg.get("client_secret")
    if not cid or not secret:
        raise BoxApiError(
            "Box API の認証情報がありません。環境変数 BOX_CLIENT_ID / BOX_CLIENT_SECRET か、"
            f"{CONFIG_PATH} (client_id / client_secret) を設定してください。")
    return cid, secret, int(cfg.get("redirect_port", 8080))


class _TokenStore:
    """access / refresh token の保存と更新。"""

    def __init__(self, client_id: str, client_secret: str, path: Path = TOKEN_PATH):
        self._id, self._secret, self._path = client_id, client_secret, path
        self.access = ""
        self._refresh = ""

    def load(self) -> bool:
        if not self._path.exists():
            return False
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self.access, self._refresh = data["access_token"], data["refresh_token"]
            return True
        except (json.JSONDecodeError, KeyError):
            raise BoxApiError(f"トークンファイルが不正です。削除して再認証してください: {self._path}")

    def _store(self, body: dict) -> None:
        self.access, self._refresh = body["access_token"], body["refresh_token"]
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(
            {"access_token": self.access, "refresh_token": self._refresh}, indent=2),
            encoding="utf-8")

    def _post(self, form: dict, context: str) -> None:
        try:
            self._store(_request(_TOKEN_URL, form={
                **form, "client_id": self._id, "client_secret": self._secret}))
        except HTTPError as e:
            raise _api_error(e, context)

    def fetch_from_code(self, code: str, redirect_uri: str) -> None:
        self._post({"grant_type": "authorization_code", "code": code,
                    "redirect_uri": redirect_uri},
                   "認証コードからトークンを取得できませんでした。")

    def refresh(self) -> None:
        self._post({"grant_type": "refresh_token", "refresh_token": self._refresh},
                   "アクセストークンの更新に失敗しました。")


def _receive_auth_code(port: int, state: str) -> str:
    """localhost で認可コードを待つ。タイムアウトあり。state が一致したものだけ受け付ける。"""
    result: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            qs = parse_qs(urlparse(self.path).query)
            if qs.get("state", [None])[0] == state and qs.get("code"):
                result["code"] = qs["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("認証完了。このタブを閉じてください。".encode("utf-8"))

        def log_message(self, *args):
            pass

    deadline = time.monotonic() + _AUTH_TIMEOUT
    with HTTPServer(("localhost", port), Handler) as server:
        while "code" not in result and (left := deadline - time.monotonic()) > 0:
            server.timeout = left
            server.handle_request()          # favicon 等の別リクエストが来ても待ち続ける
    if "code" not in result:
        raise BoxApiError("認証コードを取得できませんでした (タイムアウト)。")
    return result["code"]


def authenticate() -> None:
    """ブラウザ認証してトークンを保存する (ターミナルから実行する)。"""
    cid, secret, port = load_credentials()
    redirect = f"http://localhost:{port}/callback"
    state = os.urandom(8).hex()
    url = f"{_AUTH_URL}?" + urlencode({"response_type": "code", "client_id": cid,
                                       "redirect_uri": redirect, "state": state})
    sys.stdout.write(f"ブラウザで認証してください:\n{url}\n")
    webbrowser.open(url)
    _TokenStore(cid, secret).fetch_from_code(_receive_auth_code(port, state), redirect)
    sys.stdout.write(f"認証完了。トークンを保存しました: {TOKEN_PATH}\n")


# ================================================================
# REST セッション
# ================================================================

class _Session:
    """401 なら1回だけトークンを更新して再試行する GET ラッパー。"""

    def __init__(self, tokens: _TokenStore):
        self._t = tokens

    def get(self, url: str, params: dict | None = None,
            extra_headers: dict | None = None, context: str = "") -> Any:
        for attempt in (1, 2):
            headers = {"Authorization": f"Bearer {self._t.access}", **(extra_headers or {})}
            try:
                return _request(url, headers=headers, params=params)
            except HTTPError as e:
                if e.code == 401 and attempt == 1:
                    self._t.refresh()
                    continue
                raise _api_error(e, context)

    def get_all(self, url: str, params: dict | None = None, context: str = "") -> list[dict]:
        """offset ページネーションで全件取得。"""
        params = {"limit": 1000, **(params or {}), "offset": 0}
        items: list[dict] = []
        while True:
            body = self.get(url, params, context=context)
            entries = body.get("entries", [])
            items.extend(entries)
            params["offset"] += len(entries)
            if not entries or params["offset"] >= body.get("total_count", len(items)):
                return items
            time.sleep(0.05)


# ================================================================
# BoxPathConverter
# ================================================================

class BoxPathConverter:
    """Box Drive パス ⇔ Box Web URL。drive_root は Box Drive の同期ルート (例 ``D:\\Box``)。"""

    def __init__(self, drive_root: str | Path):
        self._root = Path(drive_root)
        cid, secret, _port = load_credentials()
        tokens = _TokenStore(cid, secret)
        if not tokens.load():
            raise BoxApiError("Box API が未認証です。ターミナルで `python box_api.py auth` を実行してください。")
        self._s = _Session(tokens)

    # ---- 公開 API ----

    def url_to_drive_path(self, url: str) -> Path:
        """file / folder / notes URL と共有リンク → Drive パス。"""
        shared, item = self._resolve_url(url)
        path = self._root.joinpath(*(e["name"] for e in item["path_collection"]["entries"][1:]),
                                   item["name"])
        if shared:
            check = path.parent if item["type"] == "file" else path
            if not check.exists():
                raise BoxNotSyncedError(
                    f"共有リンク先が Box Drive に見つかりません。\n  期待されるパス: {path}\n"
                    f"  アイテムID: {item['id']}（{item['type']}）\n"
                    "アクセス権がないか、同期されていない可能性があります。")
        return path

    def drive_path_to_url(self, drive_path: str | Path) -> str:
        """Drive パス → Box Web URL。"""
        p = Path(drive_path)
        try:
            rel = p.relative_to(self._root)
        except ValueError:
            raise BoxPathError(f"Box Drive のルート配下ではありません。\n  指定: {p}\n  ルート: {self._root}")
        kind, item_id = self._resolve_drive_path(rel, p)
        if kind == "folder":
            endpoint = "folder"
        else:
            endpoint = "notes" if p.suffix.lower() == _BOXNOTE_EXT else "file"
        return f"https://app.box.com/{endpoint}/{item_id}"

    # ---- URL → item ----

    def _resolve_url(self, url: str) -> tuple[bool, dict]:
        if m := _RE_ITEM.match(url):
            kind, item_id = m.groups()
            api = "folders" if kind == "folder" else "files"   # notes は API 上 file
            item = self._s.get(f"{_API_BASE}/{api}/{item_id}", {"fields": _FIELDS_ITEM},
                               context=f"{kind} 情報の取得に失敗しました（ID: {item_id}）。")
            return False, item
        if _RE_SHARED.match(url):
            item = self._s.get(
                f"{_API_BASE}/shared_items", {"fields": _FIELDS_ITEM},
                extra_headers={"BoxApi": f"shared_link={url}"},
                context=f"共有リンクの解決に失敗しました（無効・期限切れ・権限なしの可能性）。\nURL: {url}")
            return True, item
        raise BoxUrlError(f"対応していない URL 形式です: {url}\n"
                          "対応: /file/<id>、/folder/<id>、/notes/<id>、/s/<token>")

    # ---- Drive パス → id ----

    def _resolve_drive_path(self, rel: Path, full: Path) -> tuple[ItemType, str]:
        suffix = full.suffix.lower()
        is_file = bool(suffix) and (full.is_file() or suffix == _BOXNOTE_EXT)
        kind: ItemType = "file" if is_file else "folder"
        found = self._search(rel, kind)
        return (kind, found) if found else self._walk(rel, kind)

    def _search(self, rel: Path, kind: ItemType) -> str | None:
        """search API の候補を path_collection で検証する (速いが取りこぼしあり)。"""
        try:
            body = self._s.get(f"{_API_BASE}/search", {
                "query": rel.name, "type": kind, "fields": _FIELDS_ITEM, "limit": 100})
        except BoxApiError:
            return None
        want = str(rel).casefold()
        for item in body.get("entries", []):
            if item["name"] != rel.name:
                continue
            names = [e["name"] for e in item["path_collection"]["entries"][1:]] + [item["name"]]
            if str(Path(*names)).casefold() == want:
                return item["id"]
        return None

    def _walk(self, rel: Path, kind: ItemType) -> tuple[ItemType, str]:
        """ルートから1階層ずつ降りる (確実だが遅い)。"""
        folder_id = "0"
        parts = rel.parts
        for i, part in enumerate(parts):
            want = kind if i == len(parts) - 1 else "folder"
            entries = self._s.get_all(
                f"{_API_BASE}/folders/{folder_id}/items", {"fields": "id,name,type"},
                context=f"フォルダ内アイテムの取得に失敗しました（フォルダID: {folder_id}）。")
            folder_id = next((e["id"] for e in entries
                              if e["name"] == part and e["type"] == want), None)
            if folder_id is None:
                raise BoxNotFoundError(
                    f"Box 上に見つかりません。\n  探索中: {rel}\n  見つからない: {part!r}"
                    f"（階層 {i + 1}/{len(parts)}）\n同期されていないか、削除された可能性があります。")
        return kind, folder_id


_converter: BoxPathConverter | None = None


def get_converter(drive_root: str | Path) -> BoxPathConverter:
    """遅延生成 (config 再読込のたびに認証情報を読みに行かない)。"""
    global _converter
    if _converter is None:
        _converter = BoxPathConverter(drive_root)
    return _converter


if __name__ == "__main__":
    if sys.argv[1:] == ["auth"]:
        authenticate()
    else:
        sys.stdout.write("usage: python box_api.py auth\n")
