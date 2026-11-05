"""Box Drive 連携 (~/.xefm/extensions/box.py)

  box-url-from-cursor   カーソル位置のアイテムを Box URL に変換 → クリップボード + ブラウザで開く
  box-url-from-panel    現在のフォルダを同様に変換
  box-open-clipboard    クリップボードのパス / Box URL (通常・共有) をローカルで開く

解決順序:
  1. sync.db (Box Drive のローカル DB) を直接参照 … 速い。通常 URL とローカルパスのみ。
  2. 1 で解決できない時・共有リンクの時は box_api.py (Box API) にフォールバック。
     API を使うには初回認証が要る → box_api.py の docstring 参照。
"""
from __future__ import annotations

import os
import re
import shutil
import sqlite3
import tempfile
import webbrowser
import winreg
from contextlib import closing, contextmanager
from functools import lru_cache
from pathlib import Path

import win32clipboard

from .box_api import BoxConverterError, BoxNotSyncedError, get_converter

BOX_URL = "https://app.box.com/"
BOXNOTE_EXT = ".boxnote"
BOXDB = Path.home() / "AppData" / "Local" / "Box" / "Box" / "data" / "sync.db"
TMPDB = Path(tempfile.gettempdir()) / "xefm_box_sync.db"

_RE_ITEM_ID = re.compile(r"/(?:folder|file|notes)/(\d+)")
_RE_SHARED = re.compile(r"\.box\.com/s/")


# ---- Box Drive のルート ------------------------------------------------------------

@lru_cache(maxsize=1)
def box_root() -> str:
    """Box Drive の同期ルート。レジストリの CustomBoxLocation 配下の Box、無ければ ~\\Box。"""
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Box\Box") as key:
            return str(Path(winreg.QueryValueEx(key, "CustomBoxLocation")[0]) / "Box")
    except FileNotFoundError:
        return str(Path.home() / "Box")


# ---- sync.db 方式 ------------------------------------------------------------------

@contextmanager
def _sync_db():
    """Box Drive が使用中の sync.db は、コピーしてから開く (WAL があれば一緒に)。"""
    for suffix in ("", "-wal", "-shm"):
        Path(f"{TMPDB}{suffix}").unlink(missing_ok=True)
    shutil.copy(BOXDB, TMPDB)
    wal = Path(f"{BOXDB}-wal")
    if wal.exists():
        shutil.copy(wal, f"{TMPDB}-wal")
    with closing(sqlite3.connect(TMPDB)) as conn:
        yield conn


def _path_from_id(conn, box_id: str) -> str | None:
    """親をたどってローカルパスを組み立てる。"""
    parts: list[str] = []
    current = box_id
    for _ in range(64):                               # 循環防止
        row = conn.execute("SELECT name, parent_item_id FROM box_item WHERE box_id=?",
                           (current,)).fetchone()
        if row is None:
            return None
        name, parent = row
        parts.append(name)
        if not parent or str(parent) == "0":
            return os.path.join(box_root(), *reversed(parts))
        current = str(parent)
    return None


def _id_from_path(conn, rel_parts: list[str]) -> str | None:
    """名前の並びから box_id を探す。葉から親へ JOIN する。Windows に合わせて大小無視。"""
    names = rel_parts[::-1]
    sql = "SELECT t0.box_id FROM box_item AS t0"
    params: list[str] = []
    for i, name in enumerate(names[1:], 1):
        sql += (f" JOIN box_item AS t{i} ON t{i - 1}.parent_item_id = t{i}.box_id"
                f" AND t{i}.name = ? COLLATE NOCASE")
        params.append(name)
    sql += " WHERE t0.name = ? COLLATE NOCASE"
    params.append(names[0])
    row = conn.execute(sql, params).fetchone()
    return str(row[0]) if row else None


def _rel_parts(fpath: str) -> list[str] | None:
    """Box ルートからの相対パス要素。ルート配下でなければ None。"""
    root, p = os.path.normpath(box_root()), os.path.normpath(fpath)
    if not os.path.normcase(p).startswith(os.path.normcase(root) + os.sep):
        return None
    return [s for s in p[len(root):].split(os.sep) if s]


def url_to_local_path(url: str) -> str | None:
    """通常形式の Box URL → ローカルパス。解決できない・実在しない場合は None。"""
    m = _RE_ITEM_ID.search(url)
    if not m:
        return None
    try:
        with _sync_db() as conn:
            path = _path_from_id(conn, m.group(1))
    except (OSError, sqlite3.Error):
        return None
    return path if path and os.path.exists(path) else None


def local_path_to_url(fpath: str) -> str | None:
    """ローカルパス → Box URL。Box Drive 配下でない・DB に無い場合は None。"""
    parts = _rel_parts(fpath)
    if not parts:
        return None
    try:
        with _sync_db() as conn:
            box_id = _id_from_path(conn, parts)
    except (OSError, sqlite3.Error):
        return None
    if box_id is None:
        return None
    if os.path.isdir(fpath):
        kind = "folder"
    elif fpath.lower().endswith(BOXNOTE_EXT):
        kind = "notes"
    else:
        kind = "file"
    return f"{BOX_URL}{kind}/{box_id}"


# ---- クリップボード ----------------------------------------------------------------

def _get_clipboard() -> str | None:
    win32clipboard.OpenClipboard()
    try:
        if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
            return win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
        return None
    finally:
        win32clipboard.CloseClipboard()


def _set_clipboard(text: str) -> None:
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, text)
    finally:
        win32clipboard.CloseClipboard()


# ---- アクション --------------------------------------------------------------------

def _jump(ctx, path: str) -> None:
    if os.path.isdir(path):
        ctx.pane.cd(path)
    else:
        ctx.pane.cd(os.path.dirname(path), focus_name=os.path.basename(path))


def _open_url_for_path(ctx, fpath: str) -> None:
    url = local_path_to_url(fpath)
    if url is None:                                    # sync.db で解決できなければ API
        try:
            url = get_converter(box_root()).drive_path_to_url(fpath)
        except BoxConverterError as e:
            ctx.message(f"Box URLへの変換に失敗しました: {e}")
            return
        except Exception as e:
            ctx.message(f"Box API の利用に失敗しました: {e}")
            return
    ctx.message(url)
    _set_clipboard(url)
    webbrowser.open(url)


def box_url_from_cursor(ctx):
    """カーソル位置のアイテムを Box URL に変換し、コピーしてブラウザで開く"""
    entry = ctx.pane.focused
    if entry is None:
        ctx.message("カーソル位置に対象がありません")
        return
    _open_url_for_path(ctx, str(entry.path))


def box_url_from_panel(ctx):
    """現在のフォルダを Box URL に変換し、コピーしてブラウザで開く"""
    _open_url_for_path(ctx, str(ctx.pane.path))


def box_open_clipboard(ctx):
    """クリップボードのパス / Box URL に対応するローカルの場所へペインを移動する"""
    text = (_get_clipboard() or "").strip().strip('"')   # エクスプローラの「パスのコピー」は "" 付き
    if not text:
        ctx.message("クリップボードが空です")
        return
    if os.path.exists(text):
        _jump(ctx, text)
        return
    if ".box.com/" not in text:
        ctx.message(f"クリップボードの内容は有効なパスでも Box URL でもありません: {text}")
        return

    try:
        path = None if _RE_SHARED.search(text) else url_to_local_path(text)
        if path is None:                               # 共有リンク、または DB で解決できない
            path = str(get_converter(box_root()).url_to_drive_path(text))
            if not os.path.exists(path):
                raise BoxNotSyncedError(f"API では解決できましたが、ローカルにありません (未同期?): {path}")
    except BoxConverterError as e:
        ctx.message(str(e))
        return
    except Exception as e:
        ctx.message(f"Box API の利用に失敗しました: {e}")
        return
    _jump(ctx, path)


ACTIONS = {
    "box-url-from-cursor": {"func": box_url_from_cursor, "description": "Box Drive item under cursor -> Box URL (copy + open)"},
    "box-url-from-panel":  {"func": box_url_from_panel, "description": "Current Box Drive folder -> Box URL (copy + open)"},
    "box-open-clipboard":  {"func": box_open_clipboard, "description": "Go to the Box Drive location for the clipboard path/URL"},
}
# 英数字単体は使わず Alt 修飾で割り当てる (afx / orthodox 共通)
KEY_BINDINGS = {
    "box-url-from-cursor": ["Alt-U"],
    "box-url-from-panel":  ["Shift-Alt-U"],
    "box-open-clipboard":  ["Alt-Shift-J"],
}

