"""Run the current demand/validation/scenario/interface workflow, without calibration.

Default: 05,06,07,09,10,11,12. Pass prefixes to select notebooks; --upstream also
runs the retained 00-04 preprocessing and household demonstration notebooks.
"""
import os
for key in ['OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS']:
    os.environ[key]='1'
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import sys
import time
from pathlib import Path

import nbformat
from nbclient import NotebookClient

HERE = Path(__file__).resolve().parent
from nbconvert import HTMLExporter
args=sys.argv[1:]
prefixes=[a for a in args if not a.startswith('--')]
if not prefixes:prefixes=['00','01','02','03','04','05','06','07','09','10','11','12'] if '--upstream' in args else ['05','06','07','09','10','11','12']
NOTEBOOKS=[p for p in sorted(HERE.glob('[01]*_*.ipynb')) if any(p.name.startswith(q+'_') for q in prefixes)]
REPORTS=HERE/'outputs/current/reports';REPORTS.mkdir(parents=True,exist_ok=True)

for nb_path in NOTEBOOKS:
    t0 = time.time()
    print(f"\n=== running {nb_path.name} ===", flush=True)
    nb = nbformat.read(nb_path, as_version=4)
    client = NotebookClient(nb, timeout=-1, kernel_name="pypsa",
                            resources={"metadata": {"path": str(HERE)}})
    try:
        client.execute()
    finally:
        nbformat.write(nb, nb_path)
    html,_=HTMLExporter().from_notebook_node(nb)
    (REPORTS/(nb_path.stem+'.html')).write_text(html,encoding='utf-8')
    print(f"=== {nb_path.name} done in {(time.time() - t0) / 60:.1f} min ===", flush=True)

if '12' in prefixes:
    import runpy
    runpy.run_path(str(HERE/'tests/verify_scenarios.py'),run_name='__main__')
    runpy.run_path(str(HERE/'build_result_index.py'),run_name='__main__')
