"""基盤ごとの偽 SDK と、5基盤を同じ形で組み立てるためのケース定義。

**実クラウドを叩かない**ためのテストダブル置き場。各モジュールは
`case()` を公開し、共通契約テスト（tests/test_adapter_contract.py）が
5基盤を同じループで回せるようにする。

基盤固有の呼び出し検証は各 `tests/test_<platform>_adapter.py` に残す
（そこが比較材料であり、共通化して隠してはいけない）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from core.telemetry.schemas import Platform


class Recorded:
    """SDK エンティティ生成の記録（kind + kwargs だけの汎用スタブ）。

    azureml / databricks の fakes に同じクラスが2つあったのを1つに畳んだ。
    """

    def __init__(self, kind: str, **kwargs: Any) -> None:
        self.kind = kind
        self.kwargs = kwargs
        # ManagedOnlineEndpoint はデプロイ後に traffic を代入される
        self.traffic: dict[str, int] | None = None
        self.name = kwargs.get("name")
        self.scoring_uri = "https://example.invalid/score"


class RecordingFactory:
    """kind 名の属性アクセスで Recorded を作る汎用エンティティ工場。

    `entities.Environment(...)` / `entities.command(...)` のような
    「名前付きコンストラクタ呼び出しを記録するだけ」の偽物を、
    基盤ごとにメソッドを並べずに提供する。
    """

    def __init__(self) -> None:
        self.created: list[Recorded] = []

    def __getattr__(self, kind: str) -> Any:
        def make(**kwargs: Any) -> Recorded:
            entity = Recorded(kind, **kwargs)
            self.created.append(entity)
            return entity

        return make

    def of_kind(self, kind: str) -> list[Recorded]:
        return [e for e in self.created if e.kind == kind]


class FakePoller:
    """begin_*() が返す LRO ポーラの代役。"""

    def __init__(self, value: Any) -> None:
        self._value = value

    def result(self) -> Any:
        return self._value


class ExplodingClient:
    """どの属性を触っても例外を投げる代役。

    5基盤ぶんに同じクラスを書いていたのを1つに畳んだ。
    メッセージを変えられるようにしてあるのは、**failure_class の推定**
    （core.telemetry.tracking.classify_failure）が語で分類するため。
    権限系の語を入れれば IAM に分類されることまで含めて検証できる。
    """

    def __init__(self, message: str = "Permission denied: caller lacks permission") -> None:
        self._message = message

    def __getattr__(self, name: str) -> Any:
        def explode(*args: Any, **kwargs: Any) -> Any:
            raise PermissionError(self._message)

        return explode


class ExplodingNamespace(ExplodingClient):
    """属性アクセスで**さらに壊れた子**を返す代役（client.jobs.run_now 形式用）。"""

    def __getattr__(self, name: str) -> Any:
        return ExplodingClient(self._message)


@dataclass(frozen=True)
class AdapterCase:
    """1基盤ぶんの「組み立て方」。共通契約テストはこれだけを見る。

    Attributes:
        platform: 記録に載る基盤名
        make: 正常系の adapter を作る（deploy / predict も通る状態）
        make_failing: すべての SDK 呼び出しが失敗する adapter を作る
        model_ref: `deploy()` に渡す値（基盤ごとに URI / バージョンで異なる）
        artifact_uri: `register_model()` に渡す値
    """

    platform: Platform
    make: Callable[..., Any]
    make_failing: Callable[..., Any]
    model_ref: str
    artifact_uri: str

    @property
    def id(self) -> str:
        return self.platform.value


def all_cases() -> list[AdapterCase]:
    """5基盤ぶんのケース。ここに足し忘れると共通契約から漏れる。"""
    from tests.fakes import azureml, databricks, sagemaker, snowflake, vertex

    return [
        vertex.case(),
        sagemaker.case(),
        azureml.case(),
        databricks.case(),
        snowflake.case(),
    ]
