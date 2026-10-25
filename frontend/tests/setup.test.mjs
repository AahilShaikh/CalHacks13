import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const html = readFileSync(new URL('../../backend/aida/web/setup.html', import.meta.url), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
function element() {
  const classes = new Set();
  return { value:'', hidden:true, disabled:false, textContent:'', children:[],
    classList: { add:c=>classes.add(c), remove:c=>classes.delete(c), contains:c=>classes.has(c),
      toggle:(c,on)=>on?classes.add(c):classes.delete(c) },
    append(...items) { this.children.push(...items); }, prepend(...items) { this.children.unshift(...items); },
    replaceChildren(...items) { this.children=items; } };
}
const frame = () => ({ ok:true, blob:async()=>new Blob(['frame']) });
function setup(preview=async()=>frame()) {
  const elements = new Map([...html.matchAll(/id="([^"]+)"/g)].map(m=>[m[1],element()]));
  const calls=[];
  const selected={ device_id:'index:1', index:1, name:'USB camera', is_usb:true };
  const fetch = async (path, options={}) => {
    calls.push({ path, method:options.method??'GET' });
    if(path==='/api/v1/camera/frame.jpg')return preview();
    const data = path.endsWith('/devices') ? { devices:[selected], selected }
      : path.endsWith('/calibration') ? { samples:0, calibration:null }
      : path.endsWith('/configuration') ? { ports:[], port:null }
      : { startup_zero:{status:'ready'}, empty_reference_ready:false };
    return { ok:true, json:async()=>data };
  };
  vm.runInNewContext(script, { document:{getElementById:id=>elements.get(id),createElement:element},
    location:{search:''},URLSearchParams,URL:{createObjectURL:()=> 'blob:preview',revokeObjectURL:()=>{}},
    fetch,setTimeout:()=>0 });
  return {elements,calls};
}
async function settle(){for(let i=0;i<5;i++)await new Promise(resolve=>setImmediate(resolve));}

test('setup automatically loads a preview without re-zeroing the turntable', async()=>{
  const {elements,calls}=setup();await settle();
  assert.equal(elements.get('frame').hidden,false);
  assert.match(elements.get('preview-status').textContent,/preview loaded/);
  assert.equal(calls.filter(c=>c.path.endsWith('/frame.jpg')).length,1);
  assert.equal(calls.filter(c=>c.path.endsWith('/initialize')).length,0);
  assert.equal(elements.get('initialize').disabled,false);
});

test('preview loading blocks overlapping zero and camera actions', async()=>{
  let release;
  const pending=new Promise(resolve=>{release=resolve;});
  const {elements,calls}=setup(()=>pending);await settle();
  assert.equal(elements.get('initialize').disabled,true);
  assert.equal(elements.get('sample').disabled,true);
  await elements.get('initialize').onclick();
  assert.equal(calls.filter(c=>c.method==='POST').length,0);
  release(frame());await settle();
  assert.equal(elements.get('initialize').disabled,false);
});

test('preview failures show actionable inline errors and allow a retry',async()=>{
  const {elements}=setup(async()=>({ok:false,status:409,json:async()=>({detail:{code:'busy',message:'Another turntable operation is in progress.'}})}));
  await settle();
  assert.match(elements.get('preview-status').textContent,/Another turntable operation/);
  assert.equal(elements.get('preview-status').classList.contains('error'),true);
  assert.equal(elements.get('frame').hidden,true);
  assert.equal(elements.get('preview').disabled,false);
});
