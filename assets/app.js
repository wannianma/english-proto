/* 看视频学英语 prototype - plain JS, no build step */
(() => {
'use strict';
const $ = (s, el = document) => el.querySelector(s);
const video = $('#video'), list = $('#transcript');
const LS = { mode: 'ep_mode', book: 'ep_wordbook', learned: 'ep_learned', speed: 'ep_speed' };
const SPEEDS = [0.75, 1, 1.25];

const st = {
  L: null, cur: 0, mode: localStorage.getItem(LS.mode) || 'bi',
  loop: false, speedIdx: +(localStorage.getItem(LS.speed) ?? 1), stopAt: null,
  userScrollUntil: 0, revealed: new Set(), dict: {}, recs: {}, sr: {}, recording: null,
  book: JSON.parse(localStorage.getItem(LS.book) || '[]'),
  learned: JSON.parse(localStorage.getItem(LS.learned) || '{}'),
};
if (!(st.speedIdx in SPEEDS)) st.speedIdx = 1;

/* ---------- helpers ---------- */
const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmt = t => { t = Math.max(0, t || 0); return Math.floor(t / 60) + ':' + String(Math.floor(t % 60)).padStart(2, '0'); };
const norm = w => w.toLowerCase().replace(/[’]/g, "'").replace(/^[^a-z0-9]+|[^a-z0-9]+$/g, '');
let toastT;
function toast(msg, ms = 1800) { const t = $('#toast'); t.textContent = msg; t.hidden = false; clearTimeout(toastT); toastT = setTimeout(() => t.hidden = true, ms); }

function tokenize(text) {
  const out = [], re = /[A-Za-z0-9][A-Za-z0-9'’\-]*/g; let last = 0, m;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push({ t: text.slice(last, m.index), w: false });
    out.push({ t: m[0], w: true }); last = re.lastIndex;
  }
  if (last < text.length) out.push({ t: text.slice(last), w: false });
  return out;
}
function forms(word) {
  const w = word.toLowerCase(), f = new Set([w, w + 's', w + 'es', w + 'ed', w + 'd', w + 'ing', w + 'ly', w + "'s"]);
  if (w.endsWith('e')) f.add(w.slice(0, -1) + 'ing');
  if (w.endsWith('y')) { f.add(w.slice(0, -1) + 'ies'); f.add(w.slice(0, -1) + 'ied'); }
  return f;
}
/* find vocab occurrences in a sentence: returns {tokIndex: {end, vi}} */
function matchVocab(s, toks) {
  const wordIdx = toks.map((t, i) => t.w ? i : -1).filter(i => i >= 0);
  const found = [];
  st.L.vocab.forEach((v, vi) => {
    if (!v.sentence_ids.includes(s.id)) return;
    for (const term of (v.match && v.match.length ? v.match : [v.word])) {
      const parts = term.toLowerCase().split(/\s+/), n = parts.length;
      for (let k = 0; k + n <= wordIdx.length; k++) {
        const win = wordIdx.slice(k, k + n).map(i => toks[i].t.toLowerCase().replace(/’/g, "'"));
        if (win.slice(0, -1).join(' ') === parts.slice(0, -1).join(' ') && forms(parts[n - 1]).has(win[n - 1]))
          found.push({ start: wordIdx[k], end: wordIdx[k + n - 1], vi, len: n });
      }
    }
  });
  found.sort((a, b) => a.start - b.start || b.len - a.len);
  const res = {}; let taken = -1;
  for (const f of found) if (f.start > taken) { res[f.start] = f; taken = f.end; }
  return res;
}
/* key word to dictate for a sentence */
function dictTarget(s) {
  if (s._dt !== undefined) return s._dt;
  const toks = tokenize(s.en), m = matchVocab(s, toks);
  let best = Object.values(m).sort((a, b) => a.len - b.len)[0];
  if (!best) {
    let bl = 0;
    toks.forEach((t, i) => {
      if (!t.w || /\d/.test(t.t)) return;
      const proper = /^[A-Z]/.test(t.t) && i > 0;
      if (!proper && t.t.length >= 5 && t.t.length > bl) { bl = t.t.length; best = { start: i, end: i }; }
    });
  }
  s._dt = best ? { start: best.start, end: best.end, text: toks.slice(best.start, best.end + 1).map(x => x.t).join('') } : null;
  return s._dt;
}

/* ---------- speech synthesis ---------- */
let voices = [];
function loadVoices() { voices = window.speechSynthesis ? speechSynthesis.getVoices() : []; }
if (window.speechSynthesis) { loadVoices(); speechSynthesis.onvoiceschanged = loadVoices; }
function speak(text, lang = 'en-US', rate = 0.9) {
  if (!window.speechSynthesis) return toast('当前浏览器不支持语音朗读');
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.lang = lang; u.rate = rate;
  const v = voices.find(v => v.lang === lang) || voices.find(v => v.lang.replace('_', '-').startsWith(lang));
  if (v) u.voice = v; else if (lang === 'en-GB' && voices.length) toast('未找到英式发音，使用默认英语语音');
  speechSynthesis.speak(u);
}

/* ---------- rendering ---------- */
function renderEn(s, mode) {
  const toks = tokenize(s.en), m = matchVocab(s, toks);
  const dt = mode === 'dict' ? dictTarget(s) : null;
  const kIdx = []; let kc = 0; toks.forEach((t, i) => { if (t.w) kIdx[i] = kc++; });
  const wrap = i => toks[i].w ? `<span class="kw" data-k="${kIdx[i]}">${esc(toks[i].t)}</span>` : esc(toks[i].t);
  let html = '';
  for (let i = 0; i < toks.length; i++) {
    if (dt && i === dt.start) {
      const d = st.dict[s.id] || {};
      const w = Math.max(dt.text.length + 1, 4);
      html += `<input class="dict-input ${d.status || ''}" data-sid="${s.id}" style="width:${w + 1}ch" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="${'_'.repeat(Math.min(w, 8))}" value="${esc(d.value || '')}">`;
      i = dt.end; continue;
    }
    const f = m[i];
    if (f) {
      const text = toks.slice(i, f.end + 1).map((_, j) => wrap(i + j)).join('');
      if (mode === 'cloze') {
        const key = s.id + ':' + i, rv = st.revealed.has(key);
        html += `<span class="cloze ${rv ? 'revealed' : ''}" data-key="${key}" data-vi="${f.vi}">${text}</span>`;
      } else html += `<span class="vocab" data-vi="${f.vi}">${text}</span>`;
      i = f.end; continue;
    }
    html += wrap(i);
  }
  return html;
}
function bubbleHTML(s, i) {
  const mode = st.mode;
  let extra = '';
  if (mode === 'dict') {
    const d = st.dict[s.id] || {}, dt = dictTarget(s);
    extra = dt ? `<div class="dict-tools">
      <button class="chip" data-act="listen">🔊 听本句</button>
      <button class="chip primary" data-act="check">检查</button>
      <button class="chip" data-act="reveal">看答案</button>
      <span class="answer ${d.status || ''}">${d.msg || ''}</span></div>` : '';
  }
  return `<div class="bubble${i === st.cur ? ' active' : ''}" data-i="${i}">
    <div class="meta">${s.id} · ${fmt(s.start)}</div>
    <div class="en">${renderEn(s, mode)}</div>
    <div class="zh">${esc(s.zh || '')}</div>${extra}
    <div class="extra"></div>
    <div class="sprog" aria-hidden="true"><i></i></div></div>`;
}
function renderList() {
  list.className = 'transcript mode-' + st.mode;
  list.innerHTML = st.L.sentences.map(bubbleHTML).join('');
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.mode === st.mode));
  renderShadowPanel();
  scrollToCur(true);
}
function bubbleEl(i) { return list.children[i]; }

function setCurrent(i, opts = {}) {
  if (i === st.cur && !opts.force) return;
  const prev = bubbleEl(st.cur); if (prev) prev.classList.remove('active');
  st.cur = i;
  const el = bubbleEl(i); if (el) el.classList.add('active');
  renderShadowPanel();
  scrollToCur(opts.instant);
}
function scrollToCur(instant) {
  const el = bubbleEl(st.cur); if (!el) return;
  if (!instant && Date.now() < st.userScrollUntil) return;
  const top = el.offsetTop - list.offsetTop - list.clientHeight / 2 + el.clientHeight / 2;
  list.scrollTo({ top: Math.max(0, top), behavior: instant ? 'auto' : 'smooth' });
}
['wheel', 'touchmove'].forEach(ev => list.addEventListener(ev, () => st.userScrollUntil = Date.now() + 4000, { passive: true }));

/* ---------- karaoke progress ---------- */
/* Per-word times for the word tokens of a sentence (same order as tokenize(s.en).filter(w)).
   Aligned to the ASR word timings in s.words; words without a timing are interpolated
   between their timed neighbours (or the sentence edges), proportional to word length. */
function kTimes(s) {
  if (s._kt) return s._kt;
  const nz = x => x.toLowerCase().replace(/[^a-z0-9]/g, '');
  const toks = tokenize(s.en).filter(t => t.w);
  const W = (s.words || []).map(w => ({ n: nz(w.w || ''), s: +w.s, e: +w.e })).filter(w => w.n && isFinite(w.s) && isFinite(w.e));
  const out = toks.map(() => null);
  let p = 0, off = 0;
  toks.forEach((tk, i) => {
    const tn = nz(tk.t); if (!tn) return;
    // try to start at whisper word q (q === p continues inside the current word);
    // a token may also span several whisper words (e.g. "Zero-Gravity" -> "Zero" + "-Gravity")
    for (let q = p; q < Math.min(p + 4, W.length); q++) {
      let at = q === p ? off : 0, rem = tn, k = q, start = null, end = null;
      while (rem && k < W.length) {
        const w = W[k], chunk = w.n.slice(at), L = w.n.length, d = Math.max(0, w.e - w.s);
        if (!chunk) { k++; at = 0; continue; }
        if (chunk.startsWith(rem)) {            // token ends inside this word
          if (start == null) start = w.s + d * at / L;
          at += rem.length; end = w.s + d * at / L; rem = '';
        } else if (rem.startsWith(chunk)) {     // token continues into next word
          if (start == null) start = w.s + d * at / L;
          rem = rem.slice(chunk.length); end = w.e; k++; at = 0;
        } else break;
      }
      if (rem) continue;
      out[i] = { s: start, e: end };
      if (k < W.length && at >= W[k].n.length) { p = k + 1; off = 0; } else { p = k; off = at; }
      break;
    }
  });
  for (let i = 0; i < out.length;) {
    if (out[i]) { i++; continue; }
    let j = i; while (j < out.length && !out[j]) j++;
    const a = i > 0 ? out[i - 1].e : s.start, b = Math.max(a, j < out.length ? out[j].s : s.end);
    const lens = toks.slice(i, j).map(t => Math.max(1, nz(t.t).length)), tot = lens.reduce((x, y) => x + y, 0);
    let c = a;
    for (let k = i; k < j; k++) { const d = (b - a) * lens[k - i] / tot; out[k] = { s: c, e: c + d, interp: true }; c += d; }
    i = j;
  }
  return (s._kt = out);
}
const kara = { el: null, spans: [], ks: [], state: [], bar: null };
function updateKaraoke(t) {
  const el = bubbleEl(st.cur); if (!el) return;
  const s = st.L.sentences[st.cur];
  if (kara.el !== el) {
    if (kara.el && kara.el.isConnected) {
      kara.spans.forEach(sp => { sp.classList.remove('done', 'now'); sp.style.removeProperty('--p'); });
      if (kara.bar) kara.bar.style.transform = 'scaleX(0)';
    }
    kara.el = el; kara.spans = [...el.querySelectorAll('.en .kw')];
    kara.ks = kara.spans.map(sp => +sp.dataset.k); kara.state = kara.spans.map(() => '');
    kara.bar = el.querySelector('.sprog i');
  }
  const T = kTimes(s);
  const frac = Math.min(1, Math.max(0, (t - s.start) / Math.max(0.01, s.end - s.start)));
  if (kara.bar) kara.bar.style.transform = `scaleX(${frac.toFixed(4)})`;
  for (let j = 0; j < kara.spans.length; j++) {
    const sp = kara.spans[j], w = T[kara.ks[j]]; if (!w) continue;
    const c = t >= w.e ? 'done' : t >= w.s ? 'now' : '';
    if (c !== kara.state[j]) {
      sp.classList.toggle('done', c === 'done'); sp.classList.toggle('now', c === 'now');
      if (c !== 'now') sp.style.removeProperty('--p');
      kara.state[j] = c;
    }
    if (c === 'now') sp.style.setProperty('--p', (100 * (t - w.s) / Math.max(0.01, w.e - w.s)).toFixed(1) + '%');
  }
}

/* ---------- playback ---------- */
function safePlay() { const p = video.play(); if (p && p.catch) p.catch(() => {}); }
function indexAt(t) {
  const S = st.L.sentences; let i = 0;
  for (let k = 0; k < S.length; k++) { if (S[k].start <= t + 0.05) i = k; else break; }
  return i;
}
function tick() {
  if (st.L) {
    const t = video.currentTime, c = st.L.sentences[st.cur];
    if (st.stopAt != null && t >= st.stopAt) { video.pause(); st.stopAt = null; }
    if (st.loop && !video.paused && t >= c.end && t < c.end + 0.8) { video.currentTime = c.start; }
    else { const i = indexAt(t); if (i !== st.cur) setCurrent(i); }
    if (!seeking) { $('#seek').value = t; $('#tCur').textContent = fmt(t); }
    updateKaraoke(video.currentTime);
  }
  requestAnimationFrame(tick);
}
function goto(i, play) {
  const S = st.L.sentences; i = Math.max(0, Math.min(S.length - 1, i));
  st.stopAt = null;
  video.currentTime = S[i].start + 0.01;
  setCurrent(i, { force: true, instant: false });
  st.userScrollUntil = 0; scrollToCur();
  if (play) safePlay();
}
function playSentence(i) {
  const s = st.L.sentences[i];
  goto(i, false);
  st.stopAt = st.loop ? null : s.end + 0.15;
  safePlay();
}
function togglePlay() { st.stopAt = null; video.paused ? safePlay() : video.pause(); }
function syncPlayBtn() {
  const p = video.paused;
  $('#playIco').textContent = p ? '▶' : '❚❚'; $('#playTxt').textContent = p ? '播放' : '暂停';
  $('#bigPlay').classList.toggle('hide', !p);
}
video.addEventListener('play', syncPlayBtn); video.addEventListener('pause', syncPlayBtn);
video.addEventListener('click', togglePlay);
video.addEventListener('loadedmetadata', () => {
  $('#seek').max = video.duration; $('#tDur').textContent = fmt(video.duration);
  if (!video.seekable.length || video.seekable.end(0) < 1)
    toast('当前服务器不支持 Range 请求，视频无法跳转。请用 python serve.py 启动。', 6000);
});
video.addEventListener('error', () => toast('视频加载失败：请用本地服务器打开（不要直接双击 index.html）', 5000));
let seeking = false;
$('#seek').addEventListener('input', e => { seeking = true; $('#tCur').textContent = fmt(+e.target.value); });
$('#seek').addEventListener('change', e => { seeking = false; st.stopAt = null; video.currentTime = +e.target.value; st.userScrollUntil = 0; });

$('#playBtn').onclick = togglePlay;
$('#prevBtn').onclick = () => goto(st.cur - 1, !video.paused);
$('#nextBtn').onclick = () => goto(st.cur + 1, !video.paused);
$('#loopBtn').onclick = () => {
  st.loop = !st.loop; $('#loopBtn').classList.toggle('on', st.loop);
  if (st.loop) st.stopAt = null;
  toast(st.loop ? '复读当前句：开' : '复读：关');
};
function applySpeed() { video.defaultPlaybackRate = SPEEDS[st.speedIdx]; video.playbackRate = SPEEDS[st.speedIdx]; $('#speedVal').textContent = SPEEDS[st.speedIdx].toFixed(2).replace(/0$/, '') + 'x'; }
$('#speedBtn').onclick = () => { st.speedIdx = (st.speedIdx + 1) % SPEEDS.length; localStorage.setItem(LS.speed, st.speedIdx); applySpeed(); toast('播放速度 ' + SPEEDS[st.speedIdx] + 'x'); };

/* ---------- tabs ---------- */
$('#tabs').addEventListener('click', e => {
  const b = e.target.closest('.tab'); if (!b) return;
  st.mode = b.dataset.mode; localStorage.setItem(LS.mode, st.mode);
  if (st.mode === 'dict' || st.mode === 'shadow') { video.pause(); }
  renderList();
});

/* ---------- transcript interactions ---------- */
list.addEventListener('click', e => {
  const t = e.target;
  const vEl = t.closest('.vocab');
  if (vEl) { e.stopPropagation(); return openCard(+vEl.dataset.vi); }
  const cz = t.closest('.cloze');
  if (cz) {
    e.stopPropagation();
    if (cz.classList.contains('revealed')) return openCard(+cz.dataset.vi);
    cz.classList.add('revealed'); st.revealed.add(cz.dataset.key); return;
  }
  if (t.closest('input')) return;
  const bub = t.closest('.bubble'); if (!bub) return;
  const i = +bub.dataset.i, s = st.L.sentences[i];
  const act = t.closest('[data-act]')?.dataset.act;
  if (act) { e.stopPropagation(); return handleAct(act, i, s, bub); }
  if (st.mode === 'dict' || st.mode === 'shadow') playSentence(i);
  else goto(i, true);
});
list.addEventListener('keydown', e => {
  if (e.target.matches('.dict-input') && e.key === 'Enter') {
    const bub = e.target.closest('.bubble'), i = +bub.dataset.i;
    handleAct('check', i, st.L.sentences[i], bub);
  }
});
list.addEventListener('input', e => {
  if (e.target.matches('.dict-input')) {
    const id = +e.target.dataset.sid; st.dict[id] = { ...(st.dict[id] || {}), value: e.target.value };
  }
});
list.addEventListener('focusin', e => {
  if (e.target.matches('.dict-input')) { const i = +e.target.closest('.bubble').dataset.i; if (i !== st.cur) { st.userScrollUntil = Date.now() + 4000; st.stopAt = null; video.currentTime = st.L.sentences[i].start + 0.01; setCurrent(i); } }
});

function handleAct(act, i, s, bub) {
  if (act === 'listen' || act === 'orig') return playSentence(i);
  if (act === 'check' || act === 'reveal') {
    const dt = dictTarget(s), inp = $('.dict-input', bub), ans = $('.answer', bub);
    const val = inp.value;
    let d;
    if (act === 'reveal') d = { value: dt.text, status: 'bad', msg: '答案：' + dt.text };
    else if (!val.trim()) { inp.focus(); return toast('先输入你听到的单词'); }
    else if (norm(val) === norm(dt.text)) d = { value: val, status: 'ok', msg: '✓ 正确' };
    else d = { value: val, status: 'bad', msg: '✗ 正确答案：' + dt.text };
    st.dict[s.id] = d;
    inp.value = d.value; inp.className = 'dict-input ' + d.status;
    ans.className = 'answer ' + d.status; ans.textContent = d.msg;
    return;
  }
  if (act === 'rec') return st.recording ? stopRec() : startRec(i);
  if (act === 'mine') { const r = st.recs[s.id]; if (r) { video.pause(); new Audio(r).play(); } return; }
}

/* ---------- shadowing: record + recognise ---------- */
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
const canRec = !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder);
function renderShadowPanel() {
  list.querySelectorAll('.extra').forEach(x => x.innerHTML = '');
  if (st.mode !== 'shadow' || !st.L) return;
  const el = bubbleEl(st.cur); if (!el) return;
  const s = st.L.sentences[st.cur], recOn = st.recording && st.recording.sid === s.id;
  const hints = [];
  if (!canRec) hints.push(window.isSecureContext ? '当前浏览器不支持录音（MediaRecorder）。' : '录音需要安全环境：请用 http://localhost 或 https 打开。');
  if (!SR) hints.push('当前浏览器不支持语音识别打分（建议用 Chrome / Edge），仍可录音并回放对比。');
  $('.extra', el).innerHTML = `<div class="shadow-panel">
    <div class="row">
      <button class="chip" data-act="orig">▶ 原音</button>
      <button class="chip rec ${recOn ? 'on' : ''}" data-act="rec" ${canRec ? '' : 'disabled'}>${recOn ? '⏹ 停止录音' : '🎤 开始跟读'}</button>
      <button class="chip" data-act="mine" ${st.recs[s.id] ? '' : 'disabled'}>▶ 我的录音</button>
    </div>
    <div class="sr-result">${st.sr[s.id] || ''}</div>
    <div class="hint">${hints.join('<br>') || '先听原音，再点“开始跟读”读一遍，读完点“停止录音”。'}</div></div>`;
}
async function startRec(i) {
  const s = st.L.sentences[i];
  let stream;
  try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); }
  catch (err) { return toast('无法使用麦克风：' + (err.name === 'NotAllowedError' ? '请允许麦克风权限' : err.message), 3500); }
  video.pause(); st.stopAt = null;
  const chunks = [], mr = new MediaRecorder(stream);
  const rec = st.recording = { sid: s.id, mr, stream, words: [], recog: null, timer: null, srDone: !SR, recDone: false };
  mr.ondataavailable = e => e.data.size && chunks.push(e.data);
  mr.onstop = () => {
    stream.getTracks().forEach(t => t.stop());
    if (st.recs[s.id]) URL.revokeObjectURL(st.recs[s.id]);
    st.recs[s.id] = URL.createObjectURL(new Blob(chunks, { type: mr.mimeType || 'audio/webm' }));
    rec.recDone = true; finish();
  };
  mr.start();
  if (SR) {
    const r = rec.recog = new SR();
    r.lang = 'en-US'; r.continuous = true; r.interimResults = false; r.maxAlternatives = 1;
    r.onresult = e => { for (let k = e.resultIndex; k < e.results.length; k++) if (e.results[k].isFinal) rec.words.push(e.results[k][0].transcript); };
    r.onerror = e => { rec.srError = e.error; };
    r.onend = () => { rec.srDone = true; finish(); };
    try { r.start(); } catch (_) { rec.srDone = true; }
  }
  rec.timer = setTimeout(stopRec, ((s.end - s.start) * 2 + 4) * 1000);
  renderShadowPanel();
  function finish() {
    if (!rec.recDone || !rec.srDone) return;
    if (SR) st.sr[s.id] = scoreHTML(s, rec.words.join(' '), rec.srError);
    st.recording = null; renderShadowPanel();
  }
}
function stopRec() {
  const r = st.recording; if (!r) return;
  clearTimeout(r.timer);
  if (r.mr.state !== 'inactive') r.mr.stop();
  if (r.recog) { try { r.recog.stop(); } catch (_) {} }
}
function scoreHTML(s, heard, err) {
  if (!heard.trim()) {
    const why = { 'not-allowed': '语音识别权限被拒绝', network: '语音识别需要联网（Chrome 使用在线识别服务）', 'no-speech': '没有听到声音' }[err] || '没有识别到语音';
    return `<span class="w-bad">${why}，可以先回放录音自己对比。</span>`;
  }
  const target = tokenize(s.en).filter(t => t.w).map(t => t.t);
  const tn = target.map(norm), hn = tokenize(heard).filter(t => t.w).map(t => norm(t.t));
  // LCS alignment
  const n = tn.length, m = hn.length, dp = Array.from({ length: n + 1 }, () => new Int16Array(m + 1));
  for (let a = n - 1; a >= 0; a--) for (let b = m - 1; b >= 0; b--)
    dp[a][b] = tn[a] === hn[b] ? dp[a + 1][b + 1] + 1 : Math.max(dp[a + 1][b], dp[a][b + 1]);
  const ok = new Array(n).fill(false); let a = 0, b = 0;
  while (a < n && b < m) { if (tn[a] === hn[b]) { ok[a] = true; a++; b++; } else if (dp[a + 1][b] >= dp[a][b + 1]) a++; else b++; }
  const score = Math.round(100 * ok.filter(Boolean).length / Math.max(1, n));
  const color = score >= 85 ? 'var(--ok)' : score >= 60 ? '#d48806' : 'var(--bad)';
  return `<span class="score" style="color:${color}">${score}分</span>` +
    target.map((w, k) => `<span class="${ok[k] ? 'w-ok' : 'w-bad'}">${esc(w)}</span>`).join(' ') +
    `<div class="heard">识别结果：${esc(heard)}</div>`;
}

/* ---------- sheets ---------- */
function openSheet(title, html, onRender) {
  $('#sheetTitle').textContent = title; $('#sheetBody').innerHTML = html;
  $('#sheet').hidden = false; $('#backdrop').hidden = false; $('#sheetBody').scrollTop = 0;
  onRender && onRender($('#sheetBody'));
}
function closeSheet() { $('#sheet').hidden = true; $('#backdrop').hidden = true; }
$('#backdrop').onclick = closeSheet; $('#sheetClose').onclick = closeSheet;
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') closeSheet();
  if (e.target.matches('input')) return;
  if (e.key === ' ') { e.preventDefault(); togglePlay(); }
  if (e.key === 'ArrowLeft') goto(st.cur - 1, !video.paused);
  if (e.key === 'ArrowRight') goto(st.cur + 1, !video.paused);
});

const inBook = w => st.book.some(b => b.word === w);
function saveBook() { localStorage.setItem(LS.book, JSON.stringify(st.book)); updateBadge(); }
function updateBadge() { const b = $('#bookBadge'); b.textContent = st.book.length; b.classList.toggle('zero', !st.book.length); }

function cardHTML(v) {
  const sents = v.sentence_ids.map(id => st.L.sentences.find(s => s.id === id)).filter(Boolean);
  return `<div class="vc">
    <div><span class="vc-word">${esc(v.word)}</span><span class="vc-pos">${esc(v.pos)}</span></div>
    <div class="vc-ipa">
      <button data-say="en-US">美 ${esc(v.ipa_us)} 🔊</button>
      <button data-say="en-GB">英 ${esc(v.ipa_uk)} 🔊</button>
    </div>
    <div class="vc-sec"><div class="vc-label">中文释义</div><div class="vc-zh">${esc(v.def_zh)}</div></div>
    <div class="vc-sec"><div class="vc-label">英文释义</div><div>${esc(v.def_en)}</div></div>
    <div class="vc-sec"><div class="vc-label">例句</div><div class="vc-ex">${esc(v.example)}<button data-ex title="朗读例句">🔊</button></div></div>
    ${sents.length ? `<div class="vc-sec"><div class="vc-label">视频原句（点击跳转）</div>${sents.map(s => `<div class="vc-src" data-sid="${s.id}">${esc(s.en)}</div>`).join('<div style="height:6px"></div>')}</div>` : ''}
    <button class="vc-add ${inBook(v.word) ? 'added' : ''}" data-add>${inBook(v.word) ? '✓ 已加入生词本' : '＋ 加入生词本'}</button></div>`;
}
function openCard(vi, vObj) {
  const v = vObj || st.L.vocab[vi];
  if (!video.paused) { video.pause(); }
  openSheet('词汇卡片', cardHTML(v), body => {
    body.querySelectorAll('[data-say]').forEach(b => b.onclick = () => speak(v.word, b.dataset.say, 0.85));
    $('[data-ex]', body).onclick = () => speak(v.example, 'en-US');
    body.querySelectorAll('.vc-src').forEach(el => el.onclick = () => { closeSheet(); playSentence(st.L.sentences.findIndex(s => s.id === +el.dataset.sid)); });
    $('[data-add]', body).onclick = e => {
      if (inBook(v.word)) { st.book = st.book.filter(b => b.word !== v.word); toast('已从生词本移除'); }
      else { st.book.unshift({ ...v, lesson: st.L.title, added: Date.now() }); toast('已加入生词本'); }
      saveBook();
      const b = e.currentTarget, on = inBook(v.word);
      b.classList.toggle('added', on); b.textContent = on ? '✓ 已加入生词本' : '＋ 加入生词本';
    };
  });
}

$('#navVocab').onclick = () => openSheet(`重点词（${st.L.vocab.length}）`,
  st.L.vocab.map((v, i) => `<div class="list-item" data-vi="${i}"><div class="main-t"><div class="w">${esc(v.word)} <small style="color:var(--muted);font-weight:400">${esc(v.pos)} ${esc(v.ipa_us)}</small></div><div class="s">${esc(v.def_zh)}</div></div><span>${inBook(v.word) ? '📒' : ''}</span></div>`).join(''),
  body => body.querySelectorAll('.list-item').forEach(el => el.onclick = () => openCard(+el.dataset.vi)));

function openBook() {
  openSheet(`生词本（${st.book.length}）`, st.book.length ? st.book.map((v, i) =>
    `<div class="list-item" data-i="${i}"><div class="main-t"><div class="w">${esc(v.word)} <small style="color:var(--muted);font-weight:400">${esc(v.pos)}</small></div><div class="s">${esc(v.def_zh)}</div></div><button class="x" data-del="${i}" title="移除">✕</button></div>`).join('')
    : '<div class="empty">生词本还是空的。<br>点击字幕中的米色单词，在词汇卡片里加入生词本。</div>',
  body => body.querySelectorAll('.list-item').forEach(el => el.onclick = e => {
    const i = +el.dataset.i;
    if (e.target.closest('[data-del]')) { st.book.splice(i, 1); saveBook(); return openBook(); }
    openCard(null, st.book[i]);
  }));
}
$('#navBook').onclick = openBook;

$('#navToc').onclick = () => openSheet(`目录（${st.L.sentences.length} 句）`,
  (LESSONS.length > 1 ? `<div class="list-item lesson-switch" data-switch><span class="t">课程</span><div class="main-t"><div class="w">${esc(st.L.title)}</div><div class="s">点击切换课程（共 ${LESSONS.length} 节）</div></div><span>›</span></div>` : '') +
  st.L.sentences.map((s, i) => `<div class="list-item ${i === st.cur ? 'cur' : ''}" data-i="${i}"><span class="t">${fmt(s.start)}</span><div class="main-t"><div>${esc(s.en)}</div><div class="s">${esc(s.zh)}</div></div></div>`).join(''),
  body => {
    body.querySelectorAll('.list-item[data-i]').forEach(el => el.onclick = () => { closeSheet(); goto(+el.dataset.i, true); });
    const sw = $('[data-switch]', body); if (sw) sw.onclick = openLessonPicker;
    const c = $('.cur', body); if (c) body.scrollTop = c.offsetTop - body.clientHeight / 2;
  });

function syncLearned() {
  const on = !!st.learned[st.L.title];
  $('#navLearned').classList.toggle('on', on);
  $('#learnedIco').textContent = on ? '✓' : '○'; $('#learnedTxt').textContent = on ? '已学' : '标记已学';
}
$('#navLearned').onclick = () => {
  if (st.learned[st.L.title]) delete st.learned[st.L.title]; else st.learned[st.L.title] = Date.now();
  localStorage.setItem(LS.learned, JSON.stringify(st.learned)); syncLearned();
  toast(st.learned[st.L.title] ? '已标记为学完 🎉' : '已取消“已学”标记');
};

$('#infoBtn').onclick = () => openSheet('视频来源与授权', `<div class="info">
  <p><b>${esc(st.L.title)}</b></p>
  <p>来源：<a href="${esc(st.L.source_url)}" target="_blank" rel="noopener">${esc(st.L.source_url)}</a></p>
  ${st.L.section ? `<p>片段：${esc(st.L.section)}</p>` : ''}
  <p>授权：${esc(st.L.license)}</p>
  <p style="color:var(--muted)">字幕由语音识别自动生成并人工校对，中文翻译与词汇卡片为本原型编写。</p>${LESSONS.length > 1 ? '<p><button class="chip primary" id="pickLesson">切换课程</button></p>' : ''}</div>`,
  body => { const b = $('#pickLesson', body); if (b) b.onclick = openLessonPicker; });

/* ---------- lessons ---------- */
let LESSONS = [];
function openLessonPicker() {
  const cur = st.L && st.L.id;
  openSheet('选择课程', LESSONS.map(l => `<div class="list-item ${l.id === cur ? 'cur' : ''}" data-id="${esc(l.id)}">
      <div class="main-t"><div class="w">${esc(l.title)}</div><div class="s">${l.id === cur ? '当前课程' : (st.learned[l.title] ? '✓ 已学' : '')}</div></div>
      <span>${l.id === cur ? '✓' : '›'}</span></div>`).join('') || '<div class="empty">只有一节课</div>',
    body => body.querySelectorAll('.list-item').forEach(el => el.onclick = () => switchLesson(el.dataset.id)));
}
function switchLesson(id) {
  if (st.L && id === st.L.id) return closeSheet();
  localStorage.setItem('ep_lesson', id);
  const u = new URL(location.href); u.searchParams.set('lesson', id); location.href = u.toString();
}
$('#lessonTitle').onclick = () => LESSONS.length > 1 ? openLessonPicker() : null;

/* ---------- boot ---------- */
async function boot() {
  let path = 'lesson.json';
  try {
    const r = await fetch('lessons/index.json');
    if (r.ok) LESSONS = (await r.json()).lessons || [];
  } catch (_) {}
  if (LESSONS.length) {
    const want = new URLSearchParams(location.search).get('lesson') || localStorage.getItem('ep_lesson');
    const pick = LESSONS.find(l => l.id === want) || LESSONS[0];
    path = pick.path;
  }
  try {
    const r = await fetch(path); if (!r.ok) throw new Error(r.status);
    st.L = await r.json();
    st.L.id = st.L.id || (LESSONS.find(l => l.path === path) || {}).id || 'lesson';
  } catch (e) {
    $('#lessonTitle').textContent = '加载失败';
    list.innerHTML = `<div class="empty">无法读取课程数据（${esc(path)}）。<br>请在项目目录运行 <code>python serve.py 8000</code>，<br>然后打开 http://localhost:8000 （不能直接双击 index.html）。</div>`;
    return;
  }
  document.title = st.L.title + ' · 看视频学英语';
  $('#lessonTitle').innerHTML = `<span class="lt-text">${esc(st.L.title)}</span>${LESSONS.length > 1 ? '<span class="lt-caret">▾</span>' : ''}`;
  $('#lessonTitle').classList.toggle('switchable', LESSONS.length > 1);
  video.src = st.L.video;
  applySpeed(); updateBadge(); syncLearned(); syncPlayBtn();
  renderList();
  requestAnimationFrame(tick);
}
window.__ep = { st, scoreHTML, tokenize, matchVocab, kTimes, kara, lessons: () => LESSONS }; // for tests
boot();
})();
