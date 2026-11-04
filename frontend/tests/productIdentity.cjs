const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {stripTypeScriptTypes}=require('node:module');
const source=fs.readFileSync(require('node:path').join(__dirname,'../src/productIdentity.ts'),'utf8');
const context={exports:{}};
vm.runInNewContext(stripTypeScriptTypes(source.replaceAll('export ',''))+'\nexports.matches=productMatchesDescription;',context);
test('confirmed shorter description uses the same product',()=>{
  assert.equal(context.exports.matches({name:'DELL 22 inch LED MONITOR SE2225HM',aliases:['DELL 22 inch LED MONITOR']},'Dell 22 inch LED Monitor'),true);
});
test('a missing model does not silently merge unrelated stock without a learned alias',()=>{
  const product={name:'DELL 22 inch LED MONITOR SE2225HM'};
  assert.equal(context.exports.matches(product,'DELL 22 inch LED MONITOR'),false);
  assert.equal(context.exports.matches(product,'DELL 22 inch LED MONITOR SE2222H'),false);
  assert.equal(context.exports.matches(product,''),false);
});
test('word order and hard disk spacing follow backend identity normalization',()=>{
  assert.equal(context.exports.matches({name:'WD 1 TB Hard Disk'},'Harddisk WD 1TB'),true);
  assert.equal(context.exports.matches({name:'WD 1 TB Hard Disk'},'Harddisk WD 2TB'),false);
});
