# 医師リスト管理システム — 第1〜4段階

ReBIRTH株式会社向け、Python 3.11以降 / Windows対応の読み取り専用ツールです。
正本の全タブ取得、候補の根拠確認、重複照合、除外マスター差分、A〜Gの追記案を出力します。
バージョン0.1.2は既存Library除外マスターの `people` 形式と、出典付きの別名・旧所属にも対応します。`check-master --master <ファイル>` で内容を表示せず読み込み確認できます。
**本番Googleシートへの書き込み機能はまだありません。** 登録可能は承認対象の候補という意味です。
調査や資料の真偽確認は人が行い、確認者・日時・根拠を候補JSONに記録します。

- 正式運用ルール: [docs/OPERATIONS_POLICY.md](docs/OPERATIONS_POLICY.md)
- 開発指示: [docs/DEVELOPMENT_REQUEST.md](docs/DEVELOPMENT_REQUEST.md)
- 構成と段階別機能: [docs/DESIGN.md](docs/DESIGN.md)
- Windows認証・操作: [docs/WINDOWS.md](docs/WINDOWS.md)
- 入力と判定: [docs/INPUT_FORMAT.md](docs/INPUT_FORMAT.md)
- 更新・復旧の設計: [docs/SAFETY_AND_RECOVERY.md](docs/SAFETY_AND_RECOVERY.md)
- 検証範囲: [docs/TEST_RESULTS.md](docs/TEST_RESULTS.md)

## 架空データで試す

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install --no-deps --no-build-isolation -e .
.\.venv\Scripts\python.exe -m doctors_list dry-run --snapshot examples/snapshot.synthetic.json --fixture --candidates examples/candidates.synthetic.json --master examples/master.synthetic.json --output "$env:LOCALAPPDATA/ReBIRTH/DoctorsList/demo-report.json"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

上記は今回のWindows環境に合わせたPython 3.14の手順です。既存の自動テストはLinux / Python 3.12で実行済みで、Windows実機での確認はこれから行います。

例は架空の氏名と予約ドメイン example.invalid のみ。機密入力・OAuth・マスター・結果はリポジトリ外へ保存してください。
出力の `before` / `after` は同じ読み取り結果、`newly_added` は常に0です。
`planned_additions` と `proposed_change` が追記案で、保存済み件数とは区別します。
`actual_people: null` は本人同定が不十分なため実人数不明、`registered_rows` は人物シートの氏名がある行数です。
全シートの分類・列が未確認なら登録行数も確定せず、観測済み行数だけ別欄に表示します。

この段階の画面はCLIです。承認付き追記・Google保存後検証・Windows GUIは第5〜7段階に残っています。
