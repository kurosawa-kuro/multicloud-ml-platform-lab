"""terraform の計測ラッパ（scripts/run_terraform.py）の検証。

**terraform を実行しない**（subprocess を注入する）。ここが無いと
Golden Path ステップ1「apply の所要・リソース数が infra_event に記録される」が
空のままになり、基盤別の初期構築コストを比較できない。

守る不変条件:
  - apply は added、destroy は destroyed の件数を読む
  - **読めなかったときに 0 と記録しない**（「作らなかった」と「読めなかった」は別）
  - `-auto-approve` を付けない（Heavy 操作は terraform 自身に確認させる）
  - 記録に失敗しても terraform の終了コードをそのまま返す
"""

from __future__ import annotations

from collections.abc import Mapping

import pytest
from tests.conftest import REPO_ROOT, load_script

from core.telemetry.schemas import InfraAction, InfraEvent, Platform, Status

run_terraform = load_script("run_terraform")


@pytest.fixture(autouse=True)
def _terraform_vars(monkeypatch: pytest.MonkeyPatch):
    """Doppler が供給する Terraform 変数を模す。

    修正04 以降、`run_terraform()` は必須変数が解決できないと
    **terraform を起動する前に**落ちる（それが目的）。ここで与えるのは
    実運用で Doppler から入る値と同じ位置づけのもの。
    """
    for name in (
        "MCML_TF_VERTEX_SUBMITTER_EMAIL",
        "MCML_TF_BUDGET_EMAIL",
        "MCML_TF_DBX_JOB_PRINCIPAL",
        "MCML_TF_SF_GRANT_TO_USER",
        # 修正09 でガードレール（予算・請求先）を必須化した
        "MCML_TF_BILLING_ACCOUNT_ID",
    ):
        monkeypatch.setenv(name, "someone@example.invalid")
    # gcp-dev の project_id は TF の default 直書きをやめ Doppler 由来にした
    # （実プロジェクト ID を repo に焼かないため）。SDK 標準名の env をそのまま使う。
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "example-gcp-project")


APPLY_OUTPUT = """
google_storage_bucket.artifacts: Creation complete after 2s

Apply complete! Resources: 12 added, 0 changed, 0 destroyed.

Outputs:
gcs_bucket = "mcml-dev-abc123"
"""

DESTROY_OUTPUT = """
Destroy complete! Resources: 12 destroyed.
"""


class FakeRunner:
    """stream_command の代役（(終了コード, 全出力) を返す契約）。"""

    def __init__(self, returncode: int, output: str = "") -> None:
        self._result = (returncode, output)
        self.commands: list[list[str]] = []
        self.environments: list[Mapping[str, str]] = []

    def __call__(self, command: list[str], env: Mapping[str, str]) -> tuple[int, str]:
        self.commands.append(command)
        self.environments.append(env)
        return self._result


class SpySink:
    def __init__(self, *, explode: bool = False) -> None:
        self.events: list[InfraEvent] = []
        self._explode = explode

    def record_infra_event(self, event: InfraEvent) -> int:
        if self._explode:
            raise ConnectionError("Neon unreachable")
        self.events.append(event)
        return 1


# --- リソース数の読み取り -------------------------------------------------


def test_apply_counts_added_resources() -> None:
    assert run_terraform.parse_resource_count(APPLY_OUTPUT) == 12


def test_destroy_counts_destroyed_resources() -> None:
    assert run_terraform.parse_resource_count(DESTROY_OUTPUT) == 12


def test_unreadable_output_is_none_not_zero() -> None:
    """0 と混同すると「1つも作らなかった apply」に見える。"""
    assert run_terraform.parse_resource_count("Error: quota exceeded") is None


# --- terraform の呼び出し形 -----------------------------------------------


def test_command_targets_the_environment_directory() -> None:
    runner = FakeRunner(0, APPLY_OUTPUT)

    code, output, elapsed = run_terraform.run_terraform("apply", "gcp-dev", runner=runner)

    assert runner.commands[0][:3] == ["terraform", "-chdir=infra/environments/gcp-dev", "apply"]
    assert (code, "Apply complete!" in output) == (0, True)
    assert elapsed >= 0


def test_auto_approve_is_never_added() -> None:
    """apply / destroy は owner approval 前提（.claude/rules/terraform.md）。"""
    runner = FakeRunner(0, APPLY_OUTPUT)

    run_terraform.run_terraform("destroy", "sf-dev", runner=runner)

    assert "-auto-approve" not in runner.commands[0]


def test_extra_args_are_forwarded() -> None:
    runner = FakeRunner(0, APPLY_OUTPUT)

    run_terraform.run_terraform("apply", "aws-dev", extra_args=["-var-file=x"], runner=runner)

    assert runner.commands[0][-1] == "-var-file=x"


def test_stream_command_shows_prompts_without_trailing_newline(
    capfd: pytest.CaptureFixture[str],
) -> None:
    """改行なしの承認プロンプトが**リアルタイムで見える**こと。

    行バッファ読みだと "Enter a value:" が沈んだまま入力待ちになり、
    ユーザーは何を待たれているのか分からない（capture_output の再発防止）。
    実プロセスで検証する（printf は改行を出さない）。
    """
    code, output = run_terraform.stream_command(["bash", "-c", "printf 'Enter a value: '; exit 3"])

    assert code == 3
    assert output == "Enter a value: "
    assert capfd.readouterr().out == "Enter a value: "


def test_stream_command_merges_stderr_into_the_transcript() -> None:
    """Error: 行（stderr）も記録側の全文に含まれること（失敗原因を残す）。"""
    code, output = run_terraform.stream_command(
        ["bash", "-c", "echo plan-line; echo 'Error: quota' >&2; exit 1"]
    )

    assert code == 1
    assert "plan-line" in output
    assert "Error: quota" in output


# --- infra_events への記録 ------------------------------------------------


def test_event_carries_duration_and_resource_count() -> None:
    sink = SpySink()

    recorded = run_terraform.record_event(
        Platform.VERTEX,
        InfraAction.APPLY,
        status=Status.SUCCESS,
        duration_seconds=42.5,
        resource_count=12,
        sink=sink,
    )

    assert recorded
    event = sink.events[0]
    assert (event.platform, event.action, event.status) == (
        Platform.VERTEX,
        InfraAction.APPLY,
        Status.SUCCESS,
    )
    assert (event.duration_seconds, event.resource_count) == (42.5, 12)
    assert event.event_id  # uuid 列。同じ検査を2回流しても別行で残る


def test_failed_apply_is_recorded_as_failure() -> None:
    sink = SpySink()

    run_terraform.record_event(
        Platform.AZUREML,
        InfraAction.APPLY,
        status=Status.FAILURE,
        duration_seconds=3.0,
        resource_count=None,
        sink=sink,
    )

    assert sink.events[0].status is Status.FAILURE
    assert sink.events[0].resource_count is None


def test_recording_failure_does_not_raise() -> None:
    """telemetry は非致命。Neon へ届かなくても terraform の結果は残る。"""
    assert (
        run_terraform.record_event(
            Platform.VERTEX,
            InfraAction.DESTROY,
            status=Status.SUCCESS,
            duration_seconds=1.0,
            resource_count=1,
            sink=SpySink(explode=True),
        )
        is False
    )


def test_every_environment_maps_to_a_platform() -> None:
    """environments/ を足したらここも足す（記録漏れの検出）。"""
    assert set(run_terraform.ENV_PLATFORM.values()) == set(Platform)


# --- CLI ------------------------------------------------------------------


def test_unknown_environment_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):  # argparse が choices で弾く
        run_terraform.main(["apply", "--env", "nope-dev"])


@pytest.mark.parametrize(
    "extra",
    [["-auto-approve"], ["-var-file=dev.tfvars"], ["tfplan"], ["-auto-approve", "-lock=false"]],
    ids=["auto-approve", "var-file", "saved-plan", "two-flags"],
)
def test_terraform_arguments_reach_the_command(extra: list[str]) -> None:
    """**フラグ形式も含めて** terraform へ通ること。

    位置引数の nargs="*" だけだと argparse が `-auto-approve` を未知のオプションと
    見なして exit 2 になり、非対話実行（保存済み plan の適用）ができない。
    関数を直接叩くテストではこの経路が検証できなかったので main() から見る。

    runner / sink は **引数で注入する**（monkeypatch にしない）。既定引数は定義時に
    束縛されるため、モジュール属性の差し替えでは効かず実 terraform が走る
    —— これで 2026-08-01 に gcp-dev へ意図しない apply が飛んだ。
    """
    seen: list[list[str]] = []

    def fake_run(command: list[str], env: Mapping[str, str]) -> tuple[int, str]:
        seen.append(command)
        return 0, APPLY_OUTPUT

    code = run_terraform.main(
        ["apply", "--env", "gcp-dev", *extra], runner=fake_run, sink=SpySink()
    )

    assert code == 0
    assert seen and seen[0][-len(extra) :] == extra


def test_injected_runner_is_actually_used() -> None:
    """注入した runner が使われること（= 実 terraform を起動しないことの担保）。

    このテストが緑でないと、他のテストが実クラウドを叩いていないと言い切れない。
    """
    calls: list[list[str]] = []

    run_terraform.run_terraform(
        "destroy", "gcp-dev", runner=lambda cmd, _env: (calls.append(cmd) or (0, DESTROY_OUTPUT))
    )

    # 修正04 以降、コマンドには config.yaml / Doppler 由来の `-var` が続く。
    # ここで見たいのは「注入した runner が実際に呼ばれたか」なので前3語を見る。
    assert len(calls) == 1
    assert calls[0][:3] == ["terraform", "-chdir=infra/environments/gcp-dev", "destroy"]
    assert "-var" in calls[0], "Terraform 変数が渡っていない"


def test_saved_destroy_plan_is_applied_but_recorded_as_destroy(tmp_path, monkeypatch) -> None:
    """`terraform destroy <plan>` は terraform 自身が拒否する。

    非対話 destroy は `plan -destroy -out` → **apply** で流すしかない。
    サブコマンドだけ差し替え、記録上の action は destroy のままにすること
    （infra_events の destroy 回数が apply に化けると撤退の比較が壊れる）。
    """
    plan = tmp_path / "dbx-destroy.tfplan"
    plan.write_text("plan")
    seen: list[list[str]] = []

    def fake_runner(command: list[str], env: Mapping[str, str]) -> tuple[int, str]:
        seen.append(command)
        return 0, "Destroy complete! Resources: 8 destroyed."

    monkeypatch.chdir(tmp_path)
    run_terraform.run_terraform("destroy", "dbx-dev", extra_args=[str(plan)], runner=fake_runner)

    assert seen[0][2] == "apply", "保存済み destroy plan は apply で流す"
    assert str(plan) in seen[0]


def test_destroy_counts_destroyed_even_when_applied_from_a_saved_plan() -> None:
    """保存済み destroy plan は apply で流すので完了行が `Apply complete!` になる。

    added を採ると **destroy が毎回 0 リソース**として記録され、撤退の比較が消える
    （2026-08-01 実測）。
    """
    output = "Apply complete! Resources: 0 added, 0 changed, 2 destroyed."

    assert run_terraform.parse_resource_count(output, "destroy") == 2
    assert run_terraform.parse_resource_count(output) == 0  # apply 経路は added のまま


# --- Terraform 変数の解決（2026-08-01 追加・修正04）------------------------
#
# runbook の `export TF_VAR_*` を config.yaml / Doppler に寄せた。
# 渡し忘れがクラウド側の失敗になる前に、ローカルで名前を挙げて落とす。


def test_config_section_supplies_non_secret_variables() -> None:
    from platforms.shared.terraform_vars import resolve

    config = {"terraform": {"dbx-dev": {"create_catalog": False, "catalog_name": "workspace"}}}

    resolved = resolve("dbx-dev", config, {"MCML_TF_DBX_JOB_PRINCIPAL": "someone@example.com"})

    assert resolved["create_catalog"] == "false"  # YAML の bool は小文字リテラルへ
    assert resolved["catalog_name"] == "workspace"


def test_missing_required_variable_names_where_to_set_it() -> None:
    from platforms.shared.terraform_vars import TerraformVarError, resolve

    with pytest.raises(TerraformVarError, match="MCML_TF_DBX_JOB_PRINCIPAL"):
        resolve("dbx-dev", {"terraform": {"dbx-dev": {}}}, {})


def test_optional_variable_may_stay_unset() -> None:
    """トライアルで作れない EAI 用の値などは未設定でも通る。"""
    from platforms.shared.terraform_vars import resolve

    resolved = resolve("sf-dev", {}, {"MCML_TF_SF_GRANT_TO_USER": "MCML_USER"})

    assert "neon_secret_string" not in resolved


def test_azure_spot_is_derived_from_common_use_spot() -> None:
    """Spot の指定を config.yaml 1箇所に保つ（修正03 の残り半分）。"""
    from platforms.shared.terraform_vars import derived_vars

    assert (
        derived_vars({"common": {"use_spot": True}}, "azure-dev")["compute_cluster_vm_priority"]
        == "LowPriority"
    )
    assert (
        derived_vars({"common": {"use_spot": False}}, "azure-dev")["compute_cluster_vm_priority"]
        == "Dedicated"
    )


def test_wheel_path_is_derived_not_configured() -> None:
    """dbx の wheel_path を手で書かない（pyproject + catalog/schema から組む）。"""
    from platforms.shared.packaging_names import wheel_filename
    from platforms.shared.terraform_vars import derived_vars

    config = {"terraform": {"dbx-dev": {"catalog_name": "workspace", "schema_name": "mcml_dev"}}}

    path = derived_vars(config, "dbx-dev")["wheel_path"]

    assert path == f"/Volumes/workspace/mcml_dev/artifacts/dist/{wheel_filename()}"


def test_saved_plan_does_not_receive_var_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    """保存済み plan は変数を焼き込み済み。`-var` を足すと terraform が拒否する。"""
    captured: list[list[str]] = []

    run_terraform = load_script("run_terraform")
    run_terraform.run_terraform(
        "apply",
        "dbx-dev",
        extra_args=["/tmp/dbx.tfplan"],
        runner=lambda cmd, _env: (
            captured.append(cmd),
            (0, "Apply complete! Resources: 1 added, 0 changed, 0 destroyed."),
        )[1],
    )

    assert "-var" not in captured[0]


# --- provider が拾ってしまう env の遮断（2026-08-01 追加）------------------
#
# snowflake provider 2.19 は deprecated な `account` フィールドとして
# SNOWFLAKE_ACCOUNT を拾い、**存在すること自体**で experiment を要求して落ちる
# （provider に organization_name/account_name を明示しても回避できない・実測）。
# 同じ env を Python connector が別形式で使うので Doppler からは消せない。
# 落とすのは terraform を起動する時だけ、という境界をここで固定する。


def test_snowflake_account_is_hidden_from_terraform_on_sf_dev() -> None:
    base = {"SNOWFLAKE_ACCOUNT": "org-acct", "SNOWFLAKE_USER": "u", "PATH": "/usr/bin"}

    result = run_terraform.terraform_environment("sf-dev", base)

    assert "SNOWFLAKE_ACCOUNT" not in result
    # provider v2 が読む2つと、それ以外の env は落とさない
    assert result == {"SNOWFLAKE_USER": "u", "PATH": "/usr/bin"}


@pytest.mark.parametrize("env", ["gcp-dev", "aws-dev", "azure-dev", "dbx-dev"])
def test_other_environments_keep_every_variable(env: str) -> None:
    """遮断は sf-dev 限定。他環境の env を削ると原因不明の解決失敗になる。"""
    base = {"SNOWFLAKE_ACCOUNT": "org-acct", "PATH": "/usr/bin"}

    assert run_terraform.terraform_environment(env, base) == base


def test_runner_receives_the_filtered_environment() -> None:
    """関数が正しくても、terraform 起動へ繋がっていなければ意味がない。"""
    runner = FakeRunner(0, APPLY_OUTPUT)

    run_terraform.run_terraform("plan", "sf-dev", runner=runner)

    assert "SNOWFLAKE_ACCOUNT" not in runner.environments[0]
    # 実 env をそのまま渡していること（空 env で起動すると PATH が消えて terraform が見つからない）
    assert "PATH" in runner.environments[0]


def test_snowflake_adapter_still_reads_the_account_env() -> None:
    """Doppler から消す解決策に将来倒れないための番人。

    adapter は SNOWFLAKE_ACCOUNT（`<org>-<account>` 形式）を使う。ここが落ちたら
    「terraform だけ落とす」という前提が崩れているので、遮断範囲を見直すこと。
    """
    source = (REPO_ROOT / "src/platforms/snowflake/adapter.py").read_text(encoding="utf-8")

    assert "SNOWFLAKE_ACCOUNT" in source


# --- 非対話ガード（2026-08-01 追加・修正06）-------------------------------
#
# 端末が無い場所で素の apply を叩くと terraform が EOF で落ち、しかも
# infra_events に failure 行が残って「apply 試行回数」を汚す（実際に発生した）。
# terraform を起動する前に落とす。


def test_non_interactive_without_plan_file_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_terraform.sys.stdin, "isatty", lambda: False)

    assert run_terraform.needs_a_terminal(["-lock=false"]) is True


def test_saved_plan_needs_no_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_terraform.sys.stdin, "isatty", lambda: False)

    assert run_terraform.needs_a_terminal(["/tmp/gcp-dev.tfplan"]) is False


def test_auto_approve_needs_no_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """明示的に -auto-approve を渡した場合は呼び出し側の判断に委ねる。"""
    monkeypatch.setattr(run_terraform.sys.stdin, "isatty", lambda: False)

    assert run_terraform.needs_a_terminal(["-auto-approve"]) is False


def test_interactive_terminal_is_always_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_terraform.sys.stdin, "isatty", lambda: True)

    assert run_terraform.needs_a_terminal([]) is False


def test_guard_records_nothing_and_exits_usage(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """ガードに掛かったとき infra_events へ1行も書かないこと（偽 failure の防止）。"""
    monkeypatch.setattr(run_terraform.sys.stdin, "isatty", lambda: False)
    recorded: list[object] = []

    class RecordingSink:
        def record_infra_event(self, event: object) -> None:  # pragma: no cover - 呼ばれない
            recorded.append(event)

    code = run_terraform.main(["apply", "--env", "gcp-dev"], sink=RecordingSink())

    assert code == 2
    assert recorded == [], "ガードで落ちたのに infra_events へ書いている"
    assert "保存済み plan" in capsys.readouterr().err


def test_runbooks_do_not_export_terraform_vars() -> None:
    """runbook が `export TF_VAR_*` に戻っていないこと（修正04 の回帰防止）。

    シェルの export は config.yaml / Doppler に次ぐ**第3の設定出所**で、
    渡し忘れがクラウド側の失敗として初めて表面化していた。
    """
    runbooks = (REPO_ROOT / "docs" / "runbooks").glob("動作検証-*.md")

    for path in runbooks:
        text = path.read_text(encoding="utf-8")
        assert "export TF_VAR_" not in text, f"{path.name} に export TF_VAR_ が戻っている"


def test_every_runbook_documents_the_saved_plan_path() -> None:
    """非対話 apply の手順が5基盤で揃っていること（修正06）。"""
    runbooks = sorted((REPO_ROOT / "docs" / "runbooks").glob("動作検証-*.md"))

    assert len(runbooks) == 5
    for path in runbooks:
        text = path.read_text(encoding="utf-8")
        assert "### apply の実行（5基盤共通）" in text, f"{path.name} に共通手順が無い"
        assert "-out=/tmp/" in text, f"{path.name} に保存済み plan の手順が無い"
        # **plan も run_terraform.py 経由**であること。素の terraform plan は
        # config.yaml / Doppler 由来の -var を受け取らず、変数の抜けた plan が
        # 保存されて apply がそれを適用する（2026-08-01 実測）。
        assert "run_terraform.py \\\n  plan --env" in text or "plan --env" in text, (
            f"{path.name} の plan が run_terraform.py を経由していない"
        )


def test_guardrail_variables_are_required() -> None:
    """予算・請求先が未解決なら apply 前に落ちること（修正09）。

    2026-08-01 まで「空なら黙って作らない」だったため、Azure では
    spendingLimit=Off と重なり**ガードレールが1つも無い状態**が実在した。
    """
    from platforms.shared.terraform_vars import VAR_SPECS

    required = {env: {s.name for s in specs if s.required} for env, specs in VAR_SPECS.items()}

    assert "budget_notification_email" in required["aws-dev"]
    assert "budget_notification_email" in required["azure-dev"]
    assert "billing_account_id" in required["gcp-dev"]
