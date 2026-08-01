"""terraform apply / destroy を計測して infra_events へ記録する。

Golden Path ステップ1「infra_event に apply の所要・リソース数が記録される」
（docs/01_requirements.md）の実装。素の `terraform apply` を叩くと、
**構築に何分かかったか・何リソース作ったか**が残らず、基盤比較の1列が埋まらない。

    doppler run -- python scripts/run_terraform.py apply --env gcp-dev
    doppler run -- python scripts/run_terraform.py destroy --env gcp-dev

## 境界

ここは計測のラッパであって、承認や判断は持たない。`apply` / `destroy` は
Heavy 操作（.claude/rules/terraform.md「plan-first・owner approval 前提」）なので、
**このスクリプトは -auto-approve を付けない**。terraform 自身が対話で確認する。

記録に失敗しても terraform の結果は変えない（telemetry は非致命）。

## 非対話（CI・バックグラウンド・エージェント）で回す手順 —— 5基盤共通

対話端末が無い場所で素の apply を叩くと terraform の承認プロンプトが
`error asking for approval: EOF` で落ち、しかも **infra_events に failure 行が
1件残って「apply 試行回数」を汚す**（2026-08-01 に実際に発生し、
docs/comparison/04_azureml.md に除外注記を書く羽目になった）。

正しい手順は **保存済み plan を渡すこと**。レビューした内容と適用内容が一致し、
承認プロンプトも出ない:

    terraform -chdir=infra/environments/<env> plan -out=/tmp/<env>.tfplan
    python scripts/run_terraform.py apply --env <env> /tmp/<env>.tfplan

destroy も同じ。ただし `terraform destroy <plan>` は terraform 自身が拒否するので
`plan -destroy -out=...` の成果物を渡す（サブコマンドの読み替えは run_terraform が行い、
**記録上の action は destroy のまま**保つ）:

    terraform -chdir=infra/environments/<env> plan -destroy -out=/tmp/<env>-destroy.tfplan
    python scripts/run_terraform.py destroy --env <env> /tmp/<env>-destroy.tfplan

plan ファイル無しで非対話実行された場合は、**terraform を起動する前に**
EXIT_USAGE で落ちる（偽の failure 行を作らないため）。
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from core.telemetry.schemas import InfraAction, InfraEvent, Platform, Status

EXIT_USAGE = 2

# terraform 環境ディレクトリ → 記録する platform。
# infra/environments/ と 1 対 1（neon は比較対象ではないので記録しない）。
ENV_PLATFORM: dict[str, Platform] = {
    "gcp-dev": Platform.VERTEX,
    "aws-dev": Platform.SAGEMAKER,
    "azure-dev": Platform.AZUREML,
    "dbx-dev": Platform.DATABRICKS,
    "sf-dev": Platform.SNOWFLAKE,
}

# terraform 実行時だけ環境から落とす env（環境ごと）。詳細は terraform_environment()。
# **消してよい理由があるのは「provider が拾って壊れる」ケースだけ**。
# 変数が足りない類の問題をここで解決しない（terraform_vars.py の担当）。
TERRAFORM_ENV_BLOCKLIST: dict[str, tuple[str, ...]] = {
    "sf-dev": ("SNOWFLAKE_ACCOUNT",),
}

# "Apply complete! Resources: 12 added, 0 changed, 0 destroyed."
# "Destroy complete! Resources: 12 destroyed."
_RESOURCE_COUNT = re.compile(
    r"(?:Apply|Destroy) complete! Resources: (?:(\d+) added, (\d+) changed, )?(\d+) destroyed"
)


def parse_resource_count(output: str, action: str = "apply") -> int | None:
    """terraform の完了行からリソース数を取り出す。

    apply は added、destroy は destroyed を数える。読めなければ None
    （**0 と混同しない**。「作らなかった」と「読めなかった」は別物）。

    `action` を見るのは、**保存済み destroy plan を apply で流す**経路があるため
    （`terraform destroy <plan>` は terraform 自身が拒否する）。この場合の完了行は
    `Apply complete! Resources: 0 added, 0 changed, 2 destroyed.` になり、
    added を採ると **destroy が毎回 0 リソース**として記録される（2026-08-01 実測）。
    """
    match = _RESOURCE_COUNT.search(output)
    if not match:
        return None
    added, _changed, destroyed = match.groups()
    if action == "destroy" or added is None:
        return int(destroyed)
    return int(added)


def terraform_environment(env: str, base: Mapping[str, str] | None = None) -> dict[str, str]:
    """terraform に渡す環境変数。その env で地雷になるものだけを落とす。

    snowflake provider 2.19 は **deprecated な `account` フィールドとして
    `SNOWFLAKE_ACCOUNT` を拾い**、`PROVIDER_CONFIGURATION_ACCOUNT_FALLBACK`
    experiment を要求して plan の段階で落ちる。判定されるのは値の妥当性ではなく
    **env が存在すること自体**なので、`provider "snowflake"` に
    `organization_name` / `account_name` を明示しても回避できない（2026-08-01 実測）。

    Doppler から消す解決は取れない。`credentials.md §5` のとおり
    provider v2 は分割形式（`SNOWFLAKE_ORGANIZATION_NAME` + `SNOWFLAKE_ACCOUNT_NAME`）、
    Python connector は `<org>-<account>` 形式（`SNOWFLAKE_ACCOUNT`）と
    **形式が違うものを両方使う**ため、消すと adapter が動かなくなる。

    そこで遮断は terraform を起動する瞬間だけに閉じ込める。
    **他環境まで広げないこと**（原因の分からない変数解決失敗になる）。
    """
    source = os.environ if base is None else base
    blocked = TERRAFORM_ENV_BLOCKLIST.get(env, ())
    return {name: value for name, value in source.items() if name not in blocked}


def stream_command(command: list[str], env: Mapping[str, str] | None = None) -> tuple[int, str]:
    """コマンドを実行し、出力を**逐次**表示しながら蓄積して (終了コード, 全出力) を返す。

    `subprocess.run(capture_output=True)` にしないのは、`-auto-approve` を
    付けない apply / destroy が対話承認（"Enter a value:"）を要求するため。
    キャプチャすると**改行の無いプロンプトがバッファに沈んで見えず**、
    ユーザーは何を待たれているのか分からないまま止まる。

    行単位でなくバイト塊で読むのも同じ理由（プロンプトは改行で終わらない）。
    stdin は継承したままにする = 承認の入力は terraform に直接届く。
    """
    process = subprocess.Popen(  # noqa: S603 - 引数は choices で固定
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=0,
        env=None if env is None else dict(env),
    )
    assert process.stdout is not None
    chunks: list[bytes] = []
    fd = process.stdout.fileno()
    while True:
        chunk = os.read(fd, 4096)
        if not chunk:
            break
        chunks.append(chunk)
        sys.stdout.buffer.write(chunk)
        sys.stdout.buffer.flush()
    returncode = process.wait()
    return returncode, b"".join(chunks).decode("utf-8", errors="replace")


def terraform_variable_args(env: str) -> list[str]:
    """`-var` 引数を env/config.yaml と環境変数から組み立てる。

    以前は runbook 本文の `export TF_VAR_*` が唯一の供給元で、渡し忘れが
    クラウド側の失敗として初めて表面化していた（Databricks は4つ渡さないと ① で落ちる）。
    解決できないものは **terraform を起動する前に**名前を挙げて落とす。
    """
    from platforms.shared.config import load_yaml  # noqa: PLC0415 - pyyaml は config extra
    from platforms.shared.terraform_vars import as_cli_args, resolve  # noqa: PLC0415

    config = load_yaml(Path("env/config.yaml"))
    return as_cli_args(resolve(env, config, dict(os.environ)))


def needs_a_terminal(args: list[str]) -> bool:
    """承認プロンプトに応答できない状況か。

    terraform が対話確認を出すのは「保存済み plan でも -auto-approve でもない」とき。
    そこに端末が無いと `error asking for approval: EOF` で落ちる。
    """
    if _has_plan_file(args) or "-auto-approve" in args:
        return False
    return not sys.stdin.isatty()


def _has_plan_file(args: list[str]) -> bool:
    """引数に保存済み plan ファイルが含まれるか。

    terraform の apply / destroy が取る位置引数は plan ファイルだけなので、
    **フラグでない引数があれば plan** と見なす。実ファイル存在で判定しない:
    パスは `-chdir` 先（infra/environments/<env>）からの相対で、
    リポジトリ直下から見ると存在せず**判定が常に外れる**（2026-08-01 実測）。
    """
    return any(not a.startswith("-") for a in args)


def run_terraform(
    action: str,
    env: str,
    *,
    extra_args: list[str] | None = None,
    runner: Callable[[list[str], Mapping[str, str]], tuple[int, str]] | None = None,
) -> tuple[int, str, float]:
    """terraform を実行し (終了コード, 出力, 所要秒) を返す。

    出力は記録のためにキャプチャしつつ逐次表示する（stream_command）。
    所要には対話承認の待ち時間が含まれる点に注意（apply の純粋な実行時間より
    長く出る。厳密に測りたければ `plan -out` を読み切ってから保存済み plan を適用する）。

    `runner` の既定を **None にして関数内で解決する**のは事故防止。
    `runner=stream_command` と書くと既定値が定義時に束縛され、テストが
    モジュール属性を差し替えても効かず、**ユニットテストが実 terraform を実行する**
    （2026-08-01 に実際に発生。gcp-dev へ意図しない apply が走った）。
    """
    execute = runner if runner is not None else stream_command
    args = list(extra_args or [])
    # **保存済み destroy plan は `terraform destroy <plan>` では適用できない**
    # （`Destroy can't be called with a plan file`）。`plan -destroy -out=...` の
    # 成果物は apply で流す。非対話で destroy するにはこの経路しかないので、
    # サブコマンドだけ差し替えて **記録上の action は destroy のまま**にする
    # （infra_events の destroy 回数を apply に化けさせない）。
    subcommand = "apply" if action == "destroy" and _has_plan_file(args) else action
    # 保存済み plan には変数が焼き込まれているので `-var` を足すと terraform が拒否する。
    variable_args = [] if _has_plan_file(args) else terraform_variable_args(env)
    command = [
        "terraform",
        f"-chdir=infra/environments/{env}",
        subcommand,
        *variable_args,
        *args,
    ]
    started = time.perf_counter()
    returncode, output = execute(command, terraform_environment(env))
    elapsed = time.perf_counter() - started
    return returncode, output, elapsed


def record_event(
    platform: Platform,
    action: InfraAction,
    *,
    status: Status,
    duration_seconds: float,
    resource_count: int | None,
    sink: Any = None,
) -> bool:
    """infra_events へ1行。記録できなくても terraform の結果は変えない。"""
    try:
        if sink is None:
            from platforms.neon.run_sink import NeonRunSink  # noqa: PLC0415 - psycopg 依存

            sink = NeonRunSink()
        sink.record_infra_event(
            InfraEvent(
                event_id=str(uuid.uuid4()),
                platform=platform,
                action=action,
                status=status,
                duration_seconds=duration_seconds,
                resource_count=resource_count,
                # 残留の中身は destroy の**後**に scripts/check_residual.py が別行で記録する
                residual_resources={},
            )
        )
    except Exception as exc:  # noqa: BLE001 - telemetry は非致命
        print(f"infra_events への記録に失敗: {exc}", file=sys.stderr)
        return False
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run_terraform")
    parser.add_argument(
        "action",
        choices=["plan", "apply", "destroy"],
        help="plan は infra_events に記録しない（インフラを変えないため）",
    )
    parser.add_argument("--env", required=True, choices=sorted(ENV_PLATFORM))
    parser.add_argument(
        "--no-record", action="store_true", help="infra_events へ書かない（plan の練習用）"
    )
    parser.add_argument(
        "terraform_args",
        nargs="*",
        help="terraform へそのまま渡す引数（保存済み plan ファイル名など）。"
        "`-var-file=...` のようなフラグ形式もそのまま書ける",
    )
    return parser


def main(
    argv: list[str] | None = None,
    *,
    runner: Callable[[list[str], Mapping[str, str]], tuple[int, str]] | None = None,
    sink: Any = None,
) -> int:
    """`runner` / `sink` を注入できるのはテスト用（**既定は実 terraform / 実 Neon**）。

    注入口を明示的に持たせているのは、monkeypatch 頼みだと差し替え漏れに
    気付けず実クラウドを叩いてしまうため（上の run_terraform の注記参照）。
    """
    # フラグ形式（-auto-approve / -var-file=...）を terraform へ通すため parse_known_args。
    # 位置引数の nargs="*" だけだと argparse が「未知のオプション」として弾き、
    # **terraform 引数を1つも渡せない**（保存済み plan の適用もできない）。
    args, passthrough = build_parser().parse_known_args(argv)
    if not Path(f"infra/environments/{args.env}").is_dir():
        print(f"環境が無い: infra/environments/{args.env}", file=sys.stderr)
        return EXIT_USAGE

    extra_args = [*args.terraform_args, *passthrough]

    # 非対話 × plan ファイル無し = terraform が承認プロンプトで EOF 死する組み合わせ。
    # **terraform を起動する前に**落とす（起動すると失敗行が infra_events に残り、
    # 「apply 試行回数」という比較の一次データが Azure 起因でない失敗で汚れる）。
    # `runner` 注入時（テスト）は端末の有無を問わない。
    if args.action != "plan" and runner is None and needs_a_terminal(extra_args):
        print(
            f"非対話環境では保存済み plan が要る（承認プロンプトに応答できない）:\n"
            f"  terraform -chdir=infra/environments/{args.env} plan"
            f"{' -destroy' if args.action == 'destroy' else ''} -out=/tmp/{args.env}.tfplan\n"
            f"  {sys.argv[0]} {args.action} --env {args.env} /tmp/{args.env}.tfplan",
            file=sys.stderr,
        )
        return EXIT_USAGE

    code, output, elapsed = run_terraform(
        args.action, args.env, extra_args=extra_args, runner=runner
    )
    resource_count = parse_resource_count(output, args.action)
    status = Status.SUCCESS if code == 0 else Status.FAILURE

    print(
        f"-- terraform {args.action} {args.env}: {status.value} "
        f"{elapsed:.1f}s resources={resource_count if resource_count is not None else '不明'}"
    )

    # plan はインフラを変えないので infra_events に残さない
    # （apply / destroy の試行回数という比較の一次データを水増ししない）。
    if args.action != "plan" and not args.no_record:
        record_event(
            ENV_PLATFORM[args.env],
            InfraAction(args.action),
            status=status,
            duration_seconds=elapsed,
            resource_count=resource_count,
            sink=sink,
        )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
