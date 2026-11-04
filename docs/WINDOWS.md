# Windowsでのセットアップと読み取り

Python 3.14をインストールし、PowerShellでリポジトリへ移動します。READMEの仮想環境作成と依存導入を実行してください。
PowerShellの実行ポリシー変更は不要です。Activateではなく .venv 内の python.exe を直接使います。

## 公式OAuth

1. Google Cloudでプロジェクトを用意し、Google Sheets APIを有効化。
2. OAuth同意画面を設定。テスト公開中なら利用者をテストユーザーに追加。
3. OAuthクライアントIDを「デスクトップアプリ」で作り、JSONをダウンロード。
4. `$env:LOCALAPPDATA/ReBIRTH/DoctorsList/client_secret.json` に保存。Git管理・共有フォルダーを避け、Windowsのアクセス権を本人だけに制限。
5. 指定の正本を閲覧できるGoogleアカウントで次のコマンドを実行。

```powershell
.\.venv\Scripts\python.exe -m doctors_list auth --client "$env:LOCALAPPDATA/ReBIRTH/DoctorsList/client_secret.json" --token "$env:LOCALAPPDATA/ReBIRTH/DoctorsList/token.json"
```

ブラウザの公式Google認可画面を使います。認証コード・トークンをチャットへ送らないでください。
要求権限は `https://www.googleapis.com/auth/spreadsheets.readonly` のみです。
ローカルの一時コールバックを使い、ウェブ公開サーバーを設置しません。
既存の書き込み権限トークンは再利用せず、別ファイルで読み取り専用認証を行います。

`config/settings.example.json` をリポジトリ外にコピーしてパスを実際の値へ変更します。
`person_sheet_ids` / `non_person_sheet_ids` は現在の内容を確認した分類です。
未知のタブは一覧へ出し、保留にします。列構成が異なる人物シートは対応を確認するまで停止します。
既知の「LP・小冊子」は人物数に含めません。新設・名称変更・用途変更時は分類を再確認してください。

```powershell
$settings = "$env:LOCALAPPDATA/ReBIRTH/DoctorsList/settings.json"
.\.venv\Scripts\python.exe -m doctors_list fetch --config $settings --output "$env:LOCALAPPDATA/ReBIRTH/DoctorsList/snapshot.json"
.\.venv\Scripts\python.exe -m doctors_list dry-run --live --config $settings --candidates "$env:LOCALAPPDATA/ReBIRTH/DoctorsList/candidates.json" --master "$env:LOCALAPPDATA/ReBIRTH/DoctorsList/master.json" --output "$env:LOCALAPPDATA/ReBIRTH/DoctorsList/report.json"
```

`--live` はその場で全シートを取得。ファイルからの `--snapshot` は必ずオフライン評価として記録し、最新の正本の代わりにしません。
`--fixture` は架空データ専用です。実データでは使わないでください。
マスター省略時は削除履歴未照合のため保留。Libraryのファイルはユーザーが安全にダウンロードし、入力形式を確認してください。

## 通信・実行環境

WindowsからGoogle公式APIに接続します。制限されたクラウド環境で接続する場合は、環境設定で `accounts.google.com`、`oauth2.googleapis.com`、`sheets.googleapis.com`、`www.googleapis.com` のHTTPS通信を許可し、公式OAuthを安全に設定する必要があります。
今回のクラウド実行ではGoogle認証なし・これらのドメイン許可なしのため、実正本の読み取りは未検証です。
依存パッケージの導入・架空データの照合・模擬APIテストは独立して検証できます。
