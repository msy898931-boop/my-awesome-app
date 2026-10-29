"""Tests for the command line entry point.

``cli.main`` is the surface a human actually runs, and it had no coverage at
all — the module the README leads with was the one nothing exercised. Every
command is driven here with the pipeline and the Telegram client replaced, so
the test is about the wiring (which stage runs, what is printed, what the exit
code says) and never touches the network.
"""

from __future__ import annotations

import json

import pytest

from goldbot import cli
from goldbot.pipeline import Result


def _result() -> Result:
    """A complete, deterministic read: a plan, a narrative and news."""
    return Result(
        generated_at=1_700_000_000,
        symbol="XAUUSD",
        facts={"timeframes": {"H1": {}}},
        news={"blackout": {"active": False}, "ok": True},
        plan={
            "direction": "buy",
            "entry": 2000.0,
            "stop": 1990.0,
            "target": 2020.0,
            "rr": 2.0,
            "setup": "bos",
            "reason": "استمرار الاتجاه",
            "score": 3,
            "factors": [],
            "rejected": [],
            "levels": {},
        },
        analysis={"narrative": "قراءة تجريبية", "confidence": 0},
    )


class _FakePipeline:
    """Records the flags it was built with and returns a fixed read."""

    calls: list[dict] = []

    def __init__(self, settings, **kwargs):
        self.settings = settings

    def run(self, with_ai=True, with_news=True, max_age=None):
        _FakePipeline.calls.append({"with_ai": with_ai, "with_news": with_news})
        return _result()


@pytest.fixture
def patched(monkeypatch):
    _FakePipeline.calls = []
    monkeypatch.setattr(cli, "Pipeline", _FakePipeline)
    monkeypatch.setattr(cli, "load_settings", lambda: object())
    return _FakePipeline


def test_analyse_prints_json_and_succeeds(patched, capsys):
    code = cli.main(["analyse"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["symbol"] == "XAUUSD"
    assert payload["actionable"] is True


def test_no_ai_and_no_news_flags_reach_the_pipeline(patched):
    cli.main(["analyse", "--no-ai", "--no-news"])
    assert patched.calls[-1] == {"with_ai": False, "with_news": False}


def test_card_prints_the_plan_card(patched, capsys):
    assert cli.main(["card"]) == 0
    out = capsys.readouterr().out
    assert "XAUUSD" in out


def test_news_command_prints_the_digest(patched, capsys):
    assert cli.main(["news"]) == 0
    assert capsys.readouterr().out.strip()


def test_news_json_flag_prints_raw_json(patched, capsys):
    assert cli.main(["news", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_news_never_asks_the_model(patched):
    cli.main(["news"])
    assert patched.calls[-1]["with_ai"] is False


def test_backtest_runs_the_study(patched, monkeypatch, capsys):
    monkeypatch.setattr(
        "goldbot.backtest.engine.run_backtest", lambda settings, primary_tf=None: {"trades": 0}
    )
    assert cli.main(["backtest"]) == 0
    assert json.loads(capsys.readouterr().out) == {"trades": 0}


def test_backtest_command_forwards_the_frame(patched, monkeypatch, capsys):
    seen = {}

    def _fake(settings, primary_tf=None):
        seen["tf"] = primary_tf
        return {"trades": 0}

    monkeypatch.setattr("goldbot.backtest.engine.run_backtest", _fake)
    assert cli.main(["backtest", "--tf", "H4"]) == 0
    assert seen["tf"] == "H4"


def test_study_chart_flag_prints_the_curve(patched, monkeypatch, capsys):
    monkeypatch.setattr(
        "goldbot.backtest.study.run_study",
        lambda settings, primary_tf=None, window_count=5: _study_result(),
    )
    assert cli.main(["study", "--chart"]) == 0
    out = capsys.readouterr().out
    assert "منحنى رأس المال" in out


def test_study_svg_flag_writes_a_file(patched, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(
        "goldbot.backtest.study.run_study",
        lambda settings, primary_tf=None, window_count=5: _study_result(),
    )
    target = tmp_path / "curve.svg"
    assert cli.main(["study", "--svg", str(target)]) == 0
    svg = target.read_text(encoding="utf-8")
    assert svg.startswith("<svg")
    assert "<polyline" in svg
    assert "حُفظ المنحنى" in capsys.readouterr().out


def test_study_chart_setups_prints_one_curve_per_setup(patched, monkeypatch, capsys):
    monkeypatch.setattr(
        "goldbot.backtest.study.run_study",
        lambda settings, primary_tf=None, window_count=5: _study_result(),
    )
    assert cli.main(["study", "--chart-setups"]) == 0
    out = capsys.readouterr().out
    # Both setup titles appear, each with its own curve — not the pooled one twice.
    assert "bos" in out and "sweep" in out


def test_study_svg_dir_writes_pooled_and_per_setup(patched, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(
        "goldbot.backtest.study.run_study",
        lambda settings, primary_tf=None, window_count=5: _study_result(),
    )
    out_dir = tmp_path / "curves"
    assert cli.main(["study", "--svg-dir", str(out_dir)]) == 0
    files = sorted(p.name for p in out_dir.iterdir())
    # all + one per setup, and the names come from the labels (deterministic).
    assert files == sorted(f"{name}.svg" for name in ("all", "bos", "sweep"))
    for path in out_dir.iterdir():
        body = path.read_text(encoding="utf-8")
        assert body.startswith("<svg") and "<polyline" in body


def _study_result() -> dict:
    """A minimal but *complete* study dict: the real metric block, a short curve."""
    from goldbot.backtest.engine import Trade
    from goldbot.backtest.report import metrics

    trades = [
        Trade(
            index=i,
            time=1_700_000_000 + i * 3600,
            direction="buy",
            entry=2000.0,
            stop=1990.0,
            target=2020.0,
            rr=2.0,
            setup="bos",
            outcome="win" if r > 0 else "loss",
            r_multiple=r,
        )
        for i, r in enumerate((1.0, -1.0, 2.0))
    ] + [
        Trade(
            index=9,
            time=1_700_000_000 + 9 * 3600,
            direction="sell",
            entry=2000.0,
            stop=2010.0,
            target=1980.0,
            rr=2.0,
            setup="sweep",
            outcome="loss",
            r_multiple=-1.0,
        )
    ]
    return {
        "symbol": "XAUUSD",
        "primary_tf": "H1",
        "bars": 100,
        "span_days": 3,
        "windows": 1,
        "metrics": metrics(trades),
        "equity_curve": [1.0, 0.0, 2.0, 1.0],
        "setup_curves": {"bos": [1.0, 0.0, 2.0], "sweep": [-1.0]},
        "consistency": {"windows": 1, "positive": 1, "share": 1.0, "thin": 0},
        "control": None,
        "setup_control": {},
        "by_direction": {"buy": metrics(trades[:3]), "sell": metrics(trades[3:])},
        "by_setup": {"bos": metrics(trades[:3]), "sweep": metrics(trades[3:])},
        "notes": ["اختبار"],
    }


def test_unknown_command_exits_nonzero():
    with pytest.raises(SystemExit) as exc:
        cli.main(["nonsense"])
    assert exc.value.code == 2


# ── send ────────────────────────────────────────────────────────────────


class _FakeTelegram:
    def __init__(self, token, ok=True, raises=None):
        self.token = token
        self.ok = ok
        self.raises = raises
        self.sent: list[tuple] = []

    def send(self, chat_id, text, keyboard=None):
        if self.raises:
            raise self.raises
        self.sent.append((chat_id, text))
        return self.ok


def _settings(**over):
    class _S:
        telegram_token = "123:abc"
        telegram_chat = "555"
        enable_bot = True

    for key, value in over.items():
        setattr(_S, key, value)
    return _S()


def test_send_reports_success(patched, monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_settings", lambda: _settings())
    monkeypatch.setattr("goldbot.telegram.client.Telegram", _FakeTelegram)
    assert cli.main(["send"]) == 0
    assert "أُرسلت" in capsys.readouterr().out


def test_send_reports_a_rejected_send_as_a_failure(patched, monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_settings", lambda: _settings())
    monkeypatch.setattr(
        "goldbot.telegram.client.Telegram", lambda token: _FakeTelegram(token, ok=False)
    )
    assert cli.main(["send"]) == 1
    assert "فشل" in capsys.readouterr().out


def test_send_reports_a_transport_error_as_a_failure(patched, monkeypatch, capsys):
    from goldbot.telegram.client import TelegramError

    monkeypatch.setattr(cli, "load_settings", lambda: _settings())
    monkeypatch.setattr(
        "goldbot.telegram.client.Telegram",
        lambda token: _FakeTelegram(token, raises=TelegramError("boom")),
    )
    assert cli.main(["send"]) == 1
    assert "boom" in capsys.readouterr().out


# ── bot ─────────────────────────────────────────────────────────────────


def test_bot_refuses_when_the_switch_is_off(patched, monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_settings", lambda: _settings(enable_bot=False))
    assert cli.main(["bot"]) == 1
    assert "معطّل" in capsys.readouterr().out


def test_bot_refuses_without_a_token(patched, monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_settings", lambda: _settings(telegram_token=""))
    assert cli.main(["bot"]) == 1
    assert "TELEGRAM_BOT_TOKEN" in capsys.readouterr().out


def test_bot_starts_the_loop_when_configured(patched, monkeypatch):
    started: list[bool] = []

    class _Bot:
        def __init__(self, settings):
            self.settings = settings

        def run_forever(self):
            started.append(True)

    monkeypatch.setattr(cli, "load_settings", lambda: _settings())
    monkeypatch.setattr("goldbot.telegram.bot.Bot", _Bot)
    assert cli.main(["bot"]) == 0
    assert started == [True]
