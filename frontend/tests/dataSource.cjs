const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {stripTypeScriptTypes}=require('node:module');
const context={exports:{}};
vm.runInNewContext(stripTypeScriptTypes(fs.readFileSync(require('node:path').join(__dirname,'../src/dataSource.ts'),'utf8').replace('export ',''))+'\nexports.check=isSyntheticInvoice;',context);
test('synthetic fixtures and explicit demo metadata never count as business history',()=>{
 for(const invoice of [{filename:'SYN-P-001.pdf'},{filename:'SYN-S-002.jpeg'},{invoice_number:'SYN-S-002'},{source_type:'DEMO_RECEIPT'},{source_type:'SYNTHETIC_WORKFLOW_TEST'},{synthetic:true}])assert.equal(context.exports.check(invoice),true);
});
test('ordinary business invoices and similar supplier names stay real',()=>{
 for(const filename of ['AP-55.pdf','SYN-TECH-001.pdf','Synthetic supplier invoice.pdf','AP-SYN-001.pdf'])assert.equal(context.exports.check({filename,source_type:'UPLOAD'}),false);
});
