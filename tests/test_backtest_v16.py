"""V1.6 BACKTEST end to end on a synthetic SIP market: calendar, warmup, carry-forward,
cache reuse, research package, rejected signals, counterfactual isolation, data policy."""
import ast
import re
import json
import zipfile
from pathlib import Path

import pandas as pd
import pytest

import backtest_v16 as backtest
import bot_mode_v16 as bot_mode
import orb_replay_v15 as replay15
import research_export_v16 as research
from test_replay_v14 import C, S, DAY, make_store as make_store15
from v16_helpers import ApiError, CalendarMarket, EARLY_CLOSE, HOLIDAY, SyntheticMarket, make_store

NY = 'America/New_York'
PERIOD = ('2026-09-12', '2026-09-21')      # Saturday .. Monday: 5 sessions, holiday and early close
EXPECTED_DAYS = ['2026-09-14', '2026-09-15', EARLY_CLOSE, '2026-09-18', '2026-09-21']


@pytest.fixture(scope='module')
def run(tmp_path_factory):
    folder = tmp_path_factory.mktemp('bt')
    market = CalendarMarket()
    store = make_store(folder, market)
    events = []
    result = backtest.run_backtest(store, folder / 'drive', C, S,
                                   backtest.BacktestSettings(*PERIOD, initial_equity=25000.0),
                                   progress=events.append)
    return dict(folder=folder, market=market, store=store, result=result, events=events)


def table(run, name):
    return pd.read_parquet(run['result']['folder'] / f'{name}.parquet')


def test_run_completes_over_calendar_days_only(run):
    result = run['result']
    assert result['status'] == 'COMPLETED' and result['folder'].name.startswith('BT_')
    daily = table(run, 'daily')
    assert list(daily.date) == EXPECTED_DAYS                       # weekend and holiday skipped
    assert HOLIDAY not in set(daily.date)
    early = daily[daily.date == EARLY_CLOSE].iloc[0]
    assert early.session_close.tz_convert(NY).strftime('%H:%M') == '13:00'
    assert daily.simulated.all() and not daily.systemic_data_error.any()


def test_equity_is_carried_forward_and_warmup_is_not_traded(run):
    daily = table(run, 'daily')
    assert daily.start_equity.iloc[0] == 25000.0
    for previous, current in zip(daily.itertuples(), list(daily.itertuples())[1:]):
        assert current.start_equity == pytest.approx(previous.end_equity)
    manifest = json.loads((run['result']['folder'] / 'run_manifest.json').read_text())
    assert manifest['warmup_trading_days'] == C.rvol_days == 20
    assert manifest['warmup_start'] < PERIOD[0]
    trades = table(run, 'trades')
    assert set(trades.date) <= set(EXPECTED_DAYS)
    # capital is sized from the carried equity (25,000 -> 30 % cap)
    (trade,) = trades.itertuples()
    assert trade.shares == int(25000 * 0.30 // trade.entry_price)


def test_same_strategy_as_the_v15_replay(run, tmp_path):
    """The backtest reproduces the V1.5 replay of the same synthetic day (10,000 USD)."""
    market = SyntheticMarket()
    summary = replay15.run_replay(make_store15(tmp_path, market), tmp_path / 'drive', C, S, DAY, DAY)
    (old,) = pd.read_csv(summary / 'trades.csv').itertuples()
    single = backtest.run_backtest(make_store(tmp_path / 'b', CalendarMarket()), tmp_path / 'drive16', C, S,
                                   backtest.BacktestSettings(DAY, DAY, initial_equity=10000.0))
    (new,) = pd.read_parquet(single['folder'] / 'trades.parquet').itertuples()
    assert (new.symbol, new.shares, new.exit_reason) == (old.symbol, old.qty, old.exit_reason)
    assert new.entry_price == pytest.approx(old.entry, rel=1e-12)       # CSV round trip of V1.5
    assert new.exit_price == pytest.approx(old.exit_price_proxy, rel=1e-12)
    assert new.net_pnl == pytest.approx(old.pnl_usd)
    assert pd.Timestamp(new.exit_time) == pd.Timestamp(old.exit_at_utc)
    assert list(pd.read_parquet(single['folder'] / 'daily.parquet').date) == [DAY]    # single day


def test_second_run_uses_cache_and_completed_days(run):
    calls = len(run['market'].requests)
    again = backtest.run_backtest(make_store(run['folder'], run['market']), run['folder'] / 'drive', C, S,
                                  backtest.BacktestSettings(*PERIOD, initial_equity=25000.0))
    assert len(run['market'].requests) == calls                     # no download at all
    assert [r['status'] for r in again['records']] == ['REUSED'] * len(EXPECTED_DAYS)
    assert again['run_id'] != run['result']['run_id']               # runs are never overwritten
    index = pd.read_csv(run['folder'] / 'drive' / 'backtests' / 'backtest_runs.csv')
    assert {run['result']['run_id'], again['run_id']} <= set(index.run_id)


def test_research_files_and_bundle(run):
    folder = run['result']['folder']
    for name in ('run_manifest.json', 'summary.json', 'schema.json', 'trades.parquet', 'signals.parquet',
                 'daily.parquet', 'equity_curve.parquet', 'events.parquet', 'data_gaps.parquet',
                 'trades.csv', 'signals.csv', 'daily.csv', 'equity_curve.csv'):
        assert (folder / name).exists(), name
    bundle = run['result']['bundle']
    assert bundle.name == f'{run["result"]["run_id"]}_ANALYSIS.zip'
    with zipfile.ZipFile(bundle) as archive:
        assert set(archive.namelist()) >= {'run_manifest.json', 'summary.json', 'schema.json', 'trades.csv',
                                           'signals.csv', 'daily.csv', 'equity_curve.csv'}
    manifest = json.loads((folder / 'run_manifest.json').read_text())
    for key in ('run_id', 'research_schema_version', 'bot_version', 'created_at', 'backtest_start',
                'backtest_end', 'warmup_start', 'timezone', 'initial_equity', 'universe', 'historical_source',
                'historical_feed', 'bar_timeframe', 'data_quality_level', 'strategy_name',
                'strategy_parameters', 'risk_parameters', 'execution_parameters', 'active_filters'):
        assert key in manifest, key
    assert manifest['historical_feed'] == 'SIP' and manifest['data_quality_level'] == 'BAR_PLUS_QUOTES'
    assert manifest['strategy_parameters']['trailing_stop'] == float(C.trailing)
    assert manifest['risk_parameters']['max_positions'] == C.max_positions
    summary = json.loads((folder / 'summary.json').read_text())
    assert set(summary) >= {'performance', 'trades', 'signals', 'exits', 'risk', 'data_quality'}
    assert summary['trades']['total'] == 1 and summary['exits']['trailing_stop'] == 1


def test_schema_documents_every_column_and_types_are_plain(run):
    folder = run['result']['folder']
    schema = json.loads((folder / 'schema.json').read_text())
    for name in ('trades', 'signals', 'daily', 'equity_curve', 'events', 'data_gaps'):
        columns = [c['column'] for c in schema['tables'][name]]
        assert list(table(run, name).columns) == columns
        assert all(set(c) == {'column', 'datatype', 'unit', 'description', 'source'} for c in schema['tables'][name])
    for key in ('rvol', 'spread_bps', 'mfe', 'mae', 'risk_pct', 'breakout_distance', 'future_return',
                'future_max_drawdown', 'session_buckets'):
        assert key in schema['definitions']
    trades_csv = (folder / 'trades.csv').read_text()
    assert 'USD' not in trades_csv.split('\n', 1)[1] and '%' not in trades_csv
    assert ',false,' in trades_csv                           # booleans as true/false
    signals = pd.read_csv(folder / 'signals.csv')
    assert signals.accepted.astype(str).str.lower().isin(['true', 'false']).all()
    assert pd.Timestamp(signals.timestamp.iloc[0]).tzinfo is not None


def test_rejected_candidate_signals_are_written(run):
    signals = table(run, 'signals')
    rejected = signals[~signals.accepted]
    assert len(rejected) >= 1 and rejected.reject_reason.notna().all()
    ccc = signals[signals.symbol == 'CCC'].iloc[0]
    assert ccc.reject_stage == 'RVOL' and ccc.reject_reason == 'VOLUME_REJECT' and ccc.failed_rvol
    assert ccc.failed_price_band == False and pd.isna(ccc.failed_spread)      # not evaluated
    aaa = signals[signals.symbol == 'AAA'].iloc[0]
    assert aaa.accepted and aaa.trade_id and not aaa.failed_rvol and not aaa.failed_spread
    assert aaa.candidate_position_size == table(run, 'trades').shares.iloc[0]
    assert aaa.session_bucket == 'OPENING' and aaa.minutes_since_or_end == pytest.approx(11)
    daily = table(run, 'daily')
    day = daily[daily.date == DAY].iloc[0]
    assert day.signals_total == 2 and day.signals_accepted == 1 and day.signals_rejected == 1


def test_counterfactuals_are_research_only(run):
    signals = table(run, 'signals')
    aaa = signals[signals.symbol == 'AAA'].iloc[0]
    # AAA rallies after the breakout at 100.2: positive future returns
    assert aaa.future_return_30m > 0 and aaa.future_max_return_to_close > aaa.future_return_1m
    assert aaa.future_max_drawdown_to_close <= 0
    strategy_modules = ('us_orb_test_v02', 'us_orb_scanner_v03', 'orb_portfolio_v15', 'orb_sim_v16',
                        'orb_replay_v16', 'paper_orders_v16', 'hist_data_v16')
    for name in strategy_modules:
        path = Path(__import__(name).__file__)
        tree = ast.parse(path.read_text(encoding='utf-8'))
        imported = {alias.name for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
                    for alias in node.names} | {node.module for node in ast.walk(tree)
                                                if isinstance(node, ast.ImportFrom) and node.module}
        assert 'research_export_v16' not in imported and 'backtest_v16' not in imported, name
        assert not re.search(r'future_(return|max)', path.read_text(encoding='utf-8')), name


def test_export_refuses_unfinished_days(run, tmp_path):
    unfinished = tmp_path / 'day'
    unfinished.mkdir()
    with pytest.raises(RuntimeError, match='erst nach Abschluss'):
        research.export_run(tmp_path, 'BT_X', [dict(date=DAY, folder=unfinished, status='SIMULATED')],
                            run['store'], C, S, None, None, {})


def test_backtest_never_reaches_an_order_path(run):
    assert bot_mode.current() == 'BACKTEST'
    with pytest.raises(bot_mode.OrdersBlocked):
        bot_mode.assert_orders_allowed()
    for folder in (r['folder'] for r in run['result']['records']):
        assert json.loads((Path(folder) / 'status.json').read_text())['orders_sent'] == 0


def test_equity_curve_and_daily_metrics(run):
    curve = table(run, 'equity_curve')
    assert curve.timestamp.is_monotonic_increasing and (curve.drawdown <= 0).all()
    assert curve.equity.iloc[0] == 25000.0
    assert curve.equity.iloc[-1] == pytest.approx(table(run, 'daily').end_equity.iloc[-1])
    daily = table(run, 'daily')
    day = daily[daily.date == DAY].iloc[0]
    assert day.max_simultaneous_positions == 1 and day.trailing_stop_exits == 1
    assert day.expected_symbols == 3 and day.symbols_with_valid_data == 3 and day.data_coverage_pct == 1.0
    assert day.missing_bar_count == 0 and day.qqq_daily_return == pytest.approx(0.0)


def test_non_trading_period_is_reported_clearly(tmp_path):
    result = backtest.run_backtest(make_store(tmp_path, CalendarMarket()), tmp_path / 'drive', C, S,
                                   backtest.BacktestSettings(HOLIDAY, HOLIDAY))
    assert result['status'] == 'NO_TRADING_DAYS'
    assert result['summary']['performance']['trading_days'] == 0 and result['bundle'].exists()


def test_invalid_inputs_are_refused_before_any_download(tmp_path):
    market = CalendarMarket()
    store = make_store(tmp_path, market)
    for settings in (backtest.BacktestSettings('2026-09-18', '2026-09-14'),
                     backtest.BacktestSettings('2026-09-14', '2026-09-29'),
                     backtest.BacktestSettings('2026-09-14', '2026-09-18', feed='IEX'),
                     backtest.BacktestSettings('2026-09-14', '2026-09-18', initial_equity=0)):
        with pytest.raises(ValueError):
            backtest.run_backtest(store, tmp_path / 'drive', C, S, settings)
    assert market.requests == []


def test_symbol_without_data_is_excluded_for_that_day_only(tmp_path):
    market = CalendarMarket()
    market.missing[('BBB', DAY)] = True
    result = backtest.run_backtest(make_store(tmp_path, market), tmp_path / 'drive', C, S,
                                   backtest.BacktestSettings('2026-09-14', '2026-09-15'))
    assert result['status'] == 'COMPLETED'
    daily = pd.read_parquet(result['folder'] / 'daily.parquet').set_index('date')
    assert daily.loc[DAY, 'symbols_excluded'] == 1 and daily.loc[DAY, 'data_coverage_pct'] == pytest.approx(2 / 3)
    assert daily.loc['2026-09-14', 'symbols_excluded'] == 0
    signals = pd.read_parquet(result['folder'] / 'signals.parquet')
    bbb = signals[(signals.symbol == 'BBB') & (signals.date == DAY)].iloc[0]
    assert bbb.reject_reason == 'OR_INCOMPLETE' and bbb.data_reject_code == 'OR_INCOMPLETE'
    assert len(pd.read_parquet(result['folder'] / 'trades.parquet')) == 1        # AAA still traded


def test_systemic_data_problem_stops_and_keeps_results(tmp_path):
    market = CalendarMarket()
    market.empty_days.add(EARLY_CLOSE)
    result = backtest.run_backtest(make_store(tmp_path, market), tmp_path / 'drive', C, S,
                                   backtest.BacktestSettings(*PERIOD))
    assert result['status'] == 'INCOMPLETE_DATA'
    daily = pd.read_parquet(result['folder'] / 'daily.parquet').set_index('date')
    assert list(daily.index) == EXPECTED_DAYS                                  # no day silently removed
    assert daily.loc[EARLY_CLOSE, 'day_status'] == 'INCOMPLETE_DATA' and daily.loc[EARLY_CLOSE, 'systemic_data_error']
    assert daily.loc['2026-09-18', 'day_status'] == 'NOT_RUN' and not daily.loc['2026-09-18', 'simulated']
    assert daily.loc[DAY, 'simulated'] and len(pd.read_parquet(result['folder'] / 'trades.parquet')) == 1
    assert result['bundle'].exists()


def test_sip_quotes_forbidden_stops_without_iex(tmp_path):
    market = CalendarMarket()
    market.fail['quotes'] = ApiError(403, 'forbidden')
    result = backtest.run_backtest(make_store(tmp_path, market), tmp_path / 'drive', C, S,
                                   backtest.BacktestSettings('2026-09-14', '2026-09-15'))
    assert result['status'] == 'SIP_UNAVAILABLE'
    assert any('No automatic IEX fallback performed' in w for w in result['warnings'])
    assert {getattr(r.feed, 'value', r.feed) for _, r in market.requests} == {'sip'}
    assert not pd.read_parquet(result['folder'] / 'daily.parquet').simulated.any()
