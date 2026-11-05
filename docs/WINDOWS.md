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
開発環境ではGoogle認証を使用していません。利用者のWindowsでは0.1.2の公式OAuth・実正本全2シート取得・マスター同期・Dry Runの成功を確認済みです。
依存パッケージの導入・架空データの照合・模擬APIテストは独立して検証できます。

## 0.2.0への更新（すでに導入・読み取り認証済みの場合）

Python・仮想環境・読み取り認証・settings.jsonはそのまま利用します。GitHubから0.2.0のコードZIPをダウンロードし、PowerShellで次を実行します。`ダウンロードしたZIP名.zip` は実際のファイル名に置き換えてください。旧ソースのフォルダーを作業ディレクトリにすると旧コードが優先されるため、ホームへ移動します。

```powershell
Set-Location $env:USERPROFILE
$doctorsPython = "$env:USERPROFILE\Downloads\Doctors-List-main\Doctors-List-main\.venv\Scripts\python.exe"
$doctorsZip = "$env:USERPROFILE\Downloads\ダウンロードしたZIP名.zip"
& $doctorsPython -m pip install --no-deps --no-build-isolation "$doctorsZip"
& $doctorsPython -c "from importlib.metadata import version; print(version('rebirth-doctors-list'))"
& $doctorsPython -m doctors_list gui
```

バージョンが `0.2.0` と表示されることを確認します。次回は元の仮想環境の `Scripts\doctors-list-gui.exe` をダブルクリックするだけで起動できます。

## 毎日の画面操作

1. 「設定」で既存のsettings.jsonを選択。標準保存先は `%LOCALAPPDATA%\ReBIRTH\DoctorsList`。
2. 「候補JSON」で調査担当が作成した候補ファイルを選択。「除外マスター」は最後に保存検証されたものを使用。アプリはマスター更新・追記成功時の保存先を次回用に記憶します。初回はmaster-updated.jsonが既定です。
3. 「1. Dry Run」で全シートを取得し、登録候補・重複・除外・保留を確認。行のダブルクリックで候補の根拠と判定を確認できます。「追記差分・A〜G」タブに各セルの値と確認シート一覧を表示します。
4. 必要なら「マスター更新」で現在の正本と照合。別の新規ファイルへ保存し、選択先を更新します。原本は保持します。
5. 実在候補の根拠が確認できた場合に「2. 追記する差分を準備」。書式も取得して承認対象を保存します。準備の有効期限は15分で、日本時間の日付が変わると失効します。
6. 初めて追記する前に「書き込み用の認証を準備」を実行。Googleの公式画面で編集権限 `https://www.googleapis.com/auth/spreadsheets` を許可します。認証だけでは追記されません。読み取り用token.jsonとは別のtoken-write.jsonへ保存します。必要ならOAuth同意画面のデータアクセスにこのスコープを登録してください。
7. 判定・根拠・差分を確認し、他の編集者・自動処理・手動編集を停止。画面の2つの確認欄を選び「3. 差分を承認して追記」。その差分専用の `承認 …` を入力すると実行します。
8. 「保存検証済み」と、実際の追加人数を確認。バックアップ、操作記録、レポート、更新マスターは `operations\操作ID\` に保存します。エラー時は再追記せず「保存状態を確認」を使用します。

架空のcandidates-demo.jsonで確認するのは手順3までです。予約ドメインやsynthetic指定のサンプルは追記準備で停止します。画面上の操作だけでは外部資料の真偽を証明できないため、調査担当による根拠確認が必要です。

Google Sheets API自体は追加料金なしで利用できますが、利用上限があります。OAuthアプリが外部・テスト状態の場合、更新トークンが7日で失効することがあります。認証が失効したら対応する認証ボタンで再認証します。Googleの再認証・更新トークン失効エラーが続く場合は、該当トークンだけを本人用フォルダー内で別名退避してから再認証してください。client_secret.jsonや操作記録は削除しません。
