"""Assemble US_Aktien_Bot_V1_6_1_Backtest_Research_Cockpit.ipynb from V1.6 module and cell files.

    python tools/build_v16.py <module_dir> <cells_dir>

Module cells are written from <module_dir>/*.py (in MODULES order); the text and
code cells from <cells_dir>/*.md|*.py. V1.5 itself is never modified.
Edit later with tools/notebook_modules.py (extract/update)."""
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'US_Aktien_Bot_V1_5_Dual_IEX_SIP_Cockpit.ipynb'
TARGET = ROOT / 'US_Aktien_Bot_V1_6_1_Backtest_Research_Cockpit.ipynb'
MODULES = ['us_orb_test_v02.py', 'us_orb_scanner_v03.py', 'bot_mode_v16.py', 'orb_portfolio_v15.py',
           'orb_sim_v16.py', 'orb_cockpit_v16.py', 'orb_replay_v16.py', 'hist_data_v16.py',
           'data_access_v16.py', 'research_export_v16.py', 'backtest_v16.py', 'bot_accounts_v15.py',
           'sip_shadow_bot_v16.py', 'paper_orders_v16.py', 'iex_paper_bot_v16.py', 'dual_bot_v16.py',
           'control_panel_v16.py']


def cell(kind, text):
    item = dict(cell_type=kind, id=uuid.uuid5(uuid.NAMESPACE_URL, text[:200]).hex[:12],
                metadata={}, source=text.splitlines(keepends=True))
    if kind == 'code':
        item.update(execution_count=None, outputs=[])
    return item


def build(module_dir, cells_dir):
    module_dir, cells_dir = Path(module_dir), Path(cells_dir)
    notebook = json.loads(BASE.read_text(encoding='utf-8'))
    notebook['metadata'].setdefault('colab', {})['name'] = TARGET.name
    part = lambda name: (cells_dir / name).read_text(encoding='utf-8')
    cells = [cell('markdown', part('00_intro.md')),
             cell('code', '%pip -q install alpaca-py requests\n'),
             cell('code', part('02_settings.py'))]
    for name in MODULES:
        cells.append(cell('code', '%%writefile /content/' + name + '\n' +
                          (module_dir / name).read_text(encoding='utf-8')))
    cells += [cell('markdown', part('20_start.md')), cell('code', part('21_start.py')),
              cell('markdown', part('30_after.md')), cell('code', part('31_after.py'))]
    notebook['cells'] = cells
    TARGET.write_text(json.dumps(notebook, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')


if __name__ == '__main__':
    build(sys.argv[1], sys.argv[2])
