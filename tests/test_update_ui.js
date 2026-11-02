/* Browser update states without a browser or any device writes. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname, '../bridge/web/index.html'), 'utf8');
const elements = new Map();
const element = id => {
  if (!elements.has(id)) elements.set(id, {value: '', textContent: '', style: {}});
  return elements.get(id);
};
element('program-path').value = '/test/main.py';
const calls = [];
const context = vm.createContext({$: element, programUpdating: false, programStatus: 'idle',
  programUpdateSupported: false, serialConnected: true, selectedProgramFile: null,
  pending: false, syncing: false, frameVersion: 'old', activeSerialPort: 'fake',
  document: {hidden: false},
  AbortSignal, URLSearchParams, setTimeout() {}, loadPorts() {}, setConnectionStatus() {},
  apply() { calls.push('full-state'); },
  fetch: async url => { calls.push(url); return {ok: true, json: async () => ({program_update: {status: 'uploading', progress: 47}})}; }
});
vm.runInContext(html.slice(html.indexOf('function programBusy('), html.indexOf('function apply(s)')), context);
vm.runInContext(html.slice(html.indexOf('async function update(){'), html.indexOf('let resetSaving=')), context);
vm.runInContext(html.slice(html.indexOf('async function updateProgram(){'), html.indexOf('async function loadPorts(')), context);
(async () => {
  for (const status of ['preparing', 'uploading', 'restarting']) {
    context.applyProgramState({program_update: {status, progress: 47}, serial: {connected: true, device_program_update: 1}});
    assert.equal(element('update-program').disabled, true);
    assert.equal(element('program-progress').textContent, status === 'restarting' ? '等待重启' : '47%');
  }
  for (const status of ['failed', 'success', 'idle']) {
    context.applyProgramState({program_update: {status, progress: 100}});
    assert.equal(element('update-program').disabled, false);
  }
  context.programUpdating = true;
  context.applyProgramState({program_update: {status: 'idle', progress: 0}});
  assert.equal(element('update-program').disabled, true);
  context.programUpdating = false;
  context.programStatus = 'uploading';
  await context.update();
  assert.deepEqual(calls, ['/api/program-state']);
  assert.equal(element('update-program').disabled, true);
  calls.length = 0;
  context.programStatus = 'idle';
  await context.update();
  assert.match(calls[0], /^\/api\/state\?/);
  assert.equal(calls[1], 'full-state');
  context.fetch=async () => ({ok:true,json:async () => ({program_update:{status:'success',progress:100,message:'previous success'}})});
  element('program-path').value='';
  element('update-program').disabled=false;
  await context.updateProgram();
  assert.equal(element('update-program').disabled,true);
  assert.match(element('program-status').textContent,/更新失败.*请先选择/);
  assert.ok(!html.includes('program-overlay'));
  console.log('Update status, failure recovery and progress-only polling passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
