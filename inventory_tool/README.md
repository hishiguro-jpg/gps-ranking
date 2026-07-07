# 在庫発注計算ツール

過去の販売実績と原価・売価情報から、発注量・発注点・在庫消化にかかる期間・損益分岐点を算出し、
社内報告用のMarkdownレポートと商品ごとの推移グラフを出力するツールです。

## ファイル構成

```
inventory_tool/
├── order_calculator.py       ← メインスクリプト（発注量・損益分岐点等を算出）
├── raw_data_import.py        ← 受発注管理システムの生データをsales_history.csv形式に変換
├── estimate_selling_price.py ← 寄附金額とコーディネート報酬率から売価を推定
├── templates/                ← 入力CSVのフォーマット見本
│   ├── sales_history_template.csv
│   └── cost_master_template.csv
└── output/                   ← グラフの出力先（自動生成、Git管理外）
```

## 使い方

### 1. 販売実績データがすでにCSVで整っている場合

```bash
pip install -r requirements.txt

python inventory_tool/order_calculator.py \
  --sales  path/to/販売実績.csv \
  --costs  path/to/原価売価マスター.csv \
  --output inventory_tool/output/report.md
```

### 2. 受発注管理システムの生データ（1行=1件の受発注明細）しかない場合

商品名の表記ゆれ（【寄付額変更前】等のラベル、①②③、ポータル名の付記など）を
吸収して日次集計するインポーターを用意している。

```bash
# 出荷日を基準に日次集計（在庫消費の実態に近い）
python inventory_tool/raw_data_import.py \
  --input path/to/生データ.csv --date-column shukka \
  --output inventory_tool/sales_history_shukkabi.csv

# 寄附日を基準に日次集計（本来の需要タイミングに近い。両方出して比較すると良い）
python inventory_tool/raw_data_import.py \
  --input path/to/生データ.csv --date-column kifu \
  --output inventory_tool/sales_history_kifubi.csv
```

対応済みの商品グループは `raw_data_import.py` 内の `classify_product()` に定義されている
（現状: シリカちゃん天然水の40本・24本）。別商品を対象に追加する場合はここにルールを追加する。

ふるさと納税の寄附金額に対する一定率をコーディネート報酬（売価）とみなして概算したい場合は
`estimate_selling_price.py` を使う。3ヶ月/6ヶ月/12ヶ月の定期便は、同一の寄附番号に対して
出荷のたびに明細行ができる一方、寄附金額は毎回同額が記録されるため、寄附番号ごとの出荷件数で
按分してから報酬率を掛けて1個あたりの金額を推定する。

```bash
python inventory_tool/estimate_selling_price.py \
  --input path/to/生データ.csv --coordination-rate 0.13
```

`--coordination-rate` は比率が変わった場合に指定し直す（既定値0.13 = 13%）。

実行すると、標準出力とMarkdownレポートに以下が出力されます。

- 直近の平均消化ペースと傾向（上昇/下降/横ばい）
- 安全在庫・発注点（ROP）
- 推奨発注量
- 現在庫のみ／発注後の在庫消化にかかる見込み期間
- 損益分岐点に必要な販売数量と到達見込み時期

商品ごとの販売推移グラフ（PNG）は `--chart-dir`（既定値: `inventory_tool/output/`）に保存されます。

## 入力CSVの形式

### 販売実績CSV（`--sales`）

| 列名 | 内容 | 例 |
|---|---|---|
| date | 販売日 | 2025-04-01 |
| product | 商品名（発注マスターの`product`と一致させる） | 商品A(500g) |
| quantity | その日の販売数量 | 3 |

### 原価・売価・在庫マスターCSV（`--costs`）

| 列名 | 内容 | 例 |
|---|---|---|
| product | 商品名 | 商品A(500g) |
| unit_cost | 原価（1個あたり） | 1500 |
| selling_price | 売価（1個あたり） | 3000 |
| fixed_cost | 固定費（損益分岐点計算に使用） | 50000 |
| lead_time_days | 発注から入荷までの日数 | 14 |
| current_stock | 現在庫数 | 120 |
| on_order_qty | 発注済み・未入荷数量（無ければ0） | 0 |

※ 事業者共通の固定費がある場合は、商品ごとに按分した金額を`fixed_cost`に入力してください。
　（例：保管費用が`現在庫数×単価`で決まる場合や、搬入トラック1台あたりの固定費用がある場合は、
　それらを合算した金額を入力する）

サンプルは `templates/` フォルダを参照してください。実データのCSVは`.gitignore`で除外されるため、
このフォルダに置いても誤ってGitへコミットされることはありません。

## 主なオプション

| オプション | 既定値 | 内容 |
|---|---|---|
| `--service-level` | 0.95 | 欠品許容度（サービス率）。高いほど安全在庫が増える |
| `--window-days` | 60 | 消化ペースの算出に使う直近日数 |
| `--target-days` | 30 | リードタイムに加えて何日分の在庫を確保するか（発注量の算出に使用） |
| `--output` | なし | レポート(Markdown)の保存先ファイルパス |
| `--chart-dir` | `inventory_tool/output/` | グラフ画像の保存先 |

## 計算方法の考え方

- **消化ペース**：直近`--window-days`日間の日次販売数の平均・標準偏差を使用
- **安全在庫**：`安全係数(サービス率から決定) × 標準偏差 × √リードタイム日数`
- **発注点(ROP)**：`平均消化ペース × リードタイム日数 + 安全在庫`
- **推奨発注量**：`平均消化ペース × (リードタイム日数 + 目標日数) + 安全在庫 − 現在庫 − 発注済み数量`（0未満は0）
- **損益分岐点**：`固定費 ÷ (売価 − 原価)` で必要販売数量を算出し、現在の消化ペースで到達日を見積もり
