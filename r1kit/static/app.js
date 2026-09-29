'use strict';
// Yonetim paneli. Tum dinamik metin textContent ile eklenir (XSS'e karsi innerHTML kullanilmaz).
const S = {must: false, csrf: null, cfg: null, hw: [], schema: null, status: null, tab: 'dash', dirty: false, timer: null, live: false, es: null};
const $ = s => document.querySelector(s);
const h = (t, a, ...c) => {
  const e = document.createElement(t);
  for (const [k, v] of Object.entries(a || {})) {
    if (k === 'class') e.className = v; else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) e[k] = v;
  }
  e.append(...c.flat(Infinity).filter(x => x != null && x !== false)); return e;
};
const toast = m => { const t = $('#toast'); t.textContent = m; t.classList.add('on'); setTimeout(() => t.classList.remove('on'), 3000); };
async function api(path, method = 'GET', body) {
  const r = await fetch('/api/' + path, {method, headers: {'Content-Type': 'application/json', 'X-CSRF': S.csrf || ''}, body: body ? JSON.stringify(body) : undefined});
  const ct = r.headers.get('content-type') || '';
  const j = ct.includes('json') ? await r.json() : await r.blob();
  if (!r.ok) { if (r.status === 401) { stopSSE(); S.csrf = null; render(); } throw new Error(j.error || r.status); }
  return j;
}
const fmtBps = b => b > 1e6 ? (b / 1e6).toFixed(1) + ' Mbit/s' : b > 1e3 ? (b / 1e3).toFixed(0) + ' kbit/s' : Math.round(b) + ' bit/s';
const fmtB = b => b > 1e9 ? (b / 1e9).toFixed(2) + ' GB' : b > 1e6 ? (b / 1e6).toFixed(1) + ' MB' : (b / 1e3).toFixed(0) + ' kB';
const fmtT = s => { const d = Math.floor(s / 86400), hh = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60); return (d ? d + 'g ' : '') + hh + 's ' + m + 'dk'; };

// ---------- genel form alani
function field(f, obj, onchange) {
  const cur = obj[f.k] !== undefined ? obj[f.k] : f.default;
  const set = v => { obj[f.k] = v; S.dirty = true; onchange && onchange(); banner(); };
  let inp;
  if (f.t === 'bool') inp = h('input', {type: 'checkbox', checked: !!cur, onchange: e => set(e.target.checked)});
  else if (f.t === 'select') inp = h('select', {onchange: e => set(e.target.value)}, f.opts.map(o => h('option', {value: o, selected: String(o) === String(cur), textContent: String(o)})));
  else if (f.t === 'textarea') inp = h('textarea', {rows: 3, value: cur || '', onchange: e => set(e.target.value)});
  else if (f.t === 'json') inp = h('textarea', {rows: 4, value: JSON.stringify(cur || [], null, 1), onchange: e => { try { set(JSON.parse(e.target.value)); } catch { toast('JSON gecersiz'); } }});
  else if (f.t === 'int') inp = h('input', {type: 'number', value: cur ?? '', onchange: e => set(e.target.value === '' ? null : Number(e.target.value))});
  else inp = h('input', {type: f.t === 'password' ? 'password' : 'text', value: cur ?? '', autocomplete: 'off', onchange: e => set(e.target.value)});
  return h('div', {}, h('label', {}, f.t === 'bool' ? [inp, ' ' + f.label] : f.label), f.t === 'bool' ? null : inp, f.h ? h('div', {class: 'mut', textContent: f.h}) : null);
}
const F = (k, label, t = 'text', extra = {}) => Object.assign({k, label, t}, extra);

// ---------- yerlesim
function banner() {
  const b = $('#banner'); if (!b) return; b.replaceChildren();
  if (S.status && S.status.pending_confirm > 0) {
    b.className = 'banner';
    b.append(h('span', {textContent: 'Degisiklikler gecici uygulandi. ' + S.status.pending_confirm + ' sn icinde onaylamazsaniz geri alinacak.'}),
      h('button', {class: 'b s', textContent: 'Onayla', onclick: async () => { await api('confirm', 'POST', {}); toast('Onaylandi'); refresh(); }}));
  } else if (S.dirty) {
    b.className = 'banner';
    b.append(h('span', {textContent: 'Kaydedilmemis degisiklikler var'}),
      h('button', {class: 'b s', textContent: 'Kaydet', onclick: save}),
      h('button', {class: 'b', style: 'margin-left:0', textContent: 'Kaydet ve Guvenli Uygula', onclick: saveApply}));
  } else b.className = '';
}
async function save() { try { await api('config', 'PUT', S.cfg); S.dirty = false; toast('Kaydedildi'); banner(); } catch (e) { toast('Hata: ' + e.message); } }
async function saveApply() {
  try {
    await api('config', 'PUT', S.cfg); S.dirty = false;
    const r = await api('apply', 'POST', {safe: true, confirm_s: 90});
    toast(r.safe ? 'Uygulandi - 90 sn icinde onaylayin' : 'Uygulandi'); refresh();
  } catch (e) { toast('Hata: ' + e.message); }
}
const TABS = [['dash', 'Panel'], ['ifaces', 'Arayuzler'], ['nets', 'Aglar'], ['wan', 'Coklu WAN'], ['clients', 'Istemciler'],
  ['skills', 'Skills'], ['tools', 'Araclar'], ['portal', 'Portal'], ['system', 'Sistem']];

async function render() {
  const app = $('#app'); app.replaceChildren();
  if (!S.csrf) return login(app);
  if (S.must) S.tab = 'system';
  app.append(h('header', {}, h('h1', {textContent: 'Orange Pi R1 Toolkit'}),
      h('span', {id: 'live', class: 'tag ' + (S.live ? 'ok' : 'bad'), textContent: S.live ? '● canli' : '○ bagli degil'}),
      h('button', {class: 'b s', textContent: 'Cikis', onclick: async () => { stopSSE(); await api('logout', 'POST', {}); S.csrf = null; render(); }})),
    h('nav', {}, TABS.filter(t => !S.must || t[0] === 'system').map(([k, l]) => h('button', {class: S.tab === k ? 'on' : '', textContent: l, onclick: () => { S.tab = k; render(); }}))),
    h('div', {id: 'banner'}), h('main', {id: 'main'}));
  banner();
  if (!S.cfg) await refresh(false);
  const m = $('#main'); m.replaceChildren(await VIEWS[S.tab]());
}
async function refresh(rerender = true) {
  const st = await api('state'); S.status = st.status; S.hw = st.hw;
  if (!S.dirty) S.cfg = st.config;
  if (!S.schema) S.schema = await api('schema');
  if (rerender) render();
}

function login(app) {
  const pw = h('input', {type: 'password', placeholder: 'Yonetici parolasi', autofocus: true});
  const go = async () => {
    try { const r = await api('login', 'POST', {password: pw.value}); S.csrf = r.csrf; S.cfg = null; S.must = r.must_change; await render(); connectSSE(); if (r.must_change) toast('Ilk giris: lutfen parolayi degistirin'); }
    catch (e) { toast(e.message); }
  };
  pw.addEventListener('keydown', e => e.key === 'Enter' && go());
  app.append(h('div', {class: 'center'}, h('div', {class: 'card'}, h('h2', {textContent: 'R1 Toolkit - Yonetici Girisi'}), pw, h('br'), h('br'), h('button', {class: 'b', textContent: 'Giris', onclick: go}),
    h('p', {class: 'mut', textContent: 'Ilk parola: cihazda /etc/r1kit/first-run-password dosyasi veya "journalctl -u r1kit".'}))));
}

// ---------- gorunumler
const card = (title, ...c) => h('div', {class: 'card'}, title ? h('h2', {textContent: title}) : null, c);
const table = (heads, rows) => h('div', {class: 'scroll'}, h('table', {}, h('tr', {}, heads.map(x => h('th', {textContent: x}))), rows.map(r => h('tr', {}, r.map(c => h('td', {}, c))))));
const tag = (t, cls) => h('span', {class: 'tag ' + (cls || ''), textContent: t});

function spark(vals, color = 'var(--acc)') {
  const W = 300, H = 60, mx = Math.max(1, ...vals);
  const pts = vals.map((v, i) => (i * W / Math.max(1, vals.length - 1)).toFixed(1) + ',' + (H - 4 - v / mx * (H - 8)).toFixed(1)).join(' ');
  const ns = 'http://www.w3.org/2000/svg', s = document.createElementNS(ns, 'svg'); s.setAttribute('viewBox', `0 0 ${W} ${H}`); s.setAttribute('class', 'spark');
  const p = document.createElementNS(ns, 'polyline'); p.setAttribute('points', pts); p.setAttribute('fill', 'none'); p.setAttribute('stroke', color); p.setAttribute('stroke-width', 2); s.append(p); return s;
}

const VIEWS = {
  async dash() {
    await refresh(false);
    const st = S.status, l = st.last || {}, m = await api('metrics');
    const ifs = new Set(); m.samples.forEach(s => Object.keys(s.rates || {}).forEach(i => ifs.add(i)));
    return h('div', {}, h('div', {class: 'grid'},
      card('CPU', h('div', {class: 'kpi', textContent: (l.cpu ?? '-') + ' %'})),
      card('Bellek', h('div', {class: 'kpi', textContent: l.mem_total ? (l.mem_total - l.mem_avail) + ' / ' + l.mem_total + ' MB' : '-'})),
      card('Sicaklik', h('div', {class: 'kpi', textContent: l.temp != null ? l.temp.toFixed(0) + ' C' : '-'})),
      card('Calisma suresi', h('div', {class: 'kpi', textContent: fmtT(st.uptime)}), h('div', {class: 'mut', textContent: 'Portal yetkili: ' + st.authed}))),
      card('Uplink durumu', table(['Arayuz', 'Durum', 'IP', 'Gateway', 'RTT', 'Aktif'], st.uplinks.uplinks.map(u => [u.iface, tag(u.up ? 'UP' : 'DOWN', u.up ? 'ok' : 'bad'), u.ip || '-', u.gw || '-', u.rtt != null ? u.rtt + ' ms' : '-', u.active ? 'evet' : ''])),
        h('h3', {textContent: 'Olaylar'}), h('pre', {textContent: st.uplinks.events.map(e => new Date(e.t * 1000).toLocaleTimeString() + '  ' + e.msg).join('\n') || '-'})),
      card('Trafik (son 10 dk)', [...ifs].map(i => h('div', {}, h('div', {class: 'mut', textContent: i + '  ↓ ' + fmtBps(m.samples.at(-1).rates[i]?.rx || 0) + '  ↑ ' + fmtBps(m.samples.at(-1).rates[i]?.tx || 0)}),
        spark(m.samples.map(s => (s.rates[i]?.rx || 0) + (s.rates[i]?.tx || 0)))))),
      card('Servisler', table(['Surec', 'Durum'], Object.entries(st.procs).map(([k, v]) => [k, tag(v, v === 'running' ? 'ok' : 'bad')]))),
      card('Sistem olaylari (donanim takma/cikarma, uygulama, vb.)', h('pre', {textContent: (st.events || []).map(e => new Date(e.t * 1000).toLocaleTimeString() + '  ' + e.msg).join('\n') || '-'})));
  },

  async ifaces() {
    const ifs = S.cfg.interfaces, known = new Set(S.hw.map(x => x.name));
    const virt = Object.keys(ifs).filter(n => !known.has(n));
    const nets = Object.keys(S.cfg.networks);
    const one = (name, hw) => {
      const spec = ifs[name] || (ifs[name] = {role: 'unused'});
      const body = h('div', {}), draw = () => {
        body.replaceChildren();
        const r = spec.role, fs = [];
        if (r === 'lan' || r === 'ap') fs.push(F('network', 'Ag', 'select', {opts: nets}));
        if (r === 'wan') { fs.push(F('proto', 'Adresleme', 'select', {opts: ['dhcp', 'static'], default: 'dhcp'}), F('priority', 'Oncelik (kucuk=once)', 'int', {default: 100}), F('weight', 'Yuk dengeleme agirligi', 'int', {default: 1}), F('mac_clone', 'MAC klonla (ops.)'), F('mtu', 'MTU (ops.)', 'int'));
          if (spec.proto === 'static') fs.push(F('address', 'IP/CIDR'), F('gateway', 'Gateway'), F('dns', 'DNS (virgulle)'));
        }
        if (r === 'ap') fs.push(F('ssid', 'SSID'), F('psk', 'WPA parolasi', 'password'), F('security', 'Guvenlik', 'select', {opts: ['wpa2', 'wpa2/3', 'open'], default: 'wpa2'}),
          F('band', 'Bant', 'select', {opts: ['2.4', '5'], default: '2.4'}), F('channel', 'Kanal (auto veya no)', 'text', {default: 'auto'}), F('width', 'Kanal genisligi', 'select', {opts: ['20', '40'], default: '20'}),
          F('hidden', 'Gizli SSID', 'bool'), F('isolate', 'Istemci izolasyonu', 'bool'), F('max_clients', 'Maks istemci', 'int', {default: 32}));
        if (r === 'sta') { fs.push(F('ssid', 'Baglanilacak SSID'), F('psk', 'Parola', 'password'), F('priority', 'Oncelik', 'int', {default: 100}), F('weight', 'Agirlik', 'int', {default: 1}));
          fs.push(F('saved', 'Yedek aglar (JSON: [{ssid,psk}])', 'json')); }
        if (r === 'monitor') fs.push(F('channel', 'Kanal', 'int'));
        if (name in ifs && !known.has(name)) fs.push(F('parent', 'Ana wifi (sanal arayuz)'));
        fs.forEach(f => {
          if (f.k === 'dns') { const o = {dns: (spec.dns || []).join(',')}; body.append(field(f, o, () => { spec.dns = o.dns.split(',').map(x => x.trim()).filter(Boolean); })); }
          else body.append(field(f, spec, null));
        });
      };
      draw();
      const role = h('select', {onchange: e => { spec.role = e.target.value; S.dirty = true; if (spec.role === 'lan' || spec.role === 'ap') spec.network = spec.network || nets[0]; draw(); banner(); }},
        S.schema.roles.map(r => h('option', {value: r, selected: r === spec.role, textContent: r})));
      const w = hw && hw.wifi;
      const qr = h('div', {});
      const showQr = () => { qr.replaceChildren(h('div', {class: 'mut', textContent: 'Telefonla okutup dogrudan baglanin (en son kaydedilen ayarlari gosterir):'}),
        h('img', {src: '/api/qr/wifi/' + encodeURIComponent(name) + '.svg?t=' + Date.now(), style: 'width:170px;height:170px;background:#fff;border-radius:8px;padding:6px', alt: 'Wi-Fi baglanti QR kodu'})); };
      return card(name, hw ? h('div', {class: 'mut', textContent: `${hw.type} • ${hw.bus} • ${hw.driver || '?'} • ${hw.mac} • ${hw.carrier ? 'link var' : 'link yok'}${hw.speed ? ' • ' + hw.speed + 'Mb' : ''}` + (w ? ` • modlar: ${w.modes.join('/')} • AP+STA eszamanli: ${w.ap_sta_concurrent ? 'evet' : 'hayir'}` : '')}) : h('div', {class: 'mut', textContent: 'sanal arayuz'}),
        h('label', {textContent: 'Rol'}), role, body,
        spec.role === 'ap' ? h('button', {class: 'b s', textContent: 'Baglanti QR kodu goster', onclick: showQr}) : null, qr,
        !hw ? h('button', {class: 'b d', textContent: 'Sil', onclick: () => { delete ifs[name]; S.dirty = true; render(); }}) : null);
    };
    const addv = h('div', {class: 'row'}, h('input', {id: 'vname', placeholder: 'yeni sanal arayuz adi (orn. ap0)'}), h('input', {id: 'vpar', placeholder: 'ana wifi (orn. wlan0)'}),
      h('button', {class: 'b s', textContent: 'Sanal arayuz ekle', onclick: () => { const n = $('#vname').value, p = $('#vpar').value; if (n && p) { ifs[n] = {role: 'ap', parent: p, network: Object.keys(S.cfg.networks)[0], ssid: 'R1-' + n, psk: 'changeme-r1kit', channel: 'auto'}; S.dirty = true; render(); } }}));
    return h('div', {}, S.hw.map(x => one(x.name, x)), virt.map(n => one(n, null)),
      card('Sanal Wi-Fi arayuzu (AP+STA ayni radyoda)', h('div', {class: 'mut', textContent: 'Yalnizca surucu eszamanli AP+STA destekliyorsa calisir (bkz. yukaridaki bilgi). Ayni kanalda calismak zorundadir.'}), addv));
  },

  async nets() {
    const nets = S.cfg.networks;
    const fs = [F('address', 'Router adresi (CIDR, bos = adressiz L2 kopru)'), F('dhcp', 'DHCP sunucusu', 'bool'), F('dhcp_start', 'DHCP baslangic (host no)', 'int'), F('dhcp_end', 'DHCP bitis', 'int'), F('lease', 'Kira suresi'),
      F('portal', 'Captive portal (misafir kapisi)', 'bool'), F('isolate', 'Istemci izolasyonu', 'bool'), F('egress', 'Cikis', 'select', {opts: ['default', 'vpn']})];
    return h('div', {}, Object.entries(nets).map(([n, spec]) => card('Ag: ' + n,
      fs.map(f => field(f, spec)),
      (() => { const o = {allow_to: (spec.allow_to || []).join(',')}; return field(F('allow_to', 'Erisebilecegi diger aglar (virgulle)'), o, () => { spec.allow_to = o.allow_to.split(',').map(x => x.trim()).filter(Boolean); }); })(),
      h('button', {class: 'b d', textContent: 'Agi sil', onclick: () => { delete nets[n]; S.dirty = true; render(); }}))),
      card('Yeni ag', h('div', {class: 'row'}, h('input', {id: 'nn', placeholder: 'ad (orn. guest)'}), h('input', {id: 'na', placeholder: '192.168.70.1/24'}),
        h('button', {class: 'b', textContent: 'Ekle', onclick: () => { const n = $('#nn').value, a = $('#na').value; if (n) { nets[n] = {address: a, dhcp: true, dhcp_start: 20, dhcp_end: 230, lease: '12h', portal: false, isolate: false, egress: 'default', allow_to: []}; S.dirty = true; render(); } }}))),
      card('Port yonlendirme', h('div', {class: 'mut', textContent: 'JSON: [{"proto":"tcp","wan_port":8022,"to_ip":"192.168.50.20","to_port":22,"comment":"ssh"}]'}), field(F('port_forwards', '', 'json'), S.cfg)),
      card('Statik DHCP kiralari', h('div', {class: 'mut', textContent: 'JSON: [{"mac":"aa:bb:..","ip":"192.168.50.5","name":"nas"}]'}), field(F('static_leases', '', 'json'), S.cfg)));
  },

  async wan() {
    const u = S.cfg.uplink;
    const wans = Object.entries(S.cfg.interfaces).filter(([, s]) => s.role === 'wan' || s.role === 'sta').map(([n]) => n);
    const st = await api('uplinks');
    return h('div', {}, card('Coklu WAN politikasi',
      field(F('mode', 'Mod', 'select', {opts: ['failover', 'balance', 'single'], h: 'failover: oncelik sirasi + saglik kontrolu | balance: agirlikli akis dagitimi (cift WAN hiz toplama, tek akis toplanmaz) | single: sabit uplink'}), u),
      field(F('primary', 'Birincil (single modu)', 'select', {opts: ['', ...wans]}), u),
      (() => { const o = {t: u.targets.join(',')}; return field(F('t', 'Saglik kontrolu hedefleri (virgulle)'), o, () => { u.targets = o.t.split(',').map(x => x.trim()).filter(Boolean); }); })(),
      field(F('interval', 'Kontrol araligi (sn)', 'int'), u), field(F('fail_after', 'Kac basarisizlikta DOWN', 'int'), u), field(F('recover_after', 'Kac basarida UP', 'int'), u),
      h('div', {class: 'mut', textContent: 'Uplink oncelik/agirlik ayarlari Arayuzler sekmesindedir. Aktif: ' + (st.uplinks.filter(x => x.active).map(x => x.iface).join(', ') || 'yok')})),
      card('Durum', table(['Arayuz', 'Durum', 'IP', 'GW', 'RTT'], st.uplinks.map(x => [x.iface, tag(x.up ? 'UP' : 'DOWN', x.up ? 'ok' : 'bad'), x.ip || '-', x.gw || '-', x.rtt ?? '-'])),
        h('pre', {textContent: st.events.map(e => new Date(e.t * 1000).toLocaleTimeString() + ' ' + e.msg).join('\n')})));
  },

  async clients() {
    const c = (await api('clients')).clients;
    const act = (mac, action, extra = {}) => async () => { try { await api('clients/action', 'POST', Object.assign({mac, action}, extra)); toast('Tamam'); render(); } catch (e) { toast(e.message); } };
    return card('Bagli istemciler (' + c.length + ')', table(['Cihaz', 'IP / MAC', 'Ag', 'Wi-Fi', 'Trafik', 'Durum', 'Islem'], c.map(x => [
      x.name || '-', h('div', {}, x.ip, h('div', {class: 'mut', textContent: x.mac})), x.network, x.wifi ? `${x.wifi} ${x.signal ?? ''}dBm ${x.rate ?? ''}Mb` : 'kablolu',
      `↓${fmtB(x.down)} ↑${fmtB(x.up)}` + (x.limit ? ` (limit ${x.limit.down_mbps}/${x.limit.up_mbps})` : ''),
      x.blocked ? tag('engelli', 'bad') : x.authed ? tag('yetkili', 'ok') : tag('portalda', 'warn'),
      h('div', {class: 'row'}, h('button', {class: 'b s', textContent: 'At', onclick: act(x.mac, 'kick')}),
        h('button', {class: 'b s', textContent: x.blocked ? 'Engeli kaldir' : 'Engelle', onclick: act(x.mac, x.blocked ? 'unblock' : 'block')}),
        x.authed ? null : h('button', {class: 'b s', textContent: 'Yetkilendir', onclick: act(x.mac, 'authorize', {minutes: 240})}),
        h('button', {class: 'b s', textContent: 'Hiz limiti', onclick: () => { const d = prompt('Indirme limiti Mbit/s (bos = kaldir)', ''); if (d === null) return; const u = prompt('Yukleme limiti Mbit/s', d); act(x.mac, 'limit', {down_mbps: d, up_mbps: u})(); }}))])));
  },

  async skills() {
    const cats = {};
    S.schema.skills.forEach(s => (cats[s.category] = cats[s.category] || []).push(s));
    const out = h('div', {});
    for (const s of S.schema.skills) {
      const cfg = S.cfg.skills[s.name] = Object.assign({}, s.defaults, S.cfg.skills[s.name]);
      if (cfg.enabled === undefined) cfg.enabled = s.enabled;
      const body = h('div', {}, s.fields.map(f => field(f, cfg)));
      const acts = s.actions.filter(a => !['ping', 'traceroute', 'dns', 'wol', 'portscan', 'speedtest', 'start', 'stop', 'list', 'scan', 'optimize'].includes(a));
      out.append(card(s.title, h('div', {class: 'mut', textContent: s.desc}), field(F('enabled', 'Etkin', 'bool'), cfg), body,
        Object.keys(s.status || {}).length ? h('pre', {textContent: JSON.stringify(s.status, null, 1)}) : null,
        acts.map(a => h('button', {class: 'b s', textContent: a, onclick: async () => { toast('Calisiyor...'); try { toast(JSON.stringify(await api(`skill/${s.name}/${a}`, 'POST', {}))); } catch (e) { toast(e.message); } }}))));
    }
    return out;
  },

  async tools() {
    const out = h('pre', {textContent: ''}), host = h('input', {placeholder: 'host / IP', value: '1.1.1.1'});
    const ifsel = h('select', {}, h('option', {value: '', textContent: '(varsayilan rota)'}), S.hw.map(x => h('option', {value: x.name, textContent: x.name})));
    const call = (skill, action, extra = {}) => async () => { out.textContent = 'calisiyor...'; try { const r = await api(`skill/${skill}/${action}`, 'POST', Object.assign({host: host.value, iface: ifsel.value || undefined}, extra)); out.textContent = r.output || JSON.stringify(r, null, 2); } catch (e) { out.textContent = e.message; } };
    const mac = h('input', {placeholder: 'aa:bb:cc:dd:ee:ff'}), capf = h('input', {placeholder: 'tcpdump filtresi (orn. port 53)'});
    const files = h('div', {});
    const loadFiles = async () => { try { const r = await api('skill/capture/list', 'POST', {}); files.replaceChildren(...r.files.map(f => h('div', {}, h('a', {href: '/api/capture/' + f.name, textContent: f.name + ' (' + fmtB(f.size) + ')'})))); } catch {} };
    loadFiles();
    return h('div', {}, card('Ag testleri', h('div', {class: 'row'}, host, ifsel), h('br'), h('div', {class: 'row'},
      ...[['Ping', 'nettools', 'ping'], ['Traceroute', 'nettools', 'traceroute'], ['DNS', 'nettools', 'dns'], ['LAN port tara', 'nettools', 'portscan'], ['Hiz testi (uplink)', 'nettools', 'speedtest']].map(([l, s, a]) => h('button', {class: 'b s', textContent: l, onclick: call(s, a)}))), out),
      card('Wake-on-LAN', h('div', {class: 'row'}, mac, h('button', {class: 'b', textContent: 'Uyandir', onclick: call('nettools', 'wol', {mac: mac.value})}))),
      card('Wi-Fi tarama / kanal optimizasyonu', h('div', {class: 'mut', textContent: 'Arayuzu yukaridan secin (AP calisirken ayni arayuzde tarama basarisiz olabilir).'}), h('div', {class: 'row'},
        h('button', {class: 'b s', textContent: 'Tara', onclick: async () => { out.textContent = 'taraniyor...'; try { const r = await api('skill/wifi_survey/scan', 'POST', {iface: ifsel.value}); out.textContent = r.ok ? 'Onerilen 2.4GHz kanal: ' + r.best_2g + '\n\n' + r.aps.map(a => `${String(a.signal).padStart(6)} dBm  ch${String(a.channel).padEnd(3)} ${a.secure ? '🔒' : '  '} ${a.ssid || '(gizli)'}  ${a.bssid}`).join('\n') : r.error; } catch (e) { out.textContent = e.message; } }}),
        h('button', {class: 'b s', textContent: 'En iyi kanali kaydet', onclick: call('wifi_survey', 'optimize', {iface: ifsel.value})}))),
      card('Paket yakalama', h('div', {class: 'row'}, capf, h('button', {class: 'b', textContent: 'Baslat (30sn)', onclick: async () => { const r = await api('skill/capture/start', 'POST', {iface: ifsel.value || 'any', filter: capf.value, seconds: 30}); toast(r.file); setTimeout(loadFiles, 32000); }}),
        h('button', {class: 'b s', textContent: 'Durdur', onclick: () => api('skill/capture/stop', 'POST', {}).then(loadFiles)})), files));
  },

  async portal() {
    const v = await api('vouchers'), cnt = h('input', {type: 'number', value: 5}), mins = h('input', {type: 'number', value: 60}), dev = h('input', {type: 'number', value: 1});
    return h('div', {}, card('Captive portal', h('div', {class: 'mut', textContent: 'Portal ayarlari Skills > Captive Portal; hangi agin portal kullandigi Aglar sekmesinde. Yonetici, portal sayfasindaki "Yonetici girisi" baglantisindan bu panele girer.'})),
      card('Kuponlar', h('div', {class: 'row'}, h('div', {}, h('label', {textContent: 'Adet'}), cnt), h('div', {}, h('label', {textContent: 'Sure (dk)'}), mins), h('div', {}, h('label', {textContent: 'Cihaz sayisi'}), dev),
        h('button', {class: 'b', textContent: 'Uret', onclick: async () => { await api('vouchers', 'POST', {count: +cnt.value, minutes: +mins.value, max_devices: +dev.value}); render(); }})),
        table(['Kod', 'Sure', 'Kullanim', ''], Object.entries(v).map(([c, e]) => [c, e.minutes + ' dk', e.used_by.length + '/' + e.max_devices, h('button', {class: 'b s', textContent: 'Sil', onclick: async () => { await api('vouchers?code=' + encodeURIComponent(c), 'DELETE'); render(); }})]))));
  },

  async system() {
    const old = h('input', {type: 'password', placeholder: 'mevcut parola'}), nw = h('input', {type: 'password', placeholder: 'yeni parola (min 8)'});
    const doc = h('pre', {textContent: ''}), logs = h('pre', {textContent: ''});
    const sys = S.cfg.system, adm = S.cfg.admin;
    const pwCard = card('Parola', S.must ? h('div', {class: 'warn', textContent: 'Ilk giris: devam etmek icin yonetici parolasini degistirin.'}) : null, h('div', {class: 'row'}, old, nw, h('button', {class: 'b', textContent: 'Degistir', onclick: async () => { try { await api('password', 'POST', {old: old.value, new: nw.value}); toast('Parola degisti, tekrar giris yapin'); S.csrf = null; S.must = false; render(); } catch (e) { toast(e.message); } }})));
    if (S.must) return h('div', {}, pwCard);
    return h('div', {}, pwCard,
      card('Panele hizli erisim', S.status.mdns ? h('div', {class: 'mut', textContent: 'Bu ag icindeyken IP ezberlemeden: http://' + S.status.mdns + '/'}) : h('div', {class: 'mut', textContent: 'mDNS calismiyor; panele bridge IP adresinden erisin.'}),
        h('img', {src: '/api/qr/panel.svg?t=' + Date.now(), style: 'width:150px;height:150px;background:#fff;border-radius:8px;padding:6px', alt: 'Panel QR kodu'})),
      card('Senaryolar (hazir kurulumlar)', S.schema.presets.map(p => h('div', {}, h('b', {textContent: p.title}), h('div', {class: 'mut', textContent: p.desc}),
        h('button', {class: 'b s', textContent: 'Yukle', onclick: async () => { if (!confirm('Arayuz/ag ayarlariniz bu senaryoyla degistirilecek (uygulamadan once kaydedilir). Devam?')) return; try { await api('preset', 'POST', {name: p.name}); S.cfg = null; S.dirty = false; toast('Yuklendi; Guvenli Uygula ile devreye alin'); await refresh(false); S.dirty = true; render(); } catch (e) { toast(e.message); } }}), h('hr')))),
      card('Genel', field(F('hostname', 'Ad'), sys), field(F('country', 'Ulke kodu (Wi-Fi duzenlemesi)'), sys), field(F('domain', 'Yerel alan adi'), sys),
        (() => { const o = {d: sys.dns_servers.join(',')}; return field(F('d', 'Yedek DNS sunuculari'), o, () => { sys.dns_servers = o.d.split(',').map(x => x.trim()).filter(Boolean); }); })(),
        field(F('session_ttl', 'Oturum suresi (sn)', 'int'), adm), field(F('wan_access', 'Yonetim paneline WAN tarafindan erisim', 'bool'), adm), field(F('ssh_from_lan', 'LAN\'dan SSH', 'bool'), adm)),
      card('Uygula', h('button', {class: 'b', textContent: 'Kaydet ve Guvenli Uygula (90sn onay)', onclick: saveApply}), ' ',
        h('button', {class: 'b s', textContent: 'Kaydet ve dogrudan uygula', onclick: async () => { await save(); try { await api('apply', 'POST', {safe: false}); toast('Uygulandi'); refresh(); } catch (e) { toast(e.message); } }})),
      card('Teshis', h('div', {class: 'row'}, h('button', {class: 'b s', textContent: 'Doctor', onclick: async () => { const r = await api('doctor'); doc.textContent = r.checks.map(c => (c.ok ? '[OK ] ' : '[!!!] ') + c.name + (c.ok ? '' : '  -> ' + c.hint)).join('\n'); }}),
        h('button', {class: 'b s', textContent: 'Servis loglari', onclick: async () => { logs.textContent = (await api('logs')).log; }}),
        h('button', {class: 'b s', textContent: 'Yedek indir', onclick: async () => { const b = await api('backup'); const a = h('a', {href: URL.createObjectURL(b), download: 'r1kit-config.json'}); a.click(); }}),
        h('button', {class: 'b d', textContent: 'Yeniden baslat', onclick: () => confirm('Cihaz yeniden baslatilsin mi?') && api('system/reboot', 'POST', {})})), doc, logs));
  },
};

// Canli guncellemeler: /api/events (Server-Sent Events) durumu ~2sn'de bir, donanim
// takilip cikarildiginda ('hotplug') ise aninda gonderir. Baglanti koparsa 3sn sonra
// yeniden dener ve o araliktaki tekrar denemelerde 5sn'lik yoklamaya geri duser.
let sseRetryTimer = null, pollTimer = null;
function setLive(v) { if (S.live !== v) { S.live = v; const el = $('#live'); if (el) { el.className = 'tag ' + (v ? 'ok' : 'bad'); el.textContent = v ? '● canli' : '○ bagli degil'; } } }
function connectSSE() {
  if (!S.csrf || typeof EventSource === 'undefined') return pollFallback();
  stopSSE();
  const es = new EventSource('/api/events');
  es.addEventListener('status', e => { setLive(true); clearInterval(pollTimer); applyStatus(JSON.parse(e.data)); });
  es.addEventListener('hotplug', e => { const ev = JSON.parse(e.data); toast(ev.msg); if (['ifaces', 'dash', 'wan'].includes(S.tab)) render(); });
  es.onerror = () => { setLive(false); es.close(); S.es = null; pollFallback(); sseRetryTimer = setTimeout(connectSSE, 3000); };
  S.es = es;
}
function stopSSE() { if (S.es) { S.es.close(); S.es = null; } clearTimeout(sseRetryTimer); clearInterval(pollTimer); setLive(false); }
function pollFallback() { if (pollTimer) return; pollTimer = setInterval(async () => { if (S.csrf) { try { applyStatus((await api('state')).status); } catch {} } }, 5000); }
async function applyStatus(status) {
  const had = S.status && S.status.pending_confirm > 0;
  S.status = status; banner();
  if (had && !status.pending_confirm) render();
  if (S.tab === 'dash' && !document.hidden) { const m = $('#main'); m && m.replaceChildren(await VIEWS.dash()); }
}
(async () => { try { const s = await api('session'); if (s.logged_in) { S.csrf = s.csrf; S.must = s.must_change; } } catch {} render(); if (S.csrf) connectSSE(); })();
