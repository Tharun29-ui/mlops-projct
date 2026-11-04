const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {stripTypeScriptTypes} = require('node:module');
const source = fs.readFileSync(require('node:path').join(__dirname, '../src/auth.ts'), 'utf8');

function setup(fetch) {
  const values = new Map([['abos_access_token', 'expired'], ['abos_refresh_token', 'refresh-old']]);
  const storage = {getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key)};
  const context = {exports: {}, Headers, URLSearchParams, fetch, sessionStorage: storage,
    window: {location: {origin: 'https://app.example', assign() {}, reload() {}}},
    __env: {VITE_COGNITO_DOMAIN: 'https://auth.example', VITE_COGNITO_CLIENT_ID: 'test-client'}};
  const code = stripTypeScriptTypes(source.replaceAll('import.meta.env', '__env').replaceAll('export ', '')) + '\nObject.assign(exports, {apiFetch, signOut});';
  vm.runInNewContext(code, context);
  return {auth: context.exports, storage};
}
const okTokens = () => Response.json({access_token: 'fresh', refresh_token: 'refresh-new'});

test('simultaneous expired API calls share one refresh and save the rotated token', async () => {
  let refreshes = 0;
  const {auth, storage} = setup(async (url, init) => {
    if (String(url).includes('/oauth2/token')) {refreshes++; await new Promise(resolve => setTimeout(resolve, 10)); return okTokens();}
    return new Response('', {status: init.headers.get('Authorization') === 'Bearer fresh' ? 200 : 401});
  });
  const responses = await Promise.all(Array.from({length: 5}, () => auth.apiFetch('/api')));
  assert.ok(responses.every(r => r.status === 200));
  assert.equal(refreshes, 1);
  assert.equal(storage.getItem('abos_refresh_token'), 'refresh-new');
});

test('a delayed old 401 uses the token already refreshed by another request', async () => {
  let refreshes = 0;
  const {auth} = setup(async (url, init) => {
    if (String(url).includes('/oauth2/token')) {refreshes++; return okTokens();}
    if (init.headers.get('Authorization') === 'Bearer fresh') return new Response('', {status: 200});
    if (url === '/slow') await new Promise(resolve => setTimeout(resolve, 25));
    return new Response('', {status: 401});
  });
  const responses = await Promise.all([auth.apiFetch('/fast'), auth.apiFetch('/slow')]);
  assert.ok(responses.every(r => r.status === 200));
  assert.equal(refreshes, 1);
});

test('sign-out during refresh prevents credentials being restored', async () => {
  let release;
  let started;
  const ready = new Promise(resolve => {started = resolve;});
  const {auth, storage} = setup(async url => {
    if (String(url).includes('/oauth2/token')) {started(); return new Promise(resolve => {release = resolve;});}
    return new Response('', {status: 401});
  });
  const pending = auth.apiFetch('/api');
  await ready;
  auth.signOut();
  release(okTokens());
  assert.equal((await pending).status, 401);
  assert.equal(storage.getItem('abos_access_token'), null);
  assert.equal(storage.getItem('abos_refresh_token'), null);
});

test('failed refresh preserves the original unauthorized response', async () => {
  const {auth} = setup(async url => {
    if (String(url).includes('/oauth2/token')) throw new Error('network unavailable');
    return new Response('', {status: 401});
  });
  assert.equal((await auth.apiFetch('/api')).status, 401);
});
