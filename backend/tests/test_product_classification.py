import unittest
from app.services.product_classification import infer_product_classification, classified_product

class ClassificationTests(unittest.TestCase):
    def test_displayed_catalogue(self):
        cases = [
            ('DELL 22 inch LED MONITOR SE2225HM', 'Monitor', 'Dell', 'SE2225HM'),
            ('FRONTECH TONER CARTRIDGE Q2612A TF0126','Toner cartridge','Frontech','Q2612A / TF0126'),
            ('IMAGE STAR TONER CARTRIDGE (12A)','Toner cartridge','Image Star','12A'),
            ('IMAGE STAR TONER CARTRIDGE (88A ULTIMATE)','Toner cartridge','Image Star','88A ULTIMATE'),
            ('IMAGE TONER CARTRIDGE (88A)','Toner cartridge','Image Toner','88A'),
            ('IMAGESTAR 2365 TONER CARTRIDGE','Toner cartridge','Image Star','2365'),
            ('MARX 4U RACK 450-D','Equipment rack','Marx','4U / 450-D'),
        ]
        for name, kind, brand, model in cases:
            with self.subTest(name=name): self.assertEqual(infer_product_classification(name),dict(product_type=kind,brand=brand,model=model))

    def test_brand_does_not_imply_product_type(self):
        self.assertEqual(infer_product_classification('Dell')['product_type'],None)
        self.assertEqual(infer_product_classification('Dell Wireless Mouse 118')['product_type'],'Mouse')

    def test_manual_metadata_and_identity_are_preserved(self):
        product=dict(id='42',sku='AUTO-123',name='Dell Monitor SE2225HM',brand='Reviewed brand',product_type='Reviewed type',model='Reviewed model',hsn_sac='852852',quantity_on_hand=6,unit_cost=5076.27)
        before=dict(product);result=classified_product(product)
        self.assertEqual(product,before)
        for key,value in before.items():self.assertEqual(result[key],value)
        self.assertEqual(set(result['classification_sources'].values()),{'saved'})

    def test_missing_fields_are_suggested_without_tax_guessing(self):
        row=classified_product(dict(name='Dell Monitor SE2225HM',sku='stable',hsn_sac=None,brand='',product_type='Unclassified'))
        self.assertEqual(row['product_type'],'Monitor');self.assertEqual(row['brand'],'Dell')
        self.assertIsNone(row['hsn_sac']);self.assertEqual(row['sku'],'stable')
        self.assertEqual(row['classification_sources']['brand'],'name_derived')

    def test_unrecognized_names_require_review(self):
        row=classified_product({'name':'Special replacement part','quantity_on_hand':2})
        self.assertIsNone(row['brand']);self.assertIsNone(row['model']);self.assertIsNone(row['product_type'])
        self.assertEqual(set(row['classification_sources'].values()),{'needs_review'})

    def test_stock_capacity_does_not_become_a_tax_code(self):
        self.assertEqual(infer_product_classification('1 TB WD Purple Hard Disk')['model'],'1 TB / PURPLE')
        self.assertEqual(infer_product_classification('4 inch Thermal Paper')['product_type'],'Thermal paper')

    def test_api_returns_classification_without_mutating_saved_record(self):
        from app.api.routes.inventory import _product_dict
        row=dict(id='x',sku='x',name='Dell Monitor SE2225HM',quantity_on_hand=3,reorder_point=0,unit_cost=5)
        result=_product_dict(row)
        self.assertEqual(result['brand'],'Dell');self.assertEqual(result['product_type'],'Monitor')
        self.assertNotIn('brand',row);self.assertEqual(row['quantity_on_hand'],3)
