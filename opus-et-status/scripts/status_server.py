"""Loopback-bound HTTP dashboard for the cryo-ET pipeline.

Handlers only ever READ the poller's cache, so a page load never blocks on SSH.
The single mutating endpoint is CSRF- and Origin-guarded because any website
the user has open can issue requests to localhost.
"""
from __future__ import annotations

import argparse
import dataclasses
import html
import shlex
import json
import math
import secrets
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cluster
import config_edit
import poller as poller_mod
import sources

PAGE = r"""<!doctype html><html><head><meta charset="utf-8">
<title>OPUS-ET status</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Ccircle cx='8' cy='8' r='6' fill='%23e8590c'/%3E%3C/svg%3E">
<style>
 :root{--bg:#f6f4f1;--card:#ffffff;--fg:#1c1b1a;--line:#e6e2db;--line-strong:#d9d3ca;
       --muted:#6d6760;--accent:#e8590c;--green:#2b8a3e;--red:#c92a2a}
 *{box-sizing:border-box}
 [hidden]{display:none!important}
 body{font:14px/1.55 system-ui,-apple-system,'Segoe UI',sans-serif;margin:0 auto;
      padding:1.8rem 2.4rem 4rem;max-width:1280px;background:var(--bg);color:var(--fg)}
 h1{font-size:1.45rem;margin:0 0 1.1rem;letter-spacing:-.01em}
 h2{font-size:.72rem;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);
    margin:1.7rem 0 .5rem;font-weight:600}
 h3{font-size:.72rem;text-transform:uppercase;letter-spacing:.07em;color:var(--accent);
    margin:1.3rem 0 .4rem;font-weight:600}
 code{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:.88em}
 table{border-collapse:collapse;width:100%}
 th{font-size:.68rem;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);
    text-align:left;padding:.45rem .65rem;border-bottom:2px solid var(--line-strong);white-space:nowrap}
 td{text-align:left;padding:.5rem .65rem;border-bottom:1px solid var(--line);vertical-align:middle}
 td span.muted{white-space:nowrap}
 table tr:last-child td{border-bottom:none}
 table tr:hover td{background:#f8f6f3}
 td code{font:inherit}
 td.num{font-variant-numeric:tabular-nums}
 .stale{background:#fff4e6;padding:.65rem .9rem;border:1px solid #ffd8a8;
        border-left:3px solid var(--accent);border-radius:6px;margin-bottom:1rem}
 .waiting{background:#e7f5ff;padding:.65rem .9rem;border:1px solid #a5d8ff;
          border-left:3px solid #1971c2;border-radius:6px;margin-bottom:1rem}
 .att{padding:.55rem .9rem;border:1px solid;border-left-width:3px;
      border-radius:6px;margin-bottom:.45rem}
 .att b{margin-right:.45rem;text-transform:uppercase;font-size:.7rem;letter-spacing:.05em}
 .fn{margin:.2rem 0 1.2rem}
 .fnrow{display:grid;grid-template-columns:20rem 1fr 5.5rem 4rem;gap:.6rem;
        align-items:center;margin:.3rem 0}
 .fnlab{font-size:.85rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 .fnlab code{font-size:.78rem}
 .fbar{position:relative;height:18px;background:var(--line);border-radius:3px;
       overflow:hidden}
 .fbar i{display:block;height:100%;border-radius:3px 0 0 3px}
 .fbar i.pick{background:var(--muted)}
 .fbar i.exp{background:var(--green)}
 .fbar i.sel{background:var(--accent)}
 .fnnum{text-align:right;font-variant-numeric:tabular-nums;font-size:.85rem}
 .fnpct{text-align:right;font-variant-numeric:tabular-nums;font-size:.8rem;color:var(--muted)}
 .fnlost{font-size:.75rem;color:var(--muted);margin:.1rem 0 .5rem 20.6rem}
 .hgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));gap:.9rem}
 .hcard{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:.55rem .7rem .45rem}
 .hcard h4{margin:0 0 .15rem;font-size:.75rem;font-weight:600;letter-spacing:.02em}
 .hcard h4 span{font-weight:400;font-variant-numeric:tabular-nums}
 th .u,h4 .u{text-transform:none;letter-spacing:0}
 .att.error{background:#fff5f5;border-color:#ffc9c9;border-left-color:var(--red)}
 .att.warn{background:#fff9db;border-color:#ffe066;border-left-color:var(--accent)}
 .att.action{background:#e7f5ff;border-color:#a5d8ff;border-left-color:#1971c2}
 .FAILED,.failed{color:var(--red);font-weight:600}
 .RUNNING,.running{color:var(--green);font-weight:600}
 .PENDING,.pending,.checkpoint{color:var(--accent);font-weight:600}
 .COMPLETED{color:var(--green)}
 .TIMEOUT{color:var(--red);font-weight:600}
 .CANCELLED{color:var(--muted)}
 .chip{display:inline-block;padding:.24rem .75rem;border-radius:999px;margin:0 .4rem .4rem 0;
       font-size:.8rem;border:1px solid var(--line-strong);background:var(--card);
       color:#3f3b36;box-shadow:0 1px 1px rgba(28,27,26,.04)}
 .chip.open{background:#fff4e6;border-color:#ffc078;color:#d9480f;font-weight:600}
 .chip.ok{background:#ebfbee;border-color:#b2f2bb;color:#2b8a3e;font-weight:600}
 .bar{height:8px;background:#edeae4;border-radius:4px;width:140px;display:inline-block;
      vertical-align:middle;margin-right:.5rem}
 .bar>i{display:block;height:100%;background:var(--green);border-radius:4px;transition:width .4s ease}
 button{font:inherit;font-size:.86rem;padding:.32rem .85rem;border:1px solid var(--line-strong);
        background:var(--card);color:var(--fg);border-radius:6px;cursor:pointer;
        box-shadow:0 1px 1px rgba(28,27,26,.05);transition:background .12s}
 button:hover{background:#f5f2ee}
 button:active{transform:translateY(.5px)}
 button:disabled{opacity:.55;cursor:default;transform:none}
 /* Dark palette follows the system preference (JS mirrors matchMedia onto
    html.dark) -- every color lives in these variables plus the overrides
    below for the few hardcoded light tints. */
 html.dark{--bg:#191817;--card:#232220;--fg:#eae6e0;--line:#3a362f;--line-strong:#4c463d;
           --muted:#a59d92;--accent:#ff8a3d;--green:#5cb87a;--red:#ff7b7b}
 html.dark .stale{background:#382a1c;border-color:#7a4a1f}
 html.dark .waiting{background:#1d2e40;border-color:#31597f}
 html.dark .att.error{background:#3a1f1f;border-color:#7a3030}
 html.dark .att.warn{background:#382a1c;border-color:#7a4a1f}
 html.dark .att.action{background:#1d2e40;border-color:#31597f}
 html.dark .chip{color:#cfc8bf}
 html.dark .chip.open{background:#382a1c;color:#ffa94d;border-color:#7a4a1f}
 html.dark .chip.ok{background:#213428;color:#69c17c;border-color:#3d6b4a}
 html.dark .bar{background:#38342e}
 html.dark table tr:hover td{background:#282623}
 html.dark input.bad{background:#3a2222}
 html.dark .qph{background:#2a2826}
 html.dark button:hover{background:#2c2a28}
 html.dark #qclight{background:rgba(0,0,0,.94)}
 select,input{font:inherit;padding:.32rem .45rem;border:1px solid var(--line-strong);
              border-radius:6px;background:var(--card);color:var(--fg)}
 select:hover,input:hover{border-color:#bdb5a9}
 select:focus-visible,input:focus-visible,button:focus-visible{outline:2px solid #ffa94d;outline-offset:1px}
 input.bad{border-color:var(--red);background:#fff5f5}
 pre{font:12.5px/1.6 ui-monospace,'SF Mono',Menlo,Consolas,monospace;background:var(--card);
     border:1px solid var(--line);border-radius:8px;padding:.85rem 1rem;overflow:auto;
     max-height:18rem;white-space:pre-wrap;margin:0;box-shadow:0 1px 2px rgba(28,27,26,.04)}
 #fresh{font-size:.8rem;font-weight:400;color:var(--muted);margin-left:.6rem}
 #runlabel{margin:-.7rem 0 1rem;font-size:.85rem}
 #runlabel:empty{display:none}
 #jobbar{display:flex;align-items:center;margin:-.3rem 0 .4rem}
 #jobbar label{font-size:.8rem;display:inline-flex;align-items:center;gap:.35rem;cursor:pointer}
 #jobsum{margin-left:auto;font-size:.8rem}
 #logbar{display:flex;align-items:center;gap:.6rem;margin-bottom:.45rem}
 #logbar .lstreams{margin-left:auto;display:flex;gap:.35rem}
 #logbar button{padding:.18rem .6rem;font-size:.78rem}
 .lactive{border-color:var(--accent);color:var(--accent);font-weight:600}
 .muted{color:var(--muted)}
 /* Section panels: the dynamic containers become cards so each block reads as
    one unit; the JS that fills them is untouched. */
 #gates,#phases,#jobs,#ds,#ts,#cfgpipeline,#cfgspecies{
   background:var(--card);border:1px solid var(--line);border-radius:8px;
   padding:.15rem .55rem;box-shadow:0 1px 2px rgba(28,27,26,.04)}
 #gates{padding:.55rem .75rem .25rem}
 #cfgpipeline,#cfgspecies{padding:.4rem .85rem .6rem}
 #qcrender,#qcfilter,#qcnav{background:var(--card);border:1px solid var(--line);border-radius:8px;
   padding:.6rem .8rem;box-shadow:0 1px 2px rgba(28,27,26,.04)}
 #qcnav{position:sticky;top:2.7rem;z-index:9;margin-bottom:.7rem;display:flex;
        flex-wrap:wrap;gap:.35rem;align-items:center}
 #qcfilter{margin-bottom:.9rem;display:flex;flex-direction:column;gap:.4rem}
 .qfilt{display:flex;flex-wrap:wrap;gap:.3rem;align-items:center}
 .qfilt>.muted{min-width:5.6rem}
 .qgroup{background:var(--card);border:1px solid var(--line);border-radius:8px;
         margin:.8rem 0;padding:.6rem .8rem;box-shadow:0 1px 2px rgba(28,27,26,.04)}
 .qlab2{font-size:.7rem;text-transform:uppercase;letter-spacing:.06em;
        color:var(--muted);margin-bottom:.45rem;font-weight:600}
 .qgrid{display:flex;flex-wrap:wrap;gap:.7rem}
 .qcard{margin:0;width:156px;cursor:pointer}
 .qcard img{width:156px;height:156px;object-fit:contain;display:block;
            border:1px solid var(--line);background:#f2f0ed;border-radius:4px}
 .qcard:hover img{border-color:var(--accent)}
 .qph{width:156px;height:156px;border:1px dashed var(--line-strong);
      background:#f2f0ed;border-radius:4px}
 .qcard figcaption{font-size:.72rem;color:var(--muted);margin-top:.3rem;line-height:1.3}
 .qhint{font-size:.8rem;color:var(--muted);margin:-.25rem 0 .55rem}
 .qask{font-size:.95rem;color:var(--fg);margin:.15rem 0 .85rem;max-width:40rem}
 .qrow{display:grid;grid-template-columns:6.5rem 1fr;gap:.7rem;align-items:start;
       padding:.55rem 0;border-top:1px solid var(--line)}
 .qrow:first-of-type{border-top:none;padding-top:.1rem}
 .qrowlab{font-size:.8rem;padding-top:.2rem}
 .qrowlab .muted{margin-left:.35rem;font-variant-numeric:tabular-nums}
 .qtomo{border:none;background:none;font:inherit;color:var(--accent);cursor:pointer;
        padding:0;text-align:left;box-shadow:none}
 .qtomo:hover{text-decoration:underline;background:none}
 .qchip{border:1px solid var(--line-strong);background:var(--card);color:inherit;
        font:inherit;font-size:.78rem;padding:.15rem .6rem;border-radius:999px;
        cursor:pointer;margin:0;box-shadow:none}
 .qchip.ok,.qcheck.on{background:#fff4e6;border-color:#ffc078;color:#d9480f;font-weight:600}
 .qcheck{font-size:.82rem;padding:.28rem .75rem}
 html.dark .qchip.ok,html.dark .qcheck.on{background:#382a1c;color:#ffa94d;border-color:#7a4a1f}
 html.dark .qcard img,html.dark .qph{background:#2a2826}
 #qcmore{margin-top:1.6rem}
 #qcmore>summary{cursor:pointer;font-size:.82rem;color:var(--muted);margin-bottom:.5rem}
 .qstrip{display:flex;gap:.35rem;margin-top:.35rem;max-width:94vw;overflow-x:auto}
 .qmini{width:56px;height:56px;padding:0;border-radius:4px;overflow:hidden;opacity:.5;
        box-shadow:none}
 .qmini img{width:56px;height:56px;object-fit:cover;display:block}
 .qmini.on{opacity:1;border-color:var(--accent)}
 #tabbar{margin:1.1rem 0 1.3rem;border-bottom:1px solid var(--line-strong);
         position:sticky;top:0;background:var(--bg);z-index:10;padding-top:.3rem}
 .tab{border:none;background:none;font:inherit;padding:.55rem 1rem;cursor:pointer;
      color:var(--muted);border-bottom:2px solid transparent;margin-bottom:-1px;
      box-shadow:none;border-radius:0}
 .tab:hover{background:transparent;color:var(--fg)}
 .tab:active{transform:none}
 .tab.active{color:var(--fg);font-weight:600;border-bottom-color:var(--accent)}
 tr.jrow{cursor:pointer}
 tr.jrow:hover td{background:#f8f6f3}
 tr.excluded td{color:var(--muted)}
 #qclight{position:fixed;inset:0;z-index:50;background:rgba(22,20,18,.9);
          display:flex;flex-direction:column;align-items:center;justify-content:center;
          gap:.5rem;padding:2rem;cursor:zoom-out}
 #qclight img{max-width:94vw;max-height:80vh;object-fit:contain;background:#fff;
              border-radius:8px;box-shadow:0 8px 40px rgba(0,0,0,.5)}
 #qclight .cap{color:#f7f4f0;font-size:.9rem;font-weight:600}
 #qclight .hint{color:#c3bcb2;font-size:.75rem}
 .lnav{display:flex;align-items:center;gap:.8rem}
 .lcount{color:#c3bcb2;font-size:.8rem}
 .navbtn{border-radius:999px;width:2.2rem;height:2.2rem;padding:0;font-size:1.2rem;
         line-height:1;background:rgba(255,255,255,.12);color:#f7f4f0;
         border-color:rgba(255,255,255,.3);box-shadow:none}
 .navbtn:hover{background:rgba(255,255,255,.22)}
 .navbtn:active{transform:none}
 @media (max-width:820px){
  body{padding:1rem}
  #phases>table,#jobs>table,#ds>table,#ts>table,#inv>table,#runslist>table,
  #cfgpipeline table,#cfgspecies table{display:block;overflow-x:auto}
  .qcard{width:118px}
  .qcard img,.qph{width:118px;height:118px}
  .qrow{grid-template-columns:1fr;gap:.2rem}
 }
</style></head><body>
<h1>OPUS-ET pipeline status <span id="fresh"></span></h1>
<div id="runlabel" class="muted">__RUNLABEL__</div>
<div id="stale"></div><div id="attention"></div>
<div id="tabbar">
  <button class="tab" data-t="run">Run</button>
  <button class="tab" data-t="frames">Frames</button>
  <button class="tab" data-t="dataset">Dataset</button>
  <button class="tab" data-t="qc">Visual QC</button>
  <button class="tab" data-t="training">Training</button>
  <button class="tab" data-t="inventory">Inventory</button>
  <button class="tab" data-t="refine">Refinement</button>
  <button class="tab" data-t="config">Config</button>
  <button class="tab" data-t="runs">Runs</button>
</div>

<div id="tab-run">
<h2>Gates</h2><div id="gates"></div>
<h2>Phases</h2><div id="phases"></div>
<h2>Jobs</h2>
<div id="jobbar"><label class="muted"><input type="checkbox" id="hidefin" onchange="renderJobs()"> hide finished</label>
  <label class="muted" id="otherlab" hidden><input type="checkbox" id="showother" onchange="renderJobs()"> <span id="othertxt"></span></label>
  <span id="jobsum" class="muted"></span></div>
<div id="jobs"></div>
<h2>Log</h2>
<div id="logbar"><span id="logmeta" class="muted"></span>
  <span class="lstreams"><button id="berr" class="lactive" onclick="setStream('err')" title="standard error tail">stderr</button>
  <button id="bout" onclick="setStream('out')" title="standard output tail">stdout</button>
  <button id="bmore" onclick="setLines(LOG.lines===50?500:50)" title="lines to fetch">500 lines</button>
  <button onclick="refetchLog()" title="refetch the tail">&#8635;</button></span></div>
<pre id="log">select a job above</pre>
</div>

<div id="tab-frames" hidden>
<h2>Frame quality <span class="muted" style="text-transform:none;letter-spacing:0;font-weight:400">&mdash; the per-movie CTF fit, as <code>filter_quality</code> histograms it</span></h2>
<div id="frsum" class="muted" style="margin:-.3rem 0 .7rem"></div>
<div id="frhist"></div>
<div id="frskip" class="muted" style="margin:.5rem 0 0;font-size:.8rem"></div>
<h2>Dose and acquisition order</h2>
<div id="frdose"></div>
<h2>Per tilt series</h2>
<div id="frseries"></div>
<h2>Alignment and geometry</h2>
<div id="fralign"></div>
<h2>Worst CTF resolution</h2>
<div id="frworst"></div>
<div class="muted" style="margin-top:.5rem;font-size:.8rem">Read from WARP&rsquo;s own
<code>processed_items.json</code> in <code>warp_frameseries/</code> and <code>warp_tiltseries/</code>
&mdash; the cache <code>WarpTools filter_quality --histograms</code> summarises. Nothing is re-estimated,
and frames are grouped by the tilt series that lists them, so any naming convention works.</div>
</div>

<div id="tab-dataset" hidden>
<h2>Dataset &mdash; read-only <span id="dsfile" style="text-transform:none;font-weight:400;letter-spacing:0"></span></h2>
<div id="ds"><span class="muted">loading&hellip;</span></div>
<div class="muted" style="margin-top:.5rem;font-size:.8rem">Acquisition facts from
<code>pipeline.conf</code>. Per-tilt-series numbers live under <b>Frames</b>.</div>
</div>

<div id="tab-qc" hidden>
<h2>Visual QC <span class="muted" style="text-transform:none;letter-spacing:0;font-weight:400">&mdash; one check at a time, then the same view across tilt series</span></h2>
<div id="qcnav"></div>
<div id="qcfilter"></div>
<div id="qcmine" hidden style="margin-bottom:1.1rem"></div>
<div id="qclist"></div>
<details id="qcmore"><summary>Render a new view</summary>
<div id="qcrender"></div>
</details>
</div>

<div id="tab-training" hidden>
<h2>Training &mdash; OPUS-ET runs under <span style="text-transform:none">opuset/</span></h2>
<div id="training"></div>
</div>

<div id="tab-inventory" hidden>
<h2>Template-matching scores <span class="muted" style="text-transform:none;letter-spacing:0;font-weight:400">&mdash; the distribution Gate 2 turns on</span></h2>
<div id="tmscores"></div>
<h2>Particle funnel <span class="muted" style="text-transform:none;letter-spacing:0;font-weight:400">&mdash; where particles are gained and lost, per species</span></h2>
<div id="funnel"></div>
<h2>Pipeline inventory &mdash; per tilt series</h2>
<div id="invsum" class="muted" style="margin:-.3rem 0 .4rem"></div>
<div id="inv"></div>
<div class="muted" style="margin-top:.4rem">Checked against the cluster filesystem; TM counts any species label. Refreshes every 2 minutes.</div>
</div>

<div id="tab-refine" hidden>
<h2>M refinement <span class="muted" style="text-transform:none;letter-spacing:0;font-weight:400">&mdash; gold-standard FSC per species</span></h2>
<div id="refine"></div>
<div class="muted" style="margin-top:.4rem">Read from each species' <code>_fsc.star</code>, which M writes itself &mdash; no recomputation. Resolution is the 0.143 crossing of the phase-randomization-corrected curve.</div>
</div>

<div id="tab-config" hidden>
<div id="cfgproblem"></div>
<div style="margin:0 0 .4rem"><input id="cfgsearch" size="26" placeholder="filter keys or descriptions"
     aria-label="filter config keys" oninput="filterCfg()"></div>
<h2>Processing config <span class="muted" style="text-transform:none;letter-spacing:0;font-weight:400">&mdash; earlier stages: CTF, motion, alignment, reconstruction</span> <span id="pipefile" style="text-transform:none;font-weight:400;letter-spacing:0"></span></h2>
<div id="cfgpipeline"></div>
<h2>Species</h2><div id="spec"></div>
<div style="margin-top:.5rem">
  <input id="newspec" size="10" placeholder="new name" aria-label="new species name"
         onkeydown="if(event.key==='Enter')createSpecies()">
  <button onclick="createSpecies()">Create species conf</button>
  <span id="newmsg" class="muted"></span>
</div>
<h2>Species config <span class="muted" style="text-transform:none;letter-spacing:0;font-weight:400">&mdash; later stages: template matching, export, training</span> <span id="specfile" style="text-transform:none;font-weight:400;letter-spacing:0"></span></h2>
<div id="cfgspecies"></div>
</div>

<div id="tab-runs" hidden>
<h2>Runs on this cluster</h2>
<div id="runslist"></div>
</div>

<div id="qclight" hidden onclick="closeQc()"></div>
<script>
const CSRF="__CSRF__", RUNS_PARENT=__RUNSPARENT__, ONDEMAND='qc_ondemand',
      TABS=['run','frames','dataset','qc','training','inventory','refine','config','runs'];
let CFGWARN={}, CFGSPEC={}, LOG={job:null,stream:'err',lines:50}, LASTJOBS=[], LASTOTHER=[];
function showTab(name){
  TABS.forEach(t=>{
    document.getElementById('tab-'+t).hidden=(t!==name); });
  document.querySelectorAll('#tabbar .tab').forEach(b=>
    b.classList.toggle('active', b.dataset.t===name));
  history.replaceState(null,'','#'+name);
  // Poller-backed sources need an ssh round trip, so a tab opened before the
  // first fetch would otherwise stay empty forever.
  if(name==='qc') loadQc();
  if(name==='config'||name==='dataset') loadCfg();
}
let QC_READY=false, QC_ENTRIES=[], QC_FILTER={tomo:'all',species:'all',section:'auto',variant:'all'}, SPECIES_LIST=[],
    QC_THUMBS={}, THUMB_PENDING=false, QC_LIST=[], QC_IDX=0, QC_SECTION_CHOSEN=false;
function srcState(st,name){
  const s=(st.sources||{})[name]||{};
  if(s.error) return 'error';
  return s.fetched_at ? 'ready' : 'loading';
}
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const fmtNum=n=>{const v=Number(n);return Number.isFinite(v)?v.toFixed(1):String(n??'');};
async function tick(){
  const s=await (await fetch('/api/status')).json();
  // Live status in the tab strip: glanceable while the tab sits in the
  // background. warning sign = a source is stale; cross = a job failed.
  const act=(s.jobs||[]).filter(j=>j.state==='RUNNING'||j.state==='PENDING').length;
  const bad=(s.jobs||[]).some(j=>j.state==='FAILED'||j.state==='TIMEOUT');
  document.title=(s.stale?'\u26a0 ':'')+(bad?'\u2717 ':'')+(act?act+' running \u00b7 ':'')+'OPUS-ET';
  const fresh=document.getElementById('fresh');
  const age=(s.sources||{}).squeue && s.sources.squeue.age_s;
  fresh.textContent = age==null ? 'connecting\u2026'
    : 'live \u00b7 updated '+(age<5?'just now':Math.round(age)+'s ago');
  document.getElementById('stale').innerHTML=s.stale?`<div class="stale">stale &mdash; ${esc(s.stale)}</div>`:'';
  const ALABEL={error:'needs fixing',warn:'check',action:'needs you'};
  document.getElementById('attention').innerHTML=(s.attention||[]).map(a=>
    `<div class="att ${esc(a.level)}"><b>${esc(ALABEL[a.level]||a.level)}</b>${esc(a.text)}</div>`
  ).join('');
  const rf=s.refinement||{};
  const rkeys=Object.keys(rf).filter(k=>k[0]!=='_').sort();
  const fscSvg=(rows)=>{
    const W=430,H=150,PL=34,PB=20;
    const pts=rows.filter(r=>isFinite(r.resolution)&&r.resolution>0);
    if(!pts.length) return '';
    const fmax=Math.max(...pts.map(r=>1/r.resolution));
    const X=r=>PL+(1/r.resolution)/fmax*(W-PL-6);
    const Y=v=>6+(1-Math.max(0,Math.min(1,v)))*(H-PB-6);
    const line=(col,c)=>`<polyline fill="none" stroke="${c}" stroke-width="1.6" points="`
      +pts.map(r=>`${X(r).toFixed(1)},${Y(r[col]).toFixed(1)}`).join(' ')+'"/>';
    const y143=Y(0.143);
    // x ticks as resolution, which is what a reader thinks in
    const ticks=[0.25,0.5,0.75,1].map(f=>{
      const x=PL+f*(W-PL-6), res=1/(f*fmax);
      return `<line x1="${x}" y1="${H-PB}" x2="${x}" y2="${H-PB+3}" stroke="var(--line-strong)"/>`
        +`<text x="${x}" y="${H-PB+13}" font-size="9" text-anchor="middle" fill="var(--muted)">${res.toFixed(1)}</text>`;
    }).join('');
    return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;max-width:${W}px;height:auto">
      <line x1="${PL}" y1="${H-PB}" x2="${W-6}" y2="${H-PB}" stroke="var(--line-strong)"/>
      <line x1="${PL}" y1="6" x2="${PL}" y2="${H-PB}" stroke="var(--line-strong)"/>
      <line x1="${PL}" y1="${y143}" x2="${W-6}" y2="${y143}" stroke="var(--red)"
            stroke-width="1" stroke-dasharray="3 3"/>
      <text x="${PL-4}" y="${y143+3}" font-size="9" text-anchor="end" fill="var(--red)">0.143</text>
      <text x="${PL-4}" y="10" font-size="9" text-anchor="end" fill="var(--muted)">1.0</text>
      ${ticks}
      <text x="${(W+PL)/2}" y="${H-2}" font-size="9" text-anchor="middle" fill="var(--muted)">resolution (A)</text>
      ${line('unmasked','var(--muted)')}${line('masked','var(--green)')}${line('corrected','var(--accent)')}
    </svg>`;
  };
  document.getElementById('refine').innerHTML=rkeys.length
    ? rkeys.map(k=>{
        const e=rf[k], r=e.resolution||{};
        const num=v=>v==null?'&mdash;':v.toFixed(2)+' A';
        return `<h3>${esc(k)}</h3>
          <div style="display:flex;gap:1.5rem;flex-wrap:wrap;align-items:flex-start">
            <div>${fscSvg(e.fsc||[])}</div>
            <table style="width:auto"><tr><th>Curve</th><th>0.143</th></tr>
              <tr><td><span style="color:var(--accent)">&#9644;</span> corrected</td>
                  <td class="num"><b>${num(r.corrected)}</b></td></tr>
              <tr><td><span style="color:var(--green)">&#9644;</span> masked</td>
                  <td class="num">${num(r.masked)}</td></tr>
              <tr><td><span style="color:var(--muted)">&#9644;</span> unmasked</td>
                  <td class="num">${num(r.unmasked)}</td></tr></table>
          </div>`;
      }).join('')
      +(()=>{ const o=rf._options||{};
        const on=Object.keys(o).filter(x=>o[x]==='True');
        return on.length?`<div class="muted" style="margin-top:.6rem">Last pass refined: `
          +on.map(esc).join(', ')+(o.NIterations?` &middot; ${esc(o.NIterations)} iterations`:'')
          +'</div>':''; })()
    : '<span class="muted">no refinement output yet</span>';
  const fn=s.funnel||{};
  const fkeys=Object.keys(fn).sort();
  const fmtn=n=>(n==null?'—':n.toLocaleString());
  const fbar=(n,total,cls)=>{
    const w=(total&&n!=null)?Math.max(0.6,100*n/total):0;
    return `<div class="fbar"><i class="${cls}" style="width:${w.toFixed(1)}%"></i></div>`;
  };
  const frow=(label,n,total,cls,pct,title)=>
    `<div class="fnrow"><div class="fnlab"${title?` title="${esc(title)}"`:''}>${label}</div>`
    +`${fbar(n,total,cls)}`
    +`<div class="fnnum">${fmtn(n)}</div><div class="fnpct">${pct}</div></div>`;
  document.getElementById('funnel').innerHTML=fkeys.length
    ? fkeys.map(k=>{
        const e=fn[k], picks=e.picks, exp=e.exported;
        // Every bar is scaled to the picks count, so the empty track to the
        // right of a bar IS the loss at that stage.
        const pc=(n)=>(picks&&n!=null)?Math.round(100*n/picks)+'%':'';
        let html=`<h3>${esc(k)}</h3><div class="fn">`;
        html+=frow('picks (template matching)',picks,picks,'pick',pc(picks));
        html+=frow('exported subtomograms',exp,picks,'exp',pc(exp));
        if(exp!=null&&picks&&exp<picks)
          html+=`<div class="fnlost">${fmtn(picks-exp)} not exported</div>`;
        (e.selected||[]).forEach(sl=>{
          // The run directory is what distinguishes one selection from
          // another, so lead with it and keep the file name secondary.
          const cut=sl.name.lastIndexOf('/');
          const dir=cut>0?sl.name.slice(0,cut):'';
          const file=cut>0?sl.name.slice(cut+1):sl.name;
          html+=frow(`<code>${esc(dir)}</code>`
                     +`<span class="muted" style="font-size:.75rem"> / ${esc(file)}</span>`,
                     sl.count,picks,'sel',pc(sl.count),sl.name);
        });
        if((e.selected||[]).length&&exp)
          html+=`<div class="fnlost">selections are alternatives, not successive `
               +`filters &mdash; each is a different state pick from the same `
               +`${fmtn(exp)} exported particles</div>`;
        return html+'</div>';
      }).join('')
    : '<span class="muted">no particle counts yet</span>';
  // Parked checkpoints surface through the attention strip above, so no
  // separate waiting banner here -- it said the same thing twice.
  document.getElementById('gates').innerHTML=(s.gates||[]).map(g=>
    `<span class="chip ${g.approved?'ok':'open'}">${esc(g.label)} ${g.approved?'&#10003;':'&mdash; open'}</span>`).join('')
    ||'<span class="chip">no run state</span>';
  document.getElementById('phases').innerHTML='<table><tr><th>Phase</th><th>What it does</th><th>Status</th><th>Progress</th></tr>'+
    ((s.phases||[]).map(p=>{
      const pct=p.total?Math.round(100*p.done/p.total):0;
      return `<tr><td><code>${esc(p.phase)}</code></td><td class="muted">${esc(p.desc||'\u2014')}</td><td class="${esc(p.status||'')}">${esc(p.status||'\u2014')}</td>
        <td>${p.total?`<span class="bar"><i style="width:${pct}%"></i></span> ${p.done}/${p.total}`:'\u2014'}</td></tr>`;
    }).join('')||'<tr><td colspan=4>no phases yet</td></tr>')+'</table>';
  if(!document.getElementById('tab-qc').hidden && !QC_READY) loadQc();
  LASTJOBS=s.jobs||[];
  LASTOTHER=s.other_jobs||[];
  const olab=document.getElementById('otherlab');
  olab.hidden=!LASTOTHER.length;
  document.getElementById('othertxt').textContent=
    LASTOTHER.length+' job'+(LASTOTHER.length===1?'':'s')+' elsewhere on the cluster';
  renderJobs();
  const acc=s.accounting||{};
  document.getElementById('jobsum').textContent=[(s.jobs||[]).length+' jobs',
    acc.gpu_hours?acc.gpu_hours+' GPU\u00b7h':'',
    acc.n_failed?acc.n_failed+' failed':''].filter(Boolean).join(' \u00b7 ');
  renderInventory(s.inventory, s.recon);
  renderTmScores(s);
  renderFrames(s);
  renderTraining(s.training);
  renderRuns(s.runs);
  // The core question for this dashboard is "what broke" -- answer it
  // without a click by opening the newest failed job log once.
}
// --- frame quality -------------------------------------------------------
const FRUNIT={um:'µm',A:'Å'};
const frUnit=u=>(FRUNIT[u]!==undefined?FRUNIT[u]:(u||''));
// Fixed decimals by magnitude: astigmatism runs to 1e-4 while defocus and
// resolution read best at two places, and toPrecision would print 1e-4 as
// "0.000100" for one metric and mangle "100" for another.
function frFmt(v){
  const n=Number(v);
  if(!isFinite(n)) return '—';
  const a=Math.abs(n);
  return a>=100?n.toFixed(0):(a>=1?n.toFixed(2):n.toFixed(4));
}
const frFix=(v,d)=>{const n=Number(v);return isFinite(n)?n.toFixed(d):'\u2014';};
function histSvg(m){
  const bins=m.bins||[];
  if(!bins.length) return '';
  const W=320,H=104,PL=4,PR=4,PT=8,PB=16;
  const peak=Math.max.apply(null,bins.map(b=>b.n))||1;
  const bw=(W-PL-PR)/bins.length, u=frUnit(m.unit);
  const bars=bins.map((b,i)=>{
    // An occupied bin never renders as nothing: a 1px stub still says "some".
    const h=b.n?Math.max(1,(H-PT-PB)*b.n/peak):0;
    return `<rect x="${(PL+i*bw).toFixed(1)}" y="${(H-PB-h).toFixed(1)}"`
      +` width="${Math.max(1,bw-1).toFixed(1)}" height="${h.toFixed(1)}"`
      +` fill="var(--accent)" opacity=".8"><title>${frFmt(b.lo)}–${frFmt(b.hi)}`
      +`${u?' '+esc(u):''}: ${b.n}</title></rect>`;
  }).join('');
  const span=m.max-m.min;
  const mx=PL+(span?(m.median-m.min)/span:0.5)*(W-PL-PR);
  const lx=Math.min(W-PR-20,Math.max(PL+20,mx));
  return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto" role="img"
       aria-label="${esc(m.label)} histogram">
    <line x1="${PL}" y1="${H-PB}" x2="${W-PR}" y2="${H-PB}" stroke="var(--line-strong)"/>
    ${bars}
    <line x1="${mx.toFixed(1)}" y1="${PT-4}" x2="${mx.toFixed(1)}" y2="${H-PB}"
          stroke="var(--green)" stroke-width="1" stroke-dasharray="3 2"/>
    <text x="${PL}" y="${H-4}" font-size="9" fill="var(--muted)">${frFmt(m.min)}</text>
    <text x="${lx.toFixed(1)}" y="${H-4}" font-size="9" fill="var(--green)"
          text-anchor="middle">${frFmt(m.median)}</text>
    <text x="${W-PR}" y="${H-4}" font-size="9" fill="var(--muted)"
          text-anchor="end">${frFmt(m.max)}</text>
  </svg>`;
}
// Every series shares one scale, so the rows can be compared to each other and
// not just to themselves. Best resolution is at the top: a drooping line is a
// series getting worse across its tilts.
function trackSvg(track,mn,mx){
  const n=(track||[]).length;
  if(n<2||!isFinite(mn)||!isFinite(mx)) return '<span class="muted">&mdash;</span>';
  const W=150,H=24;
  const X=i=>1+(W-2)*i/(n-1);
  const Y=v=>(mx===mn?H/2:1+(H-2)*(v-mn)/(mx-mn));
  let d='', pen=false;
  track.forEach((v,i)=>{
    if(v==null){pen=false;return;}
    d+=(pen?'L':'M')+X(i).toFixed(1)+' '+Y(v).toFixed(1)+' ';
    pen=true;
  });
  if(!d) return '<span class="muted">&mdash;</span>';
  return `<svg viewBox="0 0 ${W} ${H}" style="width:${W}px;height:${H}px;vertical-align:middle">
    <path d="${d.trim()}" fill="none" stroke="var(--accent)" stroke-width="1.3"
          stroke-linejoin="round"/></svg>`;
}
// The tomostar list is the imported truth and WARP's cache is only what it has
// processed, so a series missing from either side still gets a row.
function mergeSeries(tomostar,warp){
  const by={};
  (tomostar||[]).forEach(t=>{ by[t.name]={name:t.name, n_tilts:t.n_tilts,
    tilt_min:t.min_tilt, tilt_max:t.max_tilt, max_dose:t.max_dose,
    excluded:!!t.excluded}; });
  (warp||[]).forEach(w=>{
    const r=by[w.name]||(by[w.name]={name:w.name});
    ['defocus_min','defocus_max','astigmatism','resolution','inclination','track']
      .forEach(k=>{ r[k]=w[k]; });
    if(r.n_tilts==null) r.n_tilts=w.n_tilts;
    if(r.tilt_min==null) r.tilt_min=w.tilt_min;
    if(r.tilt_max==null) r.tilt_max=w.tilt_max;
  });
  return Object.keys(by).sort().map(k=>by[k]);
}
function renderFrames(st){
  st=st||{};
  const f=st.frames||{}, state=srcState(st,'frames');
  const sum=document.getElementById('frsum'), hist=document.getElementById('frhist'),
        skip=document.getElementById('frskip'), ser=document.getElementById('frseries'),
        wor=document.getElementById('frworst');
  const metrics=f.metrics||[], series=f.series||[], worst=f.worst||[];
  if(!f.n_frames){
    sum.textContent = state==='error'
      ? 'could not read WARP’s quality cache'
      : (state==='ready'
         ? 'no processed_items.json yet — WARP writes it once frame-series CTF has run'
         : 'loading…');
    hist.innerHTML=''; skip.innerHTML=''; wor.innerHTML='';
    document.getElementById('frdose').innerHTML='';
    renderAlign(st,[]);
    ser.innerHTML=(st.tilt_series||[]).length
      ? '<table><tr><th>Tilt series</th><th>Tilts</th><th>Tilt range</th>'
        +'<th>Max dose <span class="u">(e&#8315;/&#8491;&#178;)</span></th></tr>'
        +(st.tilt_series||[]).map(x=>`<tr${x.excluded?' class="excluded"':''}>
          <td><code>${esc(x.name)}</code>${x.excluded
               ?' <span class="FAILED">excluded</span>':''}</td>
          <td class="num">${esc(x.n_tilts)}</td>
          <td class="num">${frFmt(x.min_tilt)}&deg; &hellip; ${frFmt(x.max_tilt)}&deg;</td>
          <td class="num">${frFix(x.max_dose,1)}</td></tr>`).join('')
        +'</table><div class="muted" style="margin-top:.4rem;font-size:.8rem">'
        +'From the tomostar files. CTF columns appear once WARP has fitted the '
        +'tilt series.</div>'
      : `<span class="muted">${srcState(st,'tilt_series')==='ready'
           ? 'no tilt series imported yet' : 'loading…'}</span>`;
    return;
  }
  const res=metrics.find(m=>m.key==='resolution'), dfc=metrics.find(m=>m.key==='defocus');
  sum.textContent=[
    f.n_frames+' movies',
    f.n_series?('across '+f.n_series+' tilt series'):'not yet grouped into tilt series',
    res?('median CTF resolution '+frFmt(res.median)+' Å'):'',
    dfc?('median defocus '+frFmt(dfc.median)+' µm'):''
  ].filter(Boolean).join(' · ');
  hist.innerHTML='<div class="hgrid">'+metrics.map(m=>{
    const u=frUnit(m.unit);
    return `<div class="hcard"><h4>${esc(m.label)}`
      +(u?` <span class="muted u">(${esc(u)})</span>`:'')
      +`<span class="muted" style="float:right">n ${m.n}</span></h4>`
      +histSvg(m)+'</div>';
  }).join('')+'</div>';
  // A metric WARP never measured, or one that is the same everywhere, has no
  // histogram worth drawing -- but saying so beats an empty card.
  skip.innerHTML=(f.skipped||[]).map(x=>
    esc(x.label)+': '+esc(x.note)).join(' · ');
  const all=[];
  series.forEach(x=>(x.track||[]).forEach(v=>{ if(v!=null) all.push(v); }));
  const tmn=all.length?Math.min.apply(null,all):NaN;
  const tmx=all.length?Math.max.apply(null,all):NaN;
  const rows=mergeSeries(st.tilt_series,series);
  ser.innerHTML=rows.length
    ? '<table><tr><th>Tilt series</th><th>Tilts</th><th>Tilt range</th>'
      +'<th title="accumulated dose at the last tilt, from the tomostar">'
      +'Max dose <span class="u">(e&#8315;/&#8491;&#178;)</span></th>'
      +'<th>Defocus <span class="u">(&micro;m)</span></th>'
      +'<th>Astig <span class="u">(&micro;m)</span></th>'
      +'<th>CTF res <span class="u">(&Aring;)</span></th>'
      +'<th title="tilt of the specimen plane WARP fitted from the CTF">Inclination</th>'
      +`<th title="per-movie CTF resolution across the series, best at top, `
      +`shared scale ${frFmt(tmn)}-${frFmt(tmx)} A">CTF across tilts</th></tr>`
      +rows.map(x=>`<tr${x.excluded?' class="excluded"':''}>
        <td><code>${esc(x.name)}</code>${x.excluded
             ?' <span class="FAILED">excluded</span>':''}</td>
        <td class="num">${x.n_tilts==null?'&mdash;':x.n_tilts}</td>
        <td class="num">${frFmt(x.tilt_min)}&deg; &hellip; ${frFmt(x.tilt_max)}&deg;</td>
        <td class="num">${x.max_dose==null?'&mdash;':frFix(x.max_dose,1)}</td>
        <td class="num">${frFmt(x.defocus_min)} &ndash; ${frFmt(x.defocus_max)}</td>
        <td class="num">${frFmt(x.astigmatism)}</td>
        <td class="num">${frFmt(x.resolution)}</td>
        <td class="num">${frFmt(x.inclination)}&deg;</td>
        <td>${trackSvg(x.track,tmn,tmx)}</td></tr>`).join('')
      +'</table><div class="muted" style="margin-top:.4rem;font-size:.8rem">'
      +'Name, tilt count, tilt range and dose come from the tomostar files; defocus, '
      +'astigmatism and CTF res are the tilt-series fit and the sparkline is the '
      +'per-movie fit, so those two are estimated separately and need not agree. '
      +'A strongly asymmetric range such as -50 &hellip; +34 matters: the missing-wedge '
      +'angles are directional. Series dropped at Gate 1 are marked '
      +'<span class="FAILED">excluded</span>.</div>'
    : '<span class="muted">no tilt series yet</span>';
  renderDose(f);
  renderAlign(st,series);
  wor.innerHTML=worst.length
    ? '<table><tr><th>Movie</th><th>Tilt series</th>'
      +'<th>CTF res <span class="u">(&Aring;)</span></th>'
      +'<th>Defocus <span class="u">(&micro;m)</span></th></tr>'
      +worst.map(w=>`<tr><td><code>${esc(w.name)}</code></td>
        <td>${w.series?'<code>'+esc(w.series)+'</code>'
                      :'<span class="muted">no tilt series</span>'}</td>
        <td class="num">${frFmt(w.resolution)}</td>
        <td class="num">${frFmt(w.defocus)}</td></tr>`).join('')
      +'</table><div class="muted" style="margin-top:.4rem;font-size:.8rem">'
      +'The worst CTF fits in the run. One bad tilt rarely matters; several from the '
      +'same series is a reason to look at that series.</div>'
    : '';
}
// --- template-matching score distributions -------------------------------
// Counts span four orders of magnitude (9305 at the noise peak, 1 in the tail)
// and the tail is where the real particles are, so the bars are log-scaled and
// say so. The green curve is the cumulative count at or above each score: it
// answers "if I cut here, how many particles do I keep?" directly.
function scoreSvg(bins,lo,hi,total){
  const W=640,H=170,PL=42,PR=42,PT=12,PB=26;
  const n=hi-lo+1;
  if(n<1) return '';
  const peak=Math.max.apply(null,bins.slice(lo,hi+1))||1;
  const lg=v=>Math.log10(1+v), top=lg(peak);
  const bw=(W-PL-PR)/n;
  const bars=[],ticks=[];
  let above=0; const cum=[];
  for(let i=hi;i>=lo;i--){ above+=bins[i]||0; cum[i]=above; }
  for(let i=lo;i<=hi;i++){
    const c=bins[i]||0, h=c?Math.max(1,(H-PT-PB)*lg(c)/top):0;
    const x=PL+(i-lo)*bw;
    bars.push(`<rect x="${x.toFixed(1)}" y="${(H-PB-h).toFixed(1)}"`
      +` width="${Math.max(1,bw-0.8).toFixed(1)}" height="${h.toFixed(1)}"`
      +` fill="var(--accent)" opacity=".8"><title>${(i/100).toFixed(2)}–`
      +`${((i+1)/100).toFixed(2)}: ${c.toLocaleString()} particles`
      +` · ${cum[i].toLocaleString()} at or above ${(i/100).toFixed(2)}</title></rect>`);
  }
  for(let p=0;Math.pow(10,p)<=peak;p++){
    const y=H-PB-(H-PT-PB)*lg(Math.pow(10,p))/top;
    ticks.push(`<line x1="${PL}" y1="${y.toFixed(1)}" x2="${W-PR}" y2="${y.toFixed(1)}"`
      +` stroke="var(--line)" stroke-width="1"/>`
      +`<text x="${PL-4}" y="${(y+3).toFixed(1)}" font-size="9" text-anchor="end"`
      +` fill="var(--muted)">${Math.pow(10,p).toLocaleString()}</text>`);
  }
  const cmax=cum[lo]||1;
  const cpath=[];
  for(let i=lo;i<=hi;i++){
    const x=PL+(i-lo+0.5)*bw, y=PT+(H-PT-PB)*(1-cum[i]/cmax);
    cpath.push((i===lo?'M':'L')+x.toFixed(1)+' '+y.toFixed(1));
  }
  const xticks=[];
  for(let i=lo;i<=hi;i++){
    if((i%5)!==0) continue;
    const x=PL+(i-lo+0.5)*bw;
    xticks.push(`<text x="${x.toFixed(1)}" y="${H-PB+13}" font-size="9"`
      +` text-anchor="middle" fill="var(--muted)">${(i/100).toFixed(2)}</text>`);
  }
  return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;max-width:${W}px;height:auto"
       role="img" aria-label="template-matching score distribution">
    ${ticks.join('')}
    <line x1="${PL}" y1="${H-PB}" x2="${W-PR}" y2="${H-PB}" stroke="var(--line-strong)"/>
    ${bars.join('')}
    <path d="${cpath.join(' ')}" fill="none" stroke="var(--green)" stroke-width="1.5"/>
    <text x="${W-PR+4}" y="${PT+4}" font-size="9" fill="var(--green)">${total.toLocaleString()}</text>
    <text x="${W-PR+4}" y="${H-PB}" font-size="9" fill="var(--green)">0</text>
    ${xticks.join('')}
    <text x="${(W-PR+PL)/2}" y="${H-2}" font-size="9" text-anchor="middle"
          fill="var(--muted)">correlation score &mdash; bars log scale, green = particles at or above</text>
  </svg>`;
}
function renderTmScores(st){
  st=st||{};
  const tm=st.tm_scores||{}, el=document.getElementById('tmscores');
  const keys=Object.keys(tm).sort();
  if(!keys.length){
    const state=srcState(st,'tm_scores');
    el.innerHTML='<span class="muted">'+(state==='error'
      ? 'could not read the particle XMLs'
      : (state==='ready'
         ? 'no template-matching candidates extracted yet'
         : 'loading…'))+'</span>';
    return;
  }
  el.innerHTML=keys.map(k=>{
    const e=tm[k], bins=e.bins||[];
    let lo=bins.findIndex(v=>v>0);
    let hi=bins.length-1; while(hi>lo&&!bins[hi]) hi--;
    if(lo<0) return `<h3>${esc(k)}</h3><div class="muted">no scores</div>`;
    const capped=e.n_capped||0, unknown=e.n_unknown_cap||0, ns=(e.series||[]).length;
    const note=[];
    if(capped) note.push(`${capped} of ${ns} series stopped at the extraction cap`
      +' &mdash; the low end of those is where counting stopped, not where the noise ends');
    if(unknown) note.push(`${unknown} more have an extraction log that does not match`
      +' the particle file, so their cap is unknown');
    return `<h3>${esc(k)}</h3>`
      +`<div class="muted" style="margin:-.2rem 0 .4rem">${e.n.toLocaleString()} candidates`
      +` across ${ns} tilt series · ${esc(e.score_type||'score')}`
      +` ${frFmt(e.min)}–${frFmt(e.max)}, mean ${frFmt(e.mean)}</div>`
      +scoreSvg(bins,lo,hi,e.n)
      +'<table style="margin-top:.4rem"><tr><th>Tilt series</th><th>Candidates</th>'
      +'<th>Best</th><th>Mean</th><th>Extraction cap</th></tr>'
      +(e.series||[]).map(s=>`<tr><td><code>${esc(s.name)}</code></td>
        <td class="num">${s.n.toLocaleString()}</td>
        <td class="num">${frFmt(s.max)}</td>
        <td class="num">${frFmt(s.mean)}</td>
        <td>${s.capped===true?'<span class="PENDING">at cap '+s.cap+'</span>'
             :(s.capped===false?'<span class="muted">under cap '+s.cap+'</span>'
                               :'<span class="muted">unknown</span>')}</td></tr>`).join('')
      +'</table>'
      +(note.length?`<div class="muted" style="margin-top:.35rem;font-size:.8rem">`
        +note.join('. ')+'.</div>':'');
  }).join('');
}
// --- dose ----------------------------------------------------------------
// Plotted against accumulated dose rather than tilt angle: in a dose-symmetric
// scheme those are different orderings. WARP's tomostar `_wrpDose` is already
// cumulative; SerialEM often leaves ExposureDose at 0.
function doseSvg(pts,mini){
  if(pts.length<3) return '';
  const W=mini?320:640, H=mini?120:180, PL=mini?30:38, PR=10, PT=10, PB=mini?16:28;
  const ds=pts.map(p=>p.cum_dose), rs=pts.map(p=>p.resolution);
  const dmax=Math.max.apply(null,ds), rmn=Math.min.apply(null,rs),
        rmx=Math.max.apply(null,rs);
  const X=d=>PL+(dmax?d/dmax:0)*(W-PL-PR);
  const Y=r=>PT+(rmx===rmn?0.5:(r-rmn)/(rmx-rmn))*(H-PT-PB);
  const dots=pts.map(p=>`<circle cx="${X(p.cum_dose).toFixed(1)}"`
    +` cy="${Y(p.resolution).toFixed(1)}" r="1.8" fill="var(--accent)" opacity=".45"/>`).join('');
  // Median per dose decile: the trend, without pretending a fit.
  const NB=12, buckets=Array.from({length:NB},()=>[]);
  pts.forEach(p=>{ const b=Math.min(NB-1,Math.floor((dmax?p.cum_dose/dmax:0)*NB));
                   buckets[b].push(p.resolution); });
  const med=[];
  buckets.forEach((b,i)=>{
    if(!b.length) return;
    b.sort((a,c)=>a-c);
    const m=b.length%2?b[(b.length-1)/2]:(b[b.length/2-1]+b[b.length/2])/2;
    med.push({x:X(dmax*(i+0.5)/NB),y:Y(m),m:m});
  });
  const path=med.map((p,i)=>(i?'L':'M')+p.x.toFixed(1)+' '+p.y.toFixed(1)).join(' ');
  return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;max-width:${W}px;height:auto"
       role="img" aria-label="CTF resolution against accumulated dose">
    <line x1="${PL}" y1="${H-PB}" x2="${W-PR}" y2="${H-PB}" stroke="var(--line-strong)"/>
    <line x1="${PL}" y1="${PT}" x2="${PL}" y2="${H-PB}" stroke="var(--line-strong)"/>
    ${dots}
    <path d="${path}" fill="none" stroke="var(--green)" stroke-width="1.8"/>
    <text x="${PL-4}" y="${PT+4}" font-size="9" text-anchor="end" fill="var(--muted)">${frFmt(rmn)}</text>
    <text x="${PL-4}" y="${H-PB}" font-size="9" text-anchor="end" fill="var(--muted)">${frFmt(rmx)}</text>
    <text x="${PL}" y="${H-PB+13}" font-size="9" fill="var(--muted)">0</text>
    <text x="${W-PR}" y="${H-PB+13}" font-size="9" text-anchor="end"
          fill="var(--muted)">${frFmt(dmax)}</text>
    ${mini?'':`<text x="${(W+PL)/2}" y="${H-2}" font-size="9" text-anchor="middle"
          fill="var(--muted)">accumulated dose (e&#8315;/&#8491;&#178;) &mdash; y: CTF resolution, best at top; green = median per bucket</text>`}
  </svg>`;
}
function renderDose(f){
  const el=document.getElementById('frdose'), dose=f.dose||{};
  const keys=Object.keys(dose).sort();
  if(!keys.length){
    el.innerHTML='<span class="muted">no dose in <code>tomostar/</code> or <code>mdoc/</code>'
      +' &mdash; WARP writes accumulated dose as <code>_wrpDose</code></span>';
    return;
  }
  const all=[];
  keys.forEach(k=>(dose[k].points||[]).forEach(p=>all.push(p)));
  // Per-series damage small multiples: the pooled curve above averages away
  // exactly the thing worth noticing -- one series whose resolution falls
  // off differently from the rest.
  const minis=keys.filter(k=>(dose[k].points||[]).length>=3);
  el.innerHTML=doseSvg(all)
    +(minis.length?`<div class="qlab2" style="margin-top:.7rem">Damage curve per tilt series</div>
      <div class="hgrid">`
      +minis.map(k=>`<div class="hcard"><h4>${esc(k)}`
        +`<span class="muted" style="float:right">n ${(dose[k].points||[]).length}</span></h4>`
        +doseSvg(dose[k].points,true)+'</div>').join('')
      +'</div>':'')
    +'<table style="margin-top:.4rem"><tr><th>Tilt series</th><th>Scheme</th>'
    +'<th>Tilts matched</th>'
    +'<th title="largest stage move from the first position in the series">Stage drift'
    +' <span class="u">(&micro;m)</span></th></tr>'
    +keys.map(k=>`<tr><td><code>${esc(k)}</code></td>
      <td>${esc(dose[k].scheme||'')}</td>
      <td class="num">${(dose[k].points||[]).length}</td>
      <td class="num">${dose[k].stage_drift==null?'&mdash;':frFix(dose[k].stage_drift,3)}</td></tr>`).join('')
    +'</table><div class="muted" style="margin-top:.4rem;font-size:.8rem">'
    +'Read from WARP <code>.tomostar</code> <code>_wrpDose</code> (already cumulative e&#8315;/&#8491;&#178;). '
    +'SerialEM <code>ExposureDose</code> is used only when it is non-zero. In a dose-symmetric scheme the '
    +'acquisition order and the tilt order are different sequences, so low dose means '
    +'low tilt and the two effects compound at the ends.</div>';
}
// --- alignment and geometry ----------------------------------------------
function shiftSvg(track,mx){
  if(!track||track.length<2||!isFinite(mx)||mx<=0) return '<span class="muted">&mdash;</span>';
  const W=150,H=24;
  const X=i=>1+(W-2)*i/(track.length-1);
  const Y=v=>H-1-(H-2)*Math.min(1,v/mx);
  const d=track.map((p,i)=>(i?'L':'M')+X(i).toFixed(1)+' '+Y(p.shift).toFixed(1)).join(' ');
  return `<svg viewBox="0 0 ${W} ${H}" style="width:${W}px;height:${H}px;vertical-align:middle">
    <path d="${d}" fill="none" stroke="var(--accent)" stroke-width="1.3"
          stroke-linejoin="round"/></svg>`;
}
function renderAlign(st,series){
  const al=st.alignment||{}, el=document.getElementById('fralign');
  const keys=Object.keys(al).sort();
  if(!keys.length){
    el.innerHTML='<span class="muted">'+(srcState(st,'alignment')==='ready'
      ? 'no AreTomo <code>.aln</code> files under <code>warp_tiltseries/tiltstack/</code> yet'
      : 'loading…')+'</span>';
    return;
  }
  const warp={}; (series||[]).forEach(s=>{ warp[s.name]=s; });
  let gmax=0;
  keys.forEach(k=>(al[k].track||[]).forEach(p=>{ if(p.shift>gmax) gmax=p.shift; }));
  el.innerHTML='<table><tr><th>Tilt series</th>'
    +'<th title="tilt axis AreTomo fitted">Axis <span class="u">(AreTomo)</span></th>'
    +'<th title="tilt axis WARP recorded for the same series">Axis <span class="u">(WARP)</span></th>'
    +'<th title="largest AreTomo shift, in pixels of the aligned (binned) stack">'
    +'Max shift <span class="u">(px)</span></th>'
    +'<th title="tilts AreTomo discarded as too dark to align">Dark</th>'
    +'<th title="WARP AreAnglesInverted: the tilt-angle handedness for this series">Angles inverted</th>'
    +`<th title="AreTomo shift magnitude across the tilts, shared scale 0-${frFmt(gmax)} px">`
    +'Shift across tilts</th></tr>'
    +keys.map(k=>{
      const a=al[k], w=warp[k]||{};
      return `<tr><td><code>${esc(k)}</code></td>
        <td class="num">${frFmt(a.axis)}&deg;</td>
        <td class="num">${w.axis==null?'&mdash;':frFmt(w.axis)+'&deg;'}</td>
        <td class="num">${frFix(a.max_shift_px,1)}</td>
        <td class="num">${a.dark?'<span class="FAILED">'+a.dark+'</span>':'0'}</td>
        <td>${a.inverted==null?'<span class="muted">&mdash;</span>':(a.inverted?'yes':'no')}</td>
        <td>${shiftSvg(a.track,gmax)}</td></tr>`;
    }).join('')
    +'</table><div class="muted" style="margin-top:.4rem;font-size:.8rem">'
    +'Shifts and axis come from each series&rsquo; AreTomo <code>.aln</code>; the WARP axis '
    +'is its own record of the same series. WARP&rsquo;s max shift is not shown separately: '
    +'it imports this alignment and stores the same movement in &aring;ngstr&ouml;m. '
    +'<code>Dark</code> counts tilts AreTomo threw out as too dark to align.</div>';
}
function renderInventory(inv, recon){
  inv=inv||[]; recon=recon||[];
  const el=document.getElementById('inv');
  const cols=[['stack','Stack'],['aligned','Aligned'],['recon','Reconstructed'],
              ['tm','TM star'],['export','Exported']];
  const done=inv.filter(x=>x.recon).length;
  const rc={}; recon.forEach(r=>{ rc[r.name]=r; });
  // The Phase-5 sanity check opus-et-warp documents, automated: every WARP
  // reconstruction must have the same nx,ny,nz -- one odd series means a
  // skipped dim update or a wrong binning, and coordinates are wrong too.
  const dims={}; recon.forEach(r=>{ dims[r.nx+'x'+r.ny+'x'+r.nz]=1; });
  const dimKeys=Object.keys(dims);
  const bad=recon.length>1&&dimKeys.length>1;
  document.getElementById('invsum').textContent=inv.length
    ? done+' of '+inv.length+' reconstructed'
      +(recon.length?(bad?' \u2014 RECON DIMS DIFFER ACROSS SERIES!'
                          :' \u00b7 dims '+dimKeys.join(' = ')
                          +' \u00b7 voxel '+recon[0].voxel_a+' A'):'')
    : '';
  el.innerHTML=inv.length
    ? '<table><tr><th>Tilt series</th>'+cols.map(c=>'<th>'+c[1]+'</th>').join('')
      +(recon.length?'<th>Recon dims</th><th>Voxel <span class="u">(&Aring;)</span></th>':'')
      +'</tr>'
      +inv.map(x=>{ const r=rc[x.name];
        return `<tr${x.excluded?' class="excluded"':''}><td><code>${esc(x.name)}</code>${x.excluded?' <span class="FAILED">excluded</span>':''}</td>`
          +cols.map(c=>`<td class="num">${x[c[0]]?'<span class="RUNNING">&#10003;</span>':'<span class="muted">&mdash;</span>'}</td>`).join('')
          +(recon.length?`<td class="num">${r?esc(r.nx+'×'+r.ny+'×'+r.nz):'<span class="muted">&mdash;</span>'}</td>`
            +`<td class="num">${r?r.voxel_a:'<span class="muted">&mdash;</span>'}</td>`:'')
          +'</tr>'; }).join('')+'</table>'
      +(bad?'<div class="stale" style="margin-top:.5rem"><b>Reconstruction dimensions differ across series.</b> '
           +'opus-et-warp documents this as the Phase-5 failure: a skipped dim update or a wrong '
           +'binning, and every downstream coordinate is wrong for the odd series.</div>':'')
    : '<span class="muted">no tilt series found under tomostar/</span>';
}
// The 5 s tick rebuilds these cards, so expansion is tracked by run dir --
// otherwise an open detail view would snap shut every poll.
const TRAIN_OPEN={}, TRAIN_RUNS=[];
function renderTraining(runs){
  runs=runs||[];
  TRAIN_RUNS.length=0; runs.forEach(r=>TRAIN_RUNS.push(r));
  const el=document.getElementById('training');
  el.innerHTML=runs.length
    ? runs.map((r,i)=>{ try{
        const pts=r.points||[], p=r.params||{};
        const last=pts.length?pts[pts.length-1].loss:'?';
        const ep=pts.length?Math.max.apply(null,pts.map(x=>x.epoch)):'?';
        const total=parseInt(p.Epochs||'',10);
        const eta=trainEta(r);
        const open=!!TRAIN_OPEN[r.dir];
        return `<div class="qgroup"><div class="qlab2">${esc(r.dir)}
          <button class="qchip" style="float:right" onclick="toggleTrain('${esc(r.dir)}')">${open?'close':'detail'}</button>
          <span style="text-transform:none">&middot; ${r.weights} checkpoint${r.weights===1?'':'s'}
          &middot; epoch ${ep}${total?' / '+total:''} &middot; loss ${last}${eta?' &middot; ETA '+eta.txt:''}</span></div>
          ${lossSvg(pts)}
          <div id="traindetail-${i}" ${open?'':'hidden'} style="margin-top:.7rem">
            ${trainOverview(r)}${trainMetrics(r)}${trainAbout(r)}
          </div></div>`;
      }catch(e){
        return `<div class="qgroup"><div class="qlab2">${esc(r.dir||'run')}</div>
          <span class="muted">could not render this run: ${esc(String(e).slice(0,120))}</span></div>`;
      } }).join('')
    : '<span class="muted">no training runs under opuset/ yet -- phase 8 writes weights and a loss curve there</span>';
}
function toggleTrain(dir){
  TRAIN_OPEN[dir]=!TRAIN_OPEN[dir];
  renderTraining(TRAIN_RUNS);
}
function trainOverview(r){
  const p=r.params||{}, pts=r.points||[];
  const last=pts.length?pts[pts.length-1].epoch:null;
  const total=parseInt(p.Epochs||'',10);
  const pct=last!=null&&total?Math.min(100,Math.round(100*last/total)):null;
  const row=(k)=>p[k]?`<tr><td><code>${esc(k)}</code></td><td class="num">${esc(p[k])}</td></tr>`:'';
  return `<div class="qlab2">Overview</div>
    <table><tr><th>Parameter</th><th>Value</th></tr>
      ${['Epochs','Batch size','Learning rate','zdim','ANGPIX','Tilt range','Tilt step']
        .map(row).join('')}</table>
    ${pct!=null?`<div class="bar" style="width:100%;margin-top:.5rem"><i style="width:${pct}%"></i></div>
      <div class="muted" style="font-size:.8rem">${last} of ${total} epochs (${pct}%)</div>`:''}
    ${trainEtaLine(r)}`;
}
// Checkpoint mtimes are a wall-clock ruler: epochs per hour from the first
// and last checkpoint, remaining epochs from the declared total.
function trainEta(r){
  const total=parseInt((r.params||{}).Epochs||'',10);
  const mt=r.mtimes||{}, ks=Object.keys(mt).map(Number).sort((a,b)=>a-b);
  if(!total||ks.length<2) return null;
  const lastEp=ks[ks.length-1];
  if(lastEp>=total) return null;
  const rate=(mt[ks[ks.length-1]]-mt[ks[0]])/(ks[ks.length-1]-ks[0]);
  if(!(rate>0)) return null;
  const mins=Math.round((total-lastEp)*rate/60);
  const txt=mins>90? '~'+Math.floor(mins/60)+' h '+(mins%60)+' m' : '~'+mins+' min';
  return {txt: txt, pace: (rate/60).toFixed(1)};
}
function trainEtaLine(r){
  const e=trainEta(r);
  return e?`<div class="muted" style="font-size:.85rem;margin-top:.3rem">checkpoint pace `
    +e.pace+' min/epoch &mdash; remaining '+e.txt+'</div>':'';
}
function trainMetrics(r){
  const pts=r.points||[];
  const fam=[['loss','loss',720,130],['beta','beta',350,110],['snr','SNR',350,110],
             ['std','std',350,110],['mu','&mu;',350,110]];
  const cards=fam.map(f=>{ const s=seriesSvg(pts,f[0],f[1],f[2],f[3]);
    return s?`<div class="hcard"><h4>${f[1]}</h4>${s}</div>`:''; }).filter(Boolean).join('');
  return cards?'<div class="qlab2" style="margin-top:.7rem">Metrics</div>'
    +'<div class="hgrid">'+cards+'</div>':'';
}
function trainAbout(r){
  const p=r.params||{};
  const paths=[['Output',p.Output],['STAR',p.STAR],['Poses',p.Poses],
               ['Mask',p.Mask],['Split',p.Split],['Warm-start',p['Warm-start']]]
    .filter(x=>x[1]).map(x=>`<tr><td><code>${esc(x[0])}</code></td>`
      +`<td style="word-break:break-all"><code>${esc(x[1])}</code></td></tr>`).join('');
  const log=r.log?`<tr><td><code>log</code></td><td style="word-break:break-all"><code>${esc(r.log)}</code></td></tr>`:'';
  return (paths||log)?'<div class="qlab2" style="margin-top:.7rem">About</div>'
    +'<table>'+paths+log+'</table>':'';
}
function lossSvg(pts){ return seriesSvg(pts,'loss','loss',720,130); }
// Small multiple for one metric over epochs: same shape as the loss curve,
// smaller and label-light, so five metrics read as one family.
function seriesSvg(pts,key,label,W,H){
  const v=pts.filter(p=>p[key]!=null&&isFinite(p[key]));
  if(v.length<2) return '';
  // Axis pads sized so the y tick labels clear the left edge, matching the
  // dose chart's look.
  const PL=46,PR=10,PT=12,PB=20;
  const ys=v.map(p=>p[key]);
  let mn=Math.min.apply(null,ys),mx=Math.max.apply(null,ys);
  if(mx-mn<1e-12){ const d=Math.abs(mn||1)/2; mn-=d; mx+=d; }
  const X=i=>PL+(W-PL-PR)*i/(v.length-1);
  // Standard orientation: larger values higher on the chart, so a falling
  // loss is drawn falling. (An earlier version put the minimum at the top,
  // which drew a decreasing loss as an ascending line.)
  const Y=x=>H-PB-(H-PT-PB)*(x-mn)/(mx-mn);
  const path=v.map((p,i)=>(i?'L':'M')+X(i).toFixed(1)+' '+Y(p[key]).toFixed(1)).join(' ');
  const fmt=x=>Math.abs(x)>=1000?x.toPrecision(3):Math.abs(x)>=1?x.toFixed(1):x.toPrecision(2);
  const yticks=[mx,(mx+mn)/2,mn].map(val=>
    `<line x1="${PL-3}" y1="${Y(val).toFixed(1)}" x2="${PL}" y2="${Y(val).toFixed(1)}" stroke="var(--line-strong)"/>`
    +`<text x="${PL-5}" y="${(Y(val)+3).toFixed(1)}" font-size="8.5" text-anchor="end" fill="var(--muted)">${fmt(val)}</text>`).join('');
  const xt=[0,.33,.67,1].map(f=>{
    const idx=Math.round(f*(v.length-1)), x=X(idx);
    return `<line x1="${x.toFixed(1)}" y1="${H-PB}" x2="${x.toFixed(1)}" y2="${H-PB+3}" stroke="var(--line-strong)"/>`
      +`<text x="${x.toFixed(1)}" y="${H-PB+13}" font-size="8.5" text-anchor="middle" fill="var(--muted)">${v[idx].epoch}</text>`; }).join('');
  return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;max-width:${W}px;background:var(--card);border:1px solid var(--line);border-radius:6px">
    <line x1="${PL}" y1="${H-PB}" x2="${W-PR}" y2="${H-PB}" stroke="var(--line-strong)"/>
    <line x1="${PL}" y1="${PT}" x2="${PL}" y2="${H-PB}" stroke="var(--line-strong)"/>
    ${yticks}${xt}
    <path d="${path}" fill="none" stroke="var(--accent)" stroke-width="1.6"/>
    <text x="${PL}" y="11" font-size="9" fill="var(--muted)">${esc(label)}</text>
  </svg>`;
}
function renderRuns(rs){
  rs=rs||[];
  const el=document.getElementById('runslist');
  el.innerHTML=rs.length
    ? '<table><tr><th>Run directory</th><th>Phases done</th><th>Waiting on you</th></tr>'
      +rs.map(r=>`<tr><td><code>${esc(r.dir)}</code></td><td class="num">${r.done}/${r.total}</td>`
        +`<td>${(r.waiting||[]).length?esc(r.waiting.join(', ')):'<span class="muted">&mdash;</span>'}</td></tr>`).join('')
      +'</table>'
    : `<span class="muted">no other runs found${RUNS_PARENT?'':' -- start the server with --runs-parent &lt;parent dir&gt; to scan one'}</span>`;
}
function renderJobs(){
  const oth=document.getElementById('showother');
  // Jobs of yours submitted from other directories are not this run's work, so
  // they are off by default -- but reachable, since a job can be submitted
  // from anywhere.
  let js=[...LASTJOBS].concat(oth&&oth.checked
    ? LASTOTHER.map(j=>Object.assign({},j,{elsewhere:true})) : []);
  const hide=document.getElementById('hidefin');
  if(hide&&hide.checked) js=js.filter(j=>j.state==='RUNNING'||j.state==='PENDING');
  // Live work first, history after: the interesting part is what runs now.
  const rank=s=>s==='RUNNING'?0:(s==='PENDING'?1:2);
  js.sort((a,b)=>rank(a.state)-rank(b.state)||
    String(a.job_id).localeCompare(String(b.job_id),undefined,{numeric:true}));
  document.getElementById('jobs').innerHTML='<table><tr><th>Job</th><th>Name</th><th>State</th><th>Elapsed</th><th>GPU</th><th>Node</th><th></th></tr>'+
    (js.map(j=>`<tr class="jrow" title="open the log tail" onclick="showLog('${esc(j.job_id)}')"><td>${esc(j.job_id)}</td>
      <td>${esc(j.name)}${j.elsewhere?' <span class="muted" style="font-size:.75rem">elsewhere</span>':''}</td>
      <td class="${esc(j.state||'')}">${j.state?esc(j.state)
        :'<span class="muted" title="SLURM accounting has no record of this job; only its log remains">no record</span>'}</td>
      <td>${j.elapsed?esc(j.elapsed):'<span class="muted">\u2014</span>'}</td>
      <td class="num">${j.gpu!=null?j.gpu:'\u2014'}</td><td>${esc(j.nodelist||'')}</td>
      <td><button onclick="event.stopPropagation();showLog('${esc(j.job_id)}')">log</button></td></tr>`).join('')
      ||`<tr><td colspan=7>${LASTJOBS.length?'all jobs hidden -- uncheck "hide finished"':'no jobs for this run directory'}</td></tr>`)+'</table>'
    +(LASTJOBS.some(j=>j.from_log)
      ? '<div class="muted" style="margin-top:.4rem;font-size:.8rem">Jobs marked '
        +'<b>no record</b> are known only from the log files in this run&rsquo;s '
        +'<code>logs/</code> &mdash; SLURM accounting returned nothing for them, so their '
        +'exit state is unknown. Their logs still open.</div>' : '');
  const logEl=document.getElementById('log');
  if(!logEl.dataset.init){
    const f=LASTJOBS.filter(j=>j.state==='FAILED'||j.state==='TIMEOUT')
      .sort((a,b)=>String(b.job_id).localeCompare(String(a.job_id),undefined,{numeric:true}))[0];
    if(f){ logEl.dataset.init='1'; showLog(f.job_id); }
  }
}
async function showLog(job,stream){
  stream=stream||'err'; LOG.job=job; LOG.stream=stream;
  const el=document.getElementById('log'); el.textContent='loading...';
  document.getElementById('logmeta').textContent='job '+job+' \u00b7 last '+LOG.lines+' lines';
  document.getElementById('berr').classList.toggle('lactive',stream==='err');
  document.getElementById('bout').classList.toggle('lactive',stream==='out');
  try{const r=await fetch(`/api/logs?job=${encodeURIComponent(job)}&stream=${encodeURIComponent(stream)}&lines=${LOG.lines}`);
      const b=await r.json(); el.textContent=b.text||b.error||'(empty)';}
  catch(e){el.textContent='failed to fetch log';}
}
function setStream(s){ if(LOG.job) showLog(LOG.job,s); }
function setLines(n){
  LOG.lines=n;
  document.getElementById('bmore').textContent=n===50?'500 lines':'50 lines';
  if(LOG.job) showLog(LOG.job,LOG.stream);
}
function refetchLog(){ if(LOG.job) showLog(LOG.job,LOG.stream); }
function allowed(sp){
  if(sp.choices&&sp.choices.length) return 'one of '+sp.choices.join(' / ');
  if(sp.min!==null&&sp.min!==undefined) return sp.min+' – '+sp.max;
  return '';
}
let SPECIES=null;
async function loadCfg(){
  const q=SPECIES?('?species='+encodeURIComponent(SPECIES)):'';
  const c=await (await fetch('/api/config'+q)).json();
  // Say which file could not be read rather than rendering an empty panel.
  document.getElementById('cfgproblem').innerHTML=c.problem
    ? `<div class="stale"><b>Config</b> &mdash; ${esc(c.problem)}</div>` : '';
  const avail=c.species_available||[];
  SPECIES_LIST=avail;
  if(!SPECIES) SPECIES=c.species_current||null;
  document.getElementById('spec').innerHTML = avail.length
    ? avail.map(s=>`<span class="chip ${s===c.species_current?'ok':''}"
        style="cursor:pointer" onclick="pickSpecies('${esc(s)}')">${esc(s)}</span>`).join('')
    : '<span class="muted">no species confs found</span>';
  const vals=c.values||{};
  CFGWARN={}; Object.keys(c.keys).forEach(k=>{ if(c.keys[k].warning) CFGWARN[k]=c.keys[k].warning; });
  CFGSPEC=c.keys;
  const ds=c.dataset||{};
  document.getElementById('dsfile').textContent=(c.files&&c.files.pipeline)||'';
  document.getElementById('ds').innerHTML=Object.keys(ds).length
    ? '<table><tr><th>Setting</th><th>Value</th></tr>'+Object.keys(ds).map(k=>
        `<tr><td><code>${esc(k)}</code></td><td class="num">${esc(ds[k])}</td></tr>`).join('')
      +'</table><div class="muted" style="margin-top:.4rem">Read-only &mdash; acquisition facts and paths describe the data, so changing them would invalidate work already done rather than reconfigure it.</div>'
    : '<span class="muted">no dataset config available</span>';
  const row=(k)=>{
    const sp=c.keys[k], cur=vals[k], absent=(cur===undefined||cur===null);
    return `<tr>
      <td><code>${esc(k)}</code>${sp.warning?' <span title="'+esc(sp.warning)+'" style="cursor:help">&#9888;</span>':''}</td>
      <td class="num">${absent?'<span class="muted">not in this file</span>':esc(cur)}</td>
      <td>${absent?'':`<input id="v_${esc(k)}" size="9" placeholder="${esc(cur)}" aria-label="new value for ${esc(k)}"
               onkeydown="if(event.key==='Enter')save('${esc(k)}')">`}</td>
      <td class="muted">${(c.recommended||{})[k]
          ? `<b style="color:var(--fg);font-weight:600">${esc((c.recommended||{})[k])}</b>
             <div style="font-size:.72rem">range ${esc(allowed(sp))}</div>`
          : esc(allowed(sp))}</td>
      <td class="muted">${esc(sp.description||'')}</td>
      <td>${absent?'':`<button onclick="save('${esc(k)}')">Save</button>`} <span id="m_${esc(k)}"></span></td>
    </tr>`;
  };
  const head='<table><tr><th>Key</th><th>Current</th><th>New value</th><th>Allowed</th><th>What it does</th><th></th></tr>';
  const inFile=(f)=>Object.keys(c.keys).filter(k=>c.keys[k].file===f);
  document.getElementById('specfile').textContent=(c.files&&c.files.species)||'';
  document.getElementById('pipefile').textContent=(c.files&&c.files.pipeline)||'';
  const order=c.stage_order||[];
  const skeys=inFile('species');
  const groups=order.filter(st=>skeys.some(k=>c.keys[k].stage===st));
  document.getElementById('cfgspecies').innerHTML=
    groups.map(st=>`<h3>${esc(st)}</h3>`+head
      +skeys.filter(k=>c.keys[k].stage===st).map(row).join('')+'</table>').join('')
    +'<div class="muted" style="margin-top:.4rem">Applies to the selected species only &mdash; switch species above to edit another.</div>';
  document.getElementById('cfgpipeline').innerHTML=
    head+inFile('pipeline').map(row).join('')+'</table>'
    +'<div class="stale" style="margin-top:.4rem"><b>Shared by every species in this run.</b> A change here affects all of them, not just the selected species.</div>';
}
function showQc(rel){
  // An overlay is always in view, wherever you clicked, and the arrows
  // browse the filtered listing without closing it.
  if(!QC_LIST.includes(rel)) QC_LIST=[rel];
  QC_IDX=QC_LIST.indexOf(rel);
  const many=QC_LIST.length>1;
  const e=entryOf(rel);
  const l=document.getElementById('qclight');
  l.innerHTML=`<div class="cap">${esc(qcCaption(e))}</div>
    <img src="/api/qc/image?path=${encodeURIComponent(rel)}" alt="${esc(qcCaption(e))}"
         onload="document.getElementById('qcload').textContent='click anywhere or press Esc to close'">
    <div class="lnav">${many?`<button class="navbtn" onclick="event.stopPropagation();navQc(-1)" title="previous image">&#8249;</button>
      <span class="lcount">${QC_IDX+1} / ${QC_LIST.length}</span>
      <button class="navbtn" onclick="event.stopPropagation();navQc(1)" title="next image">&#8250;</button>`:''}</div>
    ${qcStrip(QC_IDX)}
    <div class="hint" id="qcload" title="${esc(rel)}">loading full image&hellip;</div>`;
  l.hidden=false;
}
function navQc(d){
  if(!QC_LIST.length) return;
  showQc(QC_LIST[(QC_IDX+d+QC_LIST.length)%QC_LIST.length]);
}
function closeQc(){ document.getElementById('qclight').hidden=true; }
function syncROpts(){
  const enter="if(event.key==='Enter')renderQc()";
  const kind=document.getElementById('rkind').value;
  const on=kind==='overlay';
  document.getElementById('ropts').innerHTML = !on
    ? `<span class="muted" style="margin-left:.5rem">z</span>
       <input id="rZ" size="4" placeholder="centre" title="Z index for the XY slice"
              aria-label="z index for the XY slice" onkeydown="${enter}">
       <span class="muted">y</span>
       <input id="rY" size="4" placeholder="centre" title="Y index for the XZ slice"
              aria-label="y index for the XZ slice" onkeydown="${enter}">`
    : `<span class="muted" style="margin-left:.5rem">species</span>
       <select id="rSpecies" aria-label="species to render">${SPECIES_LIST.map(s=>
         `<option${s===SPECIES?' selected':''}>${esc(s)}</option>`).join('')}</select>
       <span class="muted">slabs</span>
       <input id="rNSlabs" size="2" value="6" title="how many z-slabs to sample"
              aria-label="how many z-slabs to sample" onkeydown="${enter}">
       <span class="muted">thickness</span>
       <input id="rThick" size="3" value="" placeholder="auto" title="slab thickness in tomogram px"
              aria-label="slab thickness in tomogram px" onkeydown="${enter}">
       <span class="muted">top-N</span>
       <input id="rTopN" size="4" value="" placeholder="auto" aria-label="top N picks"
              onkeydown="${enter}">
       <select id="rProj" title="projection" aria-label="projection"><option value="">projection</option>
         <option value="mean">mean</option><option value="min">min</option>
         <option value="max">max</option></select>`;
}
async function renderQc(){
  const tomo=document.getElementById('rtomo').value;
  const kind=document.getElementById('rkind').value;
  const m=document.getElementById('rmsg');
  // A render runs up to a minute on the login node; a second click would
  // just queue a duplicate job.
  const btn=document.querySelector('#qcrender button');
  if(btn) btn.disabled=true;
  m.textContent='rendering on the cluster, this can take a minute...'; m.className='muted';
  try{
    const r=await fetch('/api/qc/render',{method:'POST',
      headers:{'Content-Type':'application/json','X-CSRF-Token':CSRF},
      body:JSON.stringify({kind:kind, tomo:tomo,
        species:((document.getElementById('rSpecies')||{}).value)||SPECIES,
        n_slabs:(document.getElementById('rNSlabs')||{}).value,
        slab_thickness:(document.getElementById('rThick')||{}).value,
        top_n:(document.getElementById('rTopN')||{}).value,
        project:(document.getElementById('rProj')||{}).value,
        z:(document.getElementById('rZ')||{}).value,
        y:(document.getElementById('rY')||{}).value})});
    const b=await r.json().catch(()=>({}));
    if(r.ok){ m.textContent='rendered '+(b.rendered||[]).length+' image(s)'; m.className='RUNNING';
              loadQc(); }
    else { m.textContent='error: '+(b.error||r.status); m.className='FAILED'; }
  } catch(e){ m.textContent='error: '+e; m.className='FAILED'; }
  finally { if(btn) btn.disabled=false; }
}
async function loadQc(){
  if(!SPECIES_LIST.length){
    // the Config tab may never have been opened
    try{ const c=await (await fetch('/api/config')).json();
         SPECIES_LIST=c.species_available||[];
         if(!SPECIES) SPECIES=c.species_current||null; }catch(e){}
  }
  const [qr,sr]=await Promise.all([fetch('/api/qc'),fetch('/api/status')]);
  const b=await qr.json(), st=await sr.json();
  const tomos=(st.tilt_series||[]).map(x=>x.name);
  const tsS=srcState(st,'tilt_series'), qcS=srcState(st,'qc_images');
  const note=(s,empty)=>s==='loading' ? 'loading from the cluster&hellip;'
                       : s==='error' ? 'unavailable &mdash; see the stale banner' : empty;
  document.getElementById('qcrender').innerHTML=tomos.length
    ? `<select id="rtomo" aria-label="tilt series to render">${tomos.map(n=>`<option>${esc(n)}</option>`).join('')}</select>
       <select id="rkind" aria-label="what to render" onchange="syncROpts()"><option value="slices">tomogram slices</option>
         <option value="overlay">TM pick overlay</option></select>
       <span id="ropts"></span>
       <button onclick="renderQc()">Render</button> <span id="rmsg" class="muted"></span>
       <div class="muted" style="margin-top:.3rem">Runs the QC tool on the cluster login node and can take a minute.</div>`
    : `<span class="muted">${note(tsS,'no tilt series available to render')}</span>`;
  const imgs=b.images||[];
  QC_READY = imgs.length>0 && tomos.length>0;
  QC_ENTRIES = b.entries || [];
  if(!imgs.length){
    document.getElementById('qcnav').innerHTML='';
    document.getElementById('qcfilter').innerHTML='';
    document.getElementById('qclist').innerHTML=
      `<span class="muted">${note(qcS,'no QC images found')}</span>`; return; }
  const secs=presentSections(QC_ENTRIES);
  if(!QC_SECTION_CHOSEN || !secs.some(s=>s.id===QC_FILTER.section)){
    QC_FILTER.section = secs.length ? secs[secs.length-1].id : 'qc';
    QC_SECTION_CHOSEN=true;
  }
  if(QC_FILTER.tomo!=='all' && !QC_ENTRIES.some(e=>e.tomo===QC_FILTER.tomo))
    QC_FILTER.tomo='all';
  syncROpts();
  renderQcList();
}
// A section is the pipeline check you are answering. Nested folders such as
// qc/gate2_j360/ belong to Gate 2, not to reconstruction, via e.section.
const QC_SRC_LABELS={qc:'Reconstruction',
  gate1_qc:'Gate 1 · alignment', gate2_qc:'Gate 2 · picks',
  gate3_qc:'Gate 3 · states', gate4_qc:'Gate 4 · refinement',
  qc_ondemand:'Rendered here'};
const QC_SRC_HINT={qc:'Is the tomogram reconstructed and the handedness right?',
  gate1_qc:'Is the tilt-series alignment good enough to keep?',
  gate2_qc:'Do the picks land on real particles, and not on ice or carbon?',
  gate3_qc:'Which state is the one worth refining?',
  gate4_qc:'Does the final map look like the density it claims?'};
function qcSrcLabel(d){ return QC_SRC_LABELS[d]||d; }
function qcSection(e){ return e.section||e.dir; }
// Pipeline order, not alphabetical: sorting by name put Gate 2 above the
// reconstruction it is drawn on top of.
const QC_SRC_ORDER=['qc','gate1_qc','gate2_qc','gate3_qc','gate4_qc'];
function qcSrcRank(d){ const i=QC_SRC_ORDER.indexOf(d);
                       return i<0?QC_SRC_ORDER.length:i; }
function presentSections(entries){
  const have={};
  entries.forEach(e=>{ if(e.dir===ONDEMAND) return;
                       const s=qcSection(e); have[s]=(have[s]||0)+1; });
  return Object.keys(have)
    .sort((a,b)=>qcSrcRank(a)-qcSrcRank(b)||a.localeCompare(b))
    .map(id=>({id, n:have[id]}));
}
function shortTomo(t, all){
  if(!t) return 'Overview';
  const names=(all&&all.length?all:[t]).filter(Boolean);
  if(names.length<2) return t;
  let i=0, first=names[0];
  while(i<first.length && names.every(n=>n[i]===first[i])) i++;
  while(i>0 && !/[-_]/.test(first[i-1])) i--;
  return t.slice(i)||t;
}
function entryOf(rel){ return QC_ENTRIES.find(e=>e.path===rel)||{path:rel,label:rel}; }
function qcCaption(e){
  const tomos=[...new Set(QC_ENTRIES.map(x=>x.tomo).filter(Boolean))];
  return [e.tomo?shortTomo(e.tomo,tomos):null, e.species, e.label].filter(Boolean).join(' · ')||e.path;
}
function qcStrip(idx){
  const n=QC_LIST.length;
  if(n<2) return '';
  const win=8;
  let lo=Math.max(0, idx-Math.floor(win/2)), hi=Math.min(n, lo+win);
  lo=Math.max(0, hi-win);
  return `<div class="qstrip" onclick="event.stopPropagation()">`
    +QC_LIST.slice(lo,hi).map((p,i)=>{
      const k=lo+i, src=QC_THUMBS[p];
      return `<button class="qmini${k===idx?' on':''}" title="${esc(qcCaption(entryOf(p)))}"
        onclick="event.stopPropagation();showQc('${esc(p)}')">`
        +(src?`<img src="${src}" alt="">`:'')+`</button>`;
    }).join('')+'</div>';
}
function qcCard(e,showTomo){
  const src=QC_THUMBS[e.path];
  const tomos=[...new Set(QC_ENTRIES.map(x=>x.tomo).filter(Boolean))];
  const cap=[showTomo&&e.tomo?shortTomo(e.tomo,tomos):null, e.species, e.label].filter(Boolean).join(' · ');
  return `<figure class="qcard" title="${esc(e.path)}" onclick="showQc('${esc(e.path)}')">`
    +(src?`<img src="${src}" alt="${esc(cap)}">`:'<div class="qph"></div>')
    +`<figcaption>${esc(cap)}</figcaption></figure>`;
}
// One row per tilt series, so the eye can run down a column of the same view
// across series -- which is the actual QC question ("which one is bad?").
// Dataset-wide images sit in Overview, not a fake "no tilt series" filter.
function seriesRows(items){
  const byTomo={};
  items.forEach(e=>{ const t=e.tomo||''; (byTomo[t]=byTomo[t]||[]).push(e); });
  const keys=Object.keys(byTomo).sort((a,b)=>{
    if(!a) return -1; if(!b) return 1;
    return a.localeCompare(b, undefined, {numeric:true, sensitivity:'base'});
  });
  const all=keys.filter(Boolean);
  return keys.map(t=>{
    const g=byTomo[t].slice().sort((a,b)=>
      (a.species||'').localeCompare(b.species||'')||(a.slab||0)-(b.slab||0)
      ||String(a.variant||'').localeCompare(String(b.variant||''))
      ||a.label.localeCompare(b.label));
    const ov=!t;
    const lab=ov?'Overview':shortTomo(t, all);
    return `<div class="qrow"><div class="qrowlab">`
      +(ov?`<span title="dataset-wide">${esc(lab)}</span>`
          :`<button class="qtomo" onclick="pickQcTomo('${esc(t)}')"
                title="${esc(t)}">${esc(lab)}</button>`)
      +`<span class="muted">${g.length}</span></div>
      <div class="qgrid">${g.map(e=>qcCard(e,false)).join('')}</div></div>`;
  }).join('');
}
// Species is a property of a pick overlay, not of a slice, so a slice is never
// filtered out by it. An overlay with no species in its filename is matched
// only by the explicit "unlabelled" chip -- never by "ribo", which would be a
// claim the filename does not make.
function keepSpecies(e,want){
  if(want==='all') return true;
  if(e.kind!=='overlay') return true;
  return want==='unlabelled' ? !e.species : e.species===want;
}
function pickQcTomo(t){ QC_FILTER.tomo=(QC_FILTER.tomo===t?'all':t); renderQcList(); }
function pickQcSpecies(sp){ QC_FILTER.species=sp; renderQcList(); }
function pickQcSection(id){ QC_FILTER.section=id; QC_FILTER.tomo='all'; renderQcList(); }
function pickQcVariant(v){ QC_FILTER.variant=v; renderQcList(); }
async function loadThumbs(paths){
  const want=paths.filter(p=>!(p in QC_THUMBS));
  if(!want.length||THUMB_PENDING) return;
  THUMB_PENDING=true;
  // Only this batch is requested, so only this batch may be marked attempted --
  // marking all of `want` wrote off everything past the cap as a permanent
  // placeholder that never retried.
  const batch=want.slice(0,60);
  try{
    const r=await fetch('/api/qc/thumbs',{method:'POST',
      headers:{'Content-Type':'application/json','X-CSRF-Token':CSRF},
      body:JSON.stringify({paths:batch})});
    const b=await r.json();
    Object.assign(QC_THUMBS, b.thumbs||{});
  }catch(e){}
  // mark attempted so a failure cannot spin the fetch loop
  batch.forEach(p=>{ if(!(p in QC_THUMBS)) QC_THUMBS[p]=null; });
  THUMB_PENDING=false;
  renderQcList(true);
}
function renderQcList(thumbsOnly){
  const f=QC_FILTER;
  const matchVar=e=>f.variant==='all' || (f.variant==='allpicks'&&e.variant==='all')
    || (f.variant==='topN'&&e.variant==='topN');
  const keepMine=e=>(f.tomo==='all'||e.tomo===f.tomo)&&keepSpecies(e,f.species)&&matchVar(e);
  const archived=QC_ENTRIES.filter(e=>e.dir!==ONDEMAND);
  const inSec=archived.filter(e=>qcSection(e)===f.section);
  const withTomo=[...new Set(inSec.map(e=>e.tomo).filter(Boolean))]
    .sort((a,b)=>a.localeCompare(b, undefined, {numeric:true, sensitivity:'base'}));
  const withSp=[...new Set(inSec.map(e=>e.species).filter(Boolean))].sort();
  if(inSec.some(e=>e.kind==='overlay'&&!e.species)) withSp.push('unlabelled');
  if(f.species!=='all' && !withSp.includes(f.species)) QC_FILTER.species='all';
  const hasAll=inSec.some(e=>e.variant==='all'), hasTop=inSec.some(e=>e.variant==='topN');
  if(!(hasAll&&hasTop) && f.variant!=='all') QC_FILTER.variant='all';
  const keep=e=>qcSection(e)===f.section && keepMine(e);
  const mine=QC_ENTRIES.filter(e=>e.dir===ONDEMAND&&keepMine(e));
  const mineEl=document.getElementById('qcmine');
  mineEl.hidden=!mine.length;
  mineEl.innerHTML=`<div class="qgroup"><div class="qlab2">Rendered here
      <span style="text-transform:none" class="muted">(${mine.length})</span></div>`
    +seriesRows(mine)+'</div>';
  const es=archived.filter(keep);
  const secs=presentSections(QC_ENTRIES);
  if(!thumbsOnly){
  document.getElementById('qcnav').innerHTML=secs.map(s=>
    `<button class="qcheck${s.id===f.section?' on':''}" onclick="pickQcSection('${esc(s.id)}')">`
    +`${esc(qcSrcLabel(s.id))} <span class="muted">${s.n}</span></button>`).join('');
  document.getElementById('qcfilter').innerHTML=
    (withTomo.length
      ? `<div class="qfilt"><span class="muted">Tilt series</span><span id="fTomo">`
        +`<button class="qchip${f.tomo==='all'?' ok':''}" onclick="QC_FILTER.tomo='all';renderQcList()">all (${withTomo.length})</button>`
        +withTomo.map(x=>`<button class="qchip${x===f.tomo?' ok':''}"
            onclick="pickQcTomo('${esc(x)}')">${esc(shortTomo(x,withTomo))}</button>`).join('')
        +'</span></div>' : '<span id="fTomo" hidden></span>')
    +(withSp.length>1
      ? `<div class="qfilt"><span class="muted">Species</span><span id="fSpecies">`
        +[['all','all']].concat(withSp.map(x=>[x,x])).map(([v,l])=>
          `<button class="qchip${f.species===v?' ok':''}" data-sp="${esc(v)}"
             onclick="pickQcSpecies('${esc(v)}')">${esc(l)}</button>`).join('')
        +'</span></div>' : '')
    +(hasAll&&hasTop
      ? `<div class="qfilt"><span class="muted">Picks</span><span id="fVariant">`
        +[['all','all'],['allpicks','all picks'],['topN','top-N']].map(([v,l])=>
          `<button class="qchip${f.variant===v?' ok':''}"
             onclick="pickQcVariant('${v}')">${l}</button>`).join('')
        +'</span></div>' : '')
    +`<span class="muted" id="fCount"></span>`;
  }
  const c=document.getElementById('fCount');
  if(c) c.textContent=`${es.length} of ${inSec.length}`
                      +(f.tomo==='all'?'':` · ${esc(shortTomo(f.tomo,withTomo))} only`);
  const listEl=document.getElementById('qclist');
  if(!es.length){
    listEl.innerHTML='<span class="muted">nothing matches this filter</span>';
  }else{
    const dirs=[...new Set(es.map(e=>e.dir))];
    const from=dirs.length<=3?dirs.map(d=>`<code>${esc(d)}/</code>`).join(' ')
                             :`${dirs.length} folders`;
    const bare=es.filter(e=>e.kind==='overlay'&&!e.species).length;
    listEl.innerHTML=`<div class="qgroup">`
      +(QC_SRC_HINT[f.section]?`<p class="qask">${esc(QC_SRC_HINT[f.section])}</p>`:'')
      +`<div class="qhint">from ${from}</div>`
      +(bare?`<div class="qhint" style="color:var(--accent)">${bare} of these
                name no species in the filename, so they cannot be attributed to one
                &mdash; re-render them from Render a new view below to get labelled copies.</div>`:'')
      +seriesRows(es)+'</div>';
  }
  QC_LIST=[...mine,...es].map(e=>e.path);
  loadThumbs(QC_LIST);
}
function pickSpecies(s){ SPECIES=s; loadCfg(); }
async function createSpecies(){
  const el=document.getElementById('newspec'), m=document.getElementById('newmsg');
  const name=el.value.trim();
  if(!name){ m.textContent='enter a name'; m.className='PENDING'; return; }
  m.textContent='creating...'; m.className='muted';
  const r=await fetch('/api/species',{method:'POST',
    headers:{'Content-Type':'application/json','X-CSRF-Token':CSRF},
    body:JSON.stringify({name:name, template:SPECIES})});
  const b=await r.json().catch(()=>({}));
  if(r.ok){ m.textContent='created '+b.created; m.className='RUNNING';
            el.value=''; SPECIES=b.created; loadCfg(); }
  else { m.textContent='error: '+(b.error||r.status); m.className='FAILED'; }
}
// Same rules the server enforces (config_edit.KeySpec), checked before the
// round trip so a typo never leaves the input.
function validate(k,v){
  const sp=CFGSPEC[k]||{};
  if(sp.choices&&sp.choices.length)
    return sp.choices.includes(v)?'':'must be one of '+sp.choices.join(' / ');
  if(sp.type==='int'&&!/^-?\d+$/.test(v)) return 'must be an integer';
  if(sp.type==='float'&&!/^-?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$/.test(v))
    return 'must be a number';
  const n=parseFloat(v);
  if(sp.min!==null&&sp.min!==undefined&&n<sp.min) return 'must be at least '+sp.min;
  if(sp.max!==null&&sp.max!==undefined&&n>sp.max) return 'must be at most '+sp.max;
  return '';
}
async function save(k,confirmRunning){
  const el=document.getElementById('v_'+k), m=document.getElementById('m_'+k);
  const v=el?el.value.trim():'';
  if(!v){ m.textContent='enter a value'; m.className='PENDING'; return; }
  const err=validate(k,v);
  el.classList.toggle('bad',!!err);
  if(err){ m.textContent=err; m.className='FAILED'; return; }
  if(!confirmRunning && CFGWARN[k] && !confirm(CFGWARN[k]+'\n\nChange '+k+' anyway?')){
      m.textContent='cancelled'; m.className='muted'; return; }
  const body={key:k,value:v}; if(SPECIES)body.species=SPECIES;
  if(confirmRunning)body.confirm_running=true;
  const r=await fetch('/api/config',{method:'POST',
    headers:{'Content-Type':'application/json','X-CSRF-Token':CSRF},body:JSON.stringify(body)});
  const b=await r.json().catch(()=>({}));
  if(r.status===409){ if(confirm(b.error+'\n\nApply anyway?')) return save(k,true);
                      m.textContent='cancelled'; m.className='muted'; return; }
  m.textContent=r.ok?'saved':('error: '+(b.error||r.status));
  m.className=r.ok?'RUNNING':'FAILED';
  if(r.ok){ el.value=''; loadCfg(); }
}
function filterCfg(){
  const q=(document.getElementById('cfgsearch').value||'').toLowerCase();
  ['cfgpipeline','cfgspecies'].forEach(id=>{
    document.querySelectorAll('#'+id+' tbody tr').forEach(tr=>{
      tr.style.display=(!q||tr.textContent.toLowerCase().includes(q))?'':'none';
    });
  });
}
// Mirror the system color scheme onto html.dark so the palette follows the OS.
const DARKMQ=matchMedia('(prefers-color-scheme: dark)');
function applyScheme(){document.documentElement.classList.toggle('dark',DARKMQ.matches);}
if(DARKMQ.addEventListener) DARKMQ.addEventListener('change',applyScheme);
applyScheme();
document.querySelectorAll('#tabbar .tab').forEach(b=>b.onclick=()=>showTab(b.dataset.t));
document.addEventListener('keydown', e=>{
  const open=!document.getElementById('qclight').hidden;
  if(e.key==='Escape') closeQc();
  if(open&&e.key==='ArrowRight') navQc(1);
  if(open&&e.key==='ArrowLeft') navQc(-1);
  const tag=(document.activeElement||{}).tagName||'';
  if(!open&&!e.ctrlKey&&!e.metaKey&&!/(INPUT|SELECT|TEXTAREA)/.test(tag)){
    const map={1:'run',2:'frames',3:'dataset',4:'qc',5:'training',6:'inventory',7:'refine',8:'config',9:'runs'};
    if(map[e.key]) showTab(map[e.key]);
  }
});
showTab(TABS.includes(location.hash.slice(1))?location.hash.slice(1):'run');
tick(); loadCfg(); loadQc(); setInterval(tick,5000);
</script></body></html>"""


# A gate is approved exactly when a checkpoints[] entry records its decision,
# so an absent entry means it is still waiting on the human.
GATES = [
    ("alignment_qc", "Gate 1 - alignment QC"),
    ("picks_qc", "Gate 2 - picks QC"),
    ("state_selection", "Gate 3 - state selection"),
    ("refine", "Gate 4 - resolution sign-off"),
]

# Phase numbers mean nothing to a human, so the table names each stage. These
# mirror opus-et-warp/scripts/manifest.yml (the canonical map validate.sh
# reads); the dashboard shows remote data but does not parse remote YAML, so
# the names are pinned here. Drift shows up as a bare number -- harmless.
PHASE_DESCRIPTIONS = {
    "1": "Frame series: motion correction + CTF",
    "2": "Tilt series import + settings",
    "3": "Tilt stack export + AreTomo2 alignment",
    "3a": "Export tilt stacks from WARP",
    "3b": "AreTomo2 alignment + tilt-angle negation",
    "3.5": "Update TOMO_DIMS from AreTomo output",
    "4": "Import AreTomo alignments into WARP",
    "5": "CTF estimation + tomogram reconstruction",
    "5a": "Tilt-series CTF estimation",
    "5b": "Tomogram reconstruction",
    "6": "Template matching end-to-end",
    "6a": "Generate PyTOM-ready template from user MRC",
    "6b": "Generate template-matching sphere mask",
    "6c": "Generate PyTOM template-matching job XMLs",
    "6d": "Run PyTOM template matching sequentially",
    "6e": "Extract template-matching candidates",
    "6f": "Convert PyTOM XML candidates to STAR",
    "6g": "Convert PyTOM STAR to WARP-compatible STAR",
    "7": "Export subtomograms for OPUS-ET training",
    "8": "OPUS-ET training: heterogeneity + fixed-mode averaging",
    "8a": "Density-shaped training mask (optional)",
    "8b": "OPUS-ET heterogeneity training (grad mode)",
    "8c": "OPUS-ET fixed-mode averaging (half-maps)",
    "M": "MTools refinement: population, species, MCore, mask, export",
}


def _phase_sort_key(phase):
    """Natural order: '3a' after '3', '10' after '5'. A dotted suffix is a
    follow-up stage (3.5 fixes up what 3a/3b produced), so it sorts after the
    lettered subphases, matching manifest order."""
    num, suffix = "", ""
    for ch in str(phase):
        if ch.isdigit() and not suffix:
            num += ch
        else:
            suffix += ch
    return (int(num) if num else 0,
            1 if suffix.startswith(".") else 0, suffix)


def build_phases(run_state, completion):
    """Merge the run-state phase lifecycle with validate.sh disk completion."""
    merged = {}
    for phase, entry in ((run_state or {}).get("phases") or {}).items():
        merged.setdefault(str(phase), {})["status"] = (entry or {}).get("status")
    for phase, entry in (completion or {}).items():
        d = merged.setdefault(str(phase), {})
        d["completion"] = (entry or {}).get("completion")
        d["done"] = (entry or {}).get("done")
        d["total"] = (entry or {}).get("total")
    return [{"phase": p, "desc": PHASE_DESCRIPTIONS.get(p, ""), **v}
            for p, v in sorted(merged.items(), key=lambda kv: _phase_sort_key(kv[0]))]


# A gate is only actionable once the pipeline has actually stopped at it.
def _finite(o):
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _finite(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_finite(v) for v in o]
    return o


def jdump(obj):
    """Serialise for the browser.

    json.dumps emits bare Infinity/NaN, which is valid Python but INVALID
    JSON: JSON.parse rejects the whole document, so one non-finite float
    anywhere stops the entire page updating -- not just the panel that
    produced it. Replace them with null and refuse to emit them at all.
    """
    return json.dumps(_finite(obj), allow_nan=False)


def build_attention(snapshot, jobs, gates, waiting):
    """What needs a human right now, most urgent first.

    Everything else in the dashboard reports state; this answers 'do I need to
    do something?' -- a failed job, a gate holding the run, a phase that thinks
    it is running with nothing in the queue, or a filesystem about to fill.
    """
    items = []
    dead = [j for j in jobs
            if str(j.get("state", "")).upper() in ("FAILED", "TIMEOUT", "NODE_FAIL", "OUT_OF_MEMORY")]
    for j in dead[:5]:
        items.append({"level": "error",
                      "text": f"job {j.get('job_id')} ({j.get('name')}) {j.get('state')}"})

    if waiting:
        named = ", ".join(f"{p} ({PHASE_DESCRIPTIONS[p]})" if p in PHASE_DESCRIPTIONS
                          else p for p in waiting)
        items.append({"level": "action",
                      "text": f"phase {named} parked at a checkpoint"})
    for g in gates:
        if not g.get("approved") and waiting:
            items.append({"level": "action",
                          "text": f"{g['label']} not signed off -- review the "
                                  "evidence, then have the conductor record the "
                                  "checkpoint"})
            break

    # A phase marked running with nothing queued means the job died without
    # updating the run state -- the failure mode reconcile_jobs exists for.
    rs = snapshot.get("run_state")
    run_state = (rs.data if rs is not None and rs.data else {}) or {}
    live = {str(j.get("job_id")) for j in jobs
            if str(j.get("state", "")).upper() in ("RUNNING", "PENDING")}
    for phase, entry in ((run_state.get("phases") or {}).items()):
        if (entry or {}).get("status") == "running":
            pj = {str(x) for x in ((entry or {}).get("jobs") or [])}
            if pj and not (pj & live):
                items.append({"level": "warn",
                              "text": f"phase {phase} is marked running but no job "
                                      "of its own is queued"})

    ds = snapshot.get("disk")
    disk = ds.data if ds is not None and ds.data else None
    if disk and disk.get("pct_used") is not None:
        if disk["pct_used"] >= 90:
            items.append({"level": "error",
                          "text": f"filesystem {disk['pct_used']}% full, "
                                  f"{disk['avail_gb']:g} GB left"})
        elif disk["pct_used"] >= 80:
            items.append({"level": "warn",
                          "text": f"filesystem {disk['pct_used']}% full, "
                                  f"{disk['avail_gb']:g} GB left"})
    return items


def build_gates(run_state):
    approved = {c.get("gate") for c in ((run_state or {}).get("checkpoints") or [])
                if isinstance(c, dict)}
    return [{"gate": g, "label": label, "approved": g in approved} for g, label in GATES]


def build_status(snapshot, work_dir=None):
    """Shape the poller cache into the JSON the dashboard consumes."""
    sources_out, stale, jobs = {}, [], []
    now = time.monotonic()
    for name, st in snapshot.items():
        sources_out[name] = {
            "fetched_at": st.fetched_at,
            "error": st.error,
            "age_s": (now - st.fetched_at) if st.fetched_at else None,
        }
        if st.error:
            stale.append(f"{name}: {st.error}")
    squeue = snapshot.get("squeue")
    rj = snapshot.get("run_jobs")
    run_jobs = (rj.data if rj is not None and rj.data else {}) or {}
    all_live = list(squeue.data) if squeue is not None and squeue.data else []
    # squeue and sacct are account-wide. Everything below is scoped to this run
    # directory: a live job by the directory it was submitted from, a finished
    # one by the log SLURM wrote into this run's logs/.
    if work_dir:
        live = [j for j in all_live if sources.belongs_to_run(j, work_dir)]
        other = [j for j in all_live if not sources.belongs_to_run(j, work_dir)]
    else:
        live, other = all_live, []
    hist = list(run_jobs.get("sacct") or [])
    # squeue forgets a job the moment it finishes, so sacct fills in the exit
    # states; a job present in both keeps its live squeue state.
    jobs = [dataclasses.asdict(j) if dataclasses.is_dataclass(j) else dict(j)
            for j in sources.merge_jobs(live, hist)]
    # Where sacct has no accounting data -- it returns nothing at all on some
    # clusters -- the log file is the only evidence the job ran. Say so rather
    # than inventing an outcome for it.
    known = {j["job_id"] for j in jobs}
    for row in run_jobs.get("logs") or []:
        if row["job_id"] in known:
            continue
        jobs.append({"job_id": row["job_id"], "name": row["name"], "state": None,
                     "elapsed": None, "partition": None, "nodelist": None,
                     "gpu": None, "workdir": None, "from_log": True})
    jobs.sort(key=lambda j: str(j["job_id"]))
    rs = snapshot.get("run_state")
    run_state = (rs.data if rs is not None and rs.data else {}) or {}
    vd = snapshot.get("validate")
    completion = (vd.data if vd is not None and vd.data else {}) or {}
    inv = snapshot.get("inventory")
    inventory = (inv.data if inv is not None and inv.data else []) or []
    tr = snapshot.get("training")
    training = (tr.data if tr is not None and tr.data else []) or []
    rc = snapshot.get("recon")
    recons = (rc.data if rc is not None and rc.data else []) or []
    rn = snapshot.get("runs")
    runs = (rn.data if rn is not None and rn.data else []) or []
    waiting = sorted(
        (str(p) for p, e in ((run_state.get("phases") or {}).items())
         if (e or {}).get("status") == "checkpoint"),
        key=_phase_sort_key,
    )
    ts_src = snapshot.get("tilt_series")
    excluded = set(run_state.get("excluded_tomostar") or [])
    tilt_series = []
    for item in ((ts_src.data if ts_src is not None and ts_src.data else []) or []):
        d = dataclasses.asdict(item) if dataclasses.is_dataclass(item) else dict(item)
        d["excluded"] = d.get("name") in excluded
        tilt_series.append(d)
    for row in inventory:
        row["excluded"] = row.get("name") in excluded
    # GPU accounting rolls up every job sacct remembers; elapsed strings and
    # gpu counts arrive per job, so the totals are computed here.
    gpu_seconds, n_done, n_failed, n_active = 0, 0, 0, 0
    for j in jobs:
        state = j.get("state") or ""
        if state in ("RUNNING", "PENDING"):
            n_active += 1
        elif state in ("FAILED", "TIMEOUT", "CANCELLED"):
            n_failed += 1
        elif state == "COMPLETED":
            n_done += 1
        gpus = j.get("gpu")
        elapsed_s = sources.parse_elapsed(j.get("elapsed"))
        if gpus and elapsed_s:
            gpu_seconds += gpus * elapsed_s
    accounting = {"gpu_hours": round(gpu_seconds / 3600.0, 1),
                  "n_done": n_done, "n_failed": n_failed, "n_active": n_active}
    gates = build_gates(run_state)
    attention = build_attention(snapshot, jobs, gates, waiting)
    ds = snapshot.get("disk")
    # The dose curve needs both halves: accumulated dose from tomostar
    # `_wrpDose` (mdoc ExposureDose is often 0) and CTF resolution from
    # WARP's frame cache. Copy before popping -- the snapshot hands out the
    # poller's own cached dicts.
    fr = snapshot.get("frames")
    frames = dict(fr.data) if fr is not None and fr.data else {}
    by_name = frames.pop("by_name", {})
    acq = snapshot.get("acquisition")
    dose = sources.join_dose(acq.data if acq is not None else None, by_name)
    if dose:
        frames["dose"] = dose
    return {
        "sources": sources_out,
        "attention": attention,
        "refinement": ((snapshot.get("refinement").data
                        if snapshot.get("refinement") is not None else None) or {}),
        "funnel": ((snapshot.get("funnel").data
                    if snapshot.get("funnel") is not None else None) or {}),
        "frames": frames,
        "tm_scores": ((snapshot.get("tm_scores").data
                       if snapshot.get("tm_scores") is not None else None) or {}),
        "alignment": ((snapshot.get("alignment").data
                       if snapshot.get("alignment") is not None else None) or {}),
        "disk": (ds.data if ds is not None and ds.data else None),
        "tilt_series": tilt_series,
        "stale": "; ".join(stale) or None,
        "jobs": jobs,
        "phases": build_phases(run_state, completion),
        "gates": gates,
        "waiting": waiting,
        "inventory": inventory,
        "recon": recons,
        "training": training,
        "runs": runs,
        "accounting": accounting,
        "other_jobs": [dataclasses.asdict(j) if dataclasses.is_dataclass(j)
                       else dict(j) for j in other],
    }


def origin_hosts_for_bind(bind):
    """Hostnames a browser Origin may use for POSTs.

    Loopback is always allowed. A specific `--bind` IP is allowed so a lab-LAN
    dashboard works; `0.0.0.0` is not a hostname and is not added -- binding
    every interface still has no auth, so Origin stays loopback-only in that
    case.
    """
    hosts = {"127.0.0.1", "localhost"}
    if bind and bind not in ("0.0.0.0", "::"):
        hosts.add(bind)
    return hosts


def make_handler(poller, editor, csrf_token, log_fetch=None, image_fetch=None,
                 render_qc=None, render_opts=None, thumb_fetch=None, run_label="",
                 runs_parent="", work_dir="", origin_hosts=None):
    allowed_origin_hosts = frozenset(origin_hosts or origin_hosts_for_bind("127.0.0.1"))
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):  # keep the terminal quiet
            pass

        def _send(self, code, body, ctype="application/json"):
            raw = body if isinstance(body, bytes) else body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            # A live dashboard must never be cached: a stale page survives a
            # server restart, and a re-rendered QC image reuses its path.
            self.send_header("Cache-Control", "no-store, must-revalidate")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            path = urlparse(self.path).path
            qs = parse_qs(urlparse(self.path).query)
            if path == "/":
                return self._send(200, PAGE.replace("__CSRF__", csrf_token)
                                  .replace("__RUNLABEL__", run_label)
                                  .replace("__RUNSPARENT__", jdump(runs_parent)),
                                  "text/html")
            if path == "/api/status":
                return self._send(200, jdump(
                    build_status(poller.snapshot(), work_dir=work_dir)))
            if path == "/api/config":
                keys = {
                    k: {"type": s.type, "min": s.minimum, "max": s.maximum,
                        "choices": list(s.choices), "description": s.description,
                        "file": s.file, "warning": s.warning, "stage": s.stage}
                    for k, s in config_edit.ALLOWLIST.items()
                }
                species = (qs.get("species") or [None])[0]
                # These four read different files. One missing conf must not
                # blank the other three -- a single try/except around all of
                # them reported "no dataset config available" for a dataset
                # config that read perfectly well, and said nothing about why.
                problems = []

                def attempt(fn, fallback):
                    try:
                        return fn()
                    except config_edit.ValidationError:
                        raise
                    except Exception as exc:  # cluster down, or a conf deleted
                        lines = [l for l in str(exc).splitlines() if l.strip()]
                        problems.append(lines[-1][:200] if lines else repr(exc))
                        return fallback

                try:
                    values = attempt(
                        lambda: editor.current_values(species=species), {})
                    dataset = attempt(editor.dataset_values, {})
                    available = attempt(editor.available_species, [])
                    advice = attempt(
                        lambda: editor.recommendations(species=species), {})
                except config_edit.ValidationError as exc:
                    return self._send(400, jdump({"error": str(exc)}))
                return self._send(200, jdump({
                    "keys": keys,
                    "problem": "; ".join(problems) or None,
                    "values": values,
                    "dataset": dataset,
                    "recommended": advice,
                    "file": getattr(editor, "species_conf_path", ""),
                    "files": {
                        "species": getattr(editor, "species_conf_path", ""),
                        "pipeline": getattr(editor, "pipeline_conf_path", ""),
                    },
                    "stage_order": config_edit.STAGE_ORDER,
                    "species_available": available,
                    "species_current": species or
                        getattr(editor, "species_conf_path", "").rsplit("/", 1)[-1],
                }))
            if path == "/api/qc":
                st = poller.snapshot().get("qc_images")
                imgs = (st.data if st is not None and st.data else []) or []
                ts = poller.snapshot().get("tilt_series")
                known = [getattr(x, "name", None) or (x or {}).get("name")
                         for x in ((ts.data if ts is not None and ts.data else []) or [])]
                entries = [sources.parse_qc_name(i, known) for i in imgs]
                return self._send(200, jdump({"images": imgs, "entries": entries}))
            if path == "/api/qc/image":
                rel = (qs.get("path") or [""])[0]
                st = poller.snapshot().get("qc_images")
                allowed = set((st.data if st is not None and st.data else []) or [])
                # The discovered listing IS the allowlist; a path from the
                # client is matched against it and never used to build a path.
                if rel not in allowed:
                    return self._send(403, jdump({"error": "not an available QC image"}))
                if image_fetch is None:
                    return self._send(404, jdump({"error": "images unavailable"}))
                try:
                    return self._send(200, image_fetch(rel), "image/png")
                except Exception as exc:
                    return self._send(404, jdump({"error": str(exc)[:150]}))
            if path == "/api/logs":
                if log_fetch is None:
                    return self._send(404, jdump({"error": "logs unavailable"}))
                text = log_fetch(qs.get("job", [""])[0],
                                 qs.get("stream", ["err"])[0],
                                 int(qs.get("lines", ["50"])[0]))
                return self._send(200, jdump({"text": text}))
            return self._send(404, jdump({"error": "not found"}))

        def do_POST(self):
            path = urlparse(self.path).path
            if path not in ("/api/config", "/api/species", "/api/qc/render",
                            "/api/qc/thumbs"):
                return self._send(404, jdump({"error": "not found"}))
            # A cross-origin page can SEND a POST but CORS stops it READING the
            # token, so it cannot forge this. Origin check also blocks DNS
            # rebinding; JSON-only forces a preflight.
            if self.headers.get("X-CSRF-Token") != csrf_token:
                return self._send(403, jdump({"error": "bad csrf token"}))
            origin = self.headers.get("Origin")
            if origin and urlparse(origin).hostname not in allowed_origin_hosts:
                return self._send(403, jdump({"error": "bad origin"}))
            if "application/json" not in (self.headers.get("Content-Type") or ""):
                return self._send(403, jdump({"error": "json required"}))

            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")

            if path == "/api/qc/thumbs":
                if thumb_fetch is None:
                    return self._send(404, jdump({"error": "thumbnails unavailable"}))
                st = poller.snapshot().get("qc_images")
                allowed = set((st.data if st is not None and st.data else []) or [])
                # Same allowlist rule as serving an image.
                wanted = [r for r in (payload.get("paths") or []) if r in allowed]
                try:
                    return self._send(200, jdump({"thumbs": thumb_fetch(wanted)}))
                except Exception as exc:
                    return self._send(500, jdump({"error": str(exc)[:200]}))

            if path == "/api/qc/render":
                if render_qc is None:
                    return self._send(404, jdump({"error": "rendering unavailable"}))
                tomo = str(payload.get("tomo") or "")
                st = poller.snapshot().get("tilt_series")
                known = {getattr(x, "name", None) or (x or {}).get("name")
                         for x in ((st.data if st is not None and st.data else []) or [])}
                # The discovered tilt-series list is the allowlist; the name is
                # never interpolated into a path or command unvalidated.
                if tomo not in known:
                    return self._send(400, jdump(
                        {"error": f"unknown tilt series: {tomo!r}"}))
                try:
                    opts = render_opts(payload) if render_opts else {}
                    made = render_qc(str(payload.get("kind") or "slices"), tomo,
                                     payload.get("species"), opts)
                except config_edit.ValidationError as exc:
                    return self._send(400, jdump({"error": str(exc)}))
                except Exception as exc:
                    return self._send(500, jdump({"error": str(exc)[:300]}))
                return self._send(200, jdump({"ok": True, "rendered": made}))

            if path == "/api/species":
                # Creating a NEW conf cannot disturb a running job -- nothing
                # sources it yet -- so the in-flight guard does not apply.
                try:
                    name = editor.create_species(payload.get("name"),
                                                 payload.get("values") or {},
                                                 payload.get("template"))
                except config_edit.ValidationError as exc:
                    return self._send(400, jdump({"error": str(exc)}))
                return self._send(200, jdump({"ok": True, "created": name}))

            # Editing a conf mid-phase can corrupt a running job. Advisory, not
            # absolute: changing a knob for the NEXT phase is legitimate, so an
            # explicit confirm overrides.
            sq = poller.snapshot().get("squeue")
            active = [j for j in (getattr(sq, "data", None) or [])
                      if getattr(j, "state", "") in ("RUNNING", "PENDING")]
            if active and not payload.get("confirm_running"):
                return self._send(409, jdump({
                    "error": f"{len(active)} job(s) still RUNNING/PENDING",
                    "hint": "resubmit with confirm_running=true to override",
                }))

            try:
                backup = editor.apply(payload.get("key"), payload.get("value"),
                                      species=payload.get("species"))
            except config_edit.ValidationError as exc:
                return self._send(400, jdump({"error": str(exc)}))
            return self._send(200, jdump({"ok": True, "backup": backup}))

    return Handler


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="OPUS-ET pipeline status dashboard")
    p.add_argument("--host", required=True, help="SSH host/alias of the cluster")
    p.add_argument("--work-dir", required=True, help="run directory on the cluster")
    p.add_argument("--species-conf", default="species.conf")
    p.add_argument(
        "--remote-init", default="",
        help="shell snippet run before validate.sh, for clusters where python3 "
             "is not on PATH in a non-interactive session, e.g. "
             "'source ~/.bashrc && conda activate <env>'")
    p.add_argument("--port", type=int, default=8080)
    # Never default to 0.0.0.0: there is no authentication.
    p.add_argument("--bind", default="127.0.0.1")
    p.add_argument("--runs-parent", default="",
                   help="directory whose subdirectories should be scanned for "
                        "other runs (those holding .opus_run_state.json); "
                        "enables the Runs tab")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    client = cluster.ClusterClient(args.host)
    wd = args.work_dir

    def remote_sh(command):
        """Wrap a shell command with the optional environment prelude."""
        if args.remote_init:
            return ["sh", "-c", f"{args.remote_init} && {command}"]
        return ["sh", "-c", command]

    validate_cmd = remote_sh(
        f"cd {shlex.quote(wd)} && bash opus-et-warp/validate.sh --json")

    def fetch_validate():
        res = client.run(validate_cmd, timeout=180)
        text = res.stdout.strip()
        if not text.startswith(("{", "[")):
            # validate.sh needs python3; on many clusters it is absent from a
            # non-interactive shell. Surface that rather than a JSON error.
            raise RuntimeError(
                "validate.sh produced no JSON "
                f"(rc={res.rc}): {(text or res.stderr).strip()[:120]} "
                "-- try --remote-init 'source ~/.bashrc && conda activate <env>'")
        return sources.phase_completion(json.loads(text))

    fetchers = {
        "squeue": lambda: sources.parse_squeue(client.run(sources.SQUEUE_ARGV).stdout),
        # Scoped to this run: the job ids in its logs/, and sacct for exactly
        # those. The account-wide sacct that used to be here reported every job
        # the user had ever run, from any directory.
        "run_jobs": lambda: sources.parse_run_jobs(
            client.run(sources.run_jobs_cmd(wd), timeout=60).stdout),
        "run_state": lambda: sources.read_run_state(
            client.read_file(f"{wd}/.opus_run_state.json")),
        "validate": fetch_validate,
        # Tilt-series metadata only changes when series are imported or
        # excluded, so it does not need frequent refreshing.
        "refinement": lambda: sources.parse_m(
            client.run(sources.m_cmd(wd), timeout=120).stdout),
        "funnel": lambda: sources.parse_funnel(
            client.run(sources.funnel_cmd(wd), timeout=180).stdout),
        # WARP's own quality cache for every movie and tilt series -- the same
        # numbers `WarpTools filter_quality --histograms` prints.
        "frames": lambda: sources.parse_frames(
            client.run(sources.frames_cmd(wd), timeout=60).stdout),
        # The template-matching scores exist in no STAR file, only in PyTOM's
        # particle XML; binned on the cluster so counts, not floats, cross.
        "tm_scores": lambda: sources.parse_tm_scores(
            client.run(sources.tm_scores_cmd(wd), timeout=180).stdout),
        # Acquisition order and dose from tomostar `_wrpDose`, with mdoc as
        # fallback when ExposureDose is actually filled in.
        "acquisition": lambda: sources.parse_acquisition(
            client.run(sources.acquisition_cmd(wd), timeout=60).stdout),
        "alignment": lambda: sources.parse_align(
            client.run(sources.align_cmd(wd), timeout=90).stdout),
        "disk": lambda: sources.parse_df(
            client.run(sources.disk_cmd(wd), timeout=30).stdout),
        "qc_images": lambda: sources.parse_qc_list(
            client.run(sources.qc_list_cmd(wd), timeout=60).stdout),
        "tilt_series": lambda: sources.parse_tilt_series(
            client.run(sources.tilt_series_cmd(f"{wd}/tomostar"), timeout=60).stdout),
        # Filesystem probes over ssh; slow-changing, so generous TTLs keep the
        # login node unhassled.
        "inventory": lambda: sources.parse_inventory(
            client.run(sources.inventory_cmd(wd), timeout=120).stdout),
        "recon": lambda: sources.parse_recon(
            client.run(sources.recon_cmd(wd), timeout=120).stdout),
        "training": lambda: sources.parse_training(
            client.run(sources.training_cmd(wd), timeout=120).stdout),
    }
    if args.runs_parent:
        fetchers["runs"] = lambda: sources.parse_runs(
            client.run(sources.runs_cmd(args.runs_parent), timeout=60).stdout)
    ttls = dict(poller_mod.DEFAULT_TTLS)
    ttls["tilt_series"] = 300.0
    ttls["qc_images"] = 300.0
    ttls["disk"] = 120.0
    ttls["funnel"] = 300.0
    ttls["frames"] = 300.0
    ttls["tm_scores"] = 600.0
    ttls["acquisition"] = 900.0
    ttls["alignment"] = 300.0
    ttls["refinement"] = 300.0
    ttls["inventory"] = 120.0
    ttls["recon"] = 300.0
    ttls["training"] = 120.0
    ttls["runs"] = 300.0
    p = poller_mod.Poller(fetchers, ttls)
    p.start()

    editor = config_edit.ConfigEditor(
        client,
        species_conf_path=f"{wd}/opus-et-warp/{args.species_conf}",
        pipeline_conf_path=f"{wd}/opus-et-warp/pipeline.conf",
        validate_cmd=validate_cmd,
    )

    def log_fetch(job, stream, lines):
        ext = "err" if stream == "err" else "out"
        res = client.run(["bash", "-c",
                          'tail -n "$1" "$2"/*_"$3".' + ext + ' 2>/dev/null || true',
                          "_", str(min(lines, 500)), f"{wd}/logs", job])
        return res.stdout[-64000:]

    def image_fetch(rel):
        return client.read_bytes(f"{wd}/{rel}")

    def thumb_fetch(rels):
        if not rels:
            return {}
        res = client.run(remote_sh(sources.thumb_script(wd)),
                         timeout=300, input="\n".join(rels) + "\n")
        return sources.parse_thumbs(res.stdout)

    _SAFE_NAME = __import__("re").compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

    RENDER_OPTS = {          # name: (kind, low, high)
        "n_slabs": (int, 1, 50), "slab_thickness": (int, 1, 500),
        "top_n": (int, 1, 100000),
        "z": (int, 0, 100000), "y": (int, 0, 100000),
    }

    def _render_opts(raw):
        """Only known numeric options, range-checked, so nothing arbitrary
        reaches the command line."""
        out = {}
        for key, (cast, lo, hi) in RENDER_OPTS.items():
            if raw.get(key) in (None, ""):
                continue
            try:
                v = cast(raw[key])
            except (TypeError, ValueError):
                raise config_edit.ValidationError(f"{key} must be a number")
            if not (lo <= v <= hi):
                raise config_edit.ValidationError(f"{key} must be {lo}-{hi}")
            out[key] = v
        proj = raw.get("project")
        if proj:
            if proj not in ("mean", "min", "max"):
                raise config_edit.ValidationError("project must be mean, min or max")
            out["project"] = proj
        return out

    def render_qc(kind, tomo, species=None, opts=None):
        """Run a deployed QC tool on the cluster for one tilt series.

        `tomo` has already been matched against the discovered tilt-series
        list by the handler; the pattern check below is a second guard so a
        name can never reach a shell glob with anything but plain characters.
        """
        if not _SAFE_NAME.match(tomo):
            raise config_edit.ValidationError(f"unsafe tilt series name: {tomo!r}")
        q = shlex.quote
        outdir, recon = f"{wd}/qc_ondemand", f"{wd}/warp_tiltseries/reconstruction"
        tools = f"{wd}/qc_tools"
        # Resolve the tomogram, whose name carries a pixel-size suffix.
        find_mrc = (f"m=$(ls -1 {q(recon)}/{tomo}_*Apx.mrc 2>/dev/null | head -1); "
                    f'[ -n "$m" ] || {{ echo "no reconstruction for {tomo}" >&2; exit 3; }}')

        if kind == "slices":
            # Put the depth in the filename, or a second render at another z
            # would overwrite the first.
            zsuf = f"_z{opts['z']}" if opts and "z" in opts else ""
            body = (f"mkdir -p {q(outdir)} && {find_mrc} && "
                    f'python3 {q(tools)}/slice_preview.py -o {q(outdir)}/{tomo}{zsuf} "$m"')
            for flag, key in (("--z", "z"), ("--y", "y")):
                if key in (opts or {}):
                    body += f" {flag} {q(str(opts[key]))}"
            expected = [f"qc_ondemand/{tomo}{zsuf}_xy.png",
                        f"qc_ondemand/{tomo}{zsuf}_xz.png"]
        elif kind == "overlay":
            spec_text = client.read_file(editor._species_path(species))
            cfg = sources.parse_config(spec_text)
            label = cfg["TM_LABEL"].value if "TM_LABEL" in cfg else ""
            if not _SAFE_NAME.match(label or ""):
                raise config_edit.ValidationError("species conf has no usable TM_LABEL")
            pipe = sources.parse_config(client.read_file(editor.pipeline_conf_path))
            angpix = (pipe.get("ANGPIX").value if "ANGPIX" in pipe else "")
            picks = f"{wd}/template_matching/{label}/warp_star/{tomo}_warp.star"
            body = (f"mkdir -p {q(outdir)} && {find_mrc} && "
                    f'python3 {q(tools)}/tm_picks_overlay.py --tomogram "$m" '
                    f"--picks {q(picks)} --tomo {q(tomo)} "
                    + (f"--coords-angpix {q(angpix)} " if angpix else "")
                    + f"-o {q(outdir)}/{label}_{tomo}")
            for flag, key in (("--n-slabs", "n_slabs"),
                              ("--slab-thickness", "slab_thickness"),
                              ("--top-n", "top_n"), ("--project", "project")):
                if key in (opts or {}):
                    body += f" {flag} {q(str(opts[key]))}"
            expected = [f"qc_ondemand/{label}_{tomo}"]
        else:
            raise config_edit.ValidationError(f"unknown render kind: {kind!r}")

        res = client.run(remote_sh(body), timeout=600)
        if res.rc != 0:
            raise RuntimeError(
                f"render failed (rc={res.rc}): {(res.stderr or res.stdout).strip()[:200]}")
        # New files must appear in the QC listing without waiting out its TTL.
        p.invalidate("qc_images")
        p.refresh_due()
        return expected

    token = secrets.token_urlsafe(32)
    # Several instances on different ports is the documented pattern, so the
    # page must say which host/run it is watching.
    label = html.escape(f"{args.host} \u00b7 {args.work_dir}", quote=False)
    httpd = ThreadingHTTPServer((args.bind, args.port),
                                make_handler(p, editor, token, log_fetch, image_fetch,
                                            render_qc, _render_opts, thumb_fetch,
                                            run_label=label,
                                            runs_parent=args.runs_parent,
                                            work_dir=wd,
                                            origin_hosts=origin_hosts_for_bind(args.bind)))
    print(f"OPUS-ET status on http://{args.bind}:{args.port}  (ctrl-c to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        p.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
