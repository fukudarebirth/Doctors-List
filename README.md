# 医師リスト管理システム

ReBIRTH株式会社向け、Python 3.11以降 / Windows対応。**0.2.0** は、全シート取得・候補照合・Dry Runに加え、Windows操作画面、差分の明示承認付き追記、保存後検証を備えます。起動時は読み取り専用です。
調査と資料の真偽確認は人が行い、確認者・日時・根拠を候補JSONに記録します。登録候補の表示だけでは追記されません。

- [Windowsでの導入・毎日の操作](docs/WINDOWS.md)
- [入力形式・判定](docs/INPUT_FORMAT.md)
- [承認・安全対策・エラー復旧](docs/SAFETY_AND_RECOVERY.md)
- [設計](docs/DESIGN.md) / [テスト結果と未検証事項](docs/TEST_RESULTS.md)
- [正式運用ルール20項目](docs/OPERATIONS_POLICY.md) / [開発指示](docs/DEVELOPMENT_REQUEST.md)

## 初回セットアップ

リポジトリの展開先で実行します。既存利用者はWindows手順の更新方法を使ってください。

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install --no-deps --no-build-isolation .
.\.venv\Scripts\python.exe -m doctors_list gui
```

次回から `.venv\Scripts\doctors-list-gui.exe` をダブルクリックして起動できます。ショートカットをデスクトップに作成すると便利です。

## 架空データで確認

```powershell
.\.venv\Scripts\python.exe -m doctors_list dry-run --snapshot examples/snapshot.synthetic.json --fixture --candidates examples/candidates.synthetic.json --master examples/master.synthetic.json --output "$env:LOCALAPPDATA/ReBIRTH/DoctorsList/demo-report.json"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

例は架空データです。本番の承認対象にできません。Dry Runの `newly_added` は0、`planned_additions` は予定件数です。`actual_people: null` は実人数が未確定という意味で、登録行数を実人数とみなしません。
認証情報・候補・マスター・バックアップ・レポートはGit管理外の本人専用フォルダーへ保存します。

Google Sheets APIには厳密な排他制御がないため、追記中は他PC・他担当者・手動編集を停止する必要があります。追加の人物シート、結合セル、独自の入力規則など未対応の構成では追記を停止します。

159件の架空データ・模擬API・画面状態テスト成功。利用者のWindowsでは0.1.2の読み取り認証、実正本取得、マスター同期、Dry Runを確認済みです。0.2.0のWindows画面描画・書き込み認証・実API追記は未検証です。開発中に本番シートへの書き込みは実行していません。
