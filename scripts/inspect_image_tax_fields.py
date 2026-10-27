import json
from pathlib import Path
from audit_invoice_directions import session


def main():
    root=Path(__file__).resolve().parents[1]
    folder=root/'demo-results/2026-10-23/final-check'
    proof=json.loads((folder/'image-upload-checkpoint.json').read_text())
    outputs=json.loads((root/'infra/fresh-demo-deploy-outputs.json').read_text())['AutonomousBusinessOS-trial']
    aws=session()
    job=aws.resource('dynamodb').Table(outputs['InvoiceAnalysisJobTableName']).get_item(Key={'idempotency_key':proof['extraction_id']},ConsistentRead=True)['Item']
    data=aws.client('s3').get_object(Bucket=outputs['InvoiceBucketName'],Key=job['normalized_result_key'])['Body'].read()
    (folder/'image-normalized-extraction.json').write_bytes(data)
    fields=json.loads(data)['expense_documents'][0]['summary_fields']
    print(json.dumps([f for f in fields if 'TAX' in str(f.get('type')) or any(s in str(f.get('label','')).lower() for s in ['gst','tax'])],indent=2))


if __name__=='__main__':main()
