"""V1.6 historical data layer: cache-first, partial cache, validation, errors, migration."""
import json
import os

import pandas as pd
import pytest

import hist_data_v16 as hist
from v16_helpers import ApiError, CalendarMarket, EARLY_CLOSE, HOLIDAY, WALL_NOW, count, make_store

NY = 'America/New_York'
DAYS_A = ['2026-09-14', '2026-09-15']
DAYS_B = ['2026-09-14', '2026-09-15', '2026-09-17', '2026-09-18']


def test_second_request_is_served_from_cache(tmp_path):
    market = CalendarMarket()
    store = make_store(tmp_path, market)
    first = store.ensure_minute_bars(['AAA', 'QQQ'], DAYS_A)
    assert first == dict(cached=0, downloaded=4)
    calls = count(market, 'bars')
    assert calls == 1                                   # one multi-symbol request over 2 sessions
    again = make_store(tmp_path, market)                # new store instance, same Drive folder
    assert again.ensure_minute_bars(['AAA', 'QQQ'], DAYS_A) == dict(cached=4, downloaded=0)
    assert count(market, 'bars') == calls
    frame = again.day_minutes(['AAA'], '2026-09-15')
    assert len(frame) == 390 and frame.timestamp.is_monotonic_increasing
    assert str(frame.timestamp.dt.tz) == 'UTC'


def test_partial_cache_downloads_only_missing_sessions_without_duplicates(tmp_path):
    market = CalendarMarket()
    store = make_store(tmp_path, market)
    store.ensure_minute_bars(['AAA'], DAYS_A)
    market.requests.clear()
    result = store.ensure_minute_bars(['AAA'], DAYS_B)
    assert result == dict(cached=2, downloaded=2)
    (request,) = [r for k, r in market.requests if k == 'bars']
    start = hist.as_utc(request.start).tz_convert(NY)
    assert start.strftime('%Y-%m-%d %H:%M') == '2026-09-17 09:30'    # nothing before is requested again
    key = store._key('bars_1min', 'AAA', '2026-09')
    frame = store.partitions[key]
    assert not frame.duplicated(['symbol', 'timestamp']).any()
    entry = store.manifest['partitions'][key]
    assert entry['sessions'] == sorted(DAYS_B) and entry['rows'] == len(frame)
    early = store.day_minutes(['AAA'], EARLY_CLOSE)
    assert early.timestamp.max().tz_convert(NY).strftime('%H:%M') == '12:59'   # 13:00 close
    assert HOLIDAY not in entry['sessions']


def test_manifest_has_cache_metadata(tmp_path):
    store = make_store(tmp_path, CalendarMarket())
    store.ensure_minute_bars(['AAA'], DAYS_A)
    manifest = json.loads((tmp_path / 'historical' / 'historical_data_manifest.json').read_text())
    entry = manifest['partitions']['sip/bars_1min/AAA/2026-09']
    for field in ('symbol', 'feed', 'data_type', 'timeframe', 'start', 'end', 'downloaded_at',
                  'timezone', 'source', 'rows', 'file', 'checksum', 'file_version', 'sessions'):
        assert field in entry
    assert entry['feed'] == 'sip' and entry['timeframe'] == '1Min' and entry['timezone'] == 'UTC'
    assert (tmp_path / 'historical' / entry['file']).exists()


def test_damaged_or_unlisted_partition_is_downloaded_again(tmp_path):
    market = CalendarMarket()
    store = make_store(tmp_path, market)
    store.ensure_minute_bars(['AAA'], DAYS_A)
    path = tmp_path / 'historical' / 'sip' / 'bars_1min' / 'AAA' / '2026-09.parquet'
    path.write_bytes(path.read_bytes()[:-50])            # truncated file (e.g. interrupted sync)
    market.requests.clear()
    fresh = make_store(tmp_path, market)
    assert fresh.ensure_minute_bars(['AAA'], DAYS_A)['downloaded'] == 2
    assert fresh.stats['partitions_rejected'] == 1
    stray = tmp_path / 'historical' / 'sip' / 'bars_1min' / 'BBB' / '2026-09.parquet'
    stray.parent.mkdir(parents=True)
    stray.write_bytes(path.read_bytes())                 # file without manifest entry
    assert not fresh.bars_covered('BBB', '2026-09-14')


def test_unfinished_session_is_never_cached(tmp_path):
    market = CalendarMarket()
    now = pd.Timestamp('2026-09-15 19:50', tz='UTC')     # 15:50 NY, session still open
    store = make_store(tmp_path, market, now=now)
    with pytest.raises(hist.DataUnavailable, match='noch nicht abgeschlossen'):
        store.ensure_minute_bars(['AAA'], ['2026-09-15'])
    assert count(market, 'bars') == 0


def test_future_rows_are_rejected(tmp_path):
    market = CalendarMarket()
    store = make_store(tmp_path, market, now=pd.Timestamp('2026-09-15 20:30', tz='UTC'))
    original = market.get_stock_bars

    def with_future(request):
        answer = original(request)
        frame = answer.df.copy()
        late = frame.iloc[[0]].assign(timestamp=pd.Timestamp('2026-09-16 14:00', tz='UTC'))
        frame = pd.concat([frame, late], ignore_index=True)
        return type(answer)(data=answer.data, df=frame)
    market.get_stock_bars = with_future
    with pytest.raises(hist.DataUnavailable, match='Zukunft'):
        store.ensure_minute_bars(['AAA'], ['2026-09-15'])


def test_access_denied_is_a_clear_sip_warning_without_iex(tmp_path):
    market = CalendarMarket()
    market.fail['bars'] = ApiError(403, 'subscription does not permit querying SIP data')
    store = make_store(tmp_path, market)
    with pytest.raises(hist.DataAccessDenied) as caught:
        store.ensure_minute_bars(['AAA'], DAYS_A)
    text = str(caught.value)
    assert 'HISTORICAL DATA WARNING' in text and 'Requested Feed: SIP' in text
    assert 'No automatic IEX fallback performed' in text
    feeds = {getattr(r.feed, 'value', r.feed) for k, r in market.requests}
    assert feeds == {'sip'}
    with pytest.raises(ValueError, match='IEX-Fallback'):
        hist.HistoricalDataStore(tmp_path / 'x', market, market, feed='iex')


def test_rate_limit_retries_with_backoff_and_stops(tmp_path):
    market = CalendarMarket()
    sleeps = []
    store = make_store(tmp_path, market)
    store.sleep = sleeps.append
    market.fail['bars'] = ApiError(429, 'too many requests')
    with pytest.raises(hist.RateLimited):
        store.ensure_minute_bars(['AAA'], DAYS_A)
    assert count(market, 'bars') == store.retry.max_attempts        # bounded, no endless loop
    assert [s for s in sleeps if s >= 1] == list(store.retry.backoff_seconds)
    log = (tmp_path / 'historical' / 'logs' / 'download_log.jsonl').read_text()
    assert log.count('"http_status": 429') == store.retry.max_attempts
    market.fail['bars'] = ApiError(422, 'invalid')
    market.requests.clear()
    with pytest.raises(hist.InvalidRequest):
        store.ensure_minute_bars(['AAA'], DAYS_A)
    assert count(market, 'bars') == 1                                # never retried


def test_transient_error_then_success(tmp_path):
    market = CalendarMarket()
    store = make_store(tmp_path, market)
    original, state = market.get_stock_bars, {'n': 0}

    def flaky(request):
        state['n'] += 1
        if state['n'] == 1:
            raise ApiError(503, 'unavailable')
        return original(request)
    market.get_stock_bars = flaky
    assert store.ensure_minute_bars(['AAA'], DAYS_A)['downloaded'] == 2 and store.retries == 1


def test_empty_batch_is_a_data_problem_not_a_permission(tmp_path):
    market = CalendarMarket()
    market.empty_days.add('2026-09-15')
    store = make_store(tmp_path, market)
    with pytest.raises(hist.DataUnavailable, match='leeres Ergebnis'):
        store.ensure_minute_bars(['AAA', 'QQQ'], DAYS_A)
    assert store.bars_covered('AAA', '2026-09-14')          # the complete session before stays usable
    assert not store.bars_covered('AAA', '2026-09-15')


def test_single_symbol_without_bars_is_cached_as_empty_session(tmp_path):
    market = CalendarMarket()
    market.missing[('BBB', '2026-09-15')] = True
    store = make_store(tmp_path, market)
    store.ensure_minute_bars(['AAA', 'BBB'], ['2026-09-15'])
    assert store.bars_covered('BBB', '2026-09-15')
    entry = store.manifest['partitions']['sip/bars_1min/BBB/2026-09']
    assert entry['empty_sessions'] == ['2026-09-15']
    coverage = store.day_bar_coverage(['AAA', 'BBB'], '2026-09-15')
    assert list(coverage.bars) == [390, 0]


def test_missing_minutes_are_reported_not_filled(tmp_path):
    market = CalendarMarket()
    original = market.get_stock_bars

    def with_gap(request):
        answer = original(request)
        gap = pd.Timestamp('2026-09-15 10:14', tz=NY).tz_convert('UTC')
        frame = answer.df[~((answer.df.symbol == 'AAA') & (answer.df.timestamp == gap))]
        return type(answer)(data=answer.data, df=frame)
    market.get_stock_bars = with_gap
    store = make_store(tmp_path, market)
    store.ensure_minute_bars(['AAA'], ['2026-09-15'])
    (gap,) = store.data_gaps(['AAA'], '2026-09-15')
    assert gap['symbol'] == 'AAA' and gap['missing_minute_ny'] == '10:14'
    assert len(store.day_minutes(['AAA'], '2026-09-15')) == 389


def test_invalid_ohlc_is_kept_and_recorded(tmp_path):
    market = CalendarMarket()
    original = market.get_stock_bars

    def broken(request):
        answer = original(request)
        frame = answer.df.copy()
        frame.loc[frame.index[5], 'high'] = frame.loc[frame.index[5], 'low'] - 1
        return type(answer)(data=answer.data, df=frame)
    market.get_stock_bars = broken
    store = make_store(tmp_path, market)
    store.ensure_minute_bars(['AAA'], ['2026-09-15'])
    entry = store.manifest['partitions']['sip/bars_1min/AAA/2026-09']
    assert entry['validation']['2026-09-15']['ohlc_invalid'] == 1
    assert len(store.day_minutes(['AAA'], '2026-09-15')) == 390   # the engine flags that minute itself


def test_multi_symbol_batches_keep_symbols_apart(tmp_path):
    market = CalendarMarket()
    store = make_store(tmp_path, market)
    store.retry = hist.RetryPolicy(spacing_seconds=0, batch_size=2)
    store.ensure_minute_bars(['AAA', 'BBB', 'CCC', 'QQQ'], ['2026-09-15'])
    sizes = [len(r.symbol_or_symbols) for k, r in market.requests if k == 'bars']
    assert sizes == [2, 2]
    for symbol in ('AAA', 'BBB', 'CCC', 'QQQ'):
        frame = store.day_minutes([symbol], '2026-09-15')
        assert set(frame.symbol) == {symbol} and len(frame) == 390


def test_quote_windows_are_cached_and_sorted_like_the_api(tmp_path):
    market = CalendarMarket()
    store = make_store(tmp_path, market)
    end = pd.Timestamp('2026-09-15 10:00', tz=NY).tz_convert('UTC')
    first = store.quotes('AAA', end - pd.Timedelta(seconds=10), end, 1, 'desc')
    assert len(first) == 1 and first[0].timestamp <= end
    store.flush()
    again = make_store(tmp_path, market)
    before = count(market, 'quotes')
    second = again.quotes('AAA', end - pd.Timedelta(seconds=10), end, 1, 'desc')
    assert count(market, 'quotes') == before
    assert second[0].timestamp == first[0].timestamp and second[0].ask_price == first[0].ask_price
    request = [r for k, r in market.requests if k == 'quotes'][0]
    assert request.limit is None and getattr(request.feed, 'value', request.feed) == 'sip'


def test_calendar_and_universe_only_through_read_only_broker(tmp_path):
    market = CalendarMarket()
    guarded = hist.ReadOnlyBroker(market)
    assert guarded.get_calendar is not None
    for name in ('submit_order', 'close_all_positions', 'cancel_orders', 'get_account', 'get_all_positions'):
        with pytest.raises(PermissionError):
            getattr(guarded, name)
    store = make_store(tmp_path, market)
    assert isinstance(store.source_broker, hist.ReadOnlyBroker)


def write_old_day(folder, market, day, mtime):
    frame = market.get_stock_bars(type('R', (), dict(
        symbol_or_symbols=['AAA', 'QQQ'], timeframe=type('T', (), dict(value='1Min'))(),
        start=pd.Timestamp(f'{day} 09:30', tz=NY).tz_convert('UTC').to_pydatetime(),
        end=pd.Timestamp(f'{day} 16:00', tz=NY).tz_convert('UTC').to_pydatetime()))()).df
    minute = folder / 'minute'
    minute.mkdir(parents=True, exist_ok=True)
    path = minute / f'{day}.csv.gz'
    frame.to_csv(path, index=False, compression='gzip')
    (minute / f'{day}.symbols.json').write_text(json.dumps(['AAA', 'QQQ']))
    os.utime(path, (mtime.timestamp(), mtime.timestamp()))
    return path


def test_old_replay_cache_is_migrated_only_when_complete(tmp_path):
    market = CalendarMarket()
    old = tmp_path / 'replay_cache_sip'
    write_old_day(old, market, '2026-09-14', pd.Timestamp('2026-09-20', tz='UTC'))          # after close
    write_old_day(old, market, '2026-09-15', pd.Timestamp('2026-09-15 18:00', tz='UTC'))    # during session
    (old / 'universe_snapshot.csv').write_text('symbol,company,sector,tradable_now,universe_result\n'
                                               'AAA,A,Technology,True,PASS\n')
    store = hist.HistoricalDataStore(tmp_path / 'historical', market, market,
                                     retry=hist.RetryPolicy(spacing_seconds=0), now=lambda: WALL_NOW)
    result = store.migrate_replay_cache(old)
    assert result['days_accepted'] == 1 and result['days_rejected'] == 1
    assert result['rejected'] == [dict(date='2026-09-15', reason='WRITTEN_BEFORE_SESSION_COMPLETE')]
    assert store.bars_covered('AAA', '2026-09-14') and not store.bars_covered('AAA', '2026-09-15')
    assert list(store.universe().symbol) == ['AAA']
    market.requests.clear()
    assert store.ensure_minute_bars(['AAA'], ['2026-09-14'])['downloaded'] == 0
    assert store.migrate_replay_cache(old)['days_already_done'] == 2    # idempotent


def test_old_day_without_complete_qqq_is_rejected(tmp_path):
    market = CalendarMarket()
    old = tmp_path / 'replay_cache_sip'
    path = write_old_day(old, market, '2026-09-14', pd.Timestamp('2026-09-20', tz='UTC'))
    frame = pd.read_csv(path, compression='gzip')
    frame['timestamp'] = pd.to_datetime(frame.timestamp, utc=True)
    cut = pd.Timestamp('2026-09-14 12:00', tz=NY).tz_convert('UTC')
    frame[frame.timestamp < cut].to_csv(path, index=False, compression='gzip')
    os.utime(path, (pd.Timestamp('2026-09-20', tz='UTC').timestamp(),) * 2)
    store = hist.HistoricalDataStore(tmp_path / 'historical', market, market, now=lambda: WALL_NOW)
    assert store.migrate_replay_cache(old)['rejected'] == [dict(date='2026-09-14',
                                                                reason='INCOMPLETE_DAY_QQQ_CHECK')]
