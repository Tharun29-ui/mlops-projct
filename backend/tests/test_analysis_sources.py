import unittest
from types import SimpleNamespace
from app.services.analysis_sources import approved_analysis_invoices


class AnalysisSourceTests(unittest.TestCase):
    def test_late_ocr_does_not_reopen_rejected_or_approved_invoice(self):
        from app.repositories.dynamodb_store import merge_textract
        from app.services.textract_results import sync_textract_invoice
        for status in ['REJECTED','APPROVED']:
            invoice={'status':status,'lines':[],'extraction_status':'QUEUED'}
            self.assertEqual(merge_textract(invoice,{'lines':[{'description':'Late OCR'}]},'RESULTS_LOADED'),invoice)
            self.assertEqual(sync_textract_invoice(invoice),invoice)
            self.assertEqual(invoice['lines'],[])
    def test_demo_only_stays_explicitly_demo(self):
        rows=[{'status':'APPROVED','source_type':'DEMO'}]
        self.assertEqual(approved_analysis_invoices(rows),(rows,True,0))

    def test_real_history_excludes_demo(self):
        actual={'status':'APPROVED','source_type':'PDF'}
        demo={'status':'APPROVED','source_type':'DEMO'}
        self.assertEqual(approved_analysis_invoices([demo,actual]),([actual],False,1))

    def test_pending_or_rejected_actual_does_not_change_demo_cohort(self):
        demo={'status':'APPROVED','source_type':'DEMO'}
        rows=[demo,{'status':'NEEDS_REVIEW'},{'status':'REJECTED'}]
        self.assertEqual(approved_analysis_invoices(rows),([demo],True,0))

    def test_demo_receipt_is_excluded_from_real_training(self):
        demo={'status':'APPROVED','source_type':'DEMO_RECEIPT'}
        actual={'status':'APPROVED','source_type':'POS_RECEIPT'}
        self.assertEqual(approved_analysis_invoices([demo]),([demo],True,0))
        self.assertEqual(approved_analysis_invoices([demo,actual]),([actual],False,1))

    def test_local_orm_records_supported(self):
        actual=SimpleNamespace(status='APPROVED',source_type='POS_RECEIPT')
        demo=SimpleNamespace(status='APPROVED',source_type='DEMO')
        self.assertEqual(approved_analysis_invoices([actual,demo]),([actual],False,1))


if __name__=='__main__':unittest.main()
