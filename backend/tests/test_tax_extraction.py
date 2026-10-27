import unittest
from decimal import Decimal
from app.services.textract_results import _normalized_payload


class TaxExtractionTests(unittest.TestCase):
    def extract(self, fields):
        return _normalized_payload({'expense_documents':[{'summary_fields':fields,'line_items':[]}]})

    def field(self, kind, value, label='', confidence=99):
        return {'type':kind,'value':value,'label':label,'confidence':confidence}

    def test_total_tax_does_not_double_count_components_and_table_copies(self):
        result=self.extract([self.field('TAX','41.95','OUTPUT SGST'),self.field('TAX','41.95','OUTPUT CGST'),
            self.field('TAX','83.90','Total Tax Amount'),self.field('TAX','41.95','Amount')])
        self.assertEqual(result['tax_total'],Decimal('83.90'))

    def test_components_are_added_once_when_total_missing(self):
        result=self.extract([self.field('TAX','41.95','OUTPUT SGST'),self.field('TAX','41.95','OUTPUT CGST'),
            self.field('OTHER','9% 41.95','CGST Rate Amount',95)])
        self.assertEqual(result['tax_total'],Decimal('83.90'))

    def test_tax_identifier_and_pan_are_not_tax_money(self):
        result=self.extract([self.field('TAX_PAYER_ID','AGJPP4564B',"Company PAN"),
            self.field('VENDOR_GST_NUMBER','33ABCDE1234F1Z5'),self.field('RECEIVER_GST_NUMBER','33ABCDE1234F1Z5')])
        self.assertIsNone(result['tax_total'])
        self.assertEqual(result['vendor_gstin'],'33ABCDE1234F1Z5')
        self.assertEqual(result['receiver_gstin'],'33ABCDE1234F1Z5')

    def test_repeated_unlabelled_tax_is_not_summed(self):
        result=self.extract([self.field('TAX','18.00','Tax'),self.field('TAX','18.00','Amount',80)])
        self.assertEqual(result['tax_total'],Decimal('18.00'))

    def test_percentage_only_is_not_an_amount(self):
        self.assertIsNone(self.extract([self.field('TAX','18%','GST rate')])['tax_total'])


if __name__=='__main__':unittest.main()
