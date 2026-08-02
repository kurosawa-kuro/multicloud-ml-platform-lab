"""Vertex AI Pipelines の定義（修正11 の GCP 版・オーケストレータイメージ前提）。

## 他2基盤との決定的な差 —— step がコンテナ実行なので「器」が要る

    SageMaker : step = 学習ジョブの型付き宣言 → adapter の request をそのまま載せる
    Azure ML  : step = command job の合成     → adapter の job をそのまま並べる
    Vertex    : step = **コンテナ実行**        → コンテナの中身は自分で用意する

2026-08-02 の初版は学習イメージをステップに使い、実投入で exit 2 で落ちた
（学習イメージは依存最小で `scripts/` も vertex adapter も aiplatform も持たない）。
**「compile が通る」は「動く」の証拠にならない**ことをこのリポで最初に実証した事例。

正しい器は **オーケストレータイメージ**（`docker/orchestrator/Dockerfile`）。
run_phase.py と全 platforms モジュール、aiplatform / psycopg を持つ。
ステップはその中で `python /app/scripts/run_phase.py vertex <stage>` を実行する
＝ **CLI 実行とパイプライン実行で同じ driver・同じ経路**。

## 記録（ml_runs）はどうなるか

ステップ内で動くのは run_phase そのものなので、adapter の `_tracked()` も
`persist_handoff` も CLI 経路と同一に働く。学習ジョブは adapter が入れ子の
CustomJob として投げるため、entrypoint シムと `job_record` も従来どおり
＝ `write_path` / `failure_class` / `attempt` の意味論は不変。

## キャッシュは既定で無効

命中すると「実行していない成功」が生まれ、`ml_runs` に行が無いまま次へ進む。
attempt の連番と実行実態がずれ、permission friction が濁る。比較ラボにとって害。

## 非対象

deploy / predict は入れない（**常時課金**。見たいのは「学習〜登録が同じ意味論のまま
パイプラインから回るか」だけ）。
"""

# **このモジュールだけ `from __future__ import annotations` を入れない。**
# PEP 563 で注釈が文字列化されると、kfp は container component の引数注釈を解決できず
# `Artifacts must have both a schema_title and a schema_version. Got: str` で落ちる
# （2026-08-02 実測）。他モジュールの規約から外れる理由はこれ1点。
from typing import Any

# ステップが実行するコマンド。オーケストレータイメージ内の絶対パス
# （KFP は workdir を保証しないので相対パスにしない）。
RUN_PHASE = ["python", "/app/scripts/run_phase.py"]

# コンテナへ渡す env の名前。値は投入時に解決する（ここに実値を焼かない）。
NEON_URI_ENV = "NEON_MULTICLOUD_POOLED_URI"

# `MCML_VERTEX_<FIELD>` で渡す設定。terraform outputs が無いコンテナ内でも
# 同じ設定に解決させるための最小集合（config.py の解決順の第1候補）。
VERTEX_CONFIG_ENVS = (
    "MCML_VERTEX_PROJECT",
    "MCML_VERTEX_REGION",
    "MCML_VERTEX_BUCKET",
    "MCML_VERTEX_TRAINING_IMAGE_URI",
    "MCML_VERTEX_SERVICE_ACCOUNT",
    "MCML_VERTEX_EXPERIMENT",
)


def orchestrator_image(training_image_uri: str, explicit: str | None = None) -> str:
    """ステップに使う器のイメージ URI。

    明示指定（`VertexConfig.orchestrator_image_uri` / env）が常に勝つ。無ければ
    training と同じレジストリの `orchestrator` を使う —— レジストリの場所は
    1つの事実なので、二重に書かせない（修正03「同じ値を2箇所に書かない」と同じ判断）。

    **学習イメージをそのまま返すことは絶対にしない**（実投入で反証済みの誤り）。
    """
    if explicit:
        return explicit
    if "/training:" in training_image_uri:
        return training_image_uri.replace("/training:", "/orchestrator:")
    if training_image_uri.endswith("/training"):
        return training_image_uri[: -len("/training")] + "/orchestrator"
    raise ValueError(
        "orchestrator イメージを導出できない"
        f"（training_image_uri={training_image_uri!r} が */training:* 形式でない）。"
        "MCML_VERTEX_ORCHESTRATOR_IMAGE_URI で明示指定する"
    )


def step_env(environ: dict[str, str]) -> dict[str, str]:
    """ステップコンテナへ載せる env を選ぶ。

    **設定と秘密だけを通す。** 手元の環境をまるごと渡すと、無関係な資格情報まで
    ジョブ定義に載って各基盤のコンソールから読めてしまう
    （`contracts.tracking.telemetry_env` と同じ注意）。

    `CODE_REVISION` は**渡さない**。器にはビルド時に焼き込んだ値があり、
    オーケストレータ側の値で上書きすると「実際に動いたコード」と記録がずれる。
    """
    allowed = (NEON_URI_ENV, *VERTEX_CONFIG_ENVS)
    return {name: environ[name] for name in allowed if environ.get(name)}


def build_pipeline(
    image: str,
    environ: dict[str, str],
    *,
    enable_caching: bool = False,
) -> Any:
    """train → register の2ステップ DAG を組む。

    各ステップは **`run_phase.py vertex <stage>` の1回実行**。stage 間の受け渡しは
    Neon 経由（修正07 の resume）なので、KFP の artifact 配線は作らない
    ＝ CLI 実行とパイプライン実行で経路が変わらない。
    """
    from kfp import dsl  # noqa: PLC0415 - kfp は pipelines extra（既定の実行経路に載せない）

    envs = step_env(environ)

    @dsl.container_component
    def run_stage(stage: str) -> dsl.ContainerSpec:
        # 戻り値注釈は必須。無いと kfp が入力を artifact と解釈して落ちる（実測）
        return dsl.ContainerSpec(image=image, command=RUN_PHASE, args=["vertex", stage])

    @dsl.pipeline(name="mcml-vertex-train-register")
    def pipeline() -> None:
        train = run_stage(stage="train")
        register = run_stage(stage="register").after(train)
        for task in (train, register):
            task.set_caching_options(enable_caching)
            for name, value in envs.items():
                task.set_env_variable(name, value)

    return pipeline


def compile_pipeline(
    image: str,
    environ: dict[str, str],
    output_path: str,
    *,
    enable_caching: bool = False,
) -> str:
    """YAML へコンパイルする（クラウドを叩かない・課金ゼロ）。

    出力は `.yaml`（kfp 2.14 は JSON 出力が deprecated・実測）。
    ⚠️ 生成物にはステップ env（Neon 接続文字列）が平文で載る。
    `artifacts/` は gitignore 済みだが、コミット・共有・貼り付けをしない。
    """
    from kfp import compiler  # noqa: PLC0415

    compiler.Compiler().compile(
        build_pipeline(image, environ, enable_caching=enable_caching),
        package_path=output_path,
    )
    return output_path
