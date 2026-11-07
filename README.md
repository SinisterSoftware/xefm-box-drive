# Box Drive

XeFM extension for Windows that converts Box Drive paths and URLs and opens clipboard locations.

## Install

```powershell
xefm ext install github:SinisterSoftware/xefm-box-drive
```

Accept the `pywin32` dependency when prompted. If it was not synced then, run
**Tools > Advanced > Sync Python Packages** in XeFM (or `xefm deps sync`) and
reload the configuration.

The extension reads Box Drive's local database at
`%LOCALAPPDATA%\Box\Box\data\sync.db`. When Box Drive is installed, signed in,
and has created that database, URL/path conversions that the database can
resolve work without Box API credentials.

## Box API fallback (optional)

The API is used only when the local database cannot resolve a conversion, and
for Box shared links (`https://<domain>.box.com/s/...`). For example, shared
links always use the API. API access requires internet connectivity and an
OAuth 2.0 **User** app in the Box Developer Console; the authenticated Box user
must also have permission to the item. Give the app only the **Read all files
and folders stored in Box** scope. Normal conversions resolved by `sync.db` do
not require this app or API authentication.

In the app's configuration, register `http://localhost:8080/callback` as a
redirect URI. Keep the app's client ID and client secret locally, either in
`%USERPROFILE%\.xefm\box_config.json`:

```json
{
  "client_id": "your-client-id",
  "client_secret": "your-client-secret",
  "redirect_port": 8080
}
```

or in the `BOX_CLIENT_ID` and `BOX_CLIENT_SECRET` environment variables. The
optional `redirect_port` defaults to `8080`; if you change it, use the matching
`http://localhost:<port>/callback` URI in the Box app configuration. Do not
commit or share the config or token file. OAuth access and refresh tokens are
stored in `%USERPROFILE%\.xefm\box_token.json`; do not paste them into issues,
logs, or public repositories.

Authenticate once from PowerShell after installation, using a locally
installed Python 3.10 or later (the auth helper uses only Python's standard
library; it does not need XeFM's bundled Python):

```powershell
py -3 "$HOME\.xefm\extensions\box-drive\box_drive\box_api.py" auth
```

This opens the browser for the Box consent flow and saves the tokens locally.
No external executable needs to be configured: the extension uses Windows'
default browser.

Actions are available in the command palette and have suggested bindings:
`box.url-from-cursor` (`Alt-U`), `box.url-from-panel` (`Shift-Alt-U`), and
`box.open-clipboard` (`Alt-Shift-J`).

For development, run `xefm ext check .` and `python -m pytest tests`. Tests do
not launch XeFM or contact Box.
