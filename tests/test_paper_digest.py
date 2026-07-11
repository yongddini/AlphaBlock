"""paper.digest / scripts.paper_digest 테스트 — 다이제스트 문자열·발송 배선 (WAN-36)."""

from __future__ import annotations

from pathlib import Path

from config.settings import Settings
from paper.digest import build_digest, format_period_label
from paper.performance import build_performance
from paper.store import PaperTradeRecord, PaperTradeStore
from scripts import paper_digest
from strategy.models import OrderBlockDirection, SignalExitReason

# 2024-01-01 / 2024-01-08 UTC (ms).
_JAN01 = 1_704_067_200_000
_JAN08 = 1_704_672_000_000


def _rec(
    symbol: str,
    tf: str,
    *,
    entry_time: int,
    exit_time: int,
    net_pct: float,
    r: float | None,
) -> PaperTradeRecord:
    """합성 페이퍼 거래 레코드(손익은 net_pct로 직접 지정)."""
    return PaperTradeRecord(
        symbol=symbol,
        timeframe=tf,
        direction=OrderBlockDirection.BULLISH,
        entry_time=entry_time,
        entry_price=100.0,
        exit_time=exit_time,
        exit_price=100.0,
        reason=SignalExitReason.TAKE_PROFIT,
        gross_pct=net_pct,
        fee_pct=0.0,
        funding_pct=0.0,
        net_pct=net_pct,
        risk_pct=None if r is None else 1.0,
        r_multiple=r,
    )


def test_format_period_label() -> None:
    assert format_period_label(None, None) == "전체 기간"
    assert format_period_label(_JAN01, _JAN08) == "2024-01-01 ~ 2024-01-08 UTC"
    assert format_period_label(_JAN01, None) == "2024-01-01 ~ 현재 UTC"


def test_build_digest_single_series_exact() -> None:
    perf = build_performance(
        [
            _rec("BTC/USDT:USDT", "1h", entry_time=1, exit_time=1, net_pct=2.0, r=2.0),
            _rec("BTC/USDT:USDT", "1h", entry_time=2, exit_time=2, net_pct=-1.0, r=-1.0),
        ]
    )
    text = build_digest(perf, period_label="2024-01-01 ~ 2024-01-08 UTC")
    assert text == (
        "📊 *페이퍼 성과 다이제스트*\n"
        "기간: `2024-01-01 ~ 2024-01-08 UTC`\n"
        "\n"
        "거래 *2*건 · 승률 *50.0%*\n"
        "순손익: 📈 `+0.98%` · 합계 R `+1.00`\n"
        "MDD: `1.00%`"
    )


def test_build_digest_top_bottom_and_parity() -> None:
    perf = build_performance(
        [
            _rec("BTC/USDT:USDT", "1h", entry_time=1, exit_time=1, net_pct=2.0, r=2.0),
            _rec("BTC/USDT:USDT", "1h", entry_time=2, exit_time=2, net_pct=-1.0, r=-1.0),
            _rec("ETH/USDT:USDT", "4h", entry_time=3, exit_time=3, net_pct=-2.0, r=-2.0),
            _rec("SOL/USDT:USDT", "15m", entry_time=4, exit_time=4, net_pct=1.0, r=1.0),
        ]
    )
    text = build_digest(
        perf,
        period_label="P",
        parity_flagged=[("ETH/USDT:USDT", "4h")],
        top_n=1,
    )
    lines = text.splitlines()
    # 상위: SOL(+1.00), 하위: ETH(-2.00). BTC(+0.98)는 상·하위 사이라 생략.
    assert "*상위 시리즈*" in lines
    assert "• `SOL/USDT:USDT 15m` `+1.00%` (1건)" in lines
    assert "*하위 시리즈*" in lines
    assert "• `ETH/USDT:USDT 4h` `-2.00%` (1건)" in lines
    assert "⚠️ *백테스트 패리티 불일치*: `ETH/USDT:USDT 4h`" in lines
    # 상위 섹션이 하위 섹션보다 먼저 나온다.
    assert lines.index("*상위 시리즈*") < lines.index("*하위 시리즈*")


def test_build_digest_parity_clean() -> None:
    perf = build_performance(
        [_rec("BTC/USDT:USDT", "1h", entry_time=1, exit_time=1, net_pct=1.0, r=1.0)]
    )
    text = build_digest(perf, period_label="P", parity_flagged=[])
    assert "✅ 백테스트 패리티 정상" in text
    assert "패리티 불일치" not in text


def test_build_digest_empty_exact() -> None:
    perf = build_performance([])
    text = build_digest(
        perf,
        period_label="전체 기간",
        runner_line="러너: 정상 · 마지막 폴링 3분 전",
    )
    assert text == (
        "📊 *페이퍼 성과 다이제스트*\n"
        "기간: `전체 기간`\n"
        "\n"
        "기간 내 청산된 페이퍼 거래가 없습니다.\n"
        "러너: 정상 · 마지막 폴링 3분 전"
    )


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    """`.env`를 읽지 않는 테스트 설정(임시 DB 경로)."""
    base: dict[str, object] = {
        "db_path": str(tmp_path / "ohlcv.db"),
        "funding_enabled": False,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


def test_generate_digest_reads_store(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with PaperTradeStore(settings.db_path) as store:
        store.upsert_record(
            _rec("BTC/USDT:USDT", "1h", entry_time=1, exit_time=2, net_pct=3.0, r=3.0)
        )
    text = paper_digest.generate_digest(
        settings,
        since_ms=None,
        until_ms=None,
        symbols=None,
        timeframes=None,
        include_parity=False,
    )
    assert "페이퍼 성과 다이제스트" in text
    assert "거래 *1*건" in text


def test_generate_digest_missing_db_is_empty(tmp_path: Path) -> None:
    settings = _settings(tmp_path, db_path=str(tmp_path / "nope" / "missing.db"))
    text = paper_digest.generate_digest(
        settings,
        since_ms=None,
        until_ms=None,
        symbols=None,
        timeframes=None,
        include_parity=True,  # 0건이면 패리티 조립 전에 반환 → 예외 없음
    )
    assert "청산된 페이퍼 거래가 없습니다" in text


class _FakeTelegram:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def send_message(self, text: str, *, parse_mode: str | None = "Markdown") -> bool:
        self.sent.append(text)
        return True


def test_main_sends_when_enabled(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    settings = _settings(tmp_path, paper_digest_enabled=True)
    with PaperTradeStore(settings.db_path) as store:
        store.upsert_record(
            _rec("BTC/USDT:USDT", "1h", entry_time=1, exit_time=2, net_pct=3.0, r=3.0)
        )
    fake = _FakeTelegram()
    monkeypatch.setattr(paper_digest, "get_settings", lambda: settings)
    monkeypatch.setattr(paper_digest, "build_telegram_client", lambda _s: fake)

    rc = paper_digest.main(["--no-parity"])
    assert rc == 0
    assert fake.sent and "페이퍼 성과 다이제스트" in fake.sent[0]


def test_main_disabled_does_not_send(tmp_path: Path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    settings = _settings(tmp_path, paper_digest_enabled=False)
    with PaperTradeStore(settings.db_path) as store:
        store.upsert_record(
            _rec("BTC/USDT:USDT", "1h", entry_time=1, exit_time=2, net_pct=3.0, r=3.0)
        )
    fake = _FakeTelegram()
    monkeypatch.setattr(paper_digest, "get_settings", lambda: settings)
    monkeypatch.setattr(paper_digest, "build_telegram_client", lambda _s: fake)

    rc = paper_digest.main(["--no-parity"])
    assert rc == 0
    assert fake.sent == []  # 발송 안 함
    assert "페이퍼 성과 다이제스트" in capsys.readouterr().out  # 미리보기 출력


def test_main_dry_run_prints_only(tmp_path: Path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    settings = _settings(tmp_path, paper_digest_enabled=True)
    fake = _FakeTelegram()
    monkeypatch.setattr(paper_digest, "get_settings", lambda: settings)
    monkeypatch.setattr(paper_digest, "build_telegram_client", lambda _s: fake)

    rc = paper_digest.main(["--dry-run", "--no-parity"])
    assert rc == 0
    assert fake.sent == []
    assert "청산된 페이퍼 거래가 없습니다" in capsys.readouterr().out
