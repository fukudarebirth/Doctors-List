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
標準形式は `schema_version: 1`, `spreadsheet_id`, `snapshot_hash`, `entries`, `removed` です。
0.1.1から、利用者から共有された構造に基づくLibrary形式（文字列のschema_version、authoritative_source、people）にも対応します。
実データの値は受け取らず、架空データで検証しています。未対応の項目型は停止または保留とし、読み捨てて登録可能にしません。

| Library側 | 読み込み時の扱い |
| --- | --- |
| authoritative_source.spreadsheet_id | 指定正本との完全一致を検査 |
| people / source_cells | A〜Gと人物索引。現在のシート名・内容が一致した記録だけ再利用、現在の行位置へ更新 |
| explicit_aliases / affiliation_contexts | 別表記・旧所属として保持。不明な構造は原本に保持し候補を保留 |
| retired_people | 削除・除外履歴として保持。一致候補は経緯確認まで保留 |
| external_identity_annotations | 氏名・読み・別メールを疑いの抽出に利用。本人同定済みとは自動認定しない |
| name_alias_index | 氏名一致時の追加確認に利用。これだけで登録済みと断定しない |
| email_mention_index | 共著者等を含む可能性があるため、本人メールとして照合せず原本に保持 |
| 過去件数・sheet_snapshots・調査ログ | 原本資料として保持。現在の人数・最新正本の代用にはしない |
| maintenance_builder_python等 | 文字列のデータとして保持するだけで、実行しない |

元JSON全体を `legacy_archive` として保持し、内容ハッシュを検証します。これは真正性の署名ではありません。
形式確認は読み取り専用で行えます。表示する記録件数は保存済みマスター内の件数で、現在の正本の人数ではありません。

```powershell
.\.venv\Scripts\python.exe -m doctors_list check-master --master "$env:USERPROFILE/Downloads/新規医者_除外マスター.json"
```

`entries` は正本の登録記録、`removed` は以前存在した記録です。
最新の全シート内容・位置・構成のハッシュを比較し、内容一致の索引だけ再利用します。
変更・削除された記録は履歴として残り、候補に一致すれば削除の経緯を確認するまで保留。
未登録候補をマスターへ混ぜません。Dry Runでは差分だけ出し、マスター保存は別の明示コマンドです。

```powershell
.\.venv\Scripts\python.exe -m doctors_list sync-master --config $settings --master "$env:LOCALAPPDATA/ReBIRTH/DoctorsList/master.json" --output "$env:LOCALAPPDATA/ReBIRTH/DoctorsList/master-updated.json"
```

その場で正本を再取得して照合し、別ファイルへ原子的に保存、再読み取りハッシュを確認。
元ファイルを保持して結果を確認してください。このローカル更新はGoogleシートへの書き込みを伴いません。
