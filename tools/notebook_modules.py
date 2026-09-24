"""Extract the %%writefile modules of a notebook, or write edited ones back.

    python tools/notebook_modules.py extract <notebook> <dir>   # notebook -> <dir>/*.py
    python tools/notebook_modules.py update <notebook> <dir>    # <dir>/*.py -> notebook
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOTEBOOKS = [ROOT / 'US_Aktien_Bot_V1_2_Ganztag_Shadow_Simulation.ipynb',
             ROOT / 'US_Aktien_Bot_V1_3_SIP_Shadow_Simulation.ipynb',
             # V1.4.3 supersedes V1.4/V1.4.2 (same module names, so only one is tested)
             ROOT / 'US_Aktien_Bot_V1_4_3_SIP_Cockpit.ipynb']
MARKER = '%%writefile /content/'


def module_cells(notebook):
    for cell in notebook['cells']:
        source = ''.join(cell['source'])
        if cell['cell_type'] == 'code' and source.startswith(MARKER):
            header, _, body = source.partition('\n')
            yield cell, header[len(MARKER):].strip(), body


def extract(notebook_path, target):
    target = Path(target)
    target.mkdir(parents=True, exist_ok=True)
    notebook = json.loads(Path(notebook_path).read_text(encoding='utf-8'))
    for _, name, body in module_cells(notebook):
        (target / name).write_text(body, encoding='utf-8')
    return target


def update(notebook_path, source):
    source = Path(source)
    notebook = json.loads(Path(notebook_path).read_text(encoding='utf-8'))
    for cell, name, _ in module_cells(notebook):
        body = (source / name).read_text(encoding='utf-8')
        text = MARKER + name + '\n' + body
        cell['source'] = text.splitlines(keepends=True)
    Path(notebook_path).write_text(json.dumps(notebook, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')


if __name__ == '__main__':
    {'extract': extract, 'update': update}[sys.argv[1]](sys.argv[2], sys.argv[3])
