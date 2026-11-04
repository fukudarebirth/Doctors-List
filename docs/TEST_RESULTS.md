# 検証結果

検証日: 2026年11月4日（日本時間）。環境: Linux / Python 3.12.14。

実行コマンド:

```text
/workspace/doctors-list-venv/bin/python -m unittest discover -s tests -v
Ran 104 tests in 0.080s
OK
```

104件成功、失敗0、スキップ0。依存パッケージ導入、requirements.lockの導入再実行、編集可能インストール、`pip check` が成功。
Windows上の実機実行は未実施。公式OAuthの対話認証、実正本へのAPI接続、Libraryファイルの実物取り込みは未実施です。

GitHubへの初回アップロード前に、固定済み依存パッケージ全29件についてWindows 64bit / Python 3.14用wheelの取得を確認しました。これはWindowsでの動作確認とは区別しています。READMEとWindows手順は利用者が導入したPython 3.14に合わせています。

## 必須ケースとの対応

| ケース | この段階での結果 |
| --- | --- |
| 正常な新規候補 | 根拠と人の確認が揃った架空例をeligibleへ分類 |
| 完全一致の重複 | 本人メール完全一致をduplicateへ分類 |
| 同じ人物の別メール | 氏名一致で保留、本人照合の根拠で同一人物を確定 |
| 旧姓・旧字体 | 明示別名・正規化から疑いを抽出 |
| 同姓同名の別人 | 根拠付きの異なる本人キーで区別、メール衝突は解除しない |
| 代表メール | excluded、追記案に含めない |
| 年齢根拠なし | hold |
| 旧所属メール | 個別確認がない場合hold、根拠付き個別判断の例は評価可 |
| 候補同士の重複 | 両者を停止、順序で自動採用しない |
| 空行 | 登録件数から除外、末尾の0は非空として扱う |
| 同日日付 | 和暦・西暦・桁埋めなし表記で同日重複を防止 |
| 日本時間 | UTCから日付が変わる境界を確認 |
| 実データ末尾 | 途中空行・確保行数に依存せず直下の追記案を作成 |
| 行高20px・書式維持 | 予定差分の20px指定・入力スナップショット不変を検証。実APIの書式継承は未実装・未検証 |
| 二重実行 | Dry Runの再実行で同じ操作ID、Google変更0。将来の書き込み冪等性は未実装・未検証 |
| 保存エラー安全停止 | ローカル保存失敗で成功報告しない、模擬API取得失敗で停止。Google書き込み途中の失敗復旧は未実装・未検証 |

追加確認: 全タブ取得、未分類シートの保留、非人物シートへの人物表混入、列H以降、氏名なし非空行、マスターの同件数内容変更・削除履歴・並べ替え・複数シートの同一行・同一シート別名統合、オフライン/古い正本での保留、JSON重複キー・未知形式拒否、個人情報をコンソールへ出さない、認証/出力のリポジトリ外保存。

## CLI通し実行

```text
python -m doctors_list dry-run --snapshot examples/snapshot.synthetic.json --fixture --candidates examples/candidates.synthetic.json --master examples/master.synthetic.json --output /tmp/doctors-list-demo-report.json
```

終了コード0、保存結果の再読み込み成功。登録候補1件、重複1件、保留1件。追記予定は医師1名、実際の新規追加は0名。
出力例: [../examples/dry-run.synthetic.json](../examples/dry-run.synthetic.json)。全員架空、既存の実人数は未同定としてnull。
本番Googleシートは取得・更新していません。実正本の読み取りはOAuth設定後に実施する必要があります。

## 残る受入確認

- 利用者のWindows環境でOAuth・全シート読み取り・実データの列対応確認
- 実際のLibrary除外マスターのスキーマ確認と、必要なら変換アダプター
- 第5〜7段階の実装と別途テスト（承認、バックアップ、追記、書式継承、保存検証、GUI）
