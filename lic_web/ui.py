"""HTML building blocks: layout, forms, badges. Every dynamic value goes through esc()."""
from __future__ import annotations

import hashlib
import html
from typing import Any, Dict, Iterable, List, Optional, Sequence

from . import __version__

CSS = """
:root{--bg:#f5f6f8;--card:#fff;--ink:#1c2430;--mute:#5d6b7c;--line:#dde2e9;--brand:#1f4e79;--brand-ink:#fff;
--ok:#1b7f4b;--ok-bg:#e4f5ec;--warn:#9a6700;--warn-bg:#fff4d6;--bad:#b42318;--bad-bg:#fde8e6;--info:#1f5fa8;--info-bg:#e6f0fb}
@media (prefers-color-scheme:dark){:root{--bg:#12161c;--card:#1b222b;--ink:#e6ebf1;--mute:#97a4b4;--line:#2d3743;--brand:#5aa0e0;--brand-ink:#08121d;
--ok:#4cc38a;--ok-bg:#12301f;--warn:#e3b341;--warn-bg:#352a0c;--bad:#ff7b72;--bad-bg:#3b1715;--info:#79b8ff;--info-bg:#112841}}
*{box-sizing:border-box}body{margin:0;font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--ink)}
a{color:var(--brand)}header{background:var(--brand);color:var(--brand-ink);display:flex;align-items:center;gap:24px;padding:0 20px;min-height:52px;flex-wrap:wrap}
header .logo{font-weight:700;letter-spacing:.2px;color:inherit;text-decoration:none}
nav{display:flex;gap:2px;flex-wrap:wrap;flex:1}nav a{color:inherit;text-decoration:none;padding:15px 12px;opacity:.85;border-bottom:3px solid transparent}
nav a:hover{opacity:1}nav a.on{opacity:1;border-bottom-color:currentColor;font-weight:600}
.user{font-size:13px;display:flex;gap:10px;align-items:center}.user form{display:inline}.user .chip{border-color:currentColor}.me{color:inherit}
.user button{background:transparent;border:1px solid currentColor;color:inherit;border-radius:6px;padding:3px 10px;cursor:pointer}
main{max-width:1180px;margin:0 auto;padding:22px 16px 60px}h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:0 0 10px}
.sub{color:var(--mute);margin:0 0 18px}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px;margin-bottom:16px}
.grid{display:grid;gap:16px}.g2{grid-template-columns:repeat(auto-fit,minmax(340px,1fr))}.row{display:flex;gap:12px;flex-wrap:wrap;align-items:flex-start}
label{display:block;font-weight:600;font-size:13px;margin-bottom:4px}.hint{color:var(--mute);font-size:12.5px;margin-top:3px;font-weight:400}
input[type=text],input[type=password],input[type=number],select,textarea{width:100%;padding:8px 10px;border:1px solid var(--line);border-radius:7px;background:var(--card);color:var(--ink);font:inherit}
textarea{min-height:84px;font-family:ui-monospace,Consolas,monospace;font-size:13px}input:focus,select:focus,textarea:focus{outline:2px solid var(--brand);outline-offset:0}
.f{margin-bottom:14px;min-width:200px;flex:1}.f.s{flex:0 0 150px;min-width:120px}.check{display:flex;gap:8px;align-items:flex-start;font-weight:500}.check input{margin-top:4px}
button,.btn{display:inline-block;background:var(--brand);color:var(--brand-ink);border:0;border-radius:7px;padding:9px 16px;font:inherit;font-weight:600;cursor:pointer;text-decoration:none}
button.sec,.btn.sec{background:transparent;color:var(--brand);border:1px solid var(--brand)}button.bad{background:var(--bad);color:#fff}button:disabled{opacity:.5;cursor:not-allowed}
button.sm,.btn.sm{padding:4px 10px;font-size:13px}table{width:100%;border-collapse:collapse;font-size:14px}th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:12px;text-transform:uppercase;letter-spacing:.4px;color:var(--mute)}tr:hover td{background:color-mix(in srgb,var(--brand) 5%,transparent)}.num{text-align:right;font-variant-numeric:tabular-nums}
.badge{display:inline-block;padding:1px 9px;border-radius:99px;font-size:12px;font-weight:700}.b-ok,.b-done,.b-pass{background:var(--ok-bg);color:var(--ok)}
.b-warn,.b-queued,.b-no_data{background:var(--warn-bg);color:var(--warn)}.b-bad,.b-fail,.b-failed,.b-error,.b-interrupted{background:var(--bad-bg);color:var(--bad)}
.b-running,.b-info{background:var(--info-bg);color:var(--info)}.b-cancelled,.b-off{background:var(--line);color:var(--mute)}
.alert{border-radius:8px;padding:11px 14px;margin-bottom:14px;border:1px solid}.a-ok{background:var(--ok-bg);color:var(--ok);border-color:var(--ok)}
.a-warn{background:var(--warn-bg);color:var(--warn);border-color:var(--warn)}.a-bad{background:var(--bad-bg);color:var(--bad);border-color:var(--bad)}.a-info{background:var(--info-bg);color:var(--info);border-color:var(--info)}
.alert ul{margin:6px 0 0 18px;padding:0}.bar{background:var(--line);border-radius:99px;height:12px;overflow:hidden}.bar>i{display:block;height:100%;background:var(--brand);width:0;transition:width .4s}
pre.log{background:#0e141b;color:#c9d5e3;border-radius:8px;padding:12px;max-height:280px;overflow:auto;font-size:12.5px;margin:0;white-space:pre-wrap}
.kv{display:grid;grid-template-columns:140px 1fr;gap:4px 12px}.kv dt{color:var(--mute)}.kv dd{margin:0}form.inl{display:inline}.row.center{align-items:center}.bar.m10{margin:10px 0}textarea.plain{min-height:0;font-family:inherit}pre.log.full{max-height:none}.hint.mb{margin-bottom:14px}.mute{color:var(--mute)}.right{margin-left:auto}
.login{max-width:380px;margin:12vh auto}.tabs{display:flex;gap:6px;margin-bottom:10px;flex-wrap:wrap}.chip{border:1px solid var(--line);border-radius:99px;padding:2px 10px;font-size:13px;display:inline-block}
.scope{display:none;margin-top:10px;max-height:260px;overflow:auto;border:1px solid var(--line);border-radius:8px;padding:10px}.scope.show{display:block}
.disc{margin-top:12px;border:1px dashed var(--brand);border-radius:8px;padding:12px;display:none}.disc.show{display:block}.small{font-size:13px}.nowrap{white-space:nowrap}
@media(max-width:640px){header{padding:6px 12px}nav a{padding:10px 8px}.kv{grid-template-columns:1fr}}
"""

JS = r"""
(function(){
"use strict";
var csrf=(document.querySelector('meta[name=csrf]')||{}).content||'';
function $(s,r){return (r||document).querySelector(s)}function $$(s,r){return Array.prototype.slice.call((r||document).querySelectorAll(s))}
function post(url,data){return fetch(url,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(data||{})}).then(function(r){return r.json()})}
function esc(s){var d=document.createElement('div');d.textContent=s==null?'':String(s);return d.innerHTML}

/* progress bars: width from data-pct (inline styles are blocked by the CSP) */
$$('[data-pct]').forEach(function(el){el.style.width=el.getAttribute('data-pct')+'%'});

/* confirm before destructive actions */
document.addEventListener('click',function(e){var b=e.target.closest&&e.target.closest('[data-confirm]');if(b&&!window.confirm('Are you sure? This cannot be undone.')){e.preventDefault();e.stopPropagation()}},true);

/* generate: scope selector */
var scope=$('#scope-mode');if(scope){var box=$('#scope-tenants');function sync(){box.classList.toggle('show',scope.value==='selected')}scope.addEventListener('change',sync);sync();
$$('[data-sel]').forEach(function(b){b.addEventListener('click',function(){var on=b.getAttribute('data-sel')==='all';$$('input[name=tenant]').forEach(function(c){if(!b.dataset.arc||c.dataset.arc===b.dataset.arc)c.checked=on})})})}

/* test DB connections */
$$('[data-test-arc]').forEach(function(b){b.addEventListener('click',function(){var arc=b.getAttribute('data-test-arc'),out=$('[data-test-out="'+arc+'"]');out.textContent='Testing...';b.disabled=true;
post('/settings/db/test',{arc:arc}).then(function(r){out.className='small '+(r.ok?'b-ok badge':'b-bad badge');out.textContent=r.message||'?'}).catch(function(){out.textContent='Request failed'}).then(function(){b.disabled=false})})});

/* run page: live progress */
var run=$('#run-live');if(run){var id=run.getAttribute('data-run'),fin=run.getAttribute('data-finished')==='1';
function tick(){fetch('/runs/'+id+'/status.json',{credentials:'same-origin'}).then(function(r){return r.json()}).then(function(s){
$('#bar').style.width=s.progress.percent+'%';$('#pct').textContent=s.progress.percent+'%';$('#stage').textContent=s.progress.message||s.status;
var lg=$('#log');lg.textContent=s.events.join('\n');lg.scrollTop=lg.scrollHeight;var b=$('#st');b.textContent=s.status;b.className='badge b-'+s.status;
if(['done','failed','cancelled','interrupted'].indexOf(s.status)>=0){location.reload()}else{setTimeout(tick,1500)}}).catch(function(){setTimeout(tick,4000)})}
if(!fin)tick()}

/* tenants: filter */
var q=$('#tfilter');if(q){q.addEventListener('input',function(){var v=q.value.toLowerCase();$$('#ttable tbody tr').forEach(function(tr){tr.style.display=tr.textContent.toLowerCase().indexOf(v)>=0?'':'none'})})}

/* tenant form: use-default vs custom type lists */
$$('[data-types]').forEach(function(sel){var ta=$('#'+sel.getAttribute('data-types'));function sync(){ta.disabled=sel.value==='default';ta.style.opacity=sel.value==='default'?.55:1}sel.addEventListener('change',sync);sync()});
var camp=$('#campaign_ids'),swc=$('#supervisors_without_campaign');if(camp&&swc){function cs(){var has=camp.value.trim()!=='';swc.disabled=has;if(has)swc.checked=false}camp.addEventListener('input',cs);cs()}
var sn=$('#sheet_name'),cnt=$('#sheet_count');if(sn&&cnt){function c(){cnt.textContent=sn.value.length+'/31';cnt.style.color=sn.value.length>31?'var(--bad)':''}sn.addEventListener('input',c);c()}

/* tenant form: discover from DB */
var db=$('#discover-btn');if(db){db.addEventListener('click',function(){var arc=$('#arc').value,cc=parseInt($('#contact_center_id').value,10);var out=$('#disc');
if(!cc&&cc!==0){out.className='disc show';out.textContent='Enter the contact center id first.';return}
out.className='disc show';out.textContent='Asking the database...';db.disabled=true;
post('/tenants/discover',{arc:arc,cc:cc}).then(function(r){db.disabled=false;if(r.error){out.innerHTML='<b class="b-bad badge">'+esc(r.error)+'</b>';return}
var h='<p><b>Contact center '+esc(r.contact_center_id)+':</b> '+(r.name?esc(r.name)+' <button type="button" class="sm sec" data-use-name="'+esc(r.name)+'">Use as project name</button>':'<span class="mute">no name found</span>')+'</p>';
if(r.errors&&r.errors.length)h+='<div class="alert a-warn small">'+r.errors.map(esc).join('<br>')+'</div>';
if(r.user_types.length){h+='<p class="small"><b>User types seen in the last 90 days</b> - pick which licence each one counts for:</p><table class="small"><thead><tr><th>Ameyo user type</th><th class="num">Sessions</th><th class="num">Users</th><th>Counted today as</th><th>For this tenant</th></tr></thead><tbody>';
r.user_types.forEach(function(t){var cur=t.bucket||'not counted';h+='<tr><td>'+esc(t.type)+'</td><td class="num">'+t.sessions+'</td><td class="num">'+t.users+'</td><td>'+esc(cur)+'</td><td><select data-ut="'+esc(t.type)+'"><option value="">(leave as is)</option><option value="agent">Agent</option><option value="supervisor">Supervisor</option><option value="wallboard">Wallboard</option><option value="none">Not counted</option></select></td></tr>'});
h+='</tbody></table><p><button type="button" class="sm" id="apply-types">Apply to the three lists below</button> <span class="hint">Choosing any type switches that tenant to its own custom lists.</span></p>'}
if(r.campaigns.length){h+='<p class="small"><b>Campaigns</b> (tick to limit this tenant to these campaigns; leave all unticked = whole contact center):</p><div class="small">'+r.campaigns.map(function(c){return '<label class="check"><input type="checkbox" data-camp="'+esc(c.id)+'"> '+esc(c.id)+(c.name?' - '+esc(c.name):'')+'</label>'}).join('')+'</div><p><button type="button" class="sm sec" id="apply-camp">Apply campaigns</button></p>'}
if(r.wallboard_users.length){h+='<p class="small"><b>Wallboard-like users:</b> '+r.wallboard_users.map(function(w){return '<button type="button" class="sm sec" data-wb="'+esc(w.user_id)+'">'+esc(w.user_id)+'</button>'}).join(' ')+'</p>'}
out.innerHTML=h;
$$('[data-use-name]',out).forEach(function(b){b.addEventListener('click',function(){$('#project_name').value=b.getAttribute('data-use-name')})});
$$('[data-wb]',out).forEach(function(b){b.addEventListener('click',function(){$('#wallboard_user_id').value=b.getAttribute('data-wb')})});
var ap=$('#apply-types',out);if(ap)ap.addEventListener('click',function(){var L={agent:[],supervisor:[],wallboard:[]};
r.user_types.forEach(function(t){var sel=$('select[data-ut="'+t.type.replace(/"/g,'\\"')+'"]',out),v=sel.value;if(v===''){if(t.bucket&&L[t.bucket])L[t.bucket].push(t.type)}else if(L[v])L[v].push(t.type)});
['agent','supervisor','wallboard'].forEach(function(k){$('#'+k+'_user_types').value=L[k].join('\n');var m=$('select[name='+k+'_mode]');m.value='custom';m.dispatchEvent(new Event('change'))})});
var ac=$('#apply-camp',out);if(ac)ac.addEventListener('click',function(){var ids=$$('[data-camp]',out).filter(function(c){return c.checked}).map(function(c){return c.getAttribute('data-camp')});$('#campaign_ids').value=ids.join(', ');$('#campaign_ids').dispatchEvent(new Event('input'))});
}).catch(function(){db.disabled=false;out.textContent='Request failed.'})})}
})();
"""

NAV = [("generate", "/generate", "Generate", "view"), ("runs", "/runs", "Reports", "view"), ("tenants", "/tenants", "Tenants", "view"),
       ("settings", "/settings", "Settings", "settings"), ("db", "/settings/db", "Databases", "db"), ("users", "/users", "Users", "users"),
       ("audit", "/audit", "Audit", "audit")]


ASSET_VER = hashlib.sha1((CSS + JS).encode("utf-8")).hexdigest()[:10]   # changes whenever the CSS/JS change -> no stale caches


def esc(v: Any) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def badge(text: str, kind: Optional[str] = None) -> str:
    return '<span class="badge b-%s">%s</span>' % (esc(kind or text), esc(text))


def alert(kind: str, body_html: str) -> str:
    return '<div class="alert a-%s">%s</div>' % (kind, body_html)


def errors_box(errors: Sequence[str], kind: str = "bad", title: str = "Please fix the following:") -> str:
    if not errors:
        return ""
    return alert(kind, "<b>%s</b><ul>%s</ul>" % (esc(title), "".join("<li>%s</li>" % esc(e) for e in errors)))


def csrf_field(token: str) -> str:
    return '<input type="hidden" name="_csrf" value="%s">' % esc(token)


def field(label: str, name: str, value: Any = "", *, hint: str = "", type_: str = "text", extra: str = "", cls: str = "", id_: Optional[str] = None) -> str:
    return '<div class="f %s"><label for="%s">%s</label><input type="%s" id="%s" name="%s" value="%s" %s>%s</div>' % (
        cls, esc(id_ or name), esc(label), type_, esc(id_ or name), esc(name), esc(value), extra, ('<div class="hint">%s</div>' % hint) if hint else "")


def select(label: str, name: str, options: Iterable[Any], current: Any, *, hint: str = "", cls: str = "", extra: str = "") -> str:
    opts = []
    for o in options:
        val, text = (o if isinstance(o, tuple) else (o, o))
        opts.append('<option value="%s"%s>%s</option>' % (esc(val), " selected" if str(val) == str(current) else "", esc(text)))
    return '<div class="f %s"><label for="%s">%s</label><select id="%s" name="%s" %s>%s</select>%s</div>' % (
        cls, esc(name), esc(label), esc(name), esc(name), extra, "".join(opts), ('<div class="hint">%s</div>' % hint) if hint else "")


def layout(title: str, body: str, *, user: Optional[Dict[str, Any]] = None, csrf: str = "", active: str = "", can=None, flash: Optional[Dict[str, str]] = None) -> str:
    nav = ""
    if user and can:
        nav = "".join('<a href="%s"%s>%s</a>' % (href, ' class="on"' if key == active else "", label)
                      for key, href, label, perm in NAV if can(user["role"], perm))
    who = ""
    if user:
        who = ('<div class="user"><a class="me" href="/account">%s</a> <span class="chip">%s</span>'
               '<form method="post" action="/logout">%s<button type="submit">Sign out</button></form></div>') % (esc(user["username"]), esc(user["role"]), csrf_field(csrf))
    fl = alert(flash["kind"], esc(flash["text"])) if flash else ""
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<meta name=\"csrf\" content=\"%s\"><meta name=\"robots\" content=\"noindex\"><title>%s - TMONE Licence Reports</title>"
            "<link rel=\"stylesheet\" href=\"/static/app.css?v=%s\"></head><body>"
            "<header><a class=\"logo\" href=\"/\">TMONE Licence Reports</a><nav>%s</nav>%s</header><main>%s%s</main>"
            "<script src=\"/static/app.js?v=%s\"></script></body></html>") % (esc(csrf), esc(title), ASSET_VER, nav, who, fl, body, ASSET_VER)


def table(headers: Sequence[str], rows: Sequence[Sequence[str]], *, table_id: str = "", empty: str = "Nothing here yet.") -> str:
    if not rows:
        return '<p class="mute">%s</p>' % esc(empty)
    head = "".join("<th%s>%s</th>" % (' class="num"' if h.startswith("#") else "", esc(h.lstrip("#"))) for h in headers)
    body = "".join("<tr>%s</tr>" % "".join("<td%s>%s</td>" % (' class="num"' if headers[i].startswith("#") else "", c) for i, c in enumerate(r)) for r in rows)
    return '<table%s><thead><tr>%s</tr></thead><tbody>%s</tbody></table>' % (' id="%s"' % esc(table_id) if table_id else "", head, body)


def footer_version() -> str:
    return __version__
