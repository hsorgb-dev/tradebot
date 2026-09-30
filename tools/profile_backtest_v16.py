"""Profile a V1.6 backtest on a synthetic SIP market (no network, no Colab).

    python tools/profile_backtest_v16.py <module_dir> [--symbols 100] [--days 5] [--drive-ms 40]

<module_dir> holds the notebook modules (tools/notebook_modules.py extract ...).
--drive-ms adds this many milliseconds to every file write/read on the run and cache
folders, as a rough stand-in for Google Drive latency (0 = local disk only).

Prints time per phase, counts of API requests, file writes/reads and cockpit updates.
Phases are measured inclusively around well-known functions; nested phases are
reported separately (e.g. "quotes" is part of "replay")."""
import argparse
import collections
import contextlib
import functools
import io
import json
import shutil
import sys
import tempfile
import time
import zlib
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

NY = 'America/New_York'


class Market:
    """Deterministic random-walk SIP market for many symbols (bars, quotes, trades, calendar)."""

    def __init__(self, symbols, seed=7):
        self.symbols = list(symbols)
        self.rng = np.random.default_rng(seed)
        self.cache = {}
        self.requests = collections.Counter()

    def days(self, start, end):
        d, end = date.fromisoformat(str(start)[:10]), date.fromisoformat(str(end)[:10])
        while d <= end:
            if d.weekday() < 5:
                yield d
            d += timedelta(days=1)

    def get_calendar(self, request):
        self.requests['calendar'] += 1
        return [SimpleNamespace(date=d, open=datetime(d.year, d.month, d.day, 9, 30),
                                close=datetime(d.year, d.month, d.day, 16, 0))
                for d in self.days(request.start, request.end)]

    def path(self, symbol, day):
        key = (symbol, day)
        if key not in self.cache:
            seed = zlib.crc32(f'{symbol}|{day}'.encode())
            rng = np.random.default_rng(seed)
            base = 50 + (zlib.crc32(symbol.encode()) % 300)
            steps = rng.normal(0, 0.0012, 390)
            if rng.random() < 0.35:                      # some days trend up after the opening range
                steps[20:120] += 0.0009
            close = base * np.exp(np.cumsum(steps))
            volume = rng.integers(20_000, 60_000, 390).astype(float)
            volume[20:60] *= 1 + 2 * (rng.random() < 0.5)
            self.cache[key] = (close, volume)
        return self.cache[key]

    def get_stock_bars(self, request):
        self.requests['bars'] += 1
        symbols = [request.symbol_or_symbols] if isinstance(request.symbol_or_symbols, str) \
            else list(request.symbol_or_symbols)
        start = pd.Timestamp(request.start).tz_localize('UTC') if pd.Timestamp(request.start).tzinfo is None \
            else pd.Timestamp(request.start).tz_convert('UTC')
        end = pd.Timestamp(request.end).tz_localize('UTC') if pd.Timestamp(request.end).tzinfo is None \
            else pd.Timestamp(request.end).tz_convert('UTC')
        frames = []
        for d in self.days(start.tz_convert(NY).date(), end.tz_convert(NY).date()):
            opening = pd.Timestamp(f'{d} 09:30', tz=NY).tz_convert('UTC')
            for symbol in symbols:
                close, volume = self.path(symbol, str(d))
                if request.timeframe.value == '1Day':
                    frames.append(pd.DataFrame(dict(
                        symbol=[symbol], timestamp=[pd.Timestamp(str(d), tz=NY).tz_convert('UTC')],
                        open=[close[0]], high=[close.max()], low=[close.min()], close=[close[-1]],
                        volume=[volume.sum()], vwap=[close.mean()])))
                    continue
                previous = np.concatenate([[close[0]], close[:-1]])
                frames.append(pd.DataFrame(dict(
                    symbol=symbol, timestamp=opening + pd.to_timedelta(np.arange(390), unit='min'),
                    open=previous, high=np.maximum(previous, close) * 1.0003,
                    low=np.minimum(previous, close) * 0.9997, close=close, volume=volume, vwap=close)))
        frame = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
            columns=['symbol', 'timestamp', 'open', 'high', 'low', 'close', 'volume', 'vwap'])
        frame = frame[(frame.timestamp >= start) & (frame.timestamp <= end)]
        return SimpleNamespace(data={s: [1] for s in frame.symbol.unique()}, df=frame)

    def price_at(self, symbol, at):
        at = pd.Timestamp(at).tz_convert('UTC')
        opening = pd.Timestamp(f'{at.tz_convert(NY).date()} 09:30', tz=NY).tz_convert('UTC')
        i = max(0, min(389, int((at - opening) / pd.Timedelta(minutes=1)) - 1))
        return float(self.path(symbol, str(at.tz_convert(NY).date()))[0][i])

    def get_stock_quotes(self, request):
        self.requests['quotes'] += 1
        symbol = request.symbol_or_symbols
        end = pd.Timestamp(request.end)
        end = end.tz_localize('UTC') if end.tzinfo is None else end.tz_convert('UTC')
        quotes = []
        for k in range(1, 4):
            at = end - pd.Timedelta(seconds=0.2 * k)
            price = self.price_at(symbol, at)
            quotes.append(SimpleNamespace(timestamp=at.to_pydatetime(), bid_price=price * 0.9998,
                                          ask_price=price * 1.0002, bid_size=5, ask_size=5))
        return SimpleNamespace(data={symbol: quotes})

    def get_stock_trades(self, request):
        self.requests['trades'] += 1
        return SimpleNamespace(data={})

    def get_all_assets(self, filter=None):
        return [SimpleNamespace(symbol=s, tradable=True) for s in self.symbols]


class Profile:
    def __init__(self):
        self.time = collections.defaultdict(float)
        self.calls = collections.Counter()

    def wrap(self, owner, name, label):
        original = getattr(owner, name)

        @functools.wraps(original)
        def timed(*args, **kwargs):
            started = time.perf_counter()
            try:
                return original(*args, **kwargs)
            finally:
                self.time[label] += time.perf_counter() - started
                self.calls[label] += 1
        setattr(owner, name, timed)


def patch_io(profile, roots, delay):
    """Count (and optionally slow down) file writes/reads below `roots`."""
    def inside(path):
        text = str(path)
        return any(text.startswith(str(r)) for r in roots)

    def slow(kind, path):
        if inside(path):
            profile.calls[kind] += 1
            if delay:
                time.sleep(delay)
    original_replace = Path.replace
    original_write_text = Path.write_text
    original_read_text = Path.read_text
    original_read_parquet = pd.read_parquet

    def replace(self, target):
        slow('drive_writes', target)
        return original_replace(self, target)

    def write_text(self, *args, **kwargs):
        if not str(self).endswith('.incomplete'):
            slow('drive_writes', self)
        return original_write_text(self, *args, **kwargs)

    def read_text(self, *args, **kwargs):
        slow('drive_reads', self)
        return original_read_text(self, *args, **kwargs)

    def read_parquet(path, *args, **kwargs):
        slow('drive_reads', path)
        return original_read_parquet(path, *args, **kwargs)
    Path.replace, Path.write_text, Path.read_text = replace, write_text, read_text
    pd.read_parquet = read_parquet


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('module_dir')
    parser.add_argument('--symbols', type=int, default=100)
    parser.add_argument('--start', default='2026-09-14')
    parser.add_argument('--end', default='2026-09-18')
    parser.add_argument('--drive-ms', type=float, default=0.0)
    parser.add_argument('--json', default=None)
    parser.add_argument('--keep', default=None, help='copy the run folder here for comparisons')
    parser.add_argument('--html-cockpit', action='store_true',
                        help='also feed the V1.6.0 HTML cockpit (what the V1.6.0 panel did)')
    args = parser.parse_args()
    sys.path.insert(0, str(Path(args.module_dir).resolve()))
    import us_orb_test_v02 as base
    import us_orb_scanner_v03 as scanner
    import hist_data_v16 as hist
    import orb_sim_v16 as sim
    import orb_replay_v16 as replay
    import orb_cockpit_v16 as cockpit_ui
    import research_export_v16 as research
    import backtest_v16 as backtest

    symbols = [f'S{i:03d}' for i in range(args.symbols)]
    market = Market(symbols + ['QQQ', 'SPY'])
    work = Path(tempfile.mkdtemp(prefix='btprof_'))
    profile = Profile()
    patch_io(profile, [work], args.drive_ms / 1000)
    for owner, name, label in [
            (hist.HistoricalDataStore, '_call', 'api_requests'),
            (hist.HistoricalDataStore, 'ensure_minute_bars', 'data_load_minute_bars'),
            (hist.HistoricalDataStore, 'daily', 'data_load_daily_bars'),
            (hist.HistoricalDataStore, '_partition', 'cache_open_partition'),
            (hist.HistoricalDataStore, 'flush', 'cache_write'),
            (hist.HistoricalDataStore, 'quotes', 'quotes'),
            (hist.HistoricalDataStore, 'trades', 'trades'),
            (hist.HistoricalDataStore, 'day_bar_coverage', 'data_validation_coverage'),
            (replay.ReplayData, 'get_stock_bars', 'replay_bar_requests'),
            (sim, 'price_candidates_until', 'signal_scan'),
            (sim, 'inspect_signal', 'signal_checks_rvol_quote'),
            (sim, 'prepare_reference', 'rvol_warmup'),
            (sim, 'save_json', 'logging_json'),
            (sim, 'save_frame', 'logging_csv'),
            (cockpit_ui.Cockpit, 'update', 'ui_cockpit_updates'),
            (research, 'export_run', 'research_export'),
            (backtest, 'data_phase', 'phase_data'),
            (replay, 'replay_days', 'phase_replay'),
    ]:
        profile.wrap(owner, name, label)
    store = hist.HistoricalDataStore(work / 'drive' / 'data' / 'historical', market, market,
                                     retry=hist.RetryPolicy(spacing_seconds=0), sleep=lambda s: None,
                                     now=lambda: pd.Timestamp('2026-09-29 12:00', tz='UTC'))
    store.set_universe(pd.DataFrame([dict(symbol=s, company=s, sector=f'Sector{i % 8}', tradable_now=True,
                                          universe_result='PASS') for i, s in enumerate(symbols)]),
                       dict(source='profile'))
    c = base.Config(test_date=args.start)
    s = scanner.ScannerConfig()
    # V1.6.0 panel fed the HTML cockpit; V1.6.1 shows the compact aggregator only.
    ui = cockpit_ui.Cockpit(display_enabled=False, latest_path=work / 'drive' / 'cockpit_latest.html') \
        if args.html_cockpit else None
    events = collections.Counter()
    started = time.perf_counter()
    with contextlib.redirect_stdout(io.StringIO()) as out:
        result = backtest.run_backtest(store, work / 'drive', c, s,
                                       backtest.BacktestSettings(args.start, args.end), cockpit=ui,
                                       progress=lambda info: events.update([info.get('phase')]))
    total = time.perf_counter() - started
    prints = out.getvalue().count('\n')
    days = sum(1 for r in result['records'] if r.get('status') in ('SIMULATED', 'REUSED'))
    report = dict(total_seconds=round(total, 2), trading_days=days, symbols=args.symbols,
                  drive_ms=args.drive_ms, status=result['status'],
                  trades=result['summary']['trades']['total'], signals=result['summary']['signals']['total'],
                  seconds={k: round(v, 2) for k, v in sorted(profile.time.items())},
                  calls=dict(sorted(profile.calls.items())), market_requests=dict(market.requests),
                  console_lines=prints, progress_events=dict(events))
    print('BACKTEST PROFILING')
    print(f'{days} trading days x {args.symbols} symbols, drive latency {args.drive_ms} ms: '
          f'total {total:.1f} s')
    for label, seconds in sorted(profile.time.items(), key=lambda kv: -kv[1]):
        print(f'  {label:32s} {seconds:8.2f} s   {profile.calls[label]:8d} calls')
    for label in ('drive_writes', 'drive_reads'):
        print(f'  {label:32s} {profile.calls[label]:8d}')
    print(f'  console lines                    {prints:8d}')
    print(f'  API requests (market)            {sum(market.requests.values()):8d} {dict(market.requests)}')
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2))
    if args.keep:
        shutil.copytree(result['folder'], Path(args.keep), dirs_exist_ok=True)
    shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    main()
