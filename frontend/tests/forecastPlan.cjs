const test = require('node:test');
const assert = require('node:assert/strict');
const {buildMonthlyPlan} = require('../../tmp/model-ui-tests/forecastPlan.js');
const forecast = (month, quantity, product = 'disk') => ({product_id: product, forecast_date: `${month}-01`, predicted_quantity: quantity});
const stock = [{id: 'disk', unit_cost: 50, quantity_on_hand: 1}];

test('monthly profit uses the supplied cost and selling rate', () => {
  const row = buildMonthlyPlan([forecast('2026-11', 3)], stock, () => 75)[0];
  assert.equal(row.revenue, 225);
  assert.equal(row.cost, 150);
  assert.equal(row.revenue - row.cost, 75);
  assert.equal(row.purchaseCash, 100);
});

test('whole-unit purchases carry spare quantities into later months', () => {
  const rows = buildMonthlyPlan([forecast('2026-11', .2), forecast('2026-12', .2)],
    [{id: 'disk', unit_cost: 50, quantity_on_hand: 0}], () => 75);
  assert.equal(rows[0].purchaseCash, 50);
  assert.equal(rows[1].purchaseCash, 0);
});

test('unmapped demo products do not invent costs or profit', () => {
  const row = buildMonthlyPlan([forecast('2026-11', 3, 'DEMO_CAMERA')], stock, () => 75)[0];
  assert.equal(row.priced, 0);
  assert.equal(row.products, 1);
  assert.equal(row.purchaseCash, 0);
});

test('invalid demand values are rejected', () => {
  assert.throws(() => buildMonthlyPlan([forecast('2026-11', NaN)], stock, () => 75));
});
