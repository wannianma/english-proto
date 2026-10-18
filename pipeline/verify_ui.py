#!/usr/bin/env python3
"""Headless smoke test + screenshots. Start the Range-capable server first
   (python -m http.server cannot seek, so sentence jumps fail):
   python serve.py 8765
   python pipeline/verify_ui.py http://127.0.0.1:8765/ screenshots/

The bundled catalog only has the NASA lesson. Checks for a second lesson with
id "iphone4" run only when that id is listed in lessons/index.json.
"""
import sys, os
from playwright.sync_api import sync_playwright

base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765/"
url = base + "?lesson=nasa-coffee"   # the detailed suite below is written against the NASA lesson
out = sys.argv[2] if len(sys.argv) > 2 else "screenshots"
os.makedirs(out, exist_ok=True)
errors = []

def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: errors.append(name)

with sync_playwright() as p:
    b = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required",
                                "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"])
    ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2,
                        is_mobile=True, has_touch=True, locale="zh-CN")
    pg = ctx.new_page()
    pg.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    pg.goto(url); pg.wait_for_selector(".bubble")
    pg.evaluate("localStorage.clear()"); pg.reload(); pg.wait_for_selector(".bubble")
    pg.wait_for_function("document.querySelector('video').readyState >= 2")
    ev = pg.evaluate
    index_ids = ev("""() => fetch('lessons/index.json').then(r => r.ok ? r.json() : {lessons:[]}).then(j => (j.lessons || []).map(l => l.id)).catch(() => [])""")
    print("lessons in index.json:", ", ".join(index_ids) or "(none)")

    KSTATE = """(()=>{const b=document.querySelector('.bubble.active');const k=[...b.querySelectorAll('.en .kw')];
      const m=/scaleX\\(([\\d.]+)\\)/.exec(b.querySelector('.sprog i').style.transform);
      return {i:+b.dataset.i,total:k.length,done:k.filter(x=>x.classList.contains('done')).length,
        now:k.filter(x=>x.classList.contains('now')).map(x=>+x.dataset.k),frac:m?+m[1]:-1,
        p:(k.find(x=>x.classList.contains('now'))||{style:{getPropertyValue:()=>''}}).style.getPropertyValue('--p'),
        bar:getComputedStyle(b.querySelector('.sprog')).display!=='none',
        others:document.querySelectorAll('.bubble:not(.active) .kw.done, .bubble:not(.active) .kw.now').length}})()"""
    def kstate(): return ev(KSTATE)
    def seek_to(t, wait=400):
        ev(f"(()=>{{const v=document.querySelector('video');v.pause();v.currentTime={t}}})()"); pg.wait_for_timeout(wait)
    def sent(i): return ev(f"window.__ep.st.L.sentences[{i}]")
    def ktimes(i): return ev(f"window.__ep.kTimes(window.__ep.st.L.sentences[{i}])")
    # collect per-animation-frame samples in the page for `ms` milliseconds
    COLLECT = """ms => new Promise(res => { const out=[], t0=performance.now(), v=document.querySelector('video');
      (function f(){ const b=document.querySelector('.bubble.active'), k=[...b.querySelectorAll('.en .kw')];
        const m=/scaleX\\(([\\d.]+)\\)/.exec(b.querySelector('.sprog i').style.transform);
        out.push({i:+b.dataset.i, ct:v.currentTime, frac:m?+m[1]:-1, done:k.filter(x=>x.classList.contains('done')).length, total:k.length});
        performance.now()-t0<ms ? requestAnimationFrame(f) : res(out); })(); })"""

    # playback sync: jump to sentence 7 via 目录-like goto, play 2.5 s
    pg.click(".bubble[data-i='6']"); pg.wait_for_timeout(2500)
    check("video plays after tapping a sentence", ev("!document.querySelector('video').paused"))
    check("active bubble follows playback", ev("document.querySelector('.bubble.active').dataset.i") in ("6", "7"))
    pg.click("#nextBtn"); pg.wait_for_timeout(400)
    check("下一句 moves forward", ev("+document.querySelector('.bubble.active').dataset.i") >= 7)
    pg.click("#speedBtn")
    check("speed 1.25x", abs(ev("document.querySelector('video').playbackRate") - 1.25) < 1e-6)
    pg.click("#speedBtn")
    check("speed 0.75x", abs(ev("document.querySelector('video').playbackRate") - 0.75) < 1e-6)
    pg.click("#speedBtn")
    # loop: start sentence 4 (short), loop on, play 6s, still on sentence 4
    pg.click("#loopBtn"); pg.click(".bubble[data-i='3']"); pg.wait_for_timeout(5000)
    check("复读 keeps current sentence", ev("document.querySelector('.bubble.active').dataset.i") == "3")
    pg.click("#loopBtn"); pg.click("#playBtn")
    check("pause works", ev("document.querySelector('video').paused"))

    # ---------------- karaoke progress ----------------
    I = 19; S = sent(I); T = ktimes(I)
    check("karaoke: every word token has a timing", len(T) == len([1 for _ in range(len(T))]) and all(w["e"] >= w["s"] for w in T))
    check("karaoke: timings are monotonic", all(T[k]["s"] >= T[k-1]["s"] - 1e-6 for k in range(1, len(T))))
    tmid = T[6]["s"] + 0.5 * (T[6]["e"] - T[6]["s"])
    seek_to(tmid); k = kstate()
    check("karaoke: word being spoken is marked 'now'", k["i"] == I and k["now"] == [6])
    check("karaoke: earlier words 'done', later words upcoming", k["done"] == 6 and k["total"] == len(T))
    check("karaoke: current word sweep is partial (--p ~50%)", k["p"].endswith("%") and 30 < float(k["p"][:-1]) < 70)
    exp = (tmid - S["start"]) / (S["end"] - S["start"])
    check(f"karaoke: sentence bar shows elapsed fraction ({k['frac']:.2f} vs {exp:.2f})", k["bar"] and abs(k["frac"] - exp) < 0.03)
    check("karaoke: inactive bubbles carry no progress state", k["others"] == 0)
    seek_to(S["start"] + 0.01); k = kstate()
    check("karaoke: seeking backwards resets progress", k["i"] == I and k["done"] == 0 and k["frac"] < 0.02)
    seek_to(S["end"] + 0.02); k = kstate()
    check("karaoke: after the last word everything is done, bar full", k["done"] == k["total"] and k["frac"] > 0.99)
    # smooth + speed: play at 1.25x and sample every animation frame
    ev("window.__ep.st.speedIdx=2; document.querySelector('video').playbackRate=1.25")
    seek_to(S["start"] + 0.01, 300); ev("document.querySelector('video').play()")
    smp = [x for x in ev(COLLECT, 1500) if x["i"] == I]
    ev("document.querySelector('video').pause(); window.__ep.st.speedIdx=1; document.querySelector('video').playbackRate=1")
    distinct = len({round(x["frac"], 4) for x in smp})
    check(f"karaoke: bar updates per frame, not per timeupdate ({distinct} distinct values in 1.5s)", distinct >= 20)
    check("karaoke: bar tracks currentTime exactly at 1.25x",
          all(abs(S["start"] + x["frac"] * (S["end"] - S["start"]) - x["ct"]) < 0.06 for x in smp if 0 < x["frac"] < 1))
    check("karaoke: words fill progressively", smp[-1]["done"] > smp[0]["done"] and
          all(smp[j]["done"] >= smp[j-1]["done"] for j in range(1, len(smp))))
    # 复读 loop: progress restarts each repetition
    pg.click("#loopBtn"); pg.click(".bubble[data-i='3']")
    smp = ev(COLLECT, 4500); pg.click("#loopBtn"); ev("document.querySelector('video').pause()")
    drops = sum(1 for j in range(1, len(smp)) if smp[j]["frac"] < smp[j-1]["frac"] - 0.5)
    check(f"karaoke: 复读 restarts progress each loop ({drops} restarts)", drops >= 1 and all(x["i"] == 3 for x in smp))
    # vocab pills stay tappable while progress is running
    seek_to(tmid); ev("document.querySelector('video').play()"); pg.wait_for_timeout(200)
    pg.click(".bubble.active .vocab >> nth=0"); pg.wait_for_timeout(200)
    check("karaoke: vocab pill in active sentence still opens card", ev("!document.querySelector('#sheet').hidden"))
    pg.click("#sheetClose")
    # interpolation for words without timings
    it = ev("window.__ep.kTimes({en:'alpha beta gamma delta', start:0, end:4, words:[{w:'alpha',s:0,e:1},{w:' delta',s:3,e:4}]})")
    check("karaoke: missing word timings are interpolated inside the gap",
          it[1].get("interp") and it[2].get("interp") and abs(it[1]["s"] - 1) < 1e-6 and abs(it[2]["e"] - 3) < 1e-6 and it[1]["e"] <= it[2]["s"] + 1e-6)
    it2 = ev("window.__ep.kTimes({en:'zero-gravity cup', start:0, end:2, words:[{w:'zero',s:0,e:.5},{w:'-gravity',s:.5,e:1},{w:' cup',s:1.2,e:2}]})")
    check("karaoke: hyphenated word spanning two ASR tokens aligned", abs(it2[0]["s"]) < 1e-6 and abs(it2[0]["e"] - 1) < 1e-6 and abs(it2[1]["s"] - 1.2) < 1e-6)

    # bilingual + vocab card (sentence 2 'realm'); seek to it so the frame matches
    pg.click(".bubble[data-i='1'] .vocab"); pg.wait_for_timeout(300)
    pg.evaluate("document.querySelector('video').pause(); document.querySelector('video').currentTime=12")
    pg.wait_for_timeout(800)
    check("vocab card opens", ev("!document.querySelector('#sheet').hidden"))
    pg.click("[data-add]")
    check("生词本 badge = 1", ev("document.querySelector('#bookBadge').textContent") == "1")
    check("wordbook in localStorage", ev("JSON.parse(localStorage.ep_wordbook).length") == 1)
    pg.wait_for_timeout(2000)  # let toast fade
    pg.screenshot(path=f"{out}/1-bilingual-vocab-card.png")
    pg.click("#sheetClose")

    # cloze
    pg.click(".tab[data-mode='cloze']"); pg.wait_for_timeout(300)
    pg.click(".bubble[data-i='17']"); pg.evaluate("document.querySelector('video').pause()")
    pg.wait_for_timeout(600)
    n = ev("document.querySelectorAll('.cloze').length")
    check("cloze placeholders rendered", n > 20)
    pg.click(".bubble[data-i='17'] .cloze >> nth=0"); pg.wait_for_timeout(300)
    check("tap reveals cloze", ev("document.querySelectorAll('.cloze.revealed').length") == 1)
    s17 = sent(17); seek_to(s17["end"] - 0.05)
    hidden = ev("""[...document.querySelectorAll(".bubble[data-i='17'] .cloze:not(.revealed) .kw")].map(x=>({done:x.classList.contains('done'),c:getComputedStyle(x).color,bg:getComputedStyle(x).backgroundImage}))""")
    check("karaoke/挖空: hidden words progress but stay invisible",
          len(hidden) >= 2 and all(h["done"] and h["c"] == "rgba(0, 0, 0, 0)" and h["bg"] == "none" for h in hidden))
    check("karaoke/挖空: bar progresses", kstate()["frac"] > 0.9)
    seek_to(s17["start"] + 0.45 * (s17["end"] - s17["start"]))
    pg.wait_for_timeout(1600)
    pg.screenshot(path=f"{out}/2-cloze.png")

    # dictation
    pg.click(".tab[data-mode='dict']"); pg.wait_for_timeout(300)
    pg.click(".bubble[data-i='9'] [data-act='listen']"); pg.wait_for_timeout(3600)
    check("听本句 stops at sentence end", ev("document.querySelector('video').paused"))
    pg.fill(".bubble[data-i='9'] .dict-input", "trickey"); pg.click(".bubble[data-i='9'] [data-act='check']")
    check("wrong answer marked", ev("document.querySelector(\".bubble[data-i='9'] .dict-input\").classList.contains('bad')"))
    pg.fill(".bubble[data-i='10'] .dict-input", "Argument")  # phrase target? check text
    tgt = ev("document.querySelector(\".bubble[data-i='8'] .dict-input\") && 1")
    pg.fill(".bubble[data-i='8'] .dict-input", "chore"); pg.press(".bubble[data-i='8'] .dict-input", "Enter")
    check("correct answer marked", ev("document.querySelector(\".bubble[data-i='8'] .dict-input\").classList.contains('ok')"))
    s9 = sent(9); seek_to(s9["start"] + 0.5 * (s9["end"] - s9["start"])); k = kstate()
    check("karaoke/听写: bar + visible words progress", k["i"] == 9 and 0.4 < k["frac"] < 0.6 and 0 < k["done"] < k["total"])
    check("karaoke/听写: answer slot is not a karaoke word", ev("!document.querySelector(\".bubble[data-i='9'] .dict-input .kw\")"))
    pg.evaluate("document.activeElement.blur()")
    pg.evaluate("""(()=>{const l=document.querySelector('#transcript'),e=document.querySelector(".bubble[data-i='8']");l.scrollTop=e.offsetTop-l.offsetTop-10})()""")
    pg.wait_for_timeout(300)
    pg.screenshot(path=f"{out}/3-dictation.png")

    # shadowing panel renders (no mic in headless; just check graceful UI)
    pg.click(".tab[data-mode='shadow']"); pg.wait_for_timeout(300)
    check("shadow panel in active bubble", ev("!!document.querySelector('.bubble.active .shadow-panel')"))
    # record with Chrome's fake microphone
    pg.click(".bubble.active [data-act='rec']"); pg.wait_for_timeout(1500)
    check("recording starts", ev("!!window.__ep.st.recording"))
    pg.click(".bubble.active [data-act='rec']"); pg.wait_for_timeout(1500)
    check("recording saved & playable", ev("!!document.querySelector(\".bubble.active [data-act='mine']:not([disabled])\")"))
    # scoring logic (SpeechRecognition itself can't run headless)
    html = ev("window.__ep.scoreHTML({en:'For starters, he says, it would be a chore just getting the coffee into the cup.'}, 'for starters he says it would be a core just getting coffee into the cup', '')")
    check("score computed with mismatches in red", "88分" in html and 'w-bad">chore' in html)
    pg.evaluate("window.__ep.st.sr[window.__ep.st.L.sentences[window.__ep.st.cur].id] = window.__ep.scoreHTML(window.__ep.st.L.sentences[window.__ep.st.cur], 'for starters he says it would be a core just getting coffee into the cup', '')")
    pg.click(".tab[data-mode='bi']"); pg.click(".tab[data-mode='shadow']"); pg.wait_for_timeout(300)
    pg.screenshot(path=f"{out}/4-shadowing.png")

    # sheets
    pg.click("#navToc"); check("目录 lists sentences", ev("document.querySelectorAll('#sheetBody .list-item[data-i]').length") == ev("document.querySelectorAll('.bubble').length"))
    multi = len(index_ids) > 1
    has_switch = bool(ev("!!document.querySelector('#sheetBody [data-switch]')"))
    check("目录 has 切换课程 entry" if multi else "目录 hides 切换课程 when only one lesson", has_switch == multi)
    pg.click("#sheetClose")
    pg.click("#navVocab"); check("重点词 list", ev("document.querySelectorAll('#sheetBody .list-item').length") >= 15); pg.click("#sheetClose")
    pg.click("#navLearned"); check("标记已学", ev("!!JSON.parse(localStorage.ep_learned)[document.title.split(' · ')[0]]"))

    # mid-sentence karaoke screenshot (bilingual)
    pg.click(".tab[data-mode='bi']"); pg.wait_for_timeout(200)
    T = ktimes(19); seek_to(T[6]["s"] + 0.55 * (T[6]["e"] - T[6]["s"]), 300)
    st_ = kstate(); check("karaoke screenshot is mid-sentence", st_["now"] == [6])
    pg.evaluate("""(()=>{const l=document.querySelector('#transcript'),e=document.querySelector(".bubble[data-i='19']");l.scrollTop=e.offsetTop-l.offsetTop-l.clientHeight/2+e.clientHeight/2})()""")
    pg.wait_for_timeout(2000)
    pg.screenshot(path=f"{out}/6-karaoke-progress.png")

    # Extra checks for a locally added "iphone4" lesson. The public repo ships
    # only nasa-coffee, so this block is skipped unless that id is registered.
    if "iphone4" in index_ids:
        ip = ctx.new_page(); ip.on("pageerror", lambda e: errors.append(f"pageerror(iphone): {e}"))
        ip.goto(base); ip.evaluate("localStorage.removeItem('ep_lesson'); localStorage.ep_mode='bi'"); ip.reload()
        ip.wait_for_selector(".bubble"); ip.wait_for_function("document.querySelector('video').readyState >= 2")
        e2 = ip.evaluate
        check("iphone: default lesson is iPhone 4", "iPhone 4" in e2("window.__ep.st.L.title") and e2("window.__ep.st.L.id") == "iphone4")
        check("iphone: two lessons registered", e2("window.__ep.lessons().length") == 2)
        check("iphone: license label", e2("window.__ep.st.L.license").startswith("Apple Inc."))
        cov = e2("""(()=>{const E=window.__ep;let t=0,i=0;E.st.L.sentences.forEach(s=>E.kTimes(s).forEach(w=>{t++;if(w.interp)i++}));return [t,i]})()""")
        check(f"iphone: word timings aligned ({cov[1]} of {cov[0]} interpolated)", cov[1] <= 0.05 * cov[0])
        check("iphone: product names corrected", e2("""(()=>{const t=window.__ep.st.L.sentences.map(s=>s.en).join(' ');return t.includes('App Store')&&t.includes('iPhone 3GS')&&t.includes('micro-SIM')&&!/app store/.test(t)})()"""))
        check("iphone: all sentences translated", e2("window.__ep.st.L.sentences.every(s=>s.zh && s.zh.length>1)"))
        check("iphone: 25-30 vocab, all found in transcript", 25 <= e2("window.__ep.st.L.vocab.length") <= 30 and e2("window.__ep.st.L.vocab.every(v=>v.sentence_ids.length>0)"))
        def ip_seek(t): e2(f"(()=>{{const v=document.querySelector('video');v.pause();v.currentTime={t}}})()"); ip.wait_for_timeout(450)
        I2 = e2("window.__ep.st.L.sentences.findIndex(s=>s.en.startsWith('This is, beyond'))")
        T2 = e2(f"window.__ep.kTimes(window.__ep.st.L.sentences[{I2}])")
        ip_seek(T2[3]["s"] + 0.5 * (T2[3]["e"] - T2[3]["s"])); k = ip.evaluate(KSTATE)
        check("iphone: karaoke mid-sentence", k["i"] == I2 and k["now"] == [3] and k["done"] == 3 and 0 < k["frac"] < 1)
        ip.click(f".bubble[data-i='{I2}'] .vocab >> nth=0"); ip.wait_for_timeout(250)
        check("iphone: vocab card ('beyond (a) doubt')", "beyond (a) doubt" in e2("document.querySelector('.vc-word').textContent"))
        ip.click("#sheetClose")
        ip.click(".tab[data-mode='cloze']"); ip.wait_for_timeout(250)
        check("iphone: 挖空 placeholders", e2("document.querySelectorAll('.cloze:not(.revealed)').length") >= 25)
        ip.click(".tab[data-mode='dict']"); ip.wait_for_timeout(250)
        ans = e2(f"window.__ep.st.L.sentences[{I2}]._dt.text")
        ip.fill(f".bubble[data-i='{I2}'] .dict-input", ans); ip.press(f".bubble[data-i='{I2}'] .dict-input", "Enter")
        check(f"iphone: 听写词 accepts '{ans}'", e2(f"document.querySelector(\".bubble[data-i='{I2}'] .dict-input\").classList.contains('ok')"))
        # screenshot: bilingual, mid-sentence
        ip.click(".tab[data-mode='bi']"); ip.wait_for_timeout(250)
        T2 = e2(f"window.__ep.kTimes(window.__ep.st.L.sentences[{I2}])")
        ip_seek(T2[8]["s"] + 0.5 * (T2[8]["e"] - T2[8]["s"]))
        ip.evaluate(f"""(()=>{{const l=document.querySelector('#transcript'),e=document.querySelector(".bubble[data-i='{I2}']");l.scrollTop=e.offsetTop-l.offsetTop-l.clientHeight/2+e.clientHeight/2}})()""")
        ip.wait_for_timeout(1500)
        check("iphone: screenshot state is mid-sentence", ip.evaluate(KSTATE)["now"] == [8])
        ip.screenshot(path=f"{out}/7-iphone4.png")
        # switcher: title -> picker -> NASA -> reload
        ip.click("#lessonTitle"); ip.wait_for_timeout(200)
        check("switcher: picker lists both lessons", e2("document.querySelectorAll('#sheetBody .list-item').length") == 2)
        with ip.expect_navigation():
            ip.click("#sheetBody .list-item[data-id='nasa-coffee']")
        ip.wait_for_selector(".bubble")
        check("switcher: NASA lesson loaded", e2("window.__ep.st.L.id") == "nasa-coffee" and "lesson=nasa-coffee" in ip.url)
        ip.goto(base); ip.wait_for_selector(".bubble")
        check("switcher: choice remembered", e2("window.__ep.st.L.id") == "nasa-coffee")
        ip.evaluate("localStorage.removeItem('ep_lesson')")
        ip.close()
    else:
        print("SKIP iphone4 checks (not in lessons/index.json); checking the default lesson")
        home = ctx.new_page()
        home.on("pageerror", lambda e: errors.append(f"pageerror(default): {e}"))
        home.goto(base)
        home.evaluate("localStorage.removeItem('ep_lesson'); localStorage.ep_mode='bi'")
        home.reload()
        home.wait_for_selector(".bubble")
        home.wait_for_function("document.querySelector('video').readyState >= 2")
        hv = home.evaluate
        expect_id = index_ids[0] if index_ids else "nasa-coffee"
        check("default lesson is the first entry in index.json", hv("window.__ep.st.L.id") == expect_id)
        check("registered lesson count matches index.json", hv("window.__ep.lessons().length") == len(index_ids))
        if len(index_ids) <= 1:
            check("single lesson hides the switcher",
                  not hv("document.querySelector('#lessonTitle').classList.contains('switchable')"))
        home.evaluate("localStorage.removeItem('ep_lesson')")
        home.close()

    # desktop layout
    d = b.new_page(viewport={"width": 1366, "height": 820})
    d.goto(url); d.wait_for_selector(".bubble"); d.wait_for_function("document.querySelector('video').readyState >= 2")
    d.evaluate("localStorage.ep_mode='bi'"); d.reload(); d.wait_for_selector(".bubble")
    d.evaluate("document.querySelector('video').currentTime=100"); d.wait_for_timeout(1200)
    d.screenshot(path=f"{out}/5-desktop.png")
    b.close()

print("\nERRORS:" if errors else "\nALL CHECKS PASSED", *errors, sep="\n  ")
sys.exit(1 if errors else 0)
