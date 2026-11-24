/* Desbloqueo de datos cifrados (versión publicada en la nube).
   Si data.js trae window.PANEL_ENC (datos cifrados por publicar.py), pide la contraseña,
   deriva la clave con PBKDF2-SHA256 y descifra con AES-256-GCM, todo dentro del navegador.
   En el Mac, data.js trae window.PANEL_DATA en claro y esto no hace nada. */
(function () {
  const STORE = 'panel-pass';
  let cache = { id: '', key: null };
  const b64 = s => Uint8Array.from(atob(s), c => c.charCodeAt(0));
  const get = () => { try { return localStorage.getItem(STORE) || sessionStorage.getItem(STORE); } catch (_) { return null; } };
  const put = (p, remember) => { try { (remember ? localStorage : sessionStorage).setItem(STORE, p); } catch (_) {} };
  const clear = () => { try { localStorage.removeItem(STORE); sessionStorage.removeItem(STORE); } catch (_) {} };

  async function key(pass, env) {
    const id = pass + '|' + env.salt + '|' + env.iter;
    if (cache.id === id) return cache.key;
    const base = await crypto.subtle.importKey('raw', new TextEncoder().encode(pass), 'PBKDF2', false, ['deriveKey']);
    const k = await crypto.subtle.deriveKey({ name: 'PBKDF2', salt: b64(env.salt), iterations: env.iter, hash: 'SHA-256' },
      base, { name: 'AES-GCM', length: 256 }, false, ['decrypt']);
    cache = { id, key: k };
    return k;
  }
  async function decrypt(env, pass) {
    const plain = await crypto.subtle.decrypt({ name: 'AES-GCM', iv: b64(env.iv) }, await key(pass, env), b64(env.ct));
    let text;
    if (env.z === 'deflate') {
      if (!('DecompressionStream' in window)) throw new Error('compat');
      text = await new Response(new Blob([plain]).stream().pipeThrough(new DecompressionStream('deflate'))).text();
    } else text = new TextDecoder().decode(plain);
    return JSON.parse(text);
  }

  function ask(msg) {
    return new Promise(resolve => {
      let box = document.getElementById('unlockBox');
      if (!box) { box = document.createElement('div'); box.id = 'unlockBox'; document.body.appendChild(box); }
      box.style.cssText = 'position:fixed;inset:0;z-index:100;display:flex;align-items:center;justify-content:center;padding:20px;background:var(--page,#111)';
      box.innerHTML = `<form style="width:100%;max-width:340px;background:var(--surface,#1c1c1c);border:1px solid var(--border,#333);border-radius:16px;padding:22px;color:var(--ink,#eee);font:15px/1.4 system-ui,-apple-system,sans-serif">
        <div style="font-size:20px;font-weight:650;margin-bottom:6px">Panel de Mercado</div>
        <div style="color:var(--ink-2,#aaa);font-size:13.5px;margin-bottom:16px">Los datos están cifrados. Ingresa tu contraseña para verlos en este dispositivo.</div>
        <input id="unlockPass" type="password" autocomplete="current-password" placeholder="Contraseña" required
          style="width:100%;box-sizing:border-box;font:inherit;padding:11px 12px;border-radius:10px;border:1px solid var(--border,#333);background:var(--page,#111);color:inherit">
        <label style="display:flex;gap:8px;align-items:center;margin:12px 0 4px;font-size:13.5px;color:var(--ink-2,#aaa)"><input id="unlockRemember" type="checkbox" checked> Recordar en este dispositivo</label>
        <div id="unlockMsg" style="min-height:20px;font-size:13px;color:#e5484d;margin:4px 0 8px">${msg || ''}</div>
        <button style="width:100%;font:inherit;font-weight:600;padding:11px;border-radius:10px;border:0;background:var(--ink,#eee);color:var(--page,#111);cursor:pointer">Entrar</button></form>`;
      const f = box.querySelector('form');
      setTimeout(() => box.querySelector('#unlockPass').focus(), 50);
      f.addEventListener('submit', e => {
        e.preventDefault();
        box.querySelector('#unlockMsg').textContent = 'Descifrando…';
        resolve({ pass: box.querySelector('#unlockPass').value, remember: box.querySelector('#unlockRemember').checked });
      });
    });
  }
  const hide = () => { const b = document.getElementById('unlockBox'); if (b) b.remove(); };

  async function ready() {
    const env = window.PANEL_ENC;
    if (!env) return true;          // datos en claro (Mac) o ya descifrados
    window.PANEL_ENC = null;
    let pass = get(), remember = true, msg = '';
    for (;;) {
      if (!pass) ({ pass, remember } = await ask(msg));
      try {
        window.PANEL_DATA = await decrypt(env, pass);
        put(pass, remember);
        hide();
        return true;
      } catch (e) {
        if (e && e.message === 'compat') { hide(); throw new Error('Este navegador es muy antiguo para abrir los datos cifrados. Actualiza el sistema o usa otro navegador.'); }
        clear(); pass = null; msg = 'Contraseña incorrecta.';
      }
    }
  }
  function forget() { clear(); location.reload(); }
  window.PanelUnlock = { ready, forget };
})();
