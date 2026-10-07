// ChipJar client behaviours (progressive enhancement over HTMX).
(function () {
  'use strict';

  // ---- Toasts -------------------------------------------------------------
  const region = document.getElementById('toasts');

  function dismiss(toast) {
    if (!toast || toast.classList.contains('is-leaving')) return;
    toast.classList.add('is-leaving');
    toast.addEventListener('animationend', () => toast.remove(), { once: true });
    // Fallback when animations are disabled.
    setTimeout(() => toast.remove(), 400);
  }

  function armToast(toast) {
    if (toast.dataset.armed) return;
    toast.dataset.armed = '1';
    const ttl = Number(toast.dataset.ttl || 4500);
    let timer = setTimeout(() => dismiss(toast), ttl);
    // Pause while hovered/focused so Undo stays reachable.
    const pause = () => clearTimeout(timer);
    const resume = () => { clearTimeout(timer); timer = setTimeout(() => dismiss(toast), 2000); };
    toast.addEventListener('pointerenter', pause);
    toast.addEventListener('pointerleave', resume);
    toast.addEventListener('focusin', pause);
    toast.addEventListener('focusout', resume);
    toast.querySelector('.toast-close')?.addEventListener('click', () => { clearTimeout(timer); dismiss(toast); });
    const action = toast.querySelector('.toast-action');
    action?.addEventListener('click', () => clearTimeout(timer));
    action?.addEventListener('htmx:afterRequest', () => dismiss(toast));
  }

  if (region) {
    region.querySelectorAll('.toast').forEach(armToast);
    new MutationObserver(() => region.querySelectorAll('.toast:not([data-armed])').forEach(armToast))
      .observe(region, { childList: true });
  }

  // ---- Member card highlight after an out-of-band swap --------------------
  document.body.addEventListener('htmx:oobAfterSwap', (e) => {
    const el = e.detail.target;
    if (el && el.classList.contains('member-card')) {
      el.classList.add('is-flash');
      el.addEventListener('animationend', () => el.classList.remove('is-flash'), { once: true });
    }
  });

  // ---- Ledger: delete animation + swipe-to-delete on touch ----------------
  document.body.addEventListener('htmx:beforeRequest', (e) => {
    const entry = e.target.closest?.('.entry-delete')?.closest('.entry');
    if (entry) entry.classList.add('is-removing');
  });
  document.body.addEventListener('htmx:responseError', () => {
    document.querySelectorAll('.entry.is-removing').forEach((el) => el.classList.remove('is-removing'));
  });

  const touchUI = window.matchMedia('(hover: none)');
  const REVEAL = 96;
  let swipe = null;

  function setOffset(main, px, animate) {
    main.style.transition = animate ? '' : 'none';
    main.style.transform = px ? `translateX(${px}px)` : '';
  }
  function closeOpenRows(except) {
    document.querySelectorAll('.entry-main.is-open').forEach((m) => {
      if (m !== except) { m.classList.remove('is-open'); setOffset(m, 0, true); }
    });
  }

  document.addEventListener('pointerdown', (e) => {
    if (!touchUI.matches) return;
    if (e.target.closest('.entry-delete')) return;
    const main = e.target.closest('.entry-main');
    closeOpenRows(main);
    if (!main) return;
    swipe = { main, x0: e.clientX, y0: e.clientY, base: main.classList.contains('is-open') ? -REVEAL : 0, offset: 0, axis: null };
  });
  document.addEventListener('pointermove', (e) => {
    if (!swipe) return;
    const dx = e.clientX - swipe.x0;
    const dy = e.clientY - swipe.y0;
    if (!swipe.axis && Math.hypot(dx, dy) > 8) swipe.axis = Math.abs(dx) > Math.abs(dy) ? 'x' : 'y';
    if (swipe.axis !== 'x') return;
    swipe.offset = Math.min(0, swipe.base + dx);
    setOffset(swipe.main, swipe.offset, false);
  });
  function endSwipe() {
    if (!swipe) return;
    const { main, offset, axis } = swipe;
    swipe = null;
    if (axis !== 'x') return;
    if (offset < -main.offsetWidth * 0.5) {
      setOffset(main, -main.offsetWidth, true);
      main.closest('.entry')?.querySelector('.entry-delete')?.requestSubmit();
    } else if (offset < -REVEAL / 2) {
      main.classList.add('is-open');
      setOffset(main, -REVEAL, true);
    } else {
      main.classList.remove('is-open');
      setOffset(main, 0, true);
    }
  }
  document.addEventListener('pointerup', endSwipe);
  document.addEventListener('pointercancel', endSwipe);

  // ---- Quick-add form -----------------------------------------------------
  const form = document.getElementById('quickAdd');
  if (!form) return;

  const panels = form.querySelectorAll('.cat-panel');

  function syncCategory() {
    const cat = form.querySelector('input[name=category]:checked')?.value;
    panels.forEach((p) => {
      const active = p.dataset.cat === cat;
      p.hidden = !active;
      // Only the active panel's inputs may submit, so duplicate names don't collide.
      p.querySelectorAll('input, select').forEach((i) => { i.disabled = !active; });
    });
    const notes = form.querySelector('[data-notes]');
    if (notes) notes.hidden = (cat === 'incentive' || cat === 'allowance');
  }
  form.querySelectorAll('input[name=category]').forEach((r) => r.addEventListener('change', syncCategory));
  syncCategory();

  // Preset chips write into the amount box of their panel.
  form.addEventListener('click', (e) => {
    const btn = e.target.closest('.preset');
    if (!btn) return;
    const panel = btn.closest('.cat-panel');
    const amt = panel?.querySelector('input[name=amount]');
    if (amt) { amt.value = btn.dataset.amount; amt.focus(); }
    const note = panel?.querySelector('input[name=activity_type]');
    if (note && btn.dataset.note && !note.value) note.value = btn.dataset.note;
  });

  // "When" chips: Now / Earlier / Pick.
  const whenInput = form.querySelector('input[name=when]');
  const whenWrap = form.querySelector('[data-when-wrap]');
  const pad = (n) => String(n).padStart(2, '0');
  const localValue = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  function setWhenMode(mode) {
    if (!whenInput) return;
    const now = new Date(); now.setSeconds(0, 0);
    if (mode === 'now') { whenInput.value = ''; whenWrap.hidden = true; }
    else if (mode === 'yesterday') { now.setDate(now.getDate() - 1); whenInput.value = localValue(now); whenWrap.hidden = true; }
    else { if (!whenInput.value) whenInput.value = localValue(now); whenWrap.hidden = false; whenInput.focus(); }
    form.querySelectorAll('[data-when]').forEach((b) => b.setAttribute('aria-pressed', b.dataset.when === mode ? 'true' : 'false'));
  }
  form.querySelectorAll('[data-when]').forEach((b) => b.addEventListener('click', () => setWhenMode(b.dataset.when)));
  setWhenMode('now');

  // After a successful full-form add, clear the entry fields but keep member/category.
  form.addEventListener('htmx:afterRequest', (e) => {
    if (e.target !== form || !e.detail.successful) return;
    form.querySelectorAll('.cat-panel input[type=text], .cat-panel input[type=number], input[name=notes]').forEach((i) => { i.value = ''; });
    setWhenMode('now');
    if (sheetMode.matches) { closeSheet(); return; }
    form.querySelector('.cat-panel:not([hidden]) input:not([disabled])')?.focus();
  });

  // One-tap quick items: brief confirmation pulse.
  form.addEventListener('htmx:afterRequest', (e) => {
    const btn = e.target.closest?.('.quick-item');
    if (!btn || !e.detail.successful) return;
    btn.classList.remove('is-sent');
    void btn.offsetWidth;
    btn.classList.add('is-sent');
  });

  // ---- Add sheet (mobile) / scroll-to-form (desktop) ----------------------
  const sheet = document.getElementById('addSection');
  const fab = document.querySelector('.fab');
  const sheetMode = window.matchMedia('(max-width: 719px)');

  function openSheet() {
    if (!sheetMode.matches) {
      sheet.scrollIntoView({ behavior: 'smooth', block: 'start' });
      return;
    }
    document.body.classList.add('sheet-open');
    fab?.setAttribute('aria-expanded', 'true');
    sheet.focus({ preventScroll: true });
  }
  function closeSheet() {
    if (!document.body.classList.contains('sheet-open')) return;
    document.body.classList.remove('sheet-open');
    fab?.setAttribute('aria-expanded', 'false');
    fab?.focus({ preventScroll: true });
  }
  function selectMember(id) {
    const radio = form.querySelector(`input[name=household_member_id][value="${CSS.escape(id)}"]`);
    if (radio && !radio.checked) {
      radio.checked = true;
      radio.dispatchEvent(new Event('change', { bubbles: true }));
    }
  }

  document.addEventListener('click', (e) => {
    const cardAdd = e.target.closest('[data-quick-member]');
    if (cardAdd) { selectMember(cardAdd.dataset.quickMember); openSheet(); return; }
    if (e.target.closest('[data-open-sheet]')) { openSheet(); return; }
    if (e.target.closest('[data-close-sheet]')) closeSheet();
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeSheet(); });
  sheetMode.addEventListener('change', () => { if (!sheetMode.matches) closeSheet(); });
})();

// ---- Spinner wheel ----------------------------------------------------------
(function () {
  'use strict';
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
  const SPIN_MS = 4800;
  // Cumulative rotation survives panel re-renders so the wheel never snaps back.
  let rotation = 0;
  let spinning = false;

  const wheelEl = () => document.querySelector('#spinnerPanel .wheel');

  function applyRotation(animate) {
    const wheel = wheelEl();
    if (!wheel) return;
    wheel.style.transition = animate && !reduced.matches ? `transform ${SPIN_MS}ms cubic-bezier(0.12, 0.84, 0.08, 1)` : 'none';
    wheel.style.transform = `rotate(${rotation}deg)`;
  }

  function confetti(stage, colors) {
    const canvas = stage.querySelector('.confetti');
    if (!canvas || reduced.matches) return;
    const ctx = canvas.getContext('2d');
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * devicePixelRatio;
    canvas.height = rect.height * devicePixelRatio;
    ctx.scale(devicePixelRatio, devicePixelRatio);
    const cx = rect.width / 2, cy = rect.height / 2;
    const bits = Array.from({ length: 140 }, () => {
      const a = Math.random() * Math.PI * 2, v = 6 + Math.random() * 9;
      return { x: cx, y: cy, vx: Math.cos(a) * v, vy: Math.sin(a) * v - 6, r: Math.random() * Math.PI, vr: (Math.random() - 0.5) * 0.3,
               w: 6 + Math.random() * 6, h: 3 + Math.random() * 4, c: colors[Math.floor(Math.random() * colors.length)] };
    });
    const t0 = performance.now();
    (function frame(t) {
      const k = (t - t0) / 1800;
      ctx.clearRect(0, 0, rect.width, rect.height);
      if (k >= 1) return;
      ctx.globalAlpha = 1 - Math.max(0, k - 0.6) / 0.4;
      bits.forEach((b) => {
        b.vy += 0.28; b.vx *= 0.985; b.x += b.vx; b.y += b.vy; b.r += b.vr;
        ctx.save(); ctx.translate(b.x, b.y); ctx.rotate(b.r); ctx.fillStyle = b.c; ctx.fillRect(-b.w / 2, -b.h / 2, b.w, b.h); ctx.restore();
      });
      requestAnimationFrame(frame);
    })(t0);
  }

  function reveal(outcome) {
    outcome.classList.remove('is-pending');
    outcome.classList.add('is-revealed');
    spinning = false;
    document.querySelector('#spinnerPanel .wheel-hub')?.classList.remove('is-spinning');
  }

  function startSpin(outcome) {
    const wheel = wheelEl();
    const stage = wheel?.closest('.wheel-stage');
    const labels = JSON.parse(outcome.dataset.labels || '[]');
    const segs = wheel ? wheel.querySelectorAll('.wheel-label') : [];
    const idx = Number(outcome.dataset.winnerIdx);
    if (!wheel || segs.length !== labels.length || !labels.length) {
      // Picks changed under us: skip the animation and resync the wheel.
      htmx.ajax('GET', `/spin/${document.getElementById('spinnerPanel')?.dataset.spinnerId}`, { target: '#spinnerPanel', swap: 'outerHTML' });
      return;
    }
    spinning = true;
    outcome.classList.add('is-pending');
    segs.forEach((s) => s.classList.remove('is-winner'));
    stage.querySelector('.wheel-hub')?.classList.add('is-spinning');

    // Land the pointer (12 o'clock) inside the winning slice, slightly off-centre for realism.
    const size = 360 / labels.length;
    const mid = idx * size + size / 2 + (Math.random() - 0.5) * size * 0.7;
    const target = ((360 - mid) % 360 + 360) % 360;
    const delta = ((target - (rotation % 360)) % 360 + 360) % 360;
    rotation += (5 + Math.floor(Math.random() * 2)) * 360 + delta;

    const finish = () => {
      wheel.removeEventListener('transitionend', finish);
      clearTimeout(timer);
      segs[idx]?.classList.add('is-winner');
      confetti(stage, ['#f2b134', '#1b998b', '#6c5ce7', '#e2559c', '#d9643a', '#3b82c4', '#ffffff']);
      navigator.vibrate?.([30, 40, 60]);
      reveal(outcome);
      outcome.scrollIntoView({ behavior: reduced.matches ? 'auto' : 'smooth', block: 'nearest' });
    };
    const timer = setTimeout(finish, (reduced.matches ? 0 : SPIN_MS) + 150);
    wheel.addEventListener('transitionend', finish);
    requestAnimationFrame(() => applyRotation(true));
  }

  function afterSwap() {
    applyRotation(false);
    const outcome = document.getElementById('spinOutcome');
    if (outcome && outcome.dataset.winnerIdx !== undefined && !outcome.dataset.armed) {
      outcome.dataset.armed = '1';
      startSpin(outcome);
    }
  }
  // afterSettle (not afterSwap): htmx's settle step rewrites class attributes on swapped elements.
  document.body.addEventListener('htmx:afterSettle', (e) => {
    const id = e.detail.target?.id;
    if (id === 'spinnerPanel' || id === 'spinOutcome') afterSwap();
    if (id === 'moviePickerBody') {
      const dlg = document.getElementById('moviePicker');
      if (dlg && !dlg.open) dlg.showModal();
      dlg?.querySelector('input[type=search]')?.focus();
    }
  });
  // Don't let a pick re-render (or a double tap) yank the wheel mid-spin.
  document.body.addEventListener('htmx:beforeRequest', (e) => {
    const id = e.detail.target?.id;
    if (spinning && (id === 'spinnerPanel' || id === 'spinOutcome')) e.preventDefault();
  });

  // Enter/Tab out of a pick box saves it (form change/submit); then hop to the next empty box.
  let advanceFrom = null;
  document.addEventListener('keydown', (e) => {
    const input = e.target.closest?.('.pick-form input[name=label]');
    if (input && (e.key === 'Enter' || (e.key === 'Tab' && !e.shiftKey)) && input.value.trim()) advanceFrom = input.id;
  });
  document.body.addEventListener('htmx:afterSettle', (e) => {
    if (e.detail.target?.id !== 'spinnerPanel' || !advanceFrom) return;
    const from = advanceFrom;
    advanceFrom = null;
    if (document.activeElement && document.activeElement !== document.body) return;
    const inputs = [...document.querySelectorAll('.pick-form input[name=label]')];
    const ids = [...document.querySelectorAll('.pick-row')].map((r) => r.querySelector('input[name=label]')?.id || r.dataset.pickId);
    const start = ids.indexOf(from);
    const next = inputs.find((i) => ids.indexOf(i.id) > start) || inputs[0];
    next?.focus();
  });

  // Movie picker dialog: close on X, backdrop click, or after a pick lands.
  document.addEventListener('click', (e) => {
    const dlg = document.getElementById('moviePicker');
    if (!dlg?.open) return;
    if (e.target.closest('[data-close-picker]') || e.target === dlg) dlg.close();
  });
  document.body.addEventListener('htmx:afterRequest', (e) => {
    const dlg = document.getElementById('moviePicker');
    if (dlg?.open && e.detail.successful && e.target.closest?.('.movie, .place')) dlg.close();
  });

  // ---- Restaurant ABC ------------------------------------------------------
  // "Choose" on an idea fills the log form; typing a different name unlinks the Google place.
  document.addEventListener('click', (e) => {
    const btn = e.target.closest?.('[data-abc-fill]');
    const form = document.getElementById('abcForm');
    if (!btn || !form) return;
    const data = JSON.parse(btn.dataset.abcFill);
    for (const [key, value] of Object.entries(data)) {
      if (form.elements[key]) form.elements[key].value = value || '';
    }
    form.classList.remove('is-filled');
    void form.offsetWidth;
    form.classList.add('is-filled');
    form.scrollIntoView({ behavior: reduced.matches ? 'auto' : 'smooth', block: 'center' });
    form.elements.visited_on?.focus({ preventScroll: true });
  });
  document.addEventListener('input', (e) => {
    if (e.target.matches?.('#abcForm [name=name]')) e.target.form.elements.place_id.value = '';
  });
  // After picking a letter far down the board (or logging one), bring the letter card back into view.
  document.body.addEventListener('htmx:afterSettle', (e) => {
    if (e.detail.target?.id !== 'abcPanel') return;
    const card = document.querySelector('.abc-letter');
    if (card && card.getBoundingClientRect().top < 0) card.scrollIntoView({ behavior: reduced.matches ? 'auto' : 'smooth', block: 'start' });
  });
})();
