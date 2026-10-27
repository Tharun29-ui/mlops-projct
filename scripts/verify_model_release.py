"""Offline release checks: provenance, portable prediction and holdout isolation."""
import copy,hashlib,importlib.util,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from app.services.monthly_models import features
spec=importlib.util.spec_from_file_location('trainer',ROOT/'scripts/train_monthly_models.py')
trainer=importlib.util.module_from_spec(spec);spec.loader.exec_module(trainer)
source=ROOT/'ml/datasets/all-real/corpus.json'
bundle=json.loads((ROOT/'ml/artifacts/monthly/model.json').read_text())
assert hashlib.sha256(source.read_bytes()).hexdigest()==bundle['training_data_sha256']
assert bundle['source_audit']['synthetic_rows']==0
assert (ROOT/'backend/app/data/monthly-model-bundle.json').read_bytes()==(ROOT/'ml/artifacts/monthly/model.json').read_bytes()
# Deliberately synthetic unit-test rows, never written to the trained corpus.
rows=[]
for month in range(2,11):
    for key in range(3):
        history=[{'date':f'2025-{month-1:02d}-01','value':2+key}]
        rows.append({'date':f'2025-{month:02d}-01','key':str(key),
            'x':features(history,f'2025-{month:02d}-01',name='Toner 88A'),
            'last':2+key,'actual':3+key,'quantity_group':'toner:piece'})
first=trainer.evaluate_model(rows,'demand')
changed=copy.deepcopy(rows)
for row in changed:
    if row['date']>=first['test_start']:row['actual']*=100
second=trainer.evaluate_model(changed,'demand')
assert first['selected_algorithm']==second['selected_algorithm']
assert first['validation_scores']==second['validation_scores']
assert first['evaluation_model']==second['evaluation_model']
assert first['test_metrics']!=second['test_metrics']
assert len(bundle['models']['demand']['candidate_models'])==5
gate=bundle['models']['demand']['quality_gate'];metrics=bundle['models']['demand']['test_metrics']
assert gate['passed']==(metrics['rows']>=gate['minimum_test_rows'] and metrics['wape']<=gate['maximum_wape'] and metrics['mae']<=bundle['models']['demand']['baseline_metrics']['mae'])
result={'model_version':bundle['version'],'data_hash_verified':True,'packaged_model_identical':True,
    'synthetic_training_rows':0,'heldout_target_perturbation_changes_validation_selection':False,
    'heldout_target_perturbation_changes_evaluation_weights':False,
    'portable_sklearn_reproduction':'asserted for every non-baseline fit during training',
    'demand_quality_gate_blocks_promotion':not gate['passed'],'test_probe_data':'Synthetic unit-test rows only; never part of real training artifacts'}
target=ROOT/'demo-results/2026-10-24/model-strengthening/model-release-check.json'
target.write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2))
