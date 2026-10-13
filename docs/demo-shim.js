/*
 * PlantPulse static demo shim.
 *
 * GitHub Pages can only serve static files, so the real FastAPI backend isn't available.
 * This script replays API responses that were recorded from the real server (seeded with
 * demo data) so the unmodified dashboard runs in the browser. It must load before the
 * dashboard's own script. Everything here is simulated: nothing is sent anywhere.
 */
(function () {
  var DATA = window.__PP_DEMO;
  if (!DATA) return;

  // --- clock: make "now" the moment the data was recorded, then let it tick ----------
  // The dashboard shows "7h ago", "water in ~60h" etc. relative to the current time, and
  // recorded timestamps are naive local strings, so anchor on the same naive string.
  var RealDate = Date;
  var anchor = new RealDate(DATA.capturedAt).getTime();
  var loadedAt = RealDate.now();
  function fakeNow() { return anchor + (RealDate.now() - loadedAt); }
  function FakeDate() {
    var a = arguments;
    if (!(this instanceof FakeDate)) return new RealDate(fakeNow()).toString();
    if (a.length === 0) return new RealDate(fakeNow());
    return new (Function.prototype.bind.apply(RealDate, [null].concat([].slice.call(a))))();
  }
  FakeDate.prototype = RealDate.prototype;
  FakeDate.now = fakeNow;
  FakeDate.parse = RealDate.parse;
  FakeDate.UTC = RealDate.UTC;
  window.Date = FakeDate;

  // --- signed in as the demo user unless they log out --------------------------------
  try {
    if (!localStorage.getItem('ppUser')) localStorage.setItem('ppUser', JSON.stringify(DATA.user));
  } catch (e) { /* storage blocked: the login screen still works, any credentials do */ }

  // --- tiny bit of state so the pump / spray buttons behave --------------------------
  var pump = { on: false, start: null };
  var spray = { on: false, start: null };
  function iso() { return new RealDate(fakeNow()).toISOString().slice(0, 19); }

  function json(body, status) {
    return new Response(JSON.stringify(body), {
      status: status || 200,
      headers: { 'Content-Type': 'application/json' }
    });
  }
  function recorded(method, path) {
    var r = DATA.responses[method + ' ' + path];
    if (!r) r = DATA.responses[method + ' ' + path.split('?')[0]];
    return r ? JSON.parse(r.body) : undefined;
  }

  window.fetch = function (input, init) {
    var url = typeof input === 'string' ? input : input.url;
    var method = ((init && init.method) || (typeof input !== 'string' && input.method) || 'GET').toUpperCase();
    var u;
    try { u = new URL(url, location.href); } catch (e) { return Promise.reject(new TypeError('bad url')); }
    if (u.pathname.indexOf('/api/') !== 0) return Promise.reject(new TypeError('Not available in the static demo'));
    var path = u.pathname + u.search;
    var body = {};
    try { body = JSON.parse((init && init.body) || '{}'); } catch (e) {}

    if (method === 'GET') {
      if (u.pathname === '/api/pump/status') {
        return Promise.resolve(json({
          on: pump.on, start_ts: pump.start, mode: 'manual',
          runtime_sec: pump.on ? Math.round((fakeNow() - new RealDate(pump.start).getTime()) / 1000) : null
        }));
      }
      var g = recorded('GET', path);
      return Promise.resolve(g !== undefined ? json(g)
        : json({ detail: 'Not available in the static demo' }, 404));
    }

    // writes: simulate, never persist
    switch (u.pathname) {
      case '/api/pump/on':  pump = { on: true, start: iso() };  return Promise.resolve(json({ status: 'pump_on', ts: pump.start }));
      case '/api/pump/off': pump = { on: false, start: null };  return Promise.resolve(json({ status: 'pump_off' }));
      case '/api/spray/on':  spray = { on: true, start: iso() }; return Promise.resolve(json({ status: 'spray_on', on: true, start_ts: spray.start, method: 'fertigation' }));
      case '/api/spray/off': spray = { on: false, start: null }; return Promise.resolve(json({ status: 'spray_off', on: false, start_ts: null, method: 'fertigation' }));
      case '/api/auth/login':
      case '/api/auth/signup': return Promise.resolve(json({ status: 'ok', user: DATA.user }));
      case '/api/profile': return Promise.resolve(json({ status: 'ok', profile_complete: true }));
      case '/api/chat': {
        var c = recorded('POST', '/api/chat');
        if (c && body.message) {
          c.thread.title = String(body.message).slice(0, 40);
          c.messages[0].content = body.message;
          c.reply = c.messages[1].content = 'Demo mode: replies are pre-recorded. ' + c.reply;
        }
        return Promise.resolve(json(c || { reply: 'Demo mode: chat is not available.' }));
      }
    }
    var w = recorded('POST', path);
    return Promise.resolve(json(w !== undefined ? w : { status: 'ok', demo: true }));
  };

  // --- small notice so nobody mistakes this for a live system ------------------------
  document.addEventListener('DOMContentLoaded', function () {
    var b = document.createElement('a');
    b.href = 'https://github.com/Arjun-agl/Arjun-agl-Plantpulse-';
    b.target = '_blank';
    b.rel = 'noopener';
    b.textContent = 'Static demo · recorded data · view source';
    b.id = 'pp-demo-badge';
    var st = document.createElement('style');
    st.textContent = '#pp-demo-badge{position:fixed;bottom:8px;right:8px;z-index:99999;padding:5px 10px;' +
      'border-radius:999px;font:600 11px/1.2 system-ui,sans-serif;text-decoration:none;' +
      'background:rgba(8,20,14,.92);color:#2bff9a;border:1px solid rgba(43,255,154,.45)}' +
      '@media(max-width:768px){#pp-demo-badge{bottom:92px}}';
    document.head.appendChild(st);
    document.body.appendChild(b);
  });
})();
