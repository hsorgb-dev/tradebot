"""DATENZUGANG PRÜFEN: SIP bars/quotes/trades probed separately, errors told apart,
no credentials in any output."""
import json
from types import SimpleNamespace

import pandas as pd

import data_access_v16 as access
from v16_helpers import ApiError, CalendarMarket, WALL_NOW, make_store

SECRET = 'PKSECRETKEYVALUE1234567890ABCDEFGHIJ'


class Client(CalendarMarket):
    """Data client double; it carries a fake key attribute that must never leak."""

    def __init__(self, **outcomes):
        super().__init__()
        self._api_key = SECRET
        self.outcomes = outcomes

    def _answer(self, kind, request, real):
        outcome = self.outcomes.get(kind, 'ok')
        if isinstance(outcome, Exception):
            raise outcome
        if outcome == 'empty':
            return SimpleNamespace(data={}, df=pd.DataFrame(columns=['symbol', 'timestamp']))
        if outcome == 'short':
            answer = real(request)
            frame = answer.df.iloc[:5]
            return SimpleNamespace(data=answer.data, df=frame)
        return real(request)

    def get_stock_bars(self, request):
        return self._answer('bars', request, super().get_stock_bars)

    def get_stock_quotes(self, request):
        return self._answer('quotes', request, self._quotes)

    def get_stock_trades(self, request):
        return self._answer('trades', request, self._trades)

    def _quotes(self, request):
        return CalendarMarket.get_stock_quotes(self, request)

    def _trades(self, request):
        end = pd.Timestamp(request.end)
        trade = SimpleNamespace(timestamp=end.to_pydatetime(), price=100.0, size=10, conditions=['@'])
        return SimpleNamespace(data={request.symbol_or_symbols: [trade]})


def sessions(tmp_path, market):
    return make_store(tmp_path, market).sessions('2026-09-01', '2026-09-29')


def run(tmp_path, **outcomes):
    client = Client(**outcomes)
    return access.check_data_access(client, sessions(tmp_path, client), now=WALL_NOW,
                                    save_folder=tmp_path / 'caps')


def test_all_available_and_history_probe(tmp_path):
    report = run(tmp_path)
    assert {k: r['result'] for k, r in report['results'].items()} == dict(
        bars='AVAILABLE', quotes='AVAILABLE', trades='AVAILABLE')
    assert report['quality_possible'] == 'FULL_MARKET_DATA'
    assert report['history']['bars']['earliest_available_tested'] == '2016-01-04'
    text = access.format_text(report)
    assert 'HISTORICAL DATA CAPABILITIES' in text and 'SIP Bars:    AVAILABLE' in text
    assert 'SIP Quotes:  AVAILABLE' in text and 'SIP Trades:  AVAILABLE' in text
    assert json.loads(open(report['saved_to']).read())['symbol'] == 'AAPL'


def test_errors_are_told_apart(tmp_path):
    report = run(tmp_path, bars=ApiError(403, 'forbidden'), quotes=ApiError(429, 'slow down'),
                 trades=ApiError(422, 'bad'))
    results = {k: (r['result'], r['http_status']) for k, r in report['results'].items()}
    assert results == dict(bars=('NOT_AVAILABLE', 403), quotes=('RATE_LIMITED', 429),
                           trades=('INVALID_REQUEST', 422))
    assert report['quality_possible'] is None
    assert 'SIP Bars:    NOT AVAILABLE (HTTP 403)' in access.format_text(report)


def test_empty_result_is_not_a_permission_statement(tmp_path):
    report = run(tmp_path, quotes='empty', trades=ApiError(401, 'unauthorized'))
    assert report['results']['quotes']['result'] == 'EMPTY_RESULT'
    assert report['results']['trades']['result'] == 'AUTH_FAILED'
    assert 'keine Aussage über die Berechtigung' in access.format_html(report)
    assert report['quality_possible'] == 'BAR_ONLY'


def test_truncated_answer_is_flagged_as_pagination_issue(tmp_path):
    assert run(tmp_path, bars='short')['results']['bars']['result'] == 'PAGINATION_SUSPECT'


def test_no_credentials_in_any_output(tmp_path):
    report = run(tmp_path, bars=ApiError(403, f'key {SECRET} not allowed'),
                 quotes=RuntimeError(f'connection failed for {SECRET}'))
    dumped = json.dumps(report) + access.format_text(report) + access.format_html(report)
    dumped += open(report['saved_to']).read()
    assert SECRET not in dumped and 'PKSECRET' not in dumped
