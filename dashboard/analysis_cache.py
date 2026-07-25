"""분석 탭 파이프라인 결과의 디스크 캐시 (WAN-188).

사용자 관찰: *"몇 년 전 데이터는 다들 똑같이 있을 거 아냐"* — 안 바뀌는 옛 구간을 화면을
열 때마다 다시 계산할 이유가 없다. 백테스트는 결정적이라 **같은 입력이면 같은 결과**이므로,
한 번 계산한 `PipelineResult`를 디스크에 두고 다음 실행에서 읽는다.

`st.cache_data`(메모리)와 **겹치는 게 아니라 층이 다르다**: 저기는 프로세스가 살아 있는
동안만 유효해 Streamlit을 다시 띄우거나 TTL이 지나면 6년 구간을 통째로 다시 계산한다
(실측 6.80초). 이 캐시는 그 재계산을 0.89초로 줄인다(7.6배).

⚠️ **키에 코드 리비전이 들어간다 (WAN-106 원칙).** 파라미터만으로 키를 만들면 엔진 버그를
고쳐도 키가 같아 **옛 결과를 꺼내 준다** — 이 저장소가 반복해서 당한 "조용한 실패"
(WAN-91/95/112/123 부류)가 캐시 층에서 재현되는 셈이다. 그래서 `cache_key`는 `revision`을
**기본값 없는 필수 인자**로 받는다: 호출부가 잊으면 캐시가 조용히 낡는 게 아니라 `TypeError`가 난다.

저장 위치는 DB 옆(`<db 디렉터리>/cache/analysis/`)이라 DB를 바꾸면 캐시도 따라 갈라지고,
`data/cache/`는 이미 `.gitignore`에 있다. 읽기/쓰기 실패는 **치명적이지 않다** — 캐시를
못 읽으면 그냥 다시 계산한다(정확성이 성능보다 우선).
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
from pathlib import Path

from dashboard.pipeline import PipelineResult

logger = logging.getLogger(__name__)

#: 캐시 포맷·파이프라인 산출물의 버전. `PipelineResult` 스키마나 그 **의미**가 바뀌면
#: 손으로 올린다 — 올리면 옛 캐시와 키가 갈라져 저절로 다시 계산된다(옛 파일은 남는다).
CACHE_VERSION = "wan188.1"

#: DB 디렉터리 기준 캐시 하위 경로. `data/cache/`는 이미 gitignore 대상이다.
_CACHE_SUBDIR = ("cache", "analysis")


def cache_key(
    *,
    symbol: str,
    timeframe: str,
    start_ms: int,
    end_ms: int,
    params_key: str,
    revision: str,
) -> str:
    """이 파이프라인 실행을 식별하는 키(SHA-256 앞 32자).

    `revision`에 **기본값을 두지 않는 것이 이 함수의 핵심**이다 — 위 모듈 독스트링 참고.
    `params_key`는 호출부가 만든 `OrderBlockParams`/`ConfluenceParams`/`BacktestConfig`
    직렬화 문자열이라, 파라미터가 하나라도 다르면 키가 갈라진다.
    """
    payload = json.dumps(
        {
            "version": CACHE_VERSION,
            "symbol": symbol,
            "timeframe": timeframe,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "params": params_key,
            "revision": revision,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


class AnalysisCache:
    """`PipelineResult`를 gzip JSON 파일 하나로 저장/복원한다."""

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root)

    @classmethod
    def for_db(cls, db_path: str | Path) -> AnalysisCache:
        """DB 파일 옆(`<db 디렉터리>/cache/analysis/`)에 캐시를 둔다.

        DB 경로에서 파생시키므로 테스트의 `tmp_path` DB는 자동으로 격리되고, 사용자가
        DB를 갈아끼우면 캐시도 함께 갈라진다.
        """
        return cls(Path(db_path).parent.joinpath(*_CACHE_SUBDIR))

    def path_for(self, key: str) -> Path:
        return self._root / f"{key}.json.gz"

    def load(self, key: str) -> PipelineResult | None:
        """캐시된 결과. 없거나 읽을 수 없으면 `None`(호출부는 다시 계산한다).

        손상된 파일에서 예외를 올리지 않는다 — 캐시는 성능 장치라 실패해도 화면은 떠야 한다.
        """
        path = self.path_for(key)
        if not path.exists():
            return None
        try:
            return PipelineResult.model_validate_json(gzip.decompress(path.read_bytes()))
        except Exception:  # noqa: BLE001 — 손상·구버전 캐시는 무시하고 재계산한다.
            logger.warning("분석 캐시를 읽지 못해 다시 계산합니다: %s", path)
            return None

    def store(self, key: str, result: PipelineResult) -> None:
        """결과를 캐시에 쓴다. 쓰기 실패는 로그만 남기고 무시한다.

        같은 디렉터리에 임시 파일로 쓴 뒤 원자적으로 옮긴다 — 두 세션이 같은 키를 동시에
        쓰다가 **반쯤 쓰인 파일**을 다음 실행이 읽는 일이 없게 한다.
        """
        path = self.path_for(key)
        tmp = path.with_suffix(f".{id(result):x}.tmp")
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            tmp.write_bytes(gzip.compress(result.model_dump_json().encode("utf-8"), 1))
            tmp.replace(path)
        except OSError:  # pragma: no cover - 디스크 가득참 등은 치명적이지 않다.
            logger.warning("분석 캐시 쓰기 실패(무시하고 계속): %s", path)
            tmp.unlink(missing_ok=True)
