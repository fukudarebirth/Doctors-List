# 入力データと判定

`examples/*.synthetic.json` はすべて架空データです。実ファイルはチェックアウト外へ置いてください。
JSONはUTF-8、20 MiB以下。プログラムやpickleを読み込む機能はありません。

## 候補ファイル

配列の各オブジェクトに `id`, `name`, `email`, `profession`（doctor/dentist）, `affiliation` を指定。
任意の `kana`, `romanized`, `aliases`（旧姓・別表記配列）, `other_emails`, `previous_affiliations` で人が確認した別表記を記録できます。
読み・ローマ字・旧姓を推測で生成しません。氏名の類似や旧字体は候補抽出にのみ使用します。

`evidence.email`, `evidence.age`, `evidence.affiliation` はそれぞれ次を含みます。

- `confirmed: true`、`reviewer`、タイムゾーン付きISO8601の `reviewed_at`
- 根拠の具体的記述 `summary`、資料URL配列 `urls`
- メール: `personal_public: true`。`inferred` / `ocr_only` がtrueなら保留
- 所属: `current: true`。医師・歯科医師としての現職を確認した記述
- 年齢: `method: birth_date` と `birth_date: YYYY-MM-DD`、または `method: birth_year` と整数 `birth_year`
- 経歴推定: `method: career`, `max_age`, `basis_year`, `sufficient: true`, `calculation`, `nonstandard_history_checked: true`

経歴推定は人の調査を必要とし、標準卒業年齢から自動推測しません。生年のみで45〜46歳になる例は保留。
基準は運用ルールどおり2026年・45歳以下。2027年以降は基準年変更の指示確認のため保留します。
資料URLを入力しただけでは採用しません。プログラムは資料を自動調査せず、確認記録の形式と条件を検査します。
`confirmed` は調査担当の確認記録であり、本人性をプログラムが保証する署名ではありません。

旧所属メールは `old_affiliation_email: true` とし、`old_email_review` に同じ根拠記録形式と `decision: accept` を付けると個別に評価します。
この個別確認がない例は保留。一律除外にも一律採用にもしません。現職、メール資料の旧所属と掲載時点をsummaryで分けて記録。
未解消の不一致は `conflicts` に記録し、保留にします。`shared_email: true` は除外。

## 同姓同名の解決

読み・氏名だけの一致は保留。`identity_key` を使う場合は人が根拠付きで同一人物へ共通キーを割り当て、`identity_evidence` を前述の根拠形式で添えます。
単に `identity_reviewed: true` を入力しても、エンジンは根拠なしの指定を信用しません。
既存行との個別照合は `resolutions` に `{record_id, snapshot_hash, decision, review}` を指定できます。
`record_id` はレポートの `sheetId:row`、`snapshot_hash` は今回の正本内容ハッシュ、`decision` はsame_person/different_person、`review` は根拠形式。
正本内容が変われば以前の解決を使いません。本人メール完全一致は別人指定で自動解除しません。
候補同士の重複は両者を止め、人が候補ファイルを整理します。入力順で勝手に片方を採用しません。

## 除外マスター

Library IDは一般のダウンロードURLやSedimentファイルIDではありません。この実装はLibraryへ直接アクセスしません。
ダウンロードした原本を保持し、`schema_version: 1`, `spreadsheet_id`, `snapshot_hash`, `entries`, `removed` の形式で安全に読み込みます。
実ファイルの形式は未取得・未検証です。未知形式を黙って無視せずエラーにします。
変換が必要な場合は原本の氏名・本人メール・別表記・参照元・削除履歴を保持する対応表を作り、実内容を確認してから変換アダプターを追加してください。個人情報をGitへ追加しないこと。

`entries` は正本の登録記録、`removed` は以前存在した記録です。
最新の全シート内容・位置・構成のハッシュを比較し、内容一致の索引だけ再利用します。
変更・削除された記録は履歴として残り、候補に一致すれば削除の経緯を確認するまで保留。
未登録候補をマスターへ混ぜません。Dry Runでは差分だけ出し、マスター保存は別の明示コマンドです。

```powershell
.\.venv\Scripts\python.exe -m doctors_list sync-master --config $settings --master "$env:LOCALAPPDATA/ReBIRTH/DoctorsList/master.json" --output "$env:LOCALAPPDATA/ReBIRTH/DoctorsList/master-updated.json"
```

その場で正本を再取得して照合し、別ファイルへ原子的に保存、再読み取りハッシュを確認。
元ファイルを保持して結果を確認してください。このローカル更新はGoogleシートへの書き込みを伴いません。
