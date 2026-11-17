// Optional Node.js checks of dashboard logic with a small DOM/canvas test double.
// This checks interactions and drawing data; it is not browser/render verification.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const template = fs.readFileSync(path.join(__dirname, '../src/predictions_dashboard.html'), 'utf8');
const valid = {
  model_id: '</script><script>untrusted text</script>', model_mode: 'trajectory',
  source_kind: 'software_fixture', prediction_valid: true, timestamp_us: '18446744073709550615',
  outside_training_range: false, reason: 'ok', observed_position_m: [1, 2, -3],
  planned_path: [{target_timestamp_us: '18446744073709551615', position_m: [2, 3, -4]}],
  predictions: [{target_timestamp_us: '18446744073709551615', rates_rad_s: [.1, .2, .3], position_m: [1.9, 2.9, -3.9], velocity_m_s: [1, 1, -.9]}]
};
function load(frames, live = false) {
  const elements = new Map(), strokes = [], arcs = [], timers = new Map(); let nextTimer = 1;
  const canvas = new Proxy({stroke() {strokes.push(this.strokeStyle);}, arc(...args) {assert(args.every(Number.isFinite));arcs.push(args);}}, {get: (target, key) => key in target ? target[key] : () => {}});
  function element() {return {textContent: '', hidden: false, disabled: false, value: '1', children: [], className: '', appendChild(child) {this.children.push(child);},replaceChildren() {this.children = [];},getContext() {return canvas;},getBoundingClientRect() {return {width: 1000, height: 430};}};}
  const boot = {mode: live ? 'live' : 'replay', identity: valid, current: frames.at(-1), frames: live ? [] : frames, track: [], accepted: frames.length, rejected: 0, dropped: 0, status: 'valid'};
  const document = {getElementById(id) {if (!elements.has(id)) elements.set(id, element());return elements.get(id);},createElement: element};
  document.getElementById('telemetry').textContent = JSON.stringify(boot);
  const context = vm.createContext({document, window: {devicePixelRatio: 1,addEventListener() {}}, console, AbortSignal, fetch: async () => ({ok: true,json: async () => boot}),setTimeout(callback) {const id = nextTimer++;timers.set(id, callback);return id;},clearTimeout(id) {timers.delete(id);}});
  const script = template.slice(template.lastIndexOf('<script>') + '<script>'.length, template.lastIndexOf('</script>')).replace('__LIVE_MODE__', live ? 'true' : 'false');
  vm.runInContext(script, context);
  return {elements, strokes, arcs, timers, evaluate: expression => vm.runInContext(expression, context), boot};
}
const event = (packet, sequence, status = 'valid') => ({packet, sequence, status});
const invalid = {...valid, prediction_valid: false, reason: 'measurement missing', observed_position_m: null, planned_path: [], predictions: []};
const ui = load([event(valid, 1), event(invalid, 2, 'invalid prediction'), event(valid, 3), event(null, 4, 'rejected malformed packet')]);
assert.match(ui.elements.get('mode').textContent, /REPLAY/);
assert.match(ui.elements.get('source').textContent, /SYNTHETIC/);
assert.equal(ui.elements.get('samples').children.length, 0);
assert.equal(ui.elements.get('model').textContent, valid.model_id);
ui.strokes.length = 0;
ui.elements.get('timeline').value = '0';ui.elements.get('timeline').oninput();
assert.equal(ui.elements.get('anchor').textContent, valid.timestamp_us + ' µs');
assert.match(ui.elements.get('horizon').textContent, /0.001 s/);
assert.equal(ui.elements.get('rate0').textContent, '0.100');
assert.equal(ui.elements.get('samples').children.length, 1);
assert(ui.strokes.includes('#d29242'));assert(ui.strokes.includes('#257c87'));
ui.strokes.length = 0;ui.elements.get('timeline').value = '1';ui.elements.get('timeline').oninput();
assert(!ui.strokes.includes('#257c87'));assert(!ui.strokes.includes('#d29242'));assert.equal(ui.elements.get('samples').children.length, 0);
ui.elements.get('timeline').value = '3';ui.elements.get('timeline').oninput();ui.elements.get('play').onclick();
assert.equal(ui.evaluate('index'), 0);assert(ui.evaluate('playing'));
ui.timers.values().next().value();assert.equal(ui.evaluate('index'), 1);
const rates = {...valid, model_mode: 'rates', observed_position_m: null, planned_path: [], predictions: [{...valid.predictions[0], position_m: null, velocity_m_s: null}]};
const ratesUI = load([event(rates, 1)]);
assert.match(ratesUI.elements.get('pathnote').textContent, /does not predict a flight path/);
assert(!ratesUI.strokes.includes('#257c87'));assert.equal(ratesUI.elements.get('rate1').textContent, '0.200');
const extreme = JSON.parse(JSON.stringify(valid));extreme.observed_position_m = [1.7e308, -1.7e308, 0];extreme.planned_path[0].position_m = [-1.7e308, 1.7e308, 0];extreme.predictions[0].position_m = [1e308, -1e308, 0];
load([event(extreme, 1)]); // Canvas coordinates must stay finite for all contract-valid inputs.
const liveUI = load([event(valid, 1)], true);
assert.match(liveUI.elements.get('mode').textContent, /LIVE/);
liveUI.evaluate("snapshot.current={sequence:1,status:'stale source',packet:null};render()");
assert.equal(liveUI.elements.get('samples').children.length, 0);
assert.equal(liveUI.elements.get('status').textContent, 'stale source');
console.log('dashboard runtime: replay timeline/play, source labels, trajectory/rates, invalid/stale clearing, exact timestamps, extreme finite coordinates passed (DOM test double)');
