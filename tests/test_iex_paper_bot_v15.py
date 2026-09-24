"""BOT 1 – IEX_REALTIME_PAPER with a simulated Alpaca paper account (no network).

Covers: no approval / invalid approval / live endpoint -> no order at all,
valid approval -> IOC entry + broker trailing stop + flatten with position 0,
restart without a second entry, no late order for a missed signal, and that
replayed/delayed runs refuse an order hook."""
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import iex_paper_bot_v15 as bot1
import orb_replay_v15 as replay
import orb_sim_v15 as sim
import paper_orders_v15 as paper
from test_live_path_v143 import LIVE_DAY
from test_replay_v14 import C, S, SyntheticMarket, make_store

OPEN = pd.Timestamp(f'{LIVE_DAY} 09:30', tz='America/New_York').tz_convert('UTC')
ACCOUNT = 'PA123456'


class Order(SimpleNamespace):
    pass


class FakePaperClient:
    """Enough of alpaca.trading.TradingClient for BOT 1; records every order call."""

    def __init__(self, clock, calendar_source, live=False, equity=10000.0):
        self._base_url = 'https://api.alpaca.markets' if live else 'https://paper-api.alpaca.markets'
        self._sandbox = not live
        self.clock, self.calendar_source, self.equity = clock, calendar_source, equity
        self.orders, self.positions, self.submitted, self.calls = {}, {}, [], []

    # read side
    def get_clock(self):
        return SimpleNamespace(timestamp=self.clock.now().to_pydatetime(), is_open=True)

    def get_calendar(self, request):
        return self.calendar_source.get_calendar(request)

    def get_account(self):
        invested = sum(p.qty * p.current_price for p in self.positions.values())
        return SimpleNamespace(status='ACTIVE', account_number=ACCOUNT, currency='USD',
                               account_blocked=False, trading_blocked=False,
                               equity=str(self.equity), cash=str(self.equity - invested))

    def get_all_positions(self):
        return list(self.positions.values())

    def get_orders(self, filter=None):
        status = getattr(getattr(filter, 'status', None), 'value', None)
        if status == 'closed':
            return [o for o in self.orders.values() if o.status in ('filled', 'canceled')]
        return [o for o in self.orders.values() if o.status == 'new']

    def get_order_by_client_id(self, client_order_id):
        if client_order_id not in self.orders:
            raise KeyError(client_order_id)
        return self.orders[client_order_id]

    # order side
    def submit_order(self, request):
        self.calls.append('submit_order')
        self.submitted.append(request)
        cid = request.client_order_id
        if cid in self.orders:
            raise ValueError('client_order_id must be unique')
        if request.type.value == 'limit':
            order = Order(symbol=request.symbol, side='buy', qty=request.qty, status='filled',
                          filled_qty=request.qty, filled_avg_price=request.limit_price,
                          client_order_id=cid, order_type='limit', filled_at=self.clock.now())
            self.positions[request.symbol] = SimpleNamespace(symbol=request.symbol, qty=request.qty,
                                                             current_price=request.limit_price)
        else:
            order = Order(symbol=request.symbol, side='sell', qty=request.qty, status='new',
                          filled_qty=0, filled_avg_price=None, client_order_id=cid,
                          order_type='trailing_stop')
        self.orders[cid] = order

    def cancel_orders(self):
        self.calls.append('cancel_orders')
        for order in self.orders.values():
            if order.status == 'new':
                order.status = 'canceled'

    def close_all_positions(self, cancel_orders=False):
        self.calls.append('close_all_positions')
        for symbol, position in list(self.positions.items()):
            self.orders[f'auto-close-{symbol}'] = Order(
                symbol=symbol, side='sell', qty=position.qty, status='filled', filled_qty=position.qty,
                filled_avg_price=position.current_price, client_order_id=f'auto-close-{symbol}',
                order_type='market', filled_at=self.clock.now())
        self.positions.clear()


class LiveIexData(replay.DelayedData):
    """The synthetic market as a real-time data client (lag 0) at the simulated clock."""


def setup(tmp_path, wall_start=None, live=False):
    clock = replay.ReplayClock(wall_start or OPEN - pd.Timedelta(minutes=10))
    market = SyntheticMarket(day=LIVE_DAY)
    store = make_store(tmp_path, market)
    client = FakePaperClient(clock, market, live=live)
    return clock, market, client, store.universe()


def run(tmp_path, approval, wall_start=None, live=False, setup_result=None):
    clock, market, client, universe = setup_result or setup(tmp_path, wall_start, live)
    out, gateway = bot1.run_iex_day(client, LiveIexData(market, clock), tmp_path / 'drive', C, S,
                                    universe, approval=approval, clock=clock)
    return SimpleNamespace(out=out, gateway=gateway, client=client, clock=clock)


def load(folder, name):
    return json.loads((Path(folder) / name).read_text())


def test_without_approval_no_gateway_and_no_order_call(tmp_path):
    result = run(tmp_path, approval=None)
    assert result.gateway is None and result.client.calls == []
    assert load(result.out, 'status.json')['status'] == 'SHADOW_SIM_COMPLETED_NO_ORDERS'
    assert load(result.out, 'status.json')['orders_sent'] == 0
    (trade,) = load(result.out, 'shadow_trades.json')       # series 2: IEX simulation
    assert trade['symbol'] == 'AAA'
    assert load(result.out, 'manifest.json')['strategy']['trailing'] == C.trailing
    assert load(result.out, 'iex_access_check.json')['result'] == 'RECENT_IEX_BARS_AND_QUOTES_ACCEPTED'
    days = load(tmp_path / 'drive' / 'v1_5' / 'IEX_REALTIME_PAPER', 'paper_days.json')
    assert days[LIVE_DAY]['orders_enabled'] is False


@pytest.mark.parametrize('approval', ['yes', paper.approval_token(ACCOUNT, '2026-09-22'),
                                      paper.approval_token('PA999', LIVE_DAY)])
def test_invalid_approval_keeps_orders_disabled(tmp_path, approval):
    result = run(tmp_path, approval=approval)
    assert result.client.calls == [] and not result.gateway.enabled
    assert 'Freigabe' in result.gateway.disabled_reason
    assert load(result.out, 'status.json')['orders_sent'] == 0


def test_live_endpoint_is_refused_even_with_valid_approval(tmp_path):
    result = run(tmp_path, approval=paper.approval_token(ACCOUNT, LIVE_DAY), live=True)
    assert result.client.calls == [] and 'Paper-Endpunkt' in result.gateway.disabled_reason


def test_valid_approval_trades_on_paper_and_flattens(tmp_path):
    result = run(tmp_path, approval=paper.approval_token(ACCOUNT, LIVE_DAY))
    client = result.client
    entry, stop = client.submitted[:2]
    assert entry.symbol == 'AAA' and entry.side.value == 'buy' and entry.time_in_force.value == 'ioc'
    assert entry.limit_price == pytest.approx(100.21) and entry.qty == 29
    assert entry.client_order_id == f'ORB15-{LIVE_DAY}-AAA-entry'
    assert stop.side.value == 'sell' and stop.trail_percent == pytest.approx(1.0) and stop.qty == 29
    assert client.calls[-2:] == ['cancel_orders', 'close_all_positions'] and client.positions == {}
    assert result.gateway.flat_confirmed is True
    status = load(result.out, 'status.json')
    assert status['orders_sent'] == 3 and status['status'] == 'SHADOW_SIM_COMPLETED_WITH_PAPER_ORDERS'
    signal = load(result.out, 'signals/' + sim.minute_key(OPEN + pd.Timedelta(minutes=26), 'AAA'))
    assert signal['paper_order']['status'] == 'filled' and signal['shadow_status'] == 'VIRTUAL_ENTRY'
    folder = tmp_path / 'drive' / 'v1_5' / 'IEX_REALTIME_PAPER'
    fills = pd.read_csv(folder / 'IEX_REALTIME_PAPER_trades.csv')
    assert set(fills[fills.kind == 'ALPACA_FILL'].side) == {'buy', 'sell'}
    assert load(folder, 'paper_days.json')[LIVE_DAY]['flat_confirmed'] is True
    assert (tmp_path / 'drive' / 'v1_5' / 'IEX_REALTIME_PAPER_SIM' /
            'IEX_REALTIME_PAPER_SIM_trades.csv').exists()


def test_restart_never_sends_a_second_entry(tmp_path):
    state = setup(tmp_path)
    approval = paper.approval_token(ACCOUNT, LIVE_DAY)
    first = run(tmp_path, approval, setup_result=state)
    entries = [r for r in first.client.submitted if r.client_order_id.endswith('-entry')]
    gateway = paper.PaperOrderGateway(first.client, approval, LIVE_DAY, C, S, {'AAA': 'Technology'},
                                      first.clock, tmp_path / 'log')
    gateway.on_start(tmp_path, first.clock)
    now = OPEN + pd.Timedelta(minutes=26, seconds=6)
    quote = dict(quote_result='PASS', ask=100.21, quote_time_utc=now.isoformat())
    answer = gateway.on_signal(OPEN + pd.Timedelta(minutes=26), now,
                               dict(symbol='AAA', sector='Technology'), quote)
    assert answer['reason'] == 'ENTRY_ALREADY_SENT_TODAY'
    assert [r for r in first.client.submitted if r.client_order_id.endswith('-entry')] == entries


def test_missed_signal_is_not_ordered_after_a_late_start(tmp_path):
    # start at 10:30 NY: the 09:56 breakout is only documented as historical
    result = run(tmp_path, paper.approval_token(ACCOUNT, LIVE_DAY),
                 wall_start=OPEN + pd.Timedelta(hours=1))
    assert not [r for r in result.client.submitted if r.client_order_id.endswith('-entry')]
    signal = load(result.out, 'signals/' + sim.minute_key(OPEN + pd.Timedelta(minutes=26), 'AAA'))
    assert signal['status'] == 'HISTORICAL_ONLY_STARTUP_OR_RESTART'


def test_replayed_or_delayed_runs_refuse_an_order_hook(tmp_path):
    with pytest.raises(ValueError, match='Echtzeitbetrieb'):
        sim.run_dryrun(None, None, tmp_path, C, S, sim.DryRunSettings(),
                       replay=dict(kind='delayed'), order_hook=object())
