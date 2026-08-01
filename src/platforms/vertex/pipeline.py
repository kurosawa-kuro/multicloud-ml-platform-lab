"""Vertex AI Pipelines へ載せるパイプライン定義（修正11）。

## 何を確かめるための実装か

「オーケストレータをマネージドに移しても、本ラボの計測（`ml_runs` の6列）が保てるか」。
再調査（`docs/tasks/.../修正11`）で **Core 変更ゼロで載る**ことは静的に確定したので、
ここはその結論を**実際に組めることで裏づける**実装であって、仮説検証の PoC ではない。

## 設計の核心 —— ステップは「既存の実行経路をそのまま呼ぶ」

各ステップは **学習イメージ + `python scripts/run_phase.py vertex <stage>`** の1行。
adapter も `_tracked()` も `job_record` も**書き換えない**。これが成立するのは修正07 で
stage 間の受け渡しを Neon 経由にしたから:

    train     … adapter が CustomJob を投げ、成功行はジョブ側が書く（従来どおり）
    register  … `artifact_uri` を Neon から resume（`platforms.shared.resume`）

つまりパイプラインは **CLI を並べる薄い DAG** にすぎない。ステップ間で成果物 URI を
KFP の artifact として受け渡していない（＝ KFP に依存した配線を作っていない）のは意図的で、
受け渡しの正本を Neon に一本化しておくと **CLI 実行とパイプライン実行で経路が変わらない**。

## attempt が壊れない理由

`attempt` は Neon の同一 (platform, stage) 過去行数 + 1 で決まる
（`platforms.shared.contracts.tracking.TrackedOperations._next_attempt`）。
**プロセス外カウント**なので、誰がオーケストレートしても意味論が変わらない。
KFP の retry で同じステップが再実行されても「コードが再度数える = 新しい attempt」で正しい。
そのため既定で **キャッシュを切っている**（`set_caching_options(False)`）。
キャッシュが効くと実行していないのに成功扱いになり、`ml_runs` に行が生まれず
attempt の連番と実行実態がずれる。**比較ラボにとってキャッシュは害**。

## 設定の渡し方

ステップコンテナには `artifacts/*.outputs.json`（terraform outputs）が無い。
`platforms.shared.config` の解決順は
**環境変数 `MCML_<PLATFORM>_<FIELD>` > terraform outputs > config.yaml > 既定** なので、
env で渡せば同じ設定に解決される。**config の解決規約を曲げていない**ことが要点。

秘密（Neon 接続文字列）は同じく env で渡す。`platforms.shared.contracts.tracking.telemetry_env`
と同じ扱いで、本ラボの範囲では許容・本番設計では secret manager 参照に置き換える。
"""

# **このモジュールだけ `from __future__ import annotations` を入れない。**
# PEP 563 で注釈が文字列化されると、kfp は container component の引数注釈を解決できず
# `Artifacts must have both a schema_title and a schema_version. Got: str` で落ちる
# （2026-08-02 実測）。他モジュールの規約から外れる理由はこれ1点。
from typing import Any

# ステップが実行するコマンド。**既存の CLI をそのまま呼ぶ**（新しい入口を作らない）。
RUN_PHASE = ["python", "scripts/run_phase.py"]

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
)


def step_env(environ: dict[str, str]) -> dict[str, str]:
    """ステップコンテナへ載せる env を選ぶ。

    **設定と秘密だけを通す。** 手元の環境をまるごと渡すと、無関係な資格情報まで
    ジョブ定義に載って各基盤のコンソールから読めてしまう
    （`contracts.tracking.telemetry_env` と同じ注意）。

    `CODE_REVISION` は**渡さない**。コンテナにはビルド時に焼き込んだ値があり、
    オーケストレータ側の値で上書きすると「実際に動いたコード」と記録がずれる
    （比較の担保そのものが壊れる）。`telemetry_env` の判断と揃えている。
    """
    allowed = (NEON_URI_ENV, *VERTEX_CONFIG_ENVS)
    return {name: environ[name] for name in allowed if environ.get(name)}


def build_pipeline(image: str, environ: dict[str, str], *, enable_caching: bool = False) -> Any:
    """train → register の2ステップを組む。

    `deploy` / `predict` を入れないのは**常時課金**になるため。この DAG で見たいのは
    「マネージドに実行を渡しても記録が保てるか」で、そこに Endpoint は要らない。

    `enable_caching` の既定が False なのはモジュール docstring のとおり
    （キャッシュ命中は実行していない成功を作り、attempt と実態をずらす）。
    """
    from kfp import dsl  # noqa: PLC0415 - kfp は pipelines extra（既定の実行経路に載せない）

    envs = step_env(environ)

    @dsl.container_component
    def run_stage(stage: str) -> dsl.ContainerSpec:
        # 戻り値注釈は **必須**。無いと kfp が入力を artifact と解釈し
        # `Artifacts must have both a schema_title and a schema_version` で落ちる
        # （2026-08-02 実測）。
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
    """パイプラインを YAML へコンパイルする。**クラウドを叩かない**（課金ゼロ）。

    出力は `.yaml` にする。kfp 2.14 は JSON 出力を deprecated として警告し、
    将来のバージョンで落とす（2026-08-02 実測）。

    「既存イメージと既存 CLI だけで DAG が組めた」ことは、ここが通れば示せる。
    投入は別（owner 承認の対象）。
    """
    from kfp import compiler  # noqa: PLC0415

    compiler.Compiler().compile(
        build_pipeline(image, environ, enable_caching=enable_caching),
        package_path=output_path,
    )
    return output_path
