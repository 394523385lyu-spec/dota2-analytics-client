from __future__ import annotations

import base64
import html
import io
import json
from pathlib import Path
from typing import Any, Dict, Iterable

from PIL import Image
from ..assets import load_map_image, load_ward_icon
from ..paths import resource_path


ASSET_DIR = resource_path("assets", "wards")
MAP_PATH = ASSET_DIR / "minimap_dota2_1024.jpg"
OBS_PATH = ASSET_DIR / "observer.png"
SEN_PATH = ASSET_DIR / "sentry.png"


def _image_uri(path: Path, size: tuple[int, int] | None = None) -> str:
    if path == MAP_PATH:
        image = load_map_image(path)
    elif path == OBS_PATH:
        image = load_ward_icon(path, "#58A6FF")
    elif path == SEN_PATH:
        image = load_ward_icon(path, "#F2C94C")
    elif path.exists():
        image = Image.open(path)
    else:
        raise FileNotFoundError(f"缺少眼位可视化素材：{path}")
    if size:
        image.thumbnail(size, Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    format_name = "JPEG" if path.suffix.lower() in {".jpg", ".jpeg"} else "PNG"
    if format_name == "JPEG":
        image.convert("RGB").save(buffer, format="JPEG", quality=88, optimize=True)
        mime = "image/jpeg"
    else:
        image.save(buffer, format="PNG", optimize=True)
        mime = "image/png"
    return f"data:{mime};base64,{base64.b64encode(buffer.getvalue()).decode('ascii')}"


def _ward_item(row: Dict[str, Any], side: str) -> Dict[str, Any] | None:
    if row.get("x") is None or row.get("y") is None:
        return None
    ward_type = str(row.get("类型") or "")
    return {
        "match_id": str(row.get("Match ID") or ""),
        "side": side,
        "time": int(row.get("时间(秒)") or 0),
        "type": "obs" if ward_type in {"假眼", "obs"} else "sen",
        "x": float(row.get("x") or 0),
        "y": float(row.get("y") or 0),
        "duration": row.get("持续时间(秒)"),
        "status": str(row.get("消失类型") or ""),
        "expiry": row.get("消失时间(秒)"),
        "player": str(row.get("玩家") or ""),
        "result": str(row.get("结果") or ""),
        "opponent": str(row.get("对手队伍") or ""),
        "camp": str(row.get("我方阵营") or ""),
    }


def _payload(module: Dict[str, Any]) -> Dict[str, Any]:
    items = []
    for row in module.get("mine_details", []):
        item = _ward_item(row, "mine")
        if item:
            items.append(item)
    for row in module.get("opponent_details", []):
        item = _ward_item(row, "opponent")
        if item:
            items.append(item)
    return {"items": items}


def generate_ward_visualization_html(
    module: Dict[str, Any],
    team_name: str,
    output_path: str | Path | None = None,
) -> str:
    data = json.dumps(_payload(module), ensure_ascii=False)
    map_uri = _image_uri(MAP_PATH, (900, 900))
    obs_uri = _image_uri(OBS_PATH, (40, 40))
    sen_uri = _image_uri(SEN_PATH, (40, 40))
    safe_team = html.escape(team_name)
    template = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TEAM__ · 眼位可视化</title>
<style>
:root{--bg:#0d1218;--panel:#151d26;--soft:#202b36;--line:#344454;--text:#edf3f8;--muted:#9dacba;--cyan:#55c8c1;--radiant:#65d381;--dire:#e46b62}
*{box-sizing:border-box} body{margin:0;background:linear-gradient(145deg,#0b1016,#111a23);color:var(--text);font:14px/1.45 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}
.app{min-height:100vh;display:grid;grid-template-columns:350px minmax(560px,1fr);gap:14px;padding:14px}
.panel,.mapPanel{background:rgba(21,29,38,.97);border:1px solid #2d3a47;border-radius:12px;box-shadow:0 16px 40px #0006}
.panel{padding:16px;overflow:auto}.mapPanel{padding:14px;display:flex;flex-direction:column}
h1{font-size:19px;margin:0 0 4px}.muted{color:var(--muted)}.group{background:var(--soft);border:1px solid #2b3946;border-radius:8px;padding:10px;margin-top:9px}
label.title{display:flex;justify-content:space-between;color:#bdc9d4;font-size:12px;margin-bottom:7px}select,input[type=range],input[type=number]{width:100%}
select,input[type=number]{height:34px;color:var(--text);background:#111922;border:1px solid #405264;border-radius:5px;padding:0 8px}
.row{display:flex;gap:7px;flex-wrap:wrap}.pill{background:#18232e;border:1px solid #3b4d5e;border-radius:6px;padding:5px 8px;cursor:pointer}
.campBar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:0 0 10px;padding:10px 12px;background:#1d2833;border-radius:8px}
.badge{display:inline-flex;align-items:center;padding:5px 10px;border-radius:999px;font-weight:700}.radiant{background:#173f2a;color:#8ce4a4}.dire{background:#482323;color:#f28b84}.win{background:#17472b;color:#8be2a2}.loss{background:#4a2325;color:#f29191}.neutral{background:#30404f;color:#d7e2eb}
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:10px}.stat{background:#202b36;border:1px solid #2d3c49;padding:8px;border-radius:8px;text-align:center}.stat b{display:block;font-size:19px;color:var(--cyan)}
.mapWrap{position:relative;width:min(76vh,100%);aspect-ratio:1;margin:auto;border:1px solid #40505f;border-radius:9px;overflow:hidden;background:url('__MAP__') center/cover no-repeat}
#dots,canvas{position:absolute;inset:0;width:100%;height:100%}.ward{position:absolute;transform:translate(-50%,-50%);background-size:contain;background-repeat:no-repeat;filter:drop-shadow(0 1px 3px #000)}
.obs{background-image:url('__OBS__')}.sen{background-image:url('__SEN__')}.opponent{outline:2px solid #ff665f;border-radius:50%}.mine{outline:2px solid #52bfe8;border-radius:50%}
.locator{position:absolute;width:18px;height:18px;border:2px solid #fff;border-radius:50%;transform:translate(-50%,-50%);box-shadow:0 0 0 3px #0008,0 0 12px #fff;pointer-events:none}
.legend{display:flex;gap:18px;justify-content:center;flex-wrap:wrap;margin-top:9px;color:var(--muted)}.legend img{width:18px;height:18px;vertical-align:middle}
.foot{margin-top:8px;text-align:center;color:#7f91a1;font-size:12px}
@media(max-width:900px){.app{grid-template-columns:1fr}.mapWrap{width:100%}}
</style>
</head>
<body>
<main class="app">
<aside class="panel">
<h1>__TEAM__ · 眼位可视化</h1>
<div class="muted">按原脚本口径查看逐场阵营、胜负、阶段、眼位状态与热力分布</div>
<div class="group"><label class="title">比赛</label><select id="match"></select></div>
<div class="group"><label class="title">显示对象</label><div class="row">
<label class="pill"><input type="checkbox" id="mine" checked> 我方</label>
<label class="pill"><input type="checkbox" id="opponent" checked> 对手</label>
</div></div>
<div class="group"><label class="title">眼位类型</label><div class="row">
<label class="pill"><input type="checkbox" id="obs" checked> 假眼</label>
<label class="pill"><input type="checkbox" id="sen" checked> 真眼</label>
</div></div>
<div class="group"><label class="title"><span>时间轴</span><span id="timeText"></span></label><input id="time" type="range" min="0" value="0" step="1"></div>
<div class="group"><label class="title">时间显示方式</label><div class="row">
<label class="pill"><input type="radio" name="timeMode" value="placed" checked> 累计放置</label>
<label class="pill"><input type="radio" name="timeMode" value="alive"> 当前存活</label>
</div></div>
<div class="group"><label class="title">消失状态</label><select id="status">
<option value="all">全部</option><option value="被反掉">只看被反掉</option><option value="自然消失">只看自然消失</option><option value="存活结束/未知">存活结束/未知</option>
</select></div>
<div class="group"><label class="title">比赛阶段</label><div class="row">
<label class="pill"><input type="checkbox" id="early" checked> 前期 0–15</label>
<label class="pill"><input type="checkbox" id="mid" checked> 中期 15–30</label>
<label class="pill"><input type="checkbox" id="late" checked> 后期 30+</label>
</div></div>
<div class="group"><label class="title">视图</label><div class="row">
<label class="pill"><input type="radio" name="view" value="dots" checked> 点位</label>
<label class="pill"><input type="radio" name="view" value="heat"> 热力</label>
</div></div>
<div class="group"><label class="title"><span>图标大小</span><span id="sizeText">20</span></label><input id="size" type="range" min="12" max="34" value="20"></div>
<div class="group"><label class="title"><span>地图内边距</span><span id="marginText">6%</span></label><input id="margin" type="range" min="0" max="15" value="6"></div>
<div class="group"><label class="title">坐标定位</label><div class="row">
<input id="locX" type="number" placeholder="X" style="width:80px"><input id="locY" type="number" placeholder="Y" style="width:80px"><button id="locate" style="flex:1">定位</button>
</div></div>
<p class="muted">蓝色外圈为我方，红色外圈为对手。阵营与胜负按所选比赛单独显示。</p>
</aside>
<section class="mapPanel">
<div class="campBar"><span id="campBadge" class="badge neutral">全部阵营</span><span id="resultBadge" class="badge neutral">全部胜负</span><span id="opponentText" class="muted"></span></div>
<div class="stats"><div class="stat"><b id="count">0</b>当前点位</div><div class="stat"><b id="obsCount">0</b>假眼</div><div class="stat"><b id="senCount">0</b>真眼</div><div class="stat"><b id="dewardCount">0</b>被反掉</div></div>
<div class="mapWrap" id="mapWrap"><canvas id="heat"></canvas><div id="dots"></div></div>
<div class="legend"><span><img src="__OBS__"> 假眼</span><span><img src="__SEN__"> 真眼</span><span>蓝圈：我方</span><span>红圈：对手</span></div>
<div class="foot">比赛数据来自公开比赛详情；“被反掉”为眼位提前消失的口径判断。</div>
</section>
</main>
<script>
const DATA=__DATA__; const items=DATA.items||[];
const match=document.getElementById('match'),time=document.getElementById('time'),timeText=document.getElementById('timeText');
const dots=document.getElementById('dots'),canvas=document.getElementById('heat'),ctx=canvas.getContext('2d');
const ids=[...new Set(items.map(x=>x.match_id))].sort((a,b)=>Number(b)-Number(a));
const meta={};items.forEach(x=>{if(!meta[x.match_id])meta[x.match_id]={result:x.result||'未知',opponent:x.opponent||'未知对手',camp:x.camp||'未知阵营'}});
function resultMark(v){return ['胜','WIN'].includes(v)?'✅':'❌'}
match.innerHTML='<option value="ALL">全部比赛</option>'+ids.map(id=>{const m=meta[id];return `<option value="${id}">${resultMark(m.result)} ${id} · vs ${m.opponent} · ${m.camp}</option>`}).join('');
function fmt(v){v=Math.max(0,Number(v)||0);return `${Math.floor(v/60)}:${String(Math.floor(v%60)).padStart(2,'0')}`}
function maxTime(){const selected=match.value;let data=selected==='ALL'?items:items.filter(x=>x.match_id===selected);return Math.max(1800,...data.map(x=>Number(x.expiry)>0?Number(x.expiry):Number(x.time)+420))}
function syncMeta(){const camp=document.getElementById('campBadge'),result=document.getElementById('resultBadge'),opp=document.getElementById('opponentText');if(match.value==='ALL'){camp.textContent='全部阵营';camp.className='badge neutral';result.textContent='全部胜负';result.className='badge neutral';opp.textContent='';return}const m=meta[match.value];camp.textContent=`我方阵营：${m.camp}`;camp.className='badge '+(m.camp.includes('天辉')?'radiant':m.camp.includes('夜魇')?'dire':'neutral');result.textContent=`比赛结果：${m.result}`;result.className='badge '+(['胜','WIN'].includes(m.result)?'win':'loss');opp.textContent=`对手：${m.opponent}`}
function syncTime(){time.max=Math.ceil(maxTime()/60)*60;time.value=time.max;syncMeta();render()}
function phaseEnabled(v){const minute=Number(v)/60;return minute<15?document.getElementById('early').checked:minute<30?document.getElementById('mid').checked:document.getElementById('late').checked}
function filtered(){
 const selected=match.value,t=Number(time.value),status=document.getElementById('status').value;
 const alive=document.querySelector('input[name=timeMode]:checked').value==='alive';
 return items.filter(x=>(selected==='ALL'||x.match_id===selected)&&
  (x.side==='mine'?document.getElementById('mine').checked:document.getElementById('opponent').checked)&&
  (x.type==='obs'?document.getElementById('obs').checked:document.getElementById('sen').checked)&&
  Number(x.time)<=t&&(!alive||!(Number(x.expiry)>0)||Number(x.expiry)>t)&&phaseEnabled(x.time)&&(status==='all'||x.status===status));
}
function pos(x,y){const useOffset=items.length&&items.every(v=>v.x>=40&&v.x<=220&&v.y>=40&&v.y<=220);const min=useOffset?64:0,max=useOffset?192:127,m=Number(document.getElementById('margin').value)/100;let px=(x-min)/(max-min),py=1-(y-min)/(max-min);return [100*(m+px*(1-2*m)),100*(m+py*(1-2*m))]}
function drawDots(data){dots.innerHTML='';dots.style.display='block';canvas.style.display='none';const size=Number(document.getElementById('size').value);data.forEach(x=>{const [px,py]=pos(x.x,x.y);if(px<0||px>100||py<0||py>100)return;const d=document.createElement('div');d.className=`ward ${x.type} ${x.side}`;d.style.width=size+'px';d.style.height=size+'px';d.style.left=px+'%';d.style.top=py+'%';d.title=`${x.side==='mine'?'我方':'对手'} ${x.type==='obs'?'假眼':'真眼'}\n阵营：${x.camp}\n结果：${x.result}\n玩家：${x.player}\n时间：${fmt(x.time)}\n状态：${x.status}\n坐标：${x.x}, ${x.y}`;dots.appendChild(d)})}
function color(t){if(t<.33)return [0,100+400*t,255];if(t<.66)return [255*(t-.33)/.33,220,80];return [255,180*(1-(t-.66)/.34),0]}
function drawHeat(data){dots.style.display='none';canvas.style.display='block';const r=document.getElementById('mapWrap').getBoundingClientRect();canvas.width=Math.floor(r.width);canvas.height=Math.floor(r.height);ctx.clearRect(0,0,canvas.width,canvas.height);const buffer=new Float32Array(canvas.width*canvas.height),sigma=Math.max(8,canvas.width/35),rad=sigma*3;data.forEach(item=>{const [px,py]=pos(item.x,item.y),cx=px/100*canvas.width,cy=py/100*canvas.height;for(let y=Math.max(0,Math.floor(cy-rad));y<Math.min(canvas.height,Math.ceil(cy+rad));y++)for(let x=Math.max(0,Math.floor(cx-rad));x<Math.min(canvas.width,Math.ceil(cx+rad));x++){const dx=x-cx,dy=y-cy;buffer[y*canvas.width+x]+=Math.exp(-(dx*dx+dy*dy)/(2*sigma*sigma))*(item.status==='被反掉'?1.25:1)}});let max=0;for(const v of buffer)max=Math.max(max,v);const img=ctx.createImageData(canvas.width,canvas.height);for(let i=0;i<buffer.length;i++){const t=max?buffer[i]/max:0,[r,g,b]=color(t),j=i*4;img.data[j]=r;img.data[j+1]=g;img.data[j+2]=b;img.data[j+3]=255*Math.pow(t,.58)}ctx.putImageData(img,0,0)}
function render(){const data=filtered();timeText.textContent=fmt(time.value);document.getElementById('sizeText').textContent=document.getElementById('size').value;document.getElementById('marginText').textContent=document.getElementById('margin').value+'%';document.getElementById('count').textContent=data.length;document.getElementById('obsCount').textContent=data.filter(x=>x.type==='obs').length;document.getElementById('senCount').textContent=data.filter(x=>x.type==='sen').length;document.getElementById('dewardCount').textContent=data.filter(x=>x.status==='被反掉').length;document.querySelector('input[name=view]:checked').value==='heat'?drawHeat(data):drawDots(data)}
document.getElementById('locate').onclick=()=>{document.querySelectorAll('.locator').forEach(x=>x.remove());const x=Number(document.getElementById('locX').value),y=Number(document.getElementById('locY').value);if(!Number.isFinite(x)||!Number.isFinite(y))return;const [px,py]=pos(x,y),dot=document.createElement('div');dot.className='locator';dot.style.left=px+'%';dot.style.top=py+'%';document.getElementById('mapWrap').appendChild(dot)};
match.onchange=syncTime;document.querySelectorAll('input,select').forEach(e=>{if(e!==match)e.addEventListener('change',render)});time.addEventListener('input',render);document.getElementById('size').addEventListener('input',render);document.getElementById('margin').addEventListener('input',render);window.addEventListener('resize',render);syncTime();
</script>
</body></html>"""
    rendered = (
        template.replace("__TEAM__", safe_team)
        .replace("__MAP__", map_uri)
        .replace("__OBS__", obs_uri)
        .replace("__SEN__", sen_uri)
        .replace("__DATA__", data)
    )
    if output_path is not None:
        target = Path(output_path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered, encoding="utf-8")
    return rendered
