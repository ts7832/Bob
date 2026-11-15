"""Deploy Aleks's model service (tools/modal_infer.py, unchanged) with a GPU fallback list, so a busy GPU type does not
stall answers: Modal takes the first of L4, A10G, L40S that has capacity (2026-11-15: L4 had none for minutes).

    MODAL_PROFILE=oliver-universitapps modal deploy library/deploy_infer.py
"""
import os, pathlib, runpy

import modal

GPUS = os.environ.get("BOB_INFER_GPUS", "L4,A10G,L40S").split(",")
_cls = modal.App.cls


def cls(self, *a, **kw):
    if isinstance(kw.get("gpu"), str):
        kw["gpu"] = GPUS
    return _cls(self, *a, **kw)


modal.App.cls = cls
ns = runpy.run_path(str(pathlib.Path(__file__).resolve().parent.parent / "tools" / "modal_infer.py"), run_name="modal_infer")
APP = ns["APP"]
app = APP
