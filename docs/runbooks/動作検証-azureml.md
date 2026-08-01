# 動作検証: Azure ML（Phase 4・go/no-go 後）

> ## ✅ Phase 4 完了（2026-08-01）— リソースは撤収済み
>
> **完了条件8項目すべて到達。** RMSE `0.4368055090296257` でローカル基準値と完全一致、
> `write_path='direct'`。結果と考察は [04_azureml.md](../comparison/04_azureml.md)。
>
> **`mcml-dev-rg` は削除済みで課金は止まっている。** 残るのは state 用の `mcml-tfstate-rg` のみ。
> **再開するときは §1 の着手前チェックからやり直す**
> （クレジット期限 **2026-08-30** が実質のデッドライン。撤収の全手順は §9）。
>
> 以下は**この基盤で越えた契約ゲート2枚の記録**。再開時に同じ所で止まらないために残す。
>
> ### 前提: 契約変更が2回要った
>
> | # | 壁 | 症状 | 越え方 |
> |---|---|---|---|
> | 1 | offer に AML 専用コアが無い | apply が 8/9 で失敗（3回）。`ResourceNotAvailableForOffer` | 無料試用版 → **Pay-As-You-Go** |
> | 2 | `TotalLowPriorityCores 0/0` | apply が 9/10 で失敗。`ClusterMinNodesExceedCoreQuota` | プラン **Free → Basic** → 総枠を申請 |
>
> どちらも Terraform の外側にあり、**コードの修正では越えられなかった**。
> 残クレジット ¥32,777 はアップグレードで引き継がれた（次の請求日 2026-08-09）。
>
> ### 第1の壁: 反映は即時ではない。ゲート判定は1コマンド
>
> ポータルで「アップグレードしました」と出ても ARM は数分遅れる（実測 **約2分**・
> 45 秒間隔ポーリングで 3 回目に反転）。反転前に quota を触っても
> `QuotaNotAvailableForResource` で弾かれるだけなので、必ず先にこれを見る:
>
> ```
> 反転前: quotaId=FreeTrial_2014-09-01    spendingLimit=On
> 反転後: quotaId=PayAsYouGo_2014-09-01   spendingLimit=Off      ← これが着手条件
> ```
>
> コマンドは §1「offer 反映確認」。
>
> ### 反映されると何が変わるか（実測）
>
> AML の usages API が **`currentValue` を返すようになる**。これが唯一の信頼できる判定材料で、
> `az quota show` の `limit` 値は反映前後で変わらない（既定テンプレート値。
> [04_azureml.md](../comparison/04_azureml.md) の誤読要因 #2）。
>
> | | 反映前 | 反映後 |
> |---|---|---|
> | `currentValue` | **全て null** | `0`（実値） |
> | `TotalDedicatedCores` | 20（テンプレート値） | 0 / 20 |
> | `standardDSv2Family` | 6（テンプレート値） | 0 / 6 |
>
> ### 第2の壁: dedicated と low-priority は**別枠**
>
> offer を越えた直後の apply は **9/10 まで進んで compute cluster だけが落ちた**。
> module 既定の `vm_priority = "LowPriority"` が dedicated とは別の枠を引いていたため:
>
> ```
> ClusterMinNodesExceedCoreQuota
> The specified subscription has a total vCPU quota of 0 and cannot accomodate
> for at least 1 requested managed compute node which maps to 4 vCPUs.
> ```
>
> 必要量は `Standard_DS3_v2`（4 vCPU）× `max_nodes=1` = **4 vCPU**。
>
> | 枠 | apply 失敗時 | プラン変更 + 申請後（現在） |
> |---|---|---|
> | `TotalDedicatedCores` | 0 / 20 ✅ | 0 / 20 |
> | `standardDSv2Family`（dedicated） | 0 / 6 ✅ | 0 / 6 |
> | **`TotalLowPriorityCores`** | **0 / 0** ❌ | **0 / 8** ✅ |
> | `standardDSv2Family`（lowPriority） | 0 / **-1** | 0 / -1 |
>
> ⚠️ **`-1` は「無制限」ではない。** 効くのは総枠のほうで、family が `-1` でも
> 総枠が 0 なら1ノードも作れない。**family 単位の `limit` だけ見て判断しない**
> （2026-08-01 に実際に誤読し、apply を1回失敗させた）。見るべきは
> `TotalDedicatedCores` / `TotalLowPriorityCores` の**総枠**と、クラスタの `vm_priority`
> がどちらを引くか。
>
> **解決は2手**（順序が逆だと通らない）:
>
> 1. **プランを Free → Basic に変更**。これで quota 引き上げ申請が受理されるようになる
>    （変更前は同じ申請が即 `QuotaNotAvailableForResource` で拒否された）
> 2. `az quota update --resource-name TotalLowPriorityCores ... value=8`（§1）。約90秒で承認
>
> これで **`vm_priority = "LowPriority"`（Spot 相当）で完走できた**。Vertex の Spot と
> 実行形態が揃う。枠が足りない環境では `"Dedicated"` へ落とす（dedicated は既定で足りる）。
>
> ### 効かない対処（実測で否定済み）
>
> | 案 | 結果 |
> |---|---|
> | リージョン変更（eastus 等） | **無効**。offer レベルの制限でリージョン単位ではない |
> | `az ml compute update-quota` | **無効**。subscription プール内の再配分で、プールが 0 なら動かない |
> | serverless compute へ切替 | **無効**。同じ BatchAI 枠を消費する |
> | VM サイズを小さくする | **無効**。総枠が 0 なら何 vCPU でも通らない |
> | 反映前に quota 申請を投げ直す | **無効**。offer が変わるまで同じエラーで弾かれる |
> | **Free プランのまま** `az quota update` で low-priority 枠を引き上げ | **無効**。即 `QuotaNotAvailableForResource` |
> | family 単位（`--resource-type lowPriority`）で申請 | **無効**。総枠 `TotalLowPriorityCores` でしか申請できない |
>
> 効いた対処は2つだけ: **`vm_priority` を `Dedicated` に落とす**（応急）か、
> **プランを Basic にして総枠を申請する**（本手）。

| | |
|---|---|
| Tier | A（コンテナ実行型・統一単位 = 学習イメージ） |
| ENV / PLATFORM | `azure-dev` / `azureml` |
| リージョン | japaneast（Vertex は us-central1・SageMaker は ap-northeast-1。差は注記に残す） |
| **アカウント / プラン** | 個人アカウント（Default Directory）。**従量課金 Pay-As-You-Go**（2026-08-01 に無料試用版からアップグレード）。同日プランを **Free → Basic** へ変更。**この変更後に quota 引き上げ申請が受理されるようになった**（変更前は即拒否） |
| **サブスクリプション** | `Azure subscription 1`。offer `PayAsYouGo_2014-09-01` / `spendingLimit=Off`。ID・アカウント名は [credentials.md §4](./credentials.md)（値は書かない） |
| **クレジット** | ¥32,777 を引き継ぎ・**失効 2026-08-30**。以降は実費請求。請求日は毎月9日 |
| **計算枠（quota）** | **dedicated**: `TotalDedicatedCores 0/20`・`standardDSv2Family 0/6`（既定のまま）。**low-priority: `TotalLowPriorityCores 0/8`** — 当初 `0/0` だったのを Basic プラン化後に申請して引き上げた（2026-08-01）。実需は 4 vCPU（`Standard_DS3_v2` × 1ノード）。申請可否と手順は §1 |
| **ガードレール** | `spendingLimit=Off` で**自動停止なし**。予算アラート（RG スコープ・月次 ¥2,000）は Doppler の `MCML_TF_BUDGET_EMAIL` で有効化（§2）。撤収手順は §9 |
| 資格情報 | `AZURE_TENANT_ID` / `AZURE_CLIENT_ID` / `AZURE_CLIENT_SECRET` / `AZURE_SUBSCRIPTION_ID`。[credentials.md §4](./credentials.md) |
| 実装 | `src/platforms/azureml/adapter.py`（SDK v2 = azure-ai-ml）/ `docker/training/entrypoint_azureml.sh` |
| 位置づけ | **条件付き**。Tier A 3つ目で限界効用が最も低く、Phase 3 完了時に go/no-go を判断する |

共通の前提・8項目の定義・停止条件は [README.md](./README.md)。以下は Azure ML 固有分のみ。

## 0. 着手前に埋める穴（2026-08-01 に実施済み）

| 穴 | 状態 |
|---|---|
| Blob 用 ArtifactStore 未実装 | ✅ `BlobArtifactStore` を実装（`abfs://<container>/<prefix>` 形式で他2基盤と同じ形） |
| 残留検査のクライアント未配線 | ✅ `_default_azure_clients()` を配線（`MLClient` + `KeyVaultManagementClient`。scope は terraform outputs から） |

### Azure だけに要る前提（Phase 1 の GCP には無かったもの）

apply の前に**サブスクリプション側の準備が2つ**要る。どちらも Terraform 管理外。

**(a) リソースプロバイダの登録**（GCP の API 有効化に相当。GCP はこれを IaC が持つが Azure は持たない）

```bash
for ns in Microsoft.Compute Microsoft.Storage Microsoft.KeyVault \
          Microsoft.ContainerRegistry microsoft.insights \
          Microsoft.MachineLearningServices Microsoft.Quota; do
  az provider register --namespace "$ns"
done   # Registered になるまで数分待つ

# 確認（全て Registered であること）
for ns in Microsoft.Compute Microsoft.Storage Microsoft.KeyVault \
          Microsoft.ContainerRegistry microsoft.insights \
          Microsoft.MachineLearningServices Microsoft.Quota; do
  printf "%-38s %s\n" "$ns" "$(az provider show -n $ns --query registrationState -o tsv)"
done
```

未登録のまま apply すると quota が 0 に見え、原因が quota 不足に誤読される。
**`Microsoft.Quota` は §1 の枠確認そのものに要る**（未登録だと `az quota show` が
`MissingRegistrationForResourceProvider` を返し、枠を読む手段が無くなる。2026-08-01 に実際に踏んだ）。

**(b) tfstate 用ストレージのブートストラップ**（GCP のバケットと同じ chicken-and-egg）

```bash
az group create --name mcml-tfstate-rg --location japaneast
az storage account create --name <一意な名前> --resource-group mcml-tfstate-rg \
  --location japaneast --sku Standard_LRS --kind StorageV2 \
  --min-tls-version TLS1_2 --allow-blob-public-access false
az storage container create --name tfstate --account-name <同上> --auth-mode login

terraform -chdir=infra/environments/azure-dev init \
  -backend-config="resource_group_name=mcml-tfstate-rg" \
  -backend-config="storage_account_name=<同上>" \
  -backend-config="container_name=tfstate"
```

### 失敗した apply は孤児 compute を残す（再 apply を塞ぐ）

`azurerm_machine_learning_compute_cluster` の作成は非トランザクショナル。
quota で失敗しても **ARM 側には `provisioning_state=Failed` の実体が残り**、
Terraform state には入らない。次の apply は quota 検証まで到達せず
`already exists - needs to be imported` で落ちるため、**本当の原因が隠れる**。

再実行の前に必ず消す。**workspace 名はランダムサフィックス付き**なので手打ちせず state から取る:

```bash
RG=$(terraform -chdir=infra/environments/azure-dev output -raw resource_group_name)
WS=$(terraform -chdir=infra/environments/azure-dev output -raw workspace_name)
az ml compute list -g "$RG" -w "$WS" -o table          # State=Failed が残っていないか
az ml compute delete -n mcml-dev-cpu -g "$RG" -w "$WS" --yes
```

2026-08-01 の LowPriority quota 失敗でも実際に `Failed` の孤児が残り、削除してから
再 apply した（この節の手順が2回目の実測で裏付いた）。

## 1. 着手前チェック

### offer 反映確認（着手条件・これが通らないと §2 以降は必ず失敗する）

```bash
SUB=$(az account show --query id -o tsv)
az rest --method get \
  --url "https://management.azure.com/subscriptions/$SUB?api-version=2020-01-01" \
  --query subscriptionPolicies -o json
# 期待: quotaId=PayAsYouGo_2014-09-01 / spendingLimit=Off
```

反映は数分。待つならポーリングする（無反応なら最大 24 時間見る）:

```bash
for i in $(seq 1 40); do
  az rest --method get --url "https://management.azure.com/subscriptions/$SUB?api-version=2020-01-01" \
    --query subscriptionPolicies -o tsv
  sleep 45
done
```

### 計算枠の実効値（`az quota show` の limit ではなくこちらを見る）

**枠の種別（`.type`）を必ず一緒に出す。** 名前だけでは dedicated と lowPriority の
同名エントリが区別できず、`TotalLowPriorityCores` を見落とす:

```bash
az rest --method get --url \
  "https://management.azure.com/subscriptions/$SUB/providers/Microsoft.MachineLearningServices/locations/japaneast/usages?api-version=2024-04-01" \
  -o json | jq -r '.value[] | select(.name.value | test("^Total|DSv2"))
                   | "\(.type | split("/")[-2])\t\(.name.value)\t\(.currentValue)/\(.limit)"' \
  | column -t
```

実測 2026-08-01・**クラスタ稼働中の出力そのもの**（撤収後は `TotalClusters` が `0/200` に戻る）:

```
totalClusters          TotalClusters          1/200
totalDedicatedCores    TotalDedicatedCores    0/20      ← ✅ Dedicated なら実需 4 vCPU に足りる
dedicatedCores         standardDSv2Family     0/6       ← ✅
totalLowPriorityCores  TotalLowPriorityCores  0/8       ← ✅ 申請で 0 から引き上げた（下記）
lowPriorityCores       standardDSv2Family     0/-1      ← ⚠️ -1 は無制限ではない。上の総枠が効く
```

**この枠の値はサブスクリプションに残る**（リソースを消しても戻らない）ので、
再開時も `TotalLowPriorityCores 0/8` から始められる。§9 段階2 で戻した場合のみ再申請が要る。

判定は3つ:

1. `currentValue` が `null` でない（`null` なら offer 未反映。§前節へ戻る）
2. **クラスタの `vm_priority` が引く方の総枠**が実需（VM の vCPU × `max_nodes`）以上
   （`Dedicated` → `TotalDedicatedCores` / `LowPriority` → `TotalLowPriorityCores`）
3. その family の同 priority エントリが 0 でない

`az vm list-usage`（`Microsoft.Compute` 枠）は**この判定に使えない**。
`Total Regional Low-priority vCPUs 0/3` と別の数字を返すため、AML 側の総枠 0 に辿り着けない。

### quota 引き上げ申請（足りない場合）

**申請できるのは総枠だけ。しかも枠ごとに可否が違う**（2026-08-01 実測）:

| 対象 | `az quota update` | 備考 |
|---|---|---|
| `TotalLowPriorityCores` | ✅ **通る** | `--resource-type` は付けない |
| `standardDSv2Family` + `--resource-type lowPriority` | ❌ `QuotaNotAvailableForResource` | family 単位は不可 |
| `TotalDedicatedCores` | ❌ `InvalidResourceName` | 集計値で申請対象外 |
| `standardDSv2Family` + `--resource-type dedicated` | ❌ `QuotaNotAvailableForResource` | family 単位は不可 |

```bash
AML="/subscriptions/$SUB/providers/Microsoft.MachineLearningServices/locations/japaneast"
az quota update --resource-name TotalLowPriorityCores --scope "$AML" --limit-object value=8
```

**このコマンドは同期的に完了を待つ**（実測 約90秒）。`az` 側のタイムアウトで切れても
**申請は生きている**ので、必ず履歴で確認する:

```bash
az quota request status list --scope "$AML" -o table
az quota request status show --name <requestId> --scope "$AML" \
  --query "{s:provisioningState,m:message}" -o tsv
# InProgress → Succeeded になれば反映済み。上の実効値コマンドで再確認する
```

⚠️ **申請が通るかは契約プランに依存する。** 同じコマンドが、プラン変更前は即
`QuotaNotAvailableForResource` で拒否され、**プランを Free → Basic に変更した後は
`InProgress` で審査に乗り約90秒で承認された**（`TotalLowPriorityCores` 0 → 8）。
拒否されたら quota の問題ではなく**契約段階の問題**として扱う。

### その他

```bash
doppler run -- az account show                  # subscription の確認
doppler run -- az vm list-usage --location japaneast -o table | grep -i core
make PLATFORM=azureml deps-platform             # azure-ai-ml + azure-identity
make test
```

- [ ] **offer が `PayAsYouGo_2014-09-01` になっている**（上記。最優先の着手条件）
- [ ] **`make PLATFORM=azureml deps-platform` 済み**。`make deps` は基盤 SDK を入れない（学習コンテナを太らせないため基盤ごとに別 extra）
- [ ] go/no-go で go と判断した記録が `docs/decisions/decision-log.md` にある
- [ ] **AML 枠の実効値**を上の3判定で確認した（`currentValue` が返る / `vm_priority` が引く総枠が実需以上 / family が 0 でない）
- [ ] **`compute_cluster_vm_priority` が `Dedicated`** になっている（`LowPriority` は総枠 0 で必ず失敗する）
- [ ] 上記§0の穴2つを塞いだ

quota が足りずマシンサイズを落とした場合、**揃えられなかった事実を記録する**
（`docs/comparison/00_method.md`「条件が揃わない箇所」に追記）。

## 2. ① terraform apply

**apply は対話端末で実行する。** `scripts/run_terraform.py` は `-auto-approve` を**自分では
付けない**（Heavy 操作は terraform 自身に確認させる設計）。素の `make tf-apply` を
CI・バックグラウンドジョブ・エージェント経由で回すと `error asking for approval: EOF` で落ち、
しかも **`infra_events` に `failure` 行が1件残って「apply 試行回数」を汚す**
（2026-08-01 に実際に発生。[04_azureml.md](../comparison/04_azureml.md) に除外の注記あり）。

非対話で回す必要があるときは、**保存済み plan を渡す**（Vertex / Snowflake と同じ手順）。
レビューした内容と適用内容が一致し、承認プロンプトも出ない:

```bash
PYTHONPATH=src doppler run -- .venv/bin/python scripts/run_terraform.py \
  plan --env azure-dev -out=/tmp/azure-dev.tfplan
PYTHONPATH=src doppler run -- .venv/bin/python scripts/run_terraform.py \
  apply --env azure-dev /tmp/azure-dev.tfplan
```

⚠️ **plan も `run_terraform.py` 経由で回す。** 素の `terraform plan` は
config.yaml / Doppler 由来の `-var` を受け取らないため、**変数の抜けた plan が
保存され、apply がそれを適用してしまう**（2026-08-01 実測: 予算アラートが落ちて
`Plan: 9 to add`。正しくは 10）。plan は `infra_events` に記録しない。

予算アラート（§9 段階3）を有効にするなら、**この apply で一緒に入れる**のが最小手数:

```bash
# 通知先は Doppler（MCML_TF_BUDGET_EMAIL）。未解決なら apply 前に落ちる。
make ENV=azure-dev tf-init && make ENV=azure-dev tf-plan && make ENV=azure-dev tf-apply
terraform -chdir=infra/environments/azure-dev output -json > artifacts/azure-dev.outputs.json
terraform -chdir=infra/environments/azure-dev output budget_enabled   # true を確認
```

Workspace は **Storage / Key Vault / App Insights / identity を必須で要求する**
（実測済み: azurerm v4.81.0。ACR は任意だが BYOC には要る）。
構造仮説「周辺依存で初期構築量が最大」をここで実測検証する
（apply の所要とリソース数が `infra_events` に入る）。

`config.py` が読む outputs: `subscription_id` / `resource_group_name` / `workspace_name` /
`compute_cluster_name` / `container_registry_login_server`。

### apply の実行（5基盤共通）

**Terraform 変数は `export` しない。** `env/config.yaml` の `terraform:` 節（人が決める値）と
Doppler（秘密・個人識別子）から `run_terraform.py` が組み立てる（対応表は
`src/platforms/terraform_vars.py`）。解決できない変数があれば **terraform を起動する前に**
名前を挙げて落ちる。

対話端末があるなら:

```bash
make ENV=azure-dev tf-init && make ENV=azure-dev tf-plan && make ENV=azure-dev tf-apply   # yes を入力
```

**非対話（CI・バックグラウンド・エージェント）は保存済み plan を渡す。**
素の `tf-apply` は承認プロンプトが `EOF` で落ちるうえ、**`infra_events` に偽の
failure 行が残って「apply 試行回数」を汚す**（2026-08-01 に実際に発生）。
plan 無しで非対話実行するとガードが `EXIT_USAGE` で止める:

```bash
terraform -chdir=infra/environments/azure-dev plan -out=/tmp/azure-dev.tfplan
PYTHONPATH=src doppler run -- .venv/bin/python scripts/run_terraform.py \
  apply --env azure-dev /tmp/azure-dev.tfplan
```

destroy も同じ（`terraform destroy <plan>` は terraform 自身が拒否するので
`plan -destroy` の成果物を渡す。記録上の action は destroy のまま保たれる）:

```bash
PYTHONPATH=src doppler run -- .venv/bin/python scripts/run_terraform.py \
  plan --env azure-dev -destroy -out=/tmp/azure-dev-destroy.tfplan
PYTHONPATH=src doppler run -- .venv/bin/python scripts/run_terraform.py \
  destroy --env azure-dev /tmp/azure-dev-destroy.tfplan
```

## 3. 配布物の準備（自動化なし・手打ち）

```bash
make docker-build && make docker-build-serving
ACR=$(jq -r '.container_registry_login_server.value' artifacts/azure-dev.outputs.json)
doppler run -- az acr login --name "${ACR%%.*}"
docker tag mcml-training:latest "$ACR/training:latest" && docker push "$ACR/training:latest"
docker tag mcml-serving:latest  "$ACR/serving:latest"  && docker push "$ACR/serving:latest"
```

学習データは既定で Workspace 既定データストアの相対パス
（`azureml://datastores/workspaceblobstore/paths/data/california_housing`）を見る。

```bash
SA=$(jq -r '.storage_account_name.value' artifacts/azure-dev.outputs.json)
CT=$(az storage container list --account-name "$SA" --auth-mode login \
       --query "[?starts_with(name,'azureml-blobstore')].name" -o tsv)   # 名前は GUID 付き
az storage blob upload-batch --account-name "$SA" --auth-mode login \
  --destination "$CT" --destination-path "data/california_housing" \
  --source data --pattern "california_housing.*" --overwrite
```

⚠️ **サブスクリプションの Owner でもこれは通らない。** Azure は制御プレーンと
データプレーンの権限が分かれており、Blob には別途ロールが要る:

```
You do not have the required permissions needed to perform this operation.
    "Storage Blob Data Contributor" ...
```

**Storage Account スコープに1つだけ**足す（RG やサブスクリプションへ広げない）:

```bash
OID=$(az ad signed-in-user show --query id -o tsv)
SAID=$(az storage account show -n "$SA" -g mcml-dev-rg --query id -o tsv)
az role assignment create --assignee-object-id "$OID" --assignee-principal-type User \
  --role "Storage Blob Data Contributor" --scope "$SAID"
# データプレーンの RBAC 伝播を **blob 操作で** 待つ（実測 約40秒）:
until az storage blob list --account-name "$SA" --auth-mode login -c "$CT" --num-results 1 -o none 2>/dev/null
do sleep 20; done
```

⚠️ **`az storage container list` で待たない。** コンテナ一覧は付与直後から通るのに
blob 操作はまだ弾かれるため、**待ちループが素通りして次のコマンドが落ちる**
（2026-08-01 実測: container list は即成功、blob list は3回目のポーリングで成功）。

⚠️ **この付与は apply のたびに要る。** Storage Account 名はランダムサフィックス付きで
作られるため、再構築するとロール割り当ては対象ごと消える。「1回やれば済む初期設定」
ではなく **`make distribute` の前提手順**として扱う（2026-08-01 の2回目の構築で再発）。

この1件が Azure の permission friction の実測値（`docs/comparison/04_azureml.md`）。

## 4. ②〜⑤ フェーズ実行

```bash
make PLATFORM=azureml phase-train
make collect
make PLATFORM=azureml phase-register
make PLATFORM=azureml phase-deploy      # ⚠️ 常時課金
make PLATFORM=azureml phase-predict
```

| # | 何を見て成功と判定するか |
|---|---|
| ② | Command Job が `Completed`。`ml_runs` に stage=train |
| ③ | `write_path='direct'`（Tier A の仮説）。届かなければ JSONL fallback を回収 |
| ④ | Model が登録され、**バージョンが自動採番**される（Vertex の alias / SageMaker の承認とは3者3様） |
| ⑤ | `/score` が 200。`ml_runs` に stage=predict |

### 中断したら `resume` で再開する（train をやり直さない）

学習は通ったがその後で止まった場合、**引数なしの `resume` が Neon から
前段の成果物を引いて register 以降を通す**:

```bash
doppler run -- .venv/bin/python scripts/run_phase.py azureml resume
```

`resume` は `ml_runs` の直近の成功した学習 run から `model_artifact_uri` を引く
（`-- 再開元 train run=... artifact=...` と表示される）。
**別の学習コードで作られた成果物は掴まない** —— `src/core/ml` の tree hash が
現在の checkout と一致しない run は拒否して停止する（commit SHA では判定しない。
5基盤を順に回すと SHA は基盤ごとに変わるが学習コードは同一のため）。

`--artifact-uri` / `--model-version` を明示した場合は**そちらが常に勝つ**。

> **2026-08-01 以前はこれができなかった。** 学習の成功行は**ジョブ側**が書く規約で、
> ジョブは自分の成果物がどの URI で参照されるかを知らない。そのため
> `ml_runs` の train 行は5基盤とも `params={}` で、`phase-register` は
> `成果物 URI が無い`、`phase-deploy` は `参照が未解決` で単体実行できず、
> **中断のたびに train からやり直し**だった（実測: Azure で1回踏んだ）。
> 現在は adapter が成功行へ params を**追記**する（行が無ければ何もしない。
> 行を作ると `write_path='direct'` を騙るため）。

**イメージを差し替えたときは先に teardown する。** 同一タグ（`:latest`）で push しても
既存 deployment は古いダイジェストを掴んだままなので、`phase-teardown` →
`phase-all` の順で回す（teardown は約 5.5 分）。

なお `teardown` は `ml_runs` に **`stage=deploy` + `params.action=teardown`** として記録される
（`Stage` enum は train/register/deploy/predict の4値のみ。Vertex も同じ設計）。
集計時に deploy 件数へ混ざるので注意する。

## 5. Azure ML 固有の確認

- **入出力がジョブ定義側のマウント宣言**（`${{inputs.x}}` / `${{outputs.y}}`）。
  Vertex は環境変数（`AIP_MODEL_DIR`）、SageMaker は固定パス（`/opt/ml`）。**3者3様**で、
  Tier A 内でも実行契約が揃っていないことがこのフェーズの主要な発見になる。
- **推論が2階層 + traffic の3手目**。Endpoint を作り、Deployment を作り、
  トラフィック配分を**別操作**で 100% にする。ここを忘れると 404 になる。
- **1件推論がファイル渡し**（`invoke` の `request_file`）。他2基盤は文字列 / 辞書で渡せる。
  adapter は一時ファイルへ書いてから渡している。
- 推論契約は `/score`（liveness は `/health`）、port 8080。

## 6. 失敗時の切り分け

| failure_class | 典型 | 対処 |
|---|---|---|
| `quota` | **`ClusterMinNodesExceedCoreQuota`（"total vCPU quota of 0"）** | **最も出やすい**。まず `vm_priority` が引く総枠（`TotalLowPriorityCores` / `TotalDedicatedCores`）を §1 で確認する。**VM サイズを落としても無駄**（総枠が 0 なら何 vCPU でも通らない）。応急は `Dedicated` へ、本手は総枠の申請（§1） |
| `quota` | `ResourceNotAvailableForOffer` / `QuotaNotAvailableForResource` | offer 側の壁。無料試用版なら Pay-As-You-Go へのアップグレードが唯一の経路（§冒頭）。反映前は何を投げても弾かれる |
| `permission` | サービスプリンシパルに Workspace / ACR / Storage のロール不足 | 1つずつ付与 |
| `container` | マウント宣言とシムのパス不一致 | `entrypoint_azureml.sh` と job の inputs/outputs 名 |
| `sdk` | SDK v2 のエンティティ生成失敗 | `_Entities` の遅延 import が解決できているか |

## 7. ⑥⑦ teardown と残留検査

```bash
make PLATFORM=azureml phase-teardown
make ENV=azure-dev tf-destroy
```

| kind | severity | 2026-08-01 実測 |
|---|---|---|
| `online_endpoint` | FAIL | **0件**（`phase-teardown` で削除済み・332.5s） |
| `key_vault_soft_deleted` | FAIL | **0件**。`purge_soft_delete_on_destroy = true` が destroy 時に purge する（`versions.tf`）。手動 purge は不要だった |
| `registered_model` | WARN | **0件**（RG ごと削除） |
| **IaC 管理外の自動生成** | — | **1件**。`Application Insights Smart Detection` |

### destroy は1回では終わらない（RG だけ残る）

`Application Insights Smart Detection` は **App Insights 作成時に Azure が自動で作る**
アラートルールで **Terraform 管理外**。これが RG に残り、RG の削除が拒否される
（実測 817.5s で失敗。以前の 760.9s と同じ壁）。

**これはバグではなくガードレールが働いた結果。** `versions.tf` で
`prevent_deletion_if_contains_resources = true` を明示している
（「残留を静かに握り潰すと `check_residual.py` の一次データが歪む」ため）。
`false` にすれば1回で終わるが、**それは残留を隠すことになる**ので変えない。

手順は §9 段階1 と同じ:

```bash
az group delete --name mcml-dev-rg --yes          # 自動生成分ごと消える
make ENV=azure-dev tf-destroy                     # state を実態と同期（0 destroyed / 22.8s）
az group list -o table                            # mcml-tfstate-rg だけ残るのが正
```

⚠️ **`make check-residual` は完全撤収後に使えない。** RG が消えていると列挙できず
`ResourceGroupNotFound` を **ERROR** として返し、「残留 0 件」と区別がつかない。
撤収後の確認は上の `az group list` と `az keyvault list-deleted` を直接見る。

## 8. ⑧ レポート記述

[docs/comparison/04_azureml.md](../comparison/04_azureml.md) を埋める。
「周辺依存で初期構築量が最大」の仮説が当たったか（apply リソース数・所要）と、
Tier A 3基盤で実行契約が3者3様だった事実を中心に書く。

**2026-08-01 に記述済み。** 予想は3つ外れた（初期構築量・Key Vault の残留・destroy 1回完了）。
外れたこと自体が計測値なので、予想は書き換えず「予想 → 実測 → 差分」で残してある。

## 9. 検証終了後のダウングレード

**2026-08-01 に段階1まで実行済み**（`mcml-dev-rg` 削除完了・課金停止）。
以下は再度 Azure を立てた場合の撤収手順。**デッドラインは 2026-08-30**（クレジット失効日）。
これを過ぎると残っているリソースは実費請求に切り替わる。

### 前提を2つ間違えない

1. **Pay-As-You-Go から無料試用版へは戻せない。** Microsoft が経路を提供していない。
   「下げる」＝ 契約を戻すことではなく、**課金源を断つこと**。
2. **quota は上限であって課金源ではない。** 引き上げたまま放置しても ¥0。
   だから最優先は段階1のリソース削除で、quota を戻すのは
   「誤 apply 時の被害上限を戻す」ための任意手順にすぎない。

### 段階の選択

| 段階 | いつ | 効果 | 可逆性 |
|---|---|---|---|
| 1. リソース削除 | Phase 4 完了時（**必須**） | 課金が止まる。これだけで月次 ¥0 | 可逆（再 apply） |
| 2. quota を申請前へ戻す | 同上（任意） | 誤 apply 時の被害上限が戻る | 可逆 |
| 3. 予算アラート | Azure を残す場合 | `spendingLimit` の代替 | — |
| 4. サブスクリプション取り消し | Azure から完全撤退する場合 | 全リソース停止 | 90日以内なら復活可 |

### 段階1: リソース削除（必須）

```bash
make PLATFORM=azureml phase-teardown    # Endpoint/Deployment を先に落とす（常時課金）
make ENV=azure-dev tf-destroy           # check-residual まで走る
```

destroy が失敗した場合（実測あり: 760.9s で失敗 → `az group delete` で完了）:

```bash
az group delete --name mcml-dev-rg --yes --no-wait
az keyvault list-deleted --query "[].name" -o tsv   # 論理削除の残り
az keyvault purge --name <name>                     # 同名再 apply をブロックするので必ず purge
make check-residual
az group list -o table                              # mcml-tfstate-rg だけ残るのが正
```

**`mcml-tfstate-rg` は消さない。** terraform state の置き場で、消すと再構築時に state を失う。
Storage Account 1個で月あたり数円。

### 段階2: quota を申請前の値へ戻す（任意）

**引き上げた実績があるのは `TotalLowPriorityCores` の 0 → 8 だけ**（2026-08-01）。
戻すならこれ1本:

```bash
SUB=$(az account show --query id -o tsv)
AML="/subscriptions/$SUB/providers/Microsoft.MachineLearningServices/locations/japaneast"
az quota update --resource-name TotalLowPriorityCores --scope "$AML" --limit-object value=0
az quota request status list --scope "$AML" -o table   # Succeeded を確認
```

- 他の枠（`TotalDedicatedCores 20` / `standardDSv2Family` dedicated `6`）は
  **既定値のまま触っていない**ので戻す対象が無い。
- 課金には影響しない（quota は上限であって課金源ではない）。目的は
  **誤 apply で 8 vCPU 分の Spot ノードが立つ余地を消す**こと。急ぐ手順ではない。

### 段階3: 予算アラート（PAYG に spending limit は無い）

無料試用版の `spendingLimit=On` に相当する**自動停止は PAYG には存在しない**。
代替は予算アラートだが、これは**通知のみで停止はしない**。止めるのは段階1が唯一の手段。

**`az` で作らない。Terraform 側に既にある**
（`azurerm_consumption_budget_resource_group.monthly_guardrail`。
月次 ¥2,000・実績 50% / 90% / 予測 100% の3通知）。
`budget_notification_email` が空だと `count = 0` で作られないので、環境変数で渡して apply する:

```bash
# 通知先は Doppler（MCML_TF_BUDGET_EMAIL）
make ENV=azure-dev tf-plan && make ENV=azure-dev tf-apply
terraform -chdir=infra/environments/azure-dev output budget_enabled   # true になること
```

**`spendingLimit` が Off になった今、これを入れないとガードレールが1つも無い。**
2026-08-01 の判断で、Phase 4 の apply から有効化する方針とした（§2 のコマンドに含めてある）。
金額の根拠は [08_release_runbook.md](../08_release_runbook.md) の Tier A ¥2,000/月。

なお**ダウングレード時に予算だけ残す意味は無い**。段階1で RG ごと消えるため
（`azurerm_consumption_budget_resource_group` は RG スコープ）、予算も一緒に消える。

### 段階4: サブスクリプションの取り消し（完全撤退）

```bash
az account subscription cancel --id "$SUB" --yes    # 要 account 拡張（experimental）
```

ポータルなら「サブスクリプション」→ 対象 →「サブスクリプションの取り消し」。

- 取り消すと全リソースが停止し、**90日後に完全削除**される。それまでは
  `az account subscription enable` で戻せる。
- **`mcml-tfstate-rg` も一緒に消える。** 他基盤の検証が残っている間は実行しない。
- owner のみが判断する（[capability-boundary.md](../specs/capability-boundary.md)）。

### やらないこと

| 案 | なぜ不可 |
|---|---|
| 無料試用版へ戻す | 経路が存在しない |
| クレジットの延長 | 不可。2026-08-30 で失効する。期限内に検証を終える以外にない |
| 予算アラートで自動停止 | 通知のみ。停止はしないので段階1の代わりにならない |
