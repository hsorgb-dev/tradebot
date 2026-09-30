"""Cockpit control panel: one exclusive mode, inputs only in the cockpit, a backtest
started and inspected with buttons (widgets driven without Colab)."""
from datetime import date

import pytest

import bot_mode_v16 as bot_mode
import control_panel_v16 as panel
from test_replay_v14 import C, S
from v16_helpers import CalendarMarket, make_store

TODAY = date(2026, 9, 29)


@pytest.fixture(autouse=True)
def reset_mode():
    yield
    bot_mode._state['mode'] = None


def test_request_validation():
    ok = panel.RunRequest(mode='BACKTEST', start=date(2026, 9, 14), end=date(2026, 9, 18))
    assert panel.validate_request(ok, TODAY) == []
    assert panel.validate_request(panel.RunRequest(mode='BACKTEST', start=date(2026, 9, 18),
                                                   end=date(2026, 9, 14)), TODAY)
    assert panel.validate_request(panel.RunRequest(mode='BACKTEST', start=date(2026, 9, 14),
                                                   end=TODAY), TODAY)
    assert panel.validate_request(panel.RunRequest(mode='BACKTEST', start=None, end=None), TODAY)
    assert panel.validate_request(panel.RunRequest(mode='PAPER'), TODAY)             # approval missing
    assert panel.validate_request(panel.RunRequest(mode='OBSERVE', sip_compare=True), TODAY)
    assert panel.validate_request(panel.RunRequest(mode='DRY_RUN', sip_compare=True), TODAY) == []


def test_banners_say_what_can_happen():
    paper = panel.banner_html('PAPER')
    assert 'PAPER TRADING' in paper and 'Alpaca Paper Account' in paper
    backtest = panel.banner_html('BACKTEST')
    assert 'BACKTEST' in backtest and 'Historical Data' in backtest and 'NO LIVE ORDERS' in backtest
    assert 'keine' in panel.banner_html('DRY_RUN') and 'keine Orders' in panel.banner_html('DRY_RUN', True)


def test_schedule_is_derived_from_the_config():
    text = panel.schedule_html(C)
    assert 'Opening Range 09:30 – 09:45 NY' in text and 'Einstiege ab 09:46' in text
    assert '15:15' in text and 'Glattstellung 15:30' in text


@pytest.fixture
def app(tmp_path):
    market = CalendarMarket()
    project = tmp_path / 'drive'
    make_store(project / 'data', market)          # universe snapshot, as after a first real run
    return panel.App(project, market, market, ('k', 's'), C, S, display_now=False, background=False), market


def test_mode_selector_is_exclusive_and_shows_the_right_fields(app):
    app, _ = app
    assert app.mode.value == 'OBSERVE' and len(app.mode.options) == 4
    assert app.backtest_box.layout.display == 'none' and app.sip_compare.layout.display == 'none'
    app.mode.value = 'DRY_RUN'
    app.sip_compare.value = True
    assert app.sip_compare.layout.display is None and 'SIP-Vergleichsbot' in app.banner.value
    app.mode.value = 'PAPER'
    assert app.approval.layout.display is None and 'PAPER TRADING' in app.banner.value
    app.mode.value = 'BACKTEST'
    assert app.sip_compare.value is False                         # never combined with BACKTEST
    assert app.backtest_box.layout.display is None and 'NO LIVE ORDERS' in app.banner.value
    assert app.start_button.description == '▶ START BACKTEST'


def test_paper_without_approval_does_not_start(app):
    app, market = app
    app.mode.value = 'PAPER'
    app.safe(app.start)
    assert 'Tagesfreigabe' in app.status.value and market.requests == [] and not app.running


def test_backtest_from_the_cockpit(app, tmp_path):
    app, market = app
    app.store = make_store(app.project / 'data', market)          # test clock and no pauses
    app.store.progress = app.on_progress
    app.mode.value = 'BACKTEST'
    app.start_date.value, app.end_date.value = date(2026, 9, 14), date(2026, 9, 15)
    app.capital.value = 20000.0
    app.safe(app.start)
    assert 'BACKTEST COMPLETE' in app.result.value and 'COMPLETED' in app.result.value
    assert app.last_folder is not None and not app.running
    assert all(not b.disabled for b in app.result_buttons)
    for kind in ('trades', 'signals', 'daily', 'summary'):
        app.safe(app.show, kind)
    assert 'BACKTEST COMPLETE' in app.result.value
    app.safe(app.check_cache)
    assert 'HISTORICAL DATA CACHE' in app.status.value and 'READY' in app.status.value
    assert panel.latest_run(app.project) == app.last_folder
