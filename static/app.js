const $=id=>document.getElementById(id);
const fmt=(v,d=2)=>v==null||Number.isNaN(Number(v))?"—":Number(v).toLocaleString("en-US",{maximumFractionDigits:d});
const money=v=>v==null?"—":"$"+fmt(v,2);
const pct=v=>v==null?"—":fmt(v,1)+"%";
const big=v=>{if(v==null)return"—";v=Number(v);if(Math.abs(v)>=1e12)return(v/1e12).toFixed(2)+"T";if(Math.abs(v)>=1e9)return(v/1e9).toFixed(2)+"B";return fmt(v,0)};
const esc=s=>String(s??"").replace(/[&<>"']/g,m=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"}[m]));
const kv=(l,v)=>`<div><span>${l}</span><b>${v==null?"—":v}</b></div>`;
const mini=(l,v)=>`<div class="mini"><b>${fmt(v,0)}</b><span>${l}</span></div>`;

document.querySelectorAll(".tab").forEach(btn=>btn.onclick=()=>{
  document.querySelectorAll(".tab").forEach(x=>x.classList.remove("active"));
  btn.classList.add("active");
  ["analyzeTab","scanTab","billionTab","methodTab"].forEach(x=>$(x).classList.add("hidden"));
  $(btn.dataset.tab+"Tab").classList.remove("hidden");
  if(btn.dataset.tab==="billion") loadBillionCompanies();
});
document.querySelectorAll("[data-ticker]").forEach(b=>b.onclick=()=>{$("ticker").value=b.dataset.ticker;analyze();});
$("analyzeBtn").onclick=()=>analyze();
$("ticker").addEventListener("keydown",e=>{if(e.key==="Enter"){e.preventDefault();analyze();}});
$("scanBtn").onclick=()=>scan();

function setStatus(t,show=true){$("status").textContent=t;$("status").classList.toggle("hidden",!show)}
async function getJSON(url){
  const r=await fetch(url,{headers:{"Accept":"application/json"}});
  const txt=await r.text(); let p;
  try{p=JSON.parse(txt)}catch{throw new Error(`الخادم أعاد ردًا غير متوقع (${r.status}).`)}
  if(!r.ok||!p.ok)throw new Error(p.error||`HTTP ${r.status}`);
  return p;
}
function actClass(s){return s==="BUY ZONE"?"buy":(s==="AVOID"||s==="DISTRIBUTION")?"avoid":"wait"}

async function analyze(){
  const t=$("ticker").value.trim().toUpperCase();
  if(!t)return setStatus("اكتب رمز السهم أولًا.");
  $("analyzeBtn").disabled=true; setStatus(`جاري تحليل ${t} أسبوعيًا...`);
  try{
    const q=new URLSearchParams({ticker:t,min_cap_b:$("minCap").value||10});
    const p=await getJSON(`/api/analyze?${q}`);
    renderAnalysis(p.data); setStatus("",false);
  }catch(e){setStatus(e.message)}
  finally{$("analyzeBtn").disabled=false}
}

function renderAnalysis(d){
  const s=d.stage||{},r=d.relative_strength||{},b=d.base||{},v=d.volume||{},m=d.market||{},t=d.timing||{},q=d.quote||{};
  const displayPrice=q.price ?? s.price;
  const quoteLabel=q.label_ar || "آخر إغلاق";
  $("content").innerHTML=`
  <section class="summary">
    <div class="card">
      <div class="top"><div><h2 class="symbol">${esc(d.ticker)}</h2><div class="company">${esc(d.company)}</div></div><div class="price"><b>${money(displayPrice)}</b><span>${esc(quoteLabel)} • إغلاق التحليل ${money(s.price)}</span><span>Market Cap ${big(d.market_cap)}</span></div></div>
      <div class="stage-hero stage${s.stage}">${esc(s.label)}</div>
      <div class="action ${actClass(t.status)}">${esc(t.label_ar)} <small>(${esc(t.status)})</small></div>
      <div class="tags"><span class="tag ${d.eligible?"good":"bad"}">${d.eligible?"داخل Large/Mega Universe":"خارج الكون المستهدف"}</span>${d.sector?`<span class="tag">${esc(d.sector)}</span>`:""}<span class="tag">${esc(d.source?.weekly_prices||"—")}</span></div>
      <div class="scoregrid">${mini("Stage",s.stage_score)}${mini("RS",r.score)}${mini("Base",b.score)}${mini("Volume",v.score)}${mini("30W Distance",d.distance_score)}${mini("Market",m.score)}</div>
    </div>
    <div class="card">
      <div class="score">${fmt(d.score,0)}</div><div class="grade">التقييم الأسبوعي / 100 — ${esc(d.grade)}</div>
      <div class="kv" style="margin-top:16px">${kv("Pivot",money(t.pivot))}${kv("بعده عن Pivot",pct(t.pivot_distance_pct))}${kv("Stop هيكلي",money(t.stop))}${kv("المخاطرة",pct(t.risk_pct))}${kv("30W MA",money(s.ma30))}${kv("بعده عن 30W",pct(s.distance_30w))}</div>
    </div>
  </section>

  <section class="sections">
    <div class="section"><h3>Stage Analysis</h3><div class="kv">${kv("10W MA",money(s.ma10))}${kv("30W MA",money(s.ma30))}${kv("ميل 30W - 5W",pct(s.ma30_slope5))}${kv("ميل 30W - 10W",pct(s.ma30_slope10))}${kv("52W High",money(s.high52))}${kv("من قمة 52W",pct(s.drawdown_52h))}${kv("Distribution 8W",fmt(s.distribution_weeks_8,0))}</div><div style="margin-top:12px">${(s.reasons||[]).map(x=>`<div class="reason">${esc(x)}</div>`).join("")}</div></div>
    <div class="section"><h3>Relative Strength vs SPY</h3><div class="kv">${kv("RS Score",fmt(r.score,0))}${kv("تفوق موزون",pct(r.weighted_edge))}${kv("13W vs SPY",pct(r.relative_vs_spy?.["13"]))}${kv("26W vs SPY",pct(r.relative_vs_spy?.["26"]))}${kv("52W vs SPY",pct(r.relative_vs_spy?.["52"]))}${kv("السهم 52W",pct(r.stock_returns?.["52"]))}</div><p class="muted">${esc(r.note||"")}</p></div>
  </section>

  <section class="sections">
    <div class="section"><h3>Weekly Base</h3><div class="kv">${kv("Range 20W",pct(b.range20))}${kv("Range 12W",pct(b.range12))}${kv("Tightness 4W",pct(b.tight4))}${kv("Higher Lows",b.higher_lows?"نعم":"لا")}${kv("Upper-half closes",pct(b.upper_half_ratio))}${kv("Base Low",money(b.base_low))}</div></div>
    <div class="section"><h3>Volume</h3><div class="kv">${kv("الأسبوع / متوسط 10W",v.current_vs_10w==null?"—":fmt(v.current_vs_10w,2)+"x")}${kv("Up/Down Volume 8W",v.up_down_ratio_8w==null?"—":fmt(v.up_down_ratio_8w,2)+"x")}${kv("Breakout",v.breakout?"نعم":"لا")}</div><p class="muted">نفضّل فوليوم قوي عند الاختراق وفوليوم أهدأ أثناء الـPullback.</p></div>
  </section>

  <section class="market"><div class="market-head"><h3>Market Regime</h3><span class="pill">${esc(m.label||"—")} — ${fmt(m.score,0)}/100</span></div><div class="kv">${kv("SPY فوق 30W",m.spy?.price>m.spy?.ma30?"نعم":"لا")}${kv("SPY 30W صاعد",m.spy?.slope5>0?"نعم":"لا")}${kv("QQQ فوق 30W",m.qqq?.price>m.qqq?.ma30?"نعم":"لا")}${kv("QQQ 30W صاعد",m.qqq?.slope5>0?"نعم":"لا")}</div></section>

  ${(d.warnings||[]).length?`<section class="section" style="margin-top:15px"><h3>تنبيهات</h3><ul class="notes warn">${d.warnings.map(x=>`<li>${esc(x)}</li>`).join("")}</ul></section>`:""}
  `;
  window.scrollTo({top:$("content").offsetTop-10,behavior:"smooth"});
}

async function scan(){
  $("scanBtn").disabled=true; setStatus("جاري مسح Large/Mega Caps وتحليل المرشحين أسبوعيًا...");
  try{
    const q=new URLSearchParams({min_cap_b:$("scanCap").value||10,min_score:$("minScore").value||70,max_names:$("maxNames").value||40});
    const p=await getJSON(`/api/scan?${q}`); renderScan(p.data); setStatus("",false);
  }catch(e){setStatus(e.message)}
  finally{$("scanBtn").disabled=false}
}

function card(x){
  return `<article class="scan-card" onclick="openTicker('${esc(x.ticker)}')"><div class="scan-top"><div><b style="font:900 23px Arial">${esc(x.ticker)}</b><div class="company">${esc(x.company)}</div></div><div class="scan-score">${fmt(x.score,0)}</div></div><div class="tags"><span class="tag ${x.stage===2?"good":""}">${esc(x.stage_label)}</span><span class="tag">${esc(x.status_ar)}</span><span class="tag">${big(x.market_cap)}</span></div><div class="scan-metrics"><div><span>RS</span><b>${fmt(x.rs_score,0)}</b></div><div><span>Base</span><b>${fmt(x.base_score,0)}</b></div><div><span>Volume</span><b>${fmt(x.volume_score,0)}</b></div><div><span>30W dist</span><b>${pct(x.distance_30w)}</b></div><div><span>30W slope</span><b>${pct(x.ma30_slope5)}</b></div><div><span>Pivot</span><b>${money(x.pivot)}</b></div><div><span>Pivot dist</span><b>${pct(x.pivot_distance_pct)}</b></div><div><span>Risk</span><b>${pct(x.risk_pct)}</b></div></div></article>`;
}
function renderScan(d){
  $("content").innerHTML=`<div class="scan-head"><div><h2>ماسح واينستين الأسبوعي</h2><div class="muted">${esc(d.note)}</div></div><span class="pill">${esc(d.market?.label||"—")} — ${fmt(d.market?.score,0)}/100</span></div><div class="muted" style="margin-bottom:12px">US Stock Universe: ${d.finviz_universe_count} | فوق $${fmt(d.min_cap_b,0)}B: ${d.eligible_large_mega_count} | تم تحليل: ${d.analyzed_count} | Stage 2 مؤهلة: ${d.qualified_count} | ${d.scan_seconds}s</div><h3>Stage 2 المؤهلة</h3><section class="scan-grid">${(d.qualified||[]).map(card).join("")||'<div class="status">لا توجد Stage 2 مطابقة الآن.</div>'}</section>${(d.watch||[]).length?`<h3 style="margin-top:24px">قائمة المراقبة</h3><section class="scan-grid">${d.watch.map(card).join("")}</section>`:""}`;
}
function openTicker(t){document.querySelector('[data-tab="analyze"]').click();$("ticker").value=t;analyze();}


let billionCompaniesCache=null;

async function loadBillionCompanies(){
  if(billionCompaniesCache){
    renderBillionCompanies(billionCompaniesCache);
    return;
  }
  $("billionDirectory").innerHTML='<div class="status">جاري تحميل كل الشركات $10B+ بدون اختصار...</div>';
  try{
    const p=await getJSON("/api/billion-companies?min_cap_b=10");
    billionCompaniesCache=p.data;
    renderBillionCompanies(p.data);
  }catch(e){
    $("billionDirectory").innerHTML=`<div class="status">${esc(e.message)}</div>`;
  }
}

function renderBillionCompanies(d){
  const list=d.companies||[];
  $("billionSummary").innerHTML=`
    <div class="dir-stat"><b>${fmt(d.count,0)}</b><span>شركة $10B+</span></div>
    <div class="dir-stat"><b>${Object.keys(d.sectors||{}).length}</b><span>قطاع/تصنيف</span></div>
    <div class="dir-stat wide"><b>${esc(d.source||"Nasdaq")}</b><span>المصدر</span></div>
  `;
  renderBillionRows(list);
}

function renderBillionRows(list){
  if(!list.length){
    $("billionDirectory").innerHTML='<div class="status">لا توجد نتائج مطابقة للبحث.</div>';
    return;
  }
  $("billionDirectory").innerHTML=`
    <div class="directory-count">المعروض الآن: ${fmt(list.length,0)} شركة</div>
    <div class="directory-table">
      <div class="directory-row directory-head">
        <span>الرمز</span><span>الشركة</span><span>القطاع</span><span>الصناعة</span><span>القيمة السوقية</span><span>سعر المصدر</span>
      </div>
      ${list.map(x=>`
        <button class="directory-row directory-company" onclick="openBillionTicker('${esc(x.ticker)}')">
          <span class="dir-ticker">${esc(x.ticker)}</span>
          <span>${esc(x.company)}</span>
          <span>${esc(x.sector||"—")}</span>
          <span>${esc(x.industry||"—")}</span>
          <span>${big(x.market_cap)}</span>
          <span>${money(x.last_price)}</span>
        </button>
      `).join("")}
    </div>
  `;
}

$("billionSearch").addEventListener("input",e=>{
  if(!billionCompaniesCache)return;
  const q=e.target.value.trim().toLowerCase();
  if(!q){
    renderBillionRows(billionCompaniesCache.companies||[]);
    return;
  }
  const filtered=(billionCompaniesCache.companies||[]).filter(x=>
    [x.ticker,x.company,x.sector,x.industry].some(v=>String(v||"").toLowerCase().includes(q))
  );
  renderBillionRows(filtered);
});

function openBillionTicker(t){
  document.querySelector('[data-tab="analyze"]').click();
  $("ticker").value=t;
  $("minCap").value="10";
  analyze();
}
