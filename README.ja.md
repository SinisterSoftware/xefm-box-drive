# Box Drive

[English](README.md)

Windows 版 XeFM で Box Drive のローカルパスと Box の URL を相互に扱う拡張機能です。ファイルやフォルダの通常 URL をコピーしてブラウザで開いたり、クリップボードのパスや Box URL に対応する場所へ XeFM のペインを移動したりできます。

## 動作環境と前提

- Windows、XeFM 1.10.0 以降。
- Box Drive をインストールし、サインインしていること。Box Drive がローカルデータベースを作成している必要があります。
- Python パッケージ `pywin32`。拡張機能のインストール時に同期できます。
- ローカルデータベースで解決できない変換や共有リンクには、追加で Box API の設定、認証、インターネット接続が必要です。詳細は [Box API の設定](#box-api-の設定任意) を参照してください。

拡張機能は Box Drive のデータベース `%USERPROFILE%\AppData\Local\Box\Box\data\sync.db` をコピーして読み取ります。データベースで解決できる通常の変換には Box API の認証情報は不要です。Box Drive のルートは、レジストリの `HKLM\SOFTWARE\Box\Box` にある `CustomBoxLocation` の下の `Box` フォルダ、またはその設定がない場合は `%USERPROFILE%\Box` です。

## インストール

PowerShell で次を実行します。

```powershell
xefm ext install github:SinisterSoftware/xefm-box-drive
```

依存パッケージ `pywin32` の確認が表示されたら、インストールを許可してください。同期しなかった場合は、XeFM の **Tools > Advanced > Sync Python Packages** を実行するか、次のコマンドを実行し、設定を再読み込みします。

```powershell
xefm deps sync
```

## 使い方

アクションは XeFM のコマンドパレットから実行できます。以下のキーは拡張機能が提案する割り当てです。既存の割り当てと競合する場合は適用されないため、実際のキー設定を確認してください。

| アクション | 提案キー | 動作 |
| --- | --- | --- |
| `box.url-from-cursor` | `Alt-U` | カーソル位置のファイルまたはフォルダを Box の通常 URL に変換し、クリップボードにコピーして既定のブラウザで開きます。 |
| `box.url-from-panel` | `Shift-Alt-U` | 現在のペインのフォルダを Box の通常 URL に変換し、コピーして既定のブラウザで開きます。 |
| `box.open-clipboard` | `Alt-Shift-J` | クリップボード内の既存のローカルパス、または Box URL に対応する場所へペインを移動します。 |

### ファイルやフォルダの URL を取得する

1. XeFM で Box Drive 内の対象を表示します。
2. 対象にカーソルを合わせ、`box.url-from-cursor` を実行します。現在表示しているフォルダ自体の URL が必要なら `box.url-from-panel` を実行します。
3. 変換に成功すると URL が表示・コピーされ、ブラウザでも開きます。

生成されるのは `https://app.box.com/file/<id>`、`/folder/<id>`、または Box Note の `/notes/<id>` 形式の通常 URL です。共有リンクを新規作成する機能ではありません。URL を他の人が開けるかどうかは、その人の Box 上のアクセス権に依存します。

### クリップボードから移動する

1. 既存のローカルパス、Box の通常 URL、または共有リンクをクリップボードにテキストとしてコピーします。エクスプローラーの「パスのコピー」のような、二重引用符で囲まれたパスも使えます。
2. XeFM で `box.open-clipboard` を実行します。
3. フォルダの場合はそのフォルダへ移動し、ファイルの場合は親フォルダへ移動してファイルにカーソルを合わせます。既存のローカルパスであれば Box Drive 外でも移動できます。

Box の通常 URL は `https://app.box.com/file/123`、`https://app.box.com/folder/123`、`https://app.box.com/notes/123` のような形式です。`https://<domain>.box.com/s/<token>` 形式の共有リンクは常に Box API で解決します。URL から移動するには、対応するパスがローカルで存在する必要があります。

## Box API の設定（任意）

通常 URL やローカルパスを `sync.db` で解決できない場合、および共有リンクの場合は Box API を使用します。API を使わない場合、以下の設定は不要です。

1. Box Developer Console で OAuth 2.0 の **User** アプリを用意します。必要なスコープは **Read all files and folders stored in Box** です。認証する Box ユーザー自身にも対象へのアクセス権が必要です。
2. アプリのリダイレクト URI に `http://localhost:8080/callback` を登録します。
3. クライアント ID とクライアントシークレットを、次のいずれかの方法でローカルに設定します。

`%USERPROFILE%\.xefm\box_config.json` の例:

```json
{
  "client_id": "your-client-id",
  "client_secret": "your-client-secret",
  "redirect_port": 8080
}
```

または、XeFM と認証コマンドを起動する環境で、PowerShell の環境変数を設定します。

```powershell
$env:BOX_CLIENT_ID = "your-client-id"
$env:BOX_CLIENT_SECRET = "your-client-secret"
```

環境変数は設定した PowerShell プロセスとそこから起動したプロセスに適用されます。ID とシークレットはそれぞれ環境変数が優先され、未設定の場合は JSON の値が使われます。`redirect_port` は JSON で指定でき、省略時は `8080` です。変更した場合は Box アプリ側にも対応する `http://localhost:<port>/callback` を登録してください。

4. ローカルにインストールした Python 3.10 以降を使い、PowerShell で一度認証します。このヘルパーは Python 標準ライブラリのみを使い、XeFM に同梱された Python は必要ありません。

```powershell
py -3 "$HOME\.xefm\extensions\box-drive\box_drive\box_api.py" auth
```

既定のブラウザで Box の認可画面が開きます。認証を完了すると、アクセストークンとリフレッシュトークンが `%USERPROFILE%\.xefm\box_token.json` に保存されます。認証コマンドは localhost で応答を最大 120 秒待ちます。外部ブラウザの実行ファイルを設定する必要はありません。

**秘密情報を保護してください。** クライアントシークレット、設定ファイル、トークンファイルを共有・コミットせず、Issue、ログ、公開リポジトリにも貼り付けないでください。

## 制限とトラブルシューティング

- **対象が見つからない／変換できない:** Box Drive にサインインしているか、対象が Box Drive 内で利用できるか、`sync.db` が存在するかを確認してください。データベースで解決できない場合は API の設定と認証が必要です。URL からの移動先はローカルに存在する必要があります。
- **共有リンクが開けない:** API 設定と認証、インターネット接続、リンクの有効期限、および認証ユーザーのアクセス権を確認してください。共有先が Box Drive に見えていない場合も移動できません。
- **API 認証に失敗する:** ID とシークレット、Box アプリに登録したリダイレクト URI とポートが一致するか確認し、上記の認証コマンドを再実行してください。トークンファイルが不正な場合は、削除して再認証します。API は 401 応答時にトークンを一度更新して再試行します。
- **認証がタイムアウトする／ポートを開けない:** ブラウザで認可を完了し、指定した localhost ポートを使用できることを確認してください。ポートを変更した場合は JSON と Box アプリの両方を更新します。
- **キーが動かない:** 提案キーが別の操作と競合していないか確認し、コマンドパレットからアクションを実行してください。
- **拡張機能が読み込めない:** `pywin32` の同期を確認し、必要なら `xefm deps sync` を実行して設定を再読み込みしてください。

Box API による検索はローカルデータベースの参照より時間がかかる場合があります。また、API を使うパスから URL への変換では、拡張子のないファイルがフォルダとして扱われ、解決できない場合があります。

## 開発

拡張機能のチェックとテストを実行するには、リポジトリのルートで次を実行します。テストは XeFM を起動せず、Box にも接続しません。

```powershell
xefm ext check .
python -m pytest tests
```
