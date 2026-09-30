"""V1.6.1: checkpoint after every trading day, pause/interrupt and RESUME without
re-running finished days; the research package equals an uninterrupted run."""
import json

import pandas as pd
import pytest

import backtest_v16 as backtest
import control_panel_v16 as panel
import orb_replay_v16 as replay
from test_replay_v14 import C, S
from v16_helpers import CalendarMarket, make_store

PERIOD = ('2026-09-14', '2026-09-21')          # 5 sessions (holiday 16th, early close 17th)
DAYS = ['2026-09-14', '2026-09-15', '2026-09-17', '2026-09-18', '2026-09-21']
VOLATILE = {'run_id', 'trade_id', 'run_folder', 'detail'}


def comparable(folder, name):
    frame = pd.read_parquet(folder / f'{name}.parquet')
    return frame[[c for c in frame.columns if c not in VOLATILE]].reset_index(drop=True)


@pytest.fixture(scope='module')
def straight(tmp_path_factory):
    folder = tmp_path_factory.mktemp('straight')
    return backtest.run_backtest(make_store(folder, CalendarMarket()), folder / 'drive', C, S,
                                 backtest.BacktestSettings(*PERIOD))


def pause_after(tracker, days):
    def progress(info):
        if info.get('phase') == 'day_done' and tracker.snapshot()['days_done'] >= days:
            tracker.pause_requested = True
    return progress


def test_checkpoint_after_every_day(straight):
    state = json.loads((straight['folder'] / 'checkpoint.json').read_text())
    for key in ('run_id', 'status', 'last_completed_date', 'current_equity', 'cash', 'completed_trades',
                'daily_results', 'research_state', 'strategy_parameters', 'data_feed', 'fingerprint',
                'heartbeat_at', 'records', 'settings'):
        assert key in state, key
    assert state['status'] == 'COMPLETED' and state['last_completed_date'] == DAYS[-1]
    assert [d['date'] for d in state['daily_results']] == DAYS
    assert state['research_state']['parts'] == DAYS
    for day in DAYS:
        assert (straight['folder'] / 'parts' / day / 'signals.parquet').exists()
    assert state['current_equity'] == pytest.approx(pd.read_parquet(straight['folder'] / 'daily.parquet')
                                                    .end_equity.iloc[-1])


def test_pause_then_resume_does_not_repeat_days_and_matches(straight, tmp_path, monkeypatch):
    market = CalendarMarket()
    store = make_store(tmp_path, market)
    tracker = backtest.BacktestProgress()
    first = backtest.run_backtest(store, tmp_path / 'drive', C, S, backtest.BacktestSettings(*PERIOD),
                                  tracker=tracker, progress=pause_after(tracker, 2))
    assert first['status'] == 'PAUSED'
    state = json.loads((first['folder'] / 'checkpoint.json').read_text())
    assert state['status'] == 'PAUSED' and state['last_completed_date'] == DAYS[1]
    assert len(state['records']) == 2
    (open_run,) = backtest.find_open_runs(tmp_path / 'drive')
    assert open_run['run_id'] == first['run_id'] and not open_run['interrupted']
    simulated = []
    original = replay.replay_day
    monkeypatch.setattr(replay, 'replay_day', lambda store, day, *a, **k: simulated.append(day) or
                        original(store, day, *a, **k))
    resumed = backtest.run_backtest(make_store(tmp_path, market), tmp_path / 'drive', C, S,
                                    resume=first['folder'])
    assert resumed['status'] == 'COMPLETED' and resumed['run_id'] == first['run_id']
    assert simulated == DAYS[2:]                             # day 1 and 2 are not computed again
    assert backtest.find_open_runs(tmp_path / 'drive') == []
    for name in ('trades', 'signals', 'daily', 'equity_curve'):
        pd.testing.assert_frame_equal(comparable(resumed['folder'], name),
                                      comparable(straight['folder'], name), check_dtype=False)
    summary = json.loads((resumed['folder'] / 'summary.json').read_text())
    reference = json.loads((straight['folder'] / 'summary.json').read_text())
    assert summary['performance']['final_equity'] == pytest.approx(reference['performance']['final_equity'])
    assert summary['trades'] == reference['trades'] and summary['signals'] == reference['signals']


def test_interrupted_runtime_keeps_the_checkpoint(tmp_path, monkeypatch):
    market = CalendarMarket()
    original = replay.replay_day

    def lost(store, day, *args, **kwargs):
        if day == DAYS[2]:
            raise KeyboardInterrupt           # Colab "interrupt execution" / runtime stop mid-day
        return original(store, day, *args, **kwargs)
    monkeypatch.setattr(replay, 'replay_day', lost)
    with pytest.raises(KeyboardInterrupt):
        backtest.run_backtest(make_store(tmp_path, market), tmp_path / 'drive', C, S,
                              backtest.BacktestSettings(*PERIOD))
    (run,) = backtest.find_open_runs(tmp_path / 'drive')
    assert run['status'] == 'PAUSED' and run['last_completed_date'] == DAYS[1]
    monkeypatch.setattr(replay, 'replay_day', original)
    resumed = backtest.run_backtest(make_store(tmp_path, market), tmp_path / 'drive', C, S,
                                    resume=run['folder'])
    assert resumed['status'] == 'COMPLETED'
    assert list(pd.read_parquet(resumed['folder'] / 'daily.parquet').date) == DAYS


def test_stale_running_status_is_detected_as_interrupted(tmp_path):
    folder = tmp_path / 'drive' / 'backtests' / 'BT_20260930T061500Z_deadbeef'
    folder.mkdir(parents=True)
    old = pd.Timestamp('2026-09-30 06:15', tz='UTC')
    (folder / 'checkpoint.json').write_text(json.dumps(dict(
        run_id=folder.name, status='RUNNING', created_at=old.isoformat(), heartbeat_at=old.isoformat(),
        period=dict(start='2026-01-01', end='2026-03-31'), days_total=61, records=[{}] * 17,
        last_completed_date='2026-01-26', current_equity=10284.0)))
    (run,) = backtest.find_open_runs(tmp_path / 'drive', now=old + pd.Timedelta(minutes=10))
    assert run['interrupted']
    html = panel.open_run_html(run)
    assert 'INCOMPLETE BACKTEST FOUND' in html and '17 / 61' in html and '10,284.00' in html
    backtest.discard_run(folder)
    assert backtest.find_open_runs(tmp_path / 'drive') == []
    assert json.loads((folder / 'checkpoint.json').read_text())['status'] == 'ABORTED'


def test_resume_refuses_changed_parameters(straight, tmp_path):
    state = json.loads((straight['folder'] / 'checkpoint.json').read_text())
    state['fingerprint'] = 'other'
    copy = tmp_path / 'BT_copy'
    copy.mkdir()
    (copy / 'checkpoint.json').write_text(json.dumps(state))
    with pytest.raises(ValueError, match='nicht fortgesetzt'):
        backtest.run_backtest(make_store(tmp_path, CalendarMarket()), tmp_path / 'drive', C, S, resume=copy)


def test_no_minute_output_and_day_wise_files(straight, capsys):
    capsys.readouterr()
    day = next(r for r in straight['records'] if r['date'] == '2026-09-15')
    folder = day['folder']
    assert not list((folder / 'signals').glob('*_SUMMARY.json'))          # one file per day instead
    summaries = json.loads((folder / 'minute_summaries.json').read_text())
    assert len(summaries) > 300
    assert not (folder / 'sip_reference_minutes').exists()                  # references in memory


def test_quiet_backtest_prints_nothing_per_minute(tmp_path, capsys):
    backtest.run_backtest(make_store(tmp_path, CalendarMarket()), tmp_path / 'drive', C, S,
                          backtest.BacktestSettings('2026-09-15', '2026-09-15'))
    printed = capsys.readouterr().out
    assert printed.count('\n') <= 3 and ' NY: ' not in printed


def test_compact_running_view():
    tracker = backtest.BacktestProgress()
    tracker.set(status='RUNNING', run_id='BT_X', period=('2026-01-01', '2026-03-31'), days_total=61,
                days_done=17, universe=101, equity=10284.0, coverage=0.987, signals=248, trades=31)
    tracker.phase(5)
    tracker.day_started('2026-01-26')
    tracker.day_finished(dict(date='2026-01-26', signals_total=18, trades=3, end_equity=10284.0,
                              data_coverage_pct=0.99), '2026-01-26 | 101/101 Symbols | 18 Signals')
    html = panel.running_html(tracker.snapshot())
    for text in ('BACKTEST RUNNING', '01.01.2026 – 31.03.2026', '18 / 61', '26.01.2026', '101 Aktien',
                 '10,284.00 USD', 'Replay läuft', 'Letzte Aktualisierung', '101/101 Symbols'):
        assert text in html, text
    assert html.count('\n') < 20                                             # no minute list


def test_progress_update_is_cheap(straight):
    import time
    from types import SimpleNamespace
    tracker = backtest.BacktestProgress()
    portfolio = SimpleNamespace(closed=[], positions={}, last_equity=10000.0)
    started = time.perf_counter()
    for minute in range(390 * 100):
        tracker.update(None, portfolio, pd.Timestamp('2026-09-15 14:00', tz='UTC'), signals=())
    assert (time.perf_counter() - started) / (390 * 100) < 0.0002          # far below a millisecond
