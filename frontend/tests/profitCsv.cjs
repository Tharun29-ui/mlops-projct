const test=require('node:test');const assert=require('node:assert/strict');
const {profitCsv,readProfitCsv}=require('../../tmp/model-ui-tests/profitCsv.js');
const bill={id:'bill1',invoice_number:'AP,"one"',date:'2026-04-01',net_sales:'100.00',gross_profit:null,source_fingerprint:'a'.repeat(64),profit_note:'Verified, actual cost'};
test('blank profits stay blank and are not invented as zero',()=>{assert.deepEqual(readProfitCsv(profitCsv([bill])),[]);});
test('quoted commas and newlines round trip with a verified loss',()=>{const rows=readProfitCsv(profitCsv([{...bill,gross_profit:'-5.25',profit_note:'Verified, cost\nrecord'}]));assert.equal(rows[0].gross_profit,'-5.25');assert.equal(rows[0].note,'Verified, cost\nrecord');});
test('zero profit is a valid explicit value',()=>{assert.equal(readProfitCsv(profitCsv([{...bill,gross_profit:'0.00'}]))[0].gross_profit,'0.00');});
test('duplicate bill IDs are rejected',()=>{assert.throws(()=>readProfitCsv(profitCsv([{...bill,gross_profit:'1'},{...bill,gross_profit:'2'}])),/duplicate/);});
test('invalid numbers fingerprints and missing notes are rejected',()=>{for(const patch of [{gross_profit:'NaN'},{gross_profit:'1.234'},{gross_profit:'1',source_fingerprint:'bad'},{gross_profit:'1',profit_note:''}])assert.throws(()=>readProfitCsv(profitCsv([{...bill,...patch}])));});
test('an unrelated CSV and unclosed quote are rejected',()=>{assert.throws(()=>readProfitCsv('profit,date\n2,2026-01-01'));assert.throws(()=>readProfitCsv('"unclosed'));});
