#!/usr/bin/env python3
"""Live progress screen for a film project (read-only).

    python3 studio/dashboard.py -p MUTAH-PILOT [--port 8765]

Serves http://127.0.0.1:<port>/ : an Arabic page that refreshes itself every 30 s.
It only READS project files (state, shot list, generation log, journal) and serves media under projects/<id>/media.
"""
import argparse
import csv
import json
import mimetypes
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "studio"))

STAGE_AR = {
    "research": "البحث التاريخي", "story": "المعالجة القصصية", "screenplay": "السيناريو",
    "visual_development": "تصميم الشخصيات", "storyboard": "قائمة اللقطات", "proof_of_concept": "إثبات المفهوم",
    "production_planning": "خطة الإنتاج والميزانية", "image_production": "صور اللقطات", "video_production": "فيديوهات اللقطات",
    "audio_production": "الصوت", "editing": "المونتاج", "final": "النسخة النهائية",
}
ACCEPTED = ("ACCEPTED", "ACCEPTED_BY_HUMAN", "ACCEPTED_BY_POLICY")


def read_csv(p):
    if not p.exists():
        return []
    with open(p, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_json(p, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa
        return default


def runner_alive(pid):
    r = subprocess.run(["pgrep", "-f", "studio.py film"], capture_output=True, text=True)
    return bool(r.stdout.strip())


def event_ar(e):
    ev, job = e.get("event"), e.get("job", "")
    t = e.get("ts", "")[11:16]
    tgt = job.split(":", 1)[-1] if job else e.get("target", "")
    if ev == "JOB_START":
        kind = job.split(":")[0]
        what = {"connector": "بيولّد", "review": "المراجع بيراجع", "pick": "المراجع بيختار الأحسن في",
                "prompt_revise": "بيعدّل وصف", "create": "بيكتب", "revise": "بيعدّل"}.get(kind, kind)
        return t, "%s %s" % (what, tgt.replace("keyframe:", "صورة ").replace("shot:", "فيديو ").replace(":1", ""))
    if ev == "JOB_END":
        v = e.get("verify", "")
        ok = "✅" if e.get("status") == "DONE" else "⚠️"
        v = (v.replace("REVISION_NEEDED", "محتاج إعادة").replace("ACCEPTED_BY_POLICY", "اتختار الأحسن")
             .replace("NEEDS_HUMAN_REVIEW", "محتاج نظرة").replace("ACCEPTED", "اتقبل").replace("SUCCESS", "نجح")
             .replace("FAILED", "فشل"))
        return t, "%s خلص: %s" % (ok, v[:90])
    if ev == "MILESTONE":
        return t, "🏁 اتعمدت مرحلة %s" % e.get("gate")
    if ev == "RUN_START":
        return t, "▶️ المنسّق اشتغل"
    if ev == "RUN_END":
        return t, "⏸ المنسّق وقف"
    if ev == "ENGINE" and e.get("argv", [""])[0] == "stage":
        return t, "📍 مرحلة: %s" % STAGE_AR.get(e["argv"][-1], e["argv"][-1])
    return None


def snapshot(pdir):
    st = load_json(pdir / "PROJECT_STATUS.json", {})
    orch = load_json(pdir / "orchestrator" / "state.json", {})
    shots = read_csv(pdir / "SHOT_LIST.csv")
    gens = read_csv(pdir / "GENERATION_LOG.csv")
    reviews = st.get("reviews", [])

    def latest(target):
        c = [r for r in reviews if r["target"] == target and r["set"] == "shot_media"]
        return c[-1] if c else None

    def last_out(sid, kind):
        rows = [g for g in gens if g["shot_id"] == sid and g["outcome"] == "SUCCESS" and g["output_path"]
                and (("image" in g["capability"]) == (kind == "img"))]
        return rows[-1]["output_path"] if rows else None

    def status_of(cyc, out):
        if cyc and cyc["status"] in ACCEPTED:
            return "ok", list(cyc["files"])[0]
        if cyc and cyc["status"] == "OPEN":
            return "review", out
        if cyc and cyc["status"] in ("REVISION_NEEDED", "NEEDS_HUMAN_REVIEW"):
            return "redo", out
        return ("made", out) if out else ("none", None)

    rows, counts = [], {"img_ok": 0, "img_made": 0, "vid_ok": 0, "vid_made": 0}
    for s in shots:
        sid = s["shot_id"]
        ki, kf = status_of(latest("keyframe:" + sid), last_out(sid, "img"))
        vi, vf = status_of(latest("shot:" + sid), last_out(sid, "vid"))
        counts["img_ok"] += ki == "ok"
        counts["img_made"] += kf is not None
        counts["vid_ok"] += vi == "ok"
        counts["vid_made"] += vf is not None
        rows.append({"id": sid, "desc": s.get("description", "")[:140], "dur": s.get("duration_s"),
                     "img": ki, "img_file": kf, "vid": vi, "vid_file": vf})
    journal = []
    jp = pdir / "orchestrator" / "journal.jsonl"
    if jp.exists():
        for line in jp.read_text(encoding="utf-8").splitlines()[-300:]:
            try:
                x = event_ar(json.loads(line))
            except ValueError:
                x = None
            if x:
                journal.append(x)
    gen_cost = sum(float(g.get("cost_usd") or 0) for g in gens)
    auths = st.get("authorizations", [])
    running = [k for k, j in orch.get("jobs", {}).items() if j.get("status") == "RUNNING"]
    waits = orch.get("item_wait", {})
    blocked = orch.get("blocked", {})
    cur_stage = next((n for n, v in st.get("stages", {}).items() if isinstance(v, dict) and v.get("state") == "IN_PROGRESS"), None)
    return {
        "title": st.get("title") or pdir.name, "alive": runner_alive(None), "status": orch.get("status"),
        "stage": STAGE_AR.get(cur_stage, cur_stage or "-"), "running": running,
        "counts": counts, "total": len(shots), "shots": rows, "journal": journal[-25:][::-1],
        "llm": round(float(orch.get("llm_cost_usd") or 0), 2), "gen": round(gen_cost, 2),
        "credits_used": int(round(gen_cost / 0.02)), "auth_total": round(sum(a["amount"] for a in auths), 2),
        "blocked": [{"t": k, "r": v.get("reason", "")[:160]} for k, v in blocked.items()],
        "waits": [{"t": k, "r": v.get("reason", "")[:160]} for k, v in waits.items()
                  if not any(r["id"] == k.split(":", 1)[-1] and (r["vid"] if k.startswith("shot:") else r["img"]) == "ok"
                             for r in rows)],
    }


PAGE = r"""<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>شاشة الإنتاج</title>
<style>
:root{--bg:#f6f4ef;--card:#fff;--ink:#1d1b18;--mut:#6b655c;--ok:#2f7d4f;--rev:#b07a12;--redo:#b5452f;--none:#c9c3b8;--line:#e5e0d6}
@media (prefers-color-scheme:dark){:root{--bg:#16140f;--card:#211e18;--ink:#efe9de;--mut:#a39b8d;--line:#332e25;--none:#4a443a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 system-ui,-apple-system,"Segoe UI",Tahoma,sans-serif}
.wrap{max-width:1200px;margin:0 auto;padding:16px}
h1{font-size:22px;margin:4px 0 2px}.sub{color:var(--mut);font-size:13px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin:14px 0}
.k{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px}
.k b{display:block;font-size:24px}.k span{color:var(--mut);font-size:13px}
.bar{height:8px;background:var(--line);border-radius:6px;overflow:hidden;margin-top:6px}.bar i{display:block;height:100%;background:var(--ok)}
.alert{background:#fde8e3;color:#7a2312;border-radius:10px;padding:10px 14px;margin:8px 0}
@media (prefers-color-scheme:dark){.alert{background:#3a1d16;color:#f3c4b8}}
.live{display:inline-block;width:9px;height:9px;border-radius:50%;margin-inline-start:6px}
.cols{display:grid;grid-template-columns:2fr 1fr;gap:14px}@media(max-width:860px){.cols{grid-template-columns:1fr}}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px}
.s{background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden;font-size:12px;cursor:pointer}
.s .th{aspect-ratio:16/9;background:var(--none);display:flex;align-items:center;justify-content:center;color:var(--mut)}
.s img{width:100%;height:100%;object-fit:cover;display:block}
.s .m{padding:6px 8px;display:flex;justify-content:space-between;gap:4px;align-items:center}
.tag{border-radius:6px;padding:0 6px;color:#fff;font-size:11px;white-space:nowrap}
.ok{background:var(--ok)}.review{background:var(--rev)}.redo{background:var(--redo)}.made{background:#5a6f8c}.none{background:var(--none);color:var(--ink)}
.log{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 12px;font-size:13px}
.log div{padding:4px 0;border-bottom:1px solid var(--line)}.log div:last-child{border:0}.log small{color:var(--mut);margin-inline-end:6px}
h2{font-size:16px;margin:16px 0 8px}
dialog{border:0;border-radius:12px;max-width:min(960px,96vw);padding:0;background:var(--card);color:var(--ink)}
dialog img,dialog video{width:100%;display:block}dialog p{padding:10px 14px;margin:0}
</style></head><body><div class="wrap">
<h1 id="t">…</h1><div class="sub" id="sub"></div>
<div id="alerts"></div>
<div class="kpis" id="kpis"></div>
<div class="cols"><div><h2>اللقطات</h2><div class="grid" id="grid"></div></div>
<div><h2>آخر اللي حصل</h2><div class="log" id="log"></div></div></div>
</div><dialog id="dlg" onclick="this.close()"></dialog>
<script>
const L={ok:"معتمدة",review:"بتتراجع",redo:"بتتعاد",made:"اتعملت",none:"لسه"};
function f(p){return "/media?f="+encodeURIComponent(p)}
async function load(){
 let d; try{d=await (await fetch("/api/status",{cache:"no-store"})).json()}catch(e){document.getElementById("sub").textContent="مش قادر أوصل للبيانات";return}
 document.getElementById("t").textContent="🎬 "+d.title;
 document.getElementById("sub").innerHTML=(d.alive?'<span class="live" style="background:#2f7d4f"></span> شغال':'<span class="live" style="background:#b5452f"></span> واقف')+" · المرحلة: "+d.stage+" · آخر تحديث "+new Date().toLocaleTimeString("ar-EG");
 const c=d.counts,T=d.total||1;
 const k=(v,l,p)=>`<div class="k"><b>${v}</b><span>${l}</span>${p!=null?`<div class="bar"><i style="width:${Math.round(p*100)}%"></i></div>`:""}</div>`;
 document.getElementById("kpis").innerHTML=
  k(`${c.img_ok} / ${d.total}`,"صور معتمدة",c.img_ok/T)+k(`${c.vid_ok} / ${d.total}`,"فيديوهات معتمدة",c.vid_ok/T)+
  k(`${d.credits_used}`,"كريدت فلو اتصرف (تقريبًا)",null)+k(`$${d.llm}`,"تكلفة تشغيل الذكاء الاصطناعي",null);
 const a=[...d.blocked.map(x=>"⛔ "+x.t+": "+x.r),...d.waits.map(x=>"⏳ "+x.t+": "+x.r)];
 document.getElementById("alerts").innerHTML=a.map(x=>`<div class="alert">${x}</div>`).join("");
 document.getElementById("grid").innerHTML=d.shots.map(s=>{
  const st=s.vid!=="none"?s.vid:s.img, kind=s.vid!=="none"?"فيديو":"صورة";
  const th=s.img_file?`<img loading="lazy" src="${f(s.img_file)}">`:"—";
  return `<div class="s" data-i='${JSON.stringify(s).replace(/'/g,"&#39;")}'><div class="th">${th}</div><div class="m"><span>${s.id}</span><span class="tag ${st}">${kind} ${L[st]}</span></div></div>`}).join("");
 document.querySelectorAll(".s").forEach(e=>e.onclick=()=>{const s=JSON.parse(e.dataset.i),dl=document.getElementById("dlg");
  dl.innerHTML=(s.vid_file?`<video src="${f(s.vid_file)}" controls autoplay></video>`:s.img_file?`<img src="${f(s.img_file)}">`:"")+`<p><b>${s.id}</b> · ${s.dur||""} ث<br>${s.desc}</p>`;dl.showModal()});
 document.getElementById("log").innerHTML=d.journal.map(j=>`<div><small>${j[0]}</small>${j[1]}</div>`).join("")||"—";
}
load();setInterval(load,30000);
</script></body></html>"""


def make_handler(pdir):
    media_root = (pdir / "media").resolve()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/":
                return self.send(200, PAGE.encode(), "text/html; charset=utf-8")
            if u.path == "/api/status":
                return self.send(200, json.dumps(snapshot(pdir), ensure_ascii=False).encode(), "application/json")
            if u.path == "/media":
                rel = unquote(u.query.partition("f=")[2])
                p = (pdir / rel).resolve()
                if media_root not in p.parents or not p.is_file():
                    return self.send(404, b"not found", "text/plain")
                return self.send(200, p.read_bytes(), mimetypes.guess_type(str(p))[0] or "application/octet-stream")
            self.send(404, b"not found", "text/plain")

    return H


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-p", "--project", required=True)
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    pdir = ROOT / "projects" / a.project
    if not pdir.exists():
        sys.exit("unknown project %s" % a.project)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(pdir))
    print("dashboard: http://127.0.0.1:%d/" % a.port, flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
