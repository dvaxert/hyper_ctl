#!/usr/bin/env python3
"""Веб-интерфейс hyper_ctl: python3 hyper_ui.py [--demo] [--port 8075] [--no-browser]

Пока UI запущен, он же крутит движок эффектов. Профиль сохраняется в ~/.config/hyper_ctl/.
"""
import argparse
import json
import os
import re
import signal
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import effects as fx
from hyper_ctl import KEY_NAMES, Keyboard, Lamp, lamp_groups, name_lamps

U = fx.U

DEMO_ROWS = [
    ["esc", "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12", None, "delete"],
    ["grave", "1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "minus", "equal", ("backspace", 2), "home"],
    [("tab", 1.5), "q", "w", "e", "r", "t", "y", "u", "i", "o", "p", "lbracket", "rbracket", ("backslash", 1.5), "pageup"],
    [("capslock", 1.75), "a", "s", "d", "f", "g", "h", "j", "k", "l", "semicolon", "quote", ("enter", 2.25), "pagedown"],
    [("lshift", 2.25), "z", "x", "c", "v", "b", "n", "m", "comma", "dot", "slash", ("rshift", 1.75), "up", "end"],
    [("lctrl", 1.25), ("lmeta", 1.25), ("lalt", 1.25), ("space", 6.25 / 3), ("space", 6.25 / 3), ("space", 6.25 / 3),
     ("ralt", 1.25), (None, 1.25), "left", "down", "right"],
]


class DemoKeyboard:
    """Фейковая клавиатура для проверки UI без устройства."""
    interval = 1 / 30
    path = "demo"

    def __init__(self):
        codes = {v: k for k, v in KEY_NAMES.items()}
        self.lamps = []
        for row, keys in enumerate(DEMO_ROWS):
            x = 0.5
            for k in keys:
                name, w = k if isinstance(k, tuple) else (k, 1)
                self.lamps.append(Lamp(len(self.lamps), int((x + w / 2) * U), int((row + 0.8) * U), True, codes.get(name, 0)))
                x += w
        for side_x in (0.25, 17.2):
            for j in range(7):
                self.lamps.append(Lamp(len(self.lamps), int(side_x * U), int((0.75 + j * 0.55) * U), True, 0))
        name_lamps(self.lamps)
        self.count = len(self.lamps)

    def rescan(self): return False
    def set_colors(self, colors): pass
    def fill(self, rgb): pass
    def set_autonomous(self, enabled): pass


def preset_path(name):
    if not re.fullmatch(r"[\w\- ]{1,40}", name):
        raise ValueError("Имя профиля: буквы, цифры, пробел, - и _ (до 40 символов)")
    return os.path.join(fx.PRESETS_DIR, name + ".json")


def list_presets():
    try:
        return sorted(f[:-5] for f in os.listdir(fx.PRESETS_DIR) if f.endswith(".json"))
    except OSError:
        return []


PAGE = r"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>HyperX Control</title>
<style>
:root{--bg:#121317;--panel:#1c1e24;--panel2:#24272f;--key:#2b2e37;--line:#33363f;--text:#e8e8ec;--muted:#8b8f99;--accent:#e2234a;--ok:#2fbf71}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.4 system-ui,sans-serif}
main{max-width:1240px;margin:0 auto;padding:20px 16px 40px}
header{display:flex;flex-wrap:wrap;align-items:center;gap:14px;margin-bottom:16px}
h1{font-size:20px;margin:0;letter-spacing:.3px}
h1 b{color:var(--accent)}
.dev{color:var(--muted);font-size:13px}
.dev i{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--ok);margin-right:6px}
.dev.off i{background:#666}
.grow{flex:1}
.seg{display:inline-flex;background:var(--panel);border-radius:9px;padding:3px}
.seg button{background:none;padding:6px 12px}
.seg button.on{background:var(--accent)}
button{background:var(--panel2);color:var(--text);border:0;border-radius:8px;padding:7px 12px;font:inherit;cursor:pointer;white-space:nowrap}
button:hover{background:#30343e}
button.primary{background:var(--accent)}
button.small{padding:4px 8px;font-size:12px}
button.icon{padding:4px 7px;background:none;color:var(--muted)}
button.icon:hover{color:var(--text);background:var(--panel2)}
select,input[type=text]{background:var(--panel2);color:var(--text);border:1px solid var(--line);border-radius:8px;padding:6px 8px;font:inherit}
input[type=range]{accent-color:var(--accent);width:100%}
input[type=color]{width:34px;height:30px;border:0;background:none;padding:0;cursor:pointer}
label.inline{display:flex;align-items:center;gap:8px;color:var(--muted)}
#kb{background:var(--panel);border-radius:14px;padding:18px;user-select:none;position:relative}
#keys{position:relative;margin:0 auto}
.key{position:absolute;border-radius:6px;background:#1a1b20;display:flex;align-items:flex-end;padding:3px 6px;font-size:11px;cursor:pointer;overflow:hidden;
  box-shadow:inset 0 -3px 0 #0005}
.key .glow{position:absolute;inset:0;pointer-events:none}
.key span{position:relative;text-shadow:0 0 3px #000,0 0 2px #000;color:#fff;font-weight:500}
.key.sel{outline:2px solid #fff;outline-offset:1px}
.key.bar{border-radius:4px;box-shadow:none}
.key.bar span{display:none}
.hwmode #keys{opacity:.35;filter:grayscale(1)}
#hwnote{display:none;position:absolute;inset:0;align-items:center;justify-content:center;font-size:15px;color:var(--text)}
.hwmode #hwnote{display:flex}
.quick{display:flex;flex-wrap:wrap;gap:6px;margin-top:12px;align-items:center}
.quick .lbl{color:var(--muted);margin-right:4px}
.cols{display:grid;grid-template-columns:320px 1fr;gap:16px;margin-top:16px}
@media(max-width:860px){.cols{grid-template-columns:1fr}}
.card{background:var(--panel);border-radius:14px;padding:14px}
.card h2{font-size:13px;text-transform:uppercase;letter-spacing:.8px;color:var(--muted);margin:0 0 10px;display:flex;align-items:center;gap:8px}
.layer{display:flex;align-items:center;gap:6px;padding:7px 8px;border-radius:9px;cursor:pointer;border:1px solid transparent}
.layer:hover{background:var(--panel2)}
.layer.cur{background:var(--panel2);border-color:var(--accent)}
.layer.dis .lname{opacity:.4}
.lname{flex:1;min-width:0}
.lname small{display:block;color:var(--muted);font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tog{width:30px;height:18px;border-radius:9px;background:#444;position:relative;flex:none;cursor:pointer}
.tog::after{content:"";position:absolute;top:2px;left:2px;width:14px;height:14px;border-radius:50%;background:#fff;transition:.15s}
.tog.on{background:var(--accent)}
.tog.on::after{left:14px}
.addrow{display:flex;gap:6px;margin-top:10px}
.addrow select{flex:1}
.field{margin-bottom:14px}
.field>.t{display:flex;justify-content:space-between;color:var(--muted);margin-bottom:5px;font-size:13px}
.field>.t b{color:var(--text);font-weight:500}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.colors{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.chips button.on{background:var(--accent)}
.sw{width:26px;height:26px;border-radius:6px;cursor:pointer;border:2px solid #0006}
.muted{color:var(--muted)}
.row{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
#status{position:fixed;bottom:16px;right:16px;background:var(--panel2);padding:8px 14px;border-radius:9px;opacity:0;transition:.2s;pointer-events:none}
#status.show{opacity:1}
.hint{color:var(--muted);font-size:12px;margin-top:8px}
</style></head><body><main>
<header>
  <h1>Hyper<b>X</b> Control</h1>
  <span class="dev" id="dev"><i></i><span></span></span>
  <span class="grow"></span>
  <label class="inline">Яркость <input type="range" id="bright" min="0" max="1" step="0.01" style="width:140px"> <b id="brightv"></b></label>
  <div class="seg" id="mode"><button data-m="custom">Свои эффекты</button><button data-m="hardware">Встроенные</button></div>
</header>

<div id="kb"><div id="keys"></div><div id="hwnote">Подсветкой управляет сама клавиатура (встроенные эффекты)</div></div>
<div class="quick">
  <span class="lbl">Выделить:</span>
  <button class="small" data-sel="all">Всё</button><button class="small" data-sel="keys">Клавиши</button>
  <button class="small" data-sel="letters">Буквы</button><button class="small" data-sel="digits">Цифры</button>
  <button class="small" data-sel="fkeys">F1–F12</button><button class="small" data-sel="wasd">WASD</button>
  <button class="small" data-sel="arrows">Стрелки</button><button class="small" data-sel="mods">Модификаторы</button>
  <button class="small" data-sel="bars">Боковые полосы</button><button class="small" data-sel="none">Снять</button>
  <span class="muted" id="selinfo"></span>
</div>

<div class="cols">
  <div>
    <div class="card">
      <h2>Слои <span class="muted" style="text-transform:none;letter-spacing:0">(верхний перекрывает нижние)</span></h2>
      <div id="layers"></div>
      <div class="addrow"><select id="addfx"></select><button id="add">+ Слой</button></div>
    </div>
    <div class="card" style="margin-top:16px">
      <h2>Профили</h2>
      <div class="row"><select id="presets" style="flex:1"></select><button id="pload">Загрузить</button></div>
      <div class="row" style="margin-top:8px"><button id="psave">Сохранить как…</button><button id="pdel">Удалить</button></div>
      <div class="row" style="margin-top:8px"><button id="export">Экспорт JSON</button><button id="import">Импорт</button>
        <input type="file" id="file" accept=".json" hidden></div>
    </div>
  </div>
  <div class="card" id="editor"></div>
</div>
<div id="status"></div>
</main><script>
const $=id=>document.getElementById(id);
let S=null, P=null, cur=null, sel=new Set(), drag=false, paintColor="#ff0040";
const FX={}; const NAME={};
const LABEL={esc:"Esc",backspace:"⌫ Back",tab:"Tab",capslock:"Caps",enter:"Enter",lshift:"Shift",rshift:"Shift",lctrl:"Ctrl",rctrl:"Ctrl",
  lalt:"Alt",ralt:"Alt",lmeta:"Win",rmeta:"Win",fn:"Fn",up:"↑",down:"↓",left:"←",right:"→",delete:"Del",home:"Home",end:"End",
  pageup:"PgUp",pagedown:"PgDn",printscreen:"PrtSc",insert:"Ins",grave:"`",minus:"-",equal:"=",lbracket:"[",rbracket:"]",
  backslash:"\\",semicolon:";",quote:"'",comma:",",dot:".",slash:"/",menu:"Menu"};
const WIDTH={backspace:2,tab:1.5,backslash:1.5,capslock:1.75,enter:2.25,lshift:2.25,rshift:1.75,lctrl:1.25,lmeta:1.25,lalt:1.25,
  ralt:1.25,rctrl:1.25,rmeta:1.25,fn:1.25};
const label=n=>n.startsWith("space")?"":(LABEL[n]??(/^lamp\d+$/.test(n)?"•":n.toUpperCase()));
const SWATCHES=["#ff0000","#ff6a00","#ffd400","#00ff40","#00e5ff","#0050ff","#8a00ff","#ff00c8","#ffffff","#000000"];
const TARGETS=[["all","Всё"],["keys","Клавиши"],["bars","Полосы"]];

function toast(t){const s=$("status");s.textContent=t;s.classList.add("show");clearTimeout(toast.t);toast.t=setTimeout(()=>s.classList.remove("show"),1800)}
async function api(path,body,method){const r=await fetch(path,{method:method||(body?"POST":"GET"),body:body&&JSON.stringify(body)});
  if(!r.ok){const t=await r.text();toast("Ошибка: "+t);throw new Error(t)}return r.json()}
let saveT;function save(){clearTimeout(saveT);saveT=setTimeout(()=>api("/api/profile",P),80)}
const curLayer=()=>P.layers.find(l=>l.id===cur);
const isBar=l=>/^[lr]bar\d/.test(l.name);

// ---------- клавиатура ----------
function layout(){
  const lamps=S.lamps, keys=lamps.filter(l=>!isBar(l)), bars=lamps.filter(isBar);
  const rows={};for(const l of keys)(rows[Math.round(l.y/(U/2))]??=[]).push(l);
  for(const r of Object.values(rows)){
    r.sort((a,b)=>a.x-b.x);
    const sp=r.filter(l=>l.name.startsWith("space"));
    for(const l of r)l.w=(WIDTH[l.name]??1)*U;
    if(sp.length){const mid=sp.reduce((s,l)=>s+l.x,0)/sp.length,tot=6.25*U,w=tot/sp.length;
      sp.forEach((l,i)=>{l.w=w;l.cx=mid-tot/2+w*(i+.5)})}
    for(const l of r)l.cx??=l.x;
    for(let i=0;i+1<r.length;i++){const a=r[i],b=r[i+1],ov=(a.cx+a.w/2)-(b.cx-b.w/2);   // убрать перекрытия
      if(ov>1){a.w-=ov/2;a.cx-=ov/4;b.w-=ov/2;b.cx+=ov/4}}
  }
  const minX=Math.min(...keys.map(l=>l.cx-l.w/2)),maxX=Math.max(...keys.map(l=>l.cx+l.w/2));
  const minY=Math.min(...keys.map(l=>l.y))-U/2,maxY=Math.max(...keys.map(l=>l.y))+U/2;
  const BAR=0.45*U,PAD=0.35*U, bx0=minX-PAD-BAR, bx1=maxX+PAD+BAR;
  const box=$("kb").clientWidth-36, s=box/(bars.length?bx1-bx0:maxX-minX), ox=bars.length?bx0:minX, g=3;
  $("keys").style.width=box+"px";$("keys").style.height=(maxY-minY)*s+"px";
  for(const l of keys)Object.assign(l.el.style,{left:(l.cx-l.w/2-ox)*s+g/2+"px",top:(l.y-U/2-minY)*s+g/2+"px",width:l.w*s-g+"px",height:U*s-g+"px"});
  const segH=bars.length?((maxY-minY)-U*.3)/7:0;
  for(const l of bars){const left=l.name[0]==="l", k=+l.name.slice(4)-1;  // lbar1/rbar1 — нижний сегмент
    Object.assign(l.el.style,{left:((left?bx0:maxX+PAD)-ox)*s+"px",top:((maxY-U*.15-(k+1)*segH)-minY)*s+2+"px",width:BAR*s+"px",height:segH*s-4+"px"})}
}
function buildKeys(){
  for(const l of S.lamps){const el=document.createElement("div");el.className="key"+(isBar(l)?" bar":"");el.title=`${l.name} (#${l.id})`;
    el.innerHTML=`<div class="glow"></div><span>${label(l.name)}</span>`;l.el=el;$("keys").append(el);
    el.onmousedown=e=>{drag=true;if(!(e.shiftKey||e.ctrlKey))sel.clear();sel.has(l.id)&&(e.shiftKey||e.ctrlKey)?sel.delete(l.id):sel.add(l.id);drawSel();e.preventDefault()};
    el.onmouseenter=()=>{if(drag){sel.add(l.id);drawSel()}};
    el.ondblclick=()=>{const L=curLayer();if(L?.effect==="paint"){paintSel(paintColor)}}}
  onmouseup=()=>drag=false;onresize=layout;layout()}
function drawSel(){for(const l of S.lamps)l.el.classList.toggle("sel",sel.has(l.id));
  $("selinfo").textContent=sel.size?`выделено: ${sel.size}`:"";if(curLayer())drawTargetInfo()}
document.querySelectorAll("[data-sel]").forEach(b=>b.onclick=()=>{sel.clear();const g=b.dataset.sel;if(g!=="none")S.groups[g].forEach(i=>sel.add(i));drawSel()});

let frameBusy=false;
async function pollFrame(){
  if(!document.hidden&&!frameBusy){frameBusy=true;
    try{const f=await api("/api/frame");
      for(const l of S.lamps){const c=f.colors[l.id];l.el.querySelector(".glow").style.background=c==="#000000"?"none":
        `radial-gradient(circle at 50% 35%,${c} 0%,${c}cc 55%,${c}66 100%)`}
      const d=$("dev");d.classList.toggle("off",!f.devices);d.querySelector("span").textContent=f.devices||"клавиатура не подключена";
    }catch(e){}frameBusy=false}
  setTimeout(pollFrame,50)}

// ---------- слои ----------
function targetText(L){const k=L.keys||["all"];if(L.effect==="paint")return Object.keys(L.params.colors||{}).length+" клавиш раскрашено";
  const t=TARGETS.find(t=>t[0]===k[0]&&k.length===1);return t?t[1]:`${k.length} клавиш`}
function drawLayers(){
  const box=$("layers");box.innerHTML="";
  [...P.layers].reverse().forEach(L=>{const d=document.createElement("div");d.className="layer"+(L.id===cur?" cur":"")+(L.enabled?"":" dis");
    d.innerHTML=`<div class="tog ${L.enabled?"on":""}"></div><div class="lname">${FX[L.effect]?.title||L.effect}<small>${targetText(L)}${L.blend!=="normal"?" · "+(L.blend==="add"?"сложение":"умножение"):""}${L.opacity<1?" · "+Math.round(L.opacity*100)+"%":""}</small></div>
      <button class="icon" data-a="up" title="Выше">▲</button><button class="icon" data-a="down" title="Ниже">▼</button><button class="icon" data-a="del" title="Удалить">✕</button>`;
    d.onclick=e=>{const a=e.target.dataset.a,i=P.layers.indexOf(L);
      if(e.target.classList.contains("tog")){L.enabled=!L.enabled}
      else if(a==="up"&&i<P.layers.length-1){P.layers.splice(i,1);P.layers.splice(i+1,0,L)}
      else if(a==="down"&&i>0){P.layers.splice(i,1);P.layers.splice(i-1,0,L)}
      else if(a==="del"){P.layers.splice(i,1);if(cur===L.id)cur=P.layers.at(-1)?.id}
      else cur=L.id;
      save();drawLayers();drawEditor()};
    box.append(d)});
  if(!P.layers.length)box.innerHTML='<div class="muted">Слоёв нет — добавь эффект ниже</div>'}

function field(title,valueText,inner){const d=document.createElement("div");d.className="field";
  d.innerHTML=`<div class="t"><span>${title}</span><b>${valueText??""}</b></div>`;d.append(inner);return d}
function rangeInput(v,min,max,step,on,fmt){const w=document.createElement("div"),r=document.createElement("input");
  Object.assign(r,{type:"range",min,max,step,value:v});r.oninput=()=>{on(+r.value);w.parentElement.querySelector(".t b").textContent=fmt(+r.value)};w.append(r);return w}
function colorList(list,on){const w=document.createElement("div");w.className="colors";
  const redraw=()=>{w.innerHTML="";list.forEach((c,i)=>{const inp=document.createElement("input");inp.type="color";inp.value=c;
      inp.oninput=()=>{list[i]=inp.value;on()};inp.oncontextmenu=e=>{e.preventDefault();list.splice(i,1);on();redraw()};w.append(inp)});
    const add=document.createElement("button");add.className="small";add.textContent="+";add.onclick=()=>{list.push(list.at(-1)||"#ffffff");on();redraw()};
    const rm=document.createElement("button");rm.className="small";rm.textContent="−";rm.onclick=()=>{list.pop();on();redraw()};w.append(add,rm)};
  redraw();return w}

function drawTargetInfo(){const b=$("tsel");if(b)b.textContent=sel.size?`Выделенные (${sel.size})`:"Выделенные (выдели клавиши)"}
function paintSel(c){const L=curLayer();if(!sel.size)return toast("Сначала выдели клавиши");
  L.params.colors??={};for(const i of sel){const n=S.lamps[i].name;if(c===null)delete L.params.colors[n];else L.params.colors[n]=c}save();drawLayers()}

function drawEditor(){
  const E=$("editor");E.innerHTML="";const L=curLayer();
  if(!L){E.innerHTML='<h2>Слой</h2><div class="muted">Выбери слой слева</div>';return}
  const F=FX[L.effect];L.params??={};
  const h=document.createElement("h2");h.textContent=F.title+(F.reactive?" · реагирует на нажатия":"");E.append(h);

  if(L.effect==="paint"){
    const w=document.createElement("div");w.className="row";
    const ci=document.createElement("input");ci.type="color";ci.value=paintColor;ci.oninput=()=>paintColor=ci.value;w.append(ci);
    SWATCHES.forEach(c=>{const s=document.createElement("div");s.className="sw";s.style.background=c;s.onclick=()=>{paintColor=c;ci.value=c};w.append(s)});
    E.append(field("Цвет",null,w));
    const b=document.createElement("div");b.className="row";
    b.innerHTML='<button class="primary" id="pp">Покрасить выделенные</button><button id="pe">Стереть с выделенных</button><button id="pc">Очистить слой</button>';
    E.append(b);
    $("pp").onclick=()=>paintSel(paintColor);$("pe").onclick=()=>paintSel(null);
    $("pc").onclick=()=>{L.params.colors={};save();drawLayers()};
    const hn=document.createElement("div");hn.className="hint";hn.textContent="Выдели клавиши на клавиатуре (клик, Shift+клик, протяжка), затем «Покрасить». Двойной клик по клавише — покрасить сразу. Нераскрашенные клавиши прозрачны — сквозь них видно нижние слои.";
    E.append(hn)
  }else{
    const chips=document.createElement("div");chips.className="chips";
    for(const [k,t] of TARGETS){const b=document.createElement("button");b.className="small"+((L.keys||["all"]).join()===k?" on":"");b.textContent=t;
      b.onclick=()=>{L.keys=[k];save();drawLayers();drawEditor()};chips.append(b)}
    const bs=document.createElement("button");bs.className="small"+(!TARGETS.some(t=>(L.keys||["all"]).join()===t[0])?" on":"");bs.id="tsel";
    bs.onclick=()=>{if(!sel.size)return toast("Сначала выдели клавиши");L.keys=[...sel].map(i=>S.lamps[i].name);save();drawLayers();drawEditor()};chips.append(bs);
    const show=document.createElement("button");show.className="small";show.textContent="Показать область";
    show.onclick=()=>{sel=new Set(S.resolve(L.keys||["all"]));drawSel()};chips.append(show);
    E.append(field("Где применяется",targetText(L),chips));drawTargetInfo()}

  const g=document.createElement("div");g.className="grid2";
  g.append(field("Непрозрачность",Math.round(L.opacity*100)+"%",rangeInput(L.opacity,0,1,0.01,v=>{L.opacity=v;save();drawLayers()},v=>Math.round(v*100)+"%")));
  const bl=document.createElement("select");[["normal","Обычное (перекрывает)"],["add","Сложение (подсвечивает)"],["multiply","Умножение (маска)"]].forEach(([v,t])=>bl.add(new Option(t,v,false,L.blend===v)));
  bl.onchange=()=>{L.blend=bl.value;save();drawLayers()};g.append(field("Смешивание",null,bl));E.append(g);

  for(const sc of F.schema){if(sc.type==="keymap")continue;
    const v=L.params[sc.name]??structuredClone(sc.default);
    if(sc.type==="range")E.append(field(sc.label,v,rangeInput(v,sc.min,sc.max,sc.step,x=>{L.params[sc.name]=x;save()},x=>x)));
    else if(sc.type==="color"){const c=document.createElement("input");c.type="color";c.value=v;c.oninput=()=>{L.params[sc.name]=c.value;save()};E.append(field(sc.label,null,c))}
    else if(sc.type==="colors"){L.params[sc.name]=v;E.append(field(sc.label,"ПКМ по цвету — удалить",colorList(v,save)))}
    else if(sc.type==="select"){const s=document.createElement("select");sc.options.forEach(([o,t])=>s.add(new Option(t,o,false,v===o)));
      s.onchange=()=>{L.params[sc.name]=s.value;save()};E.append(field(sc.label,null,s))}}
  const rs=document.createElement("button");rs.textContent="Сбросить параметры";rs.onclick=()=>{L.params=L.effect==="paint"?{colors:L.params.colors}:{};save();drawEditor()};E.append(rs)}

$("add").onclick=()=>{const e=$("addfx").value;const L={id:Math.random().toString(16).slice(2,10),effect:e,enabled:true,keys:["all"],opacity:1,
  blend:FX[e].reactive?"add":"normal",params:{}};P.layers.push(L);cur=L.id;save();drawLayers();drawEditor()};

// ---------- общее ----------
function drawGlobal(){
  document.querySelectorAll("#mode button").forEach(b=>b.classList.toggle("on",b.dataset.m===(P.mode||"custom")));
  $("kb").classList.toggle("hwmode",P.mode==="hardware");$("bright").value=P.brightness??1;$("brightv").textContent=Math.round((P.brightness??1)*100)+"%"}
document.querySelectorAll("#mode button").forEach(b=>b.onclick=()=>{P.mode=b.dataset.m;save();drawGlobal()});
$("bright").oninput=()=>{P.brightness=+$("bright").value;$("brightv").textContent=Math.round(P.brightness*100)+"%";save()};

function setProfile(p){P=p;cur=P.layers.at(-1)?.id;drawGlobal();drawLayers();drawEditor()}
async function refreshPresets(){const l=(await api("/api/presets")).presets;const s=$("presets");s.innerHTML="";
  if(!l.length)s.add(new Option("— нет сохранённых —",""));l.forEach(n=>s.add(new Option(n,n)))}
$("psave").onclick=async()=>{const n=prompt("Название профиля:",$("presets").value||"Мой профиль");if(!n)return;
  await api("/api/presets/save",{name:n,profile:P});await refreshPresets();$("presets").value=n;toast("Сохранено: "+n)};
$("pload").onclick=async()=>{const n=$("presets").value;if(!n)return;const r=await api("/api/presets/load",{name:n});setProfile(r.profile);toast("Загружен: "+n)};
$("pdel").onclick=async()=>{const n=$("presets").value;if(!n||!confirm(`Удалить профиль «${n}»?`))return;await api("/api/presets/delete",{name:n});refreshPresets()};
$("export").onclick=()=>{const a=document.createElement("a");a.href=URL.createObjectURL(new Blob([JSON.stringify(P,null,1)],{type:"application/json"}));a.download="hyperx-profile.json";a.click()};
$("import").onclick=()=>$("file").click();
$("file").onchange=async e=>{try{const p=JSON.parse(await e.target.files[0].text());await api("/api/profile",p);setProfile(p);toast("Импортировано")}catch(err){toast("Не удалось прочитать файл")}e.target.value=""};

let U;
(async()=>{S=await api("/api/state");U=S.unit;
  S.effects.forEach(f=>{FX[f.id]=f;$("addfx").add(new Option(f.title+(f.reactive?" ⚡":""),f.id))});
  S.resolve=keys=>keys.flatMap(k=>S.groups[k]??S.lamps.filter(l=>l.name===k).map(l=>l.id));
  buildKeys();setProfile(S.profile);refreshPresets();pollFrame()})();
</script></body></html>"""


class App:
    def __init__(self, kb):
        self.kb = kb
        self.engine = fx.Engine(kb, fx.load_profile())
        self.stop = threading.Event()
        threading.Thread(target=self.engine.run, args=(self.stop,), daemon=True).start()

    def state(self):
        lamps = [{"id": l.id, "name": l.name, "x": l.x, "y": l.y} for l in self.kb.lamps]
        return {"lamps": lamps, "groups": lamp_groups(self.kb.lamps), "unit": U,
                "effects": fx.effects_catalog(), "profile": self.engine.profile}

    def frame(self):
        f = self.engine.frame
        colors = ["#%02x%02x%02x" % f.get(l.id, (0, 0, 0)) for l in self.kb.lamps]
        devices = self.kb.path if getattr(self.kb, "devices", True) else ""
        return {"colors": colors, "devices": devices}

    def set_profile(self, profile):
        if not isinstance(profile, dict) or not isinstance(profile.get("layers"), list):
            raise ValueError("некорректный профиль")
        self.engine.set_profile(profile)
        if self.kb.path != "demo":
            fx.save_profile(profile)


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json"):
            data = (body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/":
                self._send(200, PAGE, "text/html")
            elif self.path == "/api/state":
                self._send(200, app.state())
            elif self.path == "/api/frame":
                self._send(200, app.frame())
            elif self.path == "/api/presets":
                self._send(200, {"presets": list_presets()})
            else:
                self._send(404, "not found", "text/plain")

        def do_POST(self):
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                if self.path == "/api/profile":
                    app.set_profile(body)
                    return self._send(200, {})
                if self.path == "/api/presets/save":
                    fx.save_profile(body["profile"], preset_path(body["name"]))
                    return self._send(200, {})
                if self.path == "/api/presets/load":
                    profile = fx.load_profile(preset_path(body["name"]))
                    app.set_profile(profile)
                    return self._send(200, {"profile": profile})
                if self.path == "/api/presets/delete":
                    os.remove(preset_path(body["name"]))
                    return self._send(200, {})
                self._send(404, "not found", "text/plain")
            except Exception as e:
                self._send(400, str(e), "text/plain")

    return Handler


def main():
    p = argparse.ArgumentParser(description="Веб-интерфейс подсветки")
    p.add_argument("--device")
    p.add_argument("--port", type=int, default=8075)
    p.add_argument("--demo", action="store_true", help="без устройства, для проверки интерфейса")
    p.add_argument("--no-browser", action="store_true")
    args = p.parse_args()

    app = App(DemoKeyboard() if args.demo else Keyboard(args.device))
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(app))
    url = f"http://127.0.0.1:{args.port}/"
    print(f"Открой {url}  (Ctrl+C для выхода)")
    if not args.no_browser:
        webbrowser.open(url)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.stop.set()
        app.kb.set_autonomous(True)  # отдать подсветку клавиатуре, а не замереть на последнем кадре


if __name__ == "__main__":
    main()
