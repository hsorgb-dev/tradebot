"""V1.6 modes: exactly one main mode, orders only in PAPER (checked in the order layer),
the SIP comparison bot never orders; OBSERVE / DRY RUN / PAPER behave like V1.5."""
import json
from pathlib import Path

import pandas as pd
import pytest

import bot_mode_v16 as bot_mode
import dual_bot_v16 as dual
import iex_paper_bot_v16 as bot1
import orb_replay_v16 as replay
import orb_sim_v16 as sim
import paper_orders_v16 as paper
from test_dual_bot_v15 import shared_data
from test_iex_paper_bot_v15 import ACCOUNT, OPEN, FakePaperClient, LiveIexData, setup
from test_live_path_v143 import LIVE_DAY
from test_replay_v14 import C, S, SyntheticMarket
from v15_child_helpers import free_plan_market

APPROVAL = paper.approval_token(ACCOUNT, LIVE_DAY)


@pytest.fixture(autouse=True)
def reset_mode():
    yield
    bot_mode._state['mode'] = None


def test_plan_maps_each_mode_onto_the_v15_orchestration():
    assert bot_mode.plan('OBSERVE') == bot_mode.ModePlan('OBSERVE', 'live', dual_mode='SIP_ONLY')
    assert bot_mode.plan('DRY RUN').dual_mode == 'IEX_ONLY' and bot_mode.plan('DRY_RUN').approval is None
    assert bot_mode.plan('DRY_RUN', sip_compare=True).dual_mode == 'DUAL_MODE'
    paper_plan = bot_mode.plan('PAPER', sip_compare=True, approval=APPROVAL)
    assert (paper_plan.dual_mode, paper_plan.approval, paper_plan.orders_possible) == ('DUAL_MODE', APPROVAL, True)
    assert bot_mode.plan('BACKTEST').kind == 'backtest'
    assert bot_mode.plan('DRY_RUN', approval='anything').approval is None      # DRY RUN never passes it on


def test_invalid_combinations_are_refused():
    with pytest.raises(ValueError):
        bot_mode.plan('LIVE_MONEY')
    with pytest.raises(ValueError, match='Tagesfreigabe'):
        bot_mode.plan('PAPER')
    for mode in ('OBSERVE', 'BACKTEST'):
        with pytest.raises(ValueError, match='SIP-Vergleichsbot'):
            bot_mode.plan(mode, sip_compare=True)


@pytest.mark.parametrize('mode', [None, 'OBSERVE', 'DRY_RUN', 'BACKTEST'])
def test_orders_blocked_outside_paper(mode):
    if mode:
        bot_mode.set_mode(mode)
    with pytest.raises(bot_mode.OrdersBlocked):
        bot_mode.assert_orders_allowed()
    bot_mode.set_mode('PAPER')
    bot_mode.assert_orders_allowed()


def test_order_layer_refuses_even_a_valid_approval_in_backtest(tmp_path):
    bot_mode.set_mode('BACKTEST')
    clock, market, client, universe = setup(tmp_path)
    gateway = paper.PaperOrderGateway(client, APPROVAL, LIVE_DAY, C, S, {}, clock, tmp_path)
    gateway.on_start(tmp_path, clock)                       # the gateway's own guard
    assert gateway.enabled is False and 'OrdersBlocked' in gateway.disabled_reason
    with pytest.raises(bot_mode.OrdersBlocked):
        gateway._enter('AAA', 1, 100.0, 'id', {})
    with pytest.raises(bot_mode.OrdersBlocked):             # and the loop refuses the hook
        bot1.run_iex_day(client, LiveIexData(market, clock), tmp_path / 'drive', C, S,
                         universe, approval=APPROVAL, clock=clock)
    assert client.calls == [] and client.submitted == []


def test_simulation_loop_refuses_an_order_hook_outside_paper(tmp_path):
    bot_mode.set_mode('DRY_RUN')
    clock, market, client, universe = setup(tmp_path)
    gateway = paper.PaperOrderGateway(client, APPROVAL, LIVE_DAY, C, S, {}, clock, tmp_path)
    with pytest.raises(bot_mode.OrdersBlocked):
        sim.run_dryrun(client, LiveIexData(market, clock), tmp_path / 'drive', C, S,
                       sim.DryRunSettings(feed='iex'), clock=clock,
                       universe_loader=lambda broker: universe, order_hook=gateway)


def run_bot1(tmp_path, approval):
    clock, market, client, universe = setup(tmp_path)
    out, gateway = bot1.run_iex_day(client, LiveIexData(market, clock), tmp_path / 'drive', C, S,
                                    universe, approval=approval, clock=clock)
    return type('R', (), dict(out=out, gateway=gateway, client=client))


def test_paper_mode_sends_paper_orders_like_v15(tmp_path):
    bot_mode.set_mode('PAPER')
    result = run_bot1(tmp_path, APPROVAL)
    assert 'submit_order' in result.client.calls and result.gateway.orders_sent >= 2
    assert result.gateway.flat_confirmed is True


def test_observe_is_v15_sip_only(tmp_path):
    bot_mode.set_mode('OBSERVE')
    market = free_plan_market()
    client = FakePaperClient(market.wall, market)
    folders = dual.run_session('SIP_ONLY', client, market, ('k', 's'), tmp_path / 'drive', C, S,
                               display=False, serve=False, shared=shared_data(tmp_path, market),
                               clock=market.wall)
    assert set(folders) == {'SIP_DELAYED_SHADOW'} and client.calls == []
    assert json.loads((folders['SIP_DELAYED_SHADOW'] / 'status.json').read_text())['orders_sent'] == 0


def test_dry_run_is_v15_iex_only_without_orders(tmp_path):
    bot_mode.set_mode('DRY_RUN')
    clock = replay.ReplayClock(OPEN - pd.Timedelta(minutes=10))
    market = SyntheticMarket(day=LIVE_DAY)
    client = FakePaperClient(clock, market)
    plan = bot_mode.plan('DRY_RUN')
    folders = dual.run_session(plan.dual_mode, client, LiveIexData(market, clock), ('k', 's'),
                               tmp_path / 'drive', C, S, approval=plan.approval, display=False, serve=False,
                               shared=shared_data(tmp_path, market), clock=clock)
    assert set(folders) == {'IEX_REALTIME_PAPER'} and client.calls == []
    trades = json.loads((folders['IEX_REALTIME_PAPER'] / 'shadow_trades.json').read_text())
    assert [t['symbol'] for t in trades] == ['AAA']          # internal IEX simulation unchanged


def test_paper_with_sip_comparison_bot_only_bot1_orders(tmp_path):
    bot_mode.set_mode('PAPER')
    clock = replay.ReplayClock(OPEN - pd.Timedelta(minutes=10))
    market = SyntheticMarket(day=LIVE_DAY)
    client = FakePaperClient(clock, market)
    plan = bot_mode.plan('PAPER', sip_compare=True, approval=APPROVAL)
    dual.run_session(
        plan.dual_mode, client, LiveIexData(market, clock), ('k', 's'), tmp_path / 'drive', C, S,
        approval=plan.approval, display=False, serve=False, shared=shared_data(tmp_path, market), clock=clock,
        bot2_process_options=dict(data_factory='v15_child_helpers:free_plan_market',
                                  factory_kwargs={}, clock_factory='v15_child_helpers:wall_clock'))
    assert 'submit_order' in client.calls                                   # BOT 1 (IEX, paper)
    root = tmp_path / 'drive' / 'v1_5'
    sip = json.loads((root / 'SIP_DELAYED_SHADOW' / 'account.json').read_text())
    sip_run = sip['days'][LIVE_DAY]['run_folder']
    assert json.loads((Path(sip_run) / 'status.json').read_text())['orders_sent'] == 0
    assert all(o.client_order_id.startswith('ORB15-') or o.client_order_id.startswith('auto-close-')
               for o in client.orders.values())

