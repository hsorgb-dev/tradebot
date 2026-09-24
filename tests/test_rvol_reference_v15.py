"""V1.5 RVOL references: >= 18 of 20 valid sessions suffice, and every invalid
session records why (user decision after the GILD/LIN NOT_CHECKABLE on 2026-09-24)."""
from types import SimpleNamespace

import pandas as pd
import pytest

import orb_sim_v15 as sim
import us_orb_test_v02 as base

C = base.Config()
SYMBOL = 'GILD'
TODAY_OPEN = pd.Timestamp('2026-09-24 13:30', tz='UTC')
DECISION = TODAY_OPEN + pd.Timedelta(minutes=68)          # 10:38 NY


def sessions():
    days = pd.bdate_range('2026-08-26', periods=20)
    return [dict(date=f'{d:%Y-%m-%d}', open=pd.Timestamp(f'{d:%Y-%m-%d} 13:30', tz='UTC'),
                 close=pd.Timestamp(f'{d:%Y-%m-%d} 20:00', tz='UTC')) for d in days]


def bars(start, minutes, volume, symbol=SYMBOL):
    return [dict(symbol=symbol, timestamp=start + pd.Timedelta(minutes=i), open=100.0, high=100.2,
                 low=99.8, close=100.1, volume=float(volume), vwap=100.0, feed='sip')
            for i in range(minutes)]


class Trades:
    def __init__(self, conditions):
        self.conditions = conditions

    def get_stock_trades(self, request):
        start = pd.Timestamp(request.start).tz_localize('UTC')
        trade = SimpleNamespace(timestamp=start + pd.Timedelta(seconds=5), size=50.0,
                                conditions=self.conditions)
        return SimpleNamespace(data={SYMBOL: [trade]})


def history(bad_days=(), missing_minute_days=()):
    rows = []
    for number, session in enumerate(sessions()):
        day = bars(session['open'], 68, 100)
        if number in bad_days:
            day[10]['low'] = 101.0                           # low above high: contradictory bar
        if number in missing_minute_days:
            del day[20]
        rows += day
    return pd.DataFrame(rows)


def rvol(tmp_path, frame, min_days, trades=None):
    current = pd.DataFrame(bars(TODAY_OPEN, 68, 300))
    return sim.check_sip_rvol(trades or Trades(['@', 'I']), SYMBOL, current, frame, sessions(),
                              TODAY_OPEN, DECISION, C, tmp_path, 'sip', min_days)


def test_one_invalid_session_no_longer_blocks_the_signal(tmp_path):
    result, checks = rvol(tmp_path, history(bad_days={7}), 18)
    assert result['Ergebnis'] == 'VOLUME_PASS' and result['Vollständige_Vergleichstage'] == 19
    assert result['RVOL'] == pytest.approx(3.0)             # average over the 19 valid days
    bad = checks[checks.status != 'OK'].iloc[0]
    assert bad.reference_date == sessions()[7]['date']
    assert bad.status == 'REFERENCE_UNVERIFIED_ValueError'
    assert 'Ungültige Referenzkerze' in bad.error_detail and '13:40 UTC' in bad.error_detail


def test_v143_rule_is_still_available(tmp_path):
    result, _ = rvol(tmp_path, history(bad_days={7}), 20)
    assert result['Ergebnis'] == 'NOT_CHECKABLE'


def test_three_invalid_sessions_stay_not_checkable(tmp_path):
    result, checks = rvol(tmp_path, history(bad_days={1, 7, 12}), 18)
    assert result['Ergebnis'] == 'NOT_CHECKABLE' and result['Vollständige_Vergleichstage'] == 17
    assert (checks.status != 'OK').sum() == 3


def test_unexplained_missing_minute_records_the_trade_conditions(tmp_path):
    result, checks = rvol(tmp_path, history(missing_minute_days={4}), 18, Trades(['@', 'F']))
    assert result['Ergebnis'] == 'VOLUME_PASS'
    detail = checks[checks.status != 'OK'].iloc[0].error_detail
    assert "Bedingungen ['@', 'F']" in detail and 'nicht aus Odd-Lots erklärbar' in detail


def test_setting_is_validated():
    with pytest.raises(ValueError, match='min_rvol_reference_days'):
        sim.run_dryrun(None, None, '.', C, None,
                       sim.DryRunSettings(min_rvol_reference_days=21))
