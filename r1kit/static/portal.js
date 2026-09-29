(async () => {
  const $ = id => document.getElementById(id);
  const api = async (p, body) => {
    const r = await fetch('/api/portal/' + p, {method: body ? 'POST' : 'GET', headers: {'Content-Type': 'application/json'}, body: body ? JSON.stringify(body) : undefined});
    const j = await r.json(); if (!r.ok) throw new Error(j.error || 'hata'); return j;
  };
  const mk = (t, a = {}, ...c) => { const e = Object.assign(document.createElement(t), a); e.append(...c); return e; };
  const info = await api('info');
  $('t').textContent = info.title;
  const f = $('form');
  if (info.authed) { f.append(mk('p', {textContent: 'Baglisiniz. Internet erisiminiz aktif.'})); return; }
  const done = () => { $('msg').textContent = 'Baglandiniz! Internet erisiminiz aktif.'; f.replaceChildren(); };
  const go = (p, body) => api(p, body).then(done).catch(e => { $('msg').textContent = e.message; });
  if (info.mode === 'click') {
    const cb = mk('input', {type: 'checkbox', id: 'ag'});
    f.append(mk('p', {textContent: info.terms}), mk('label', {}, cb, ' Kabul ediyorum'),
      mk('button', {className: 'b', textContent: 'Baglan', onclick: () => go('accept', {agree: cb.checked})}));
  } else if (info.mode === 'voucher' || info.mode === 'password') {
    const inp = mk('input', {placeholder: info.mode === 'voucher' ? 'Kupon kodu' : 'Parola', type: info.mode === 'voucher' ? 'text' : 'password'});
    f.append(inp, mk('br'), mk('button', {className: 'b', textContent: 'Baglan', onclick: () =>
      go(info.mode, info.mode === 'voucher' ? {code: inp.value} : {password: inp.value})}));
  } else { f.append(mk('p', {textContent: 'Bu ag yalnizca yetkili kullanicilar icindir.'})); }
})();
