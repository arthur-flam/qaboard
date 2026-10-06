// The stage renders one scene of a video. The recorder (videos/lib/render.py) loads a scene with
// stage.load(scene, timeline), then stage.start() starts the clock (captions), and stage.run() plays the
// scene's own animation. Browser scenes are driven from Python: cursorTo, click, focus...
(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const el = (tag, attrs = {}, html = '') => {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    node.innerHTML = html;
    return node;
  };

  const THEME = {
    background: '#12172a', foreground: '#e3e7f2', cursor: '#b197fc', cursorAccent: '#12172a',
    selectionBackground: '#3b4470',
    black: '#1b2036', red: '#ff6b6b', green: '#69db7c', yellow: '#ffd43b', blue: '#74c0fc',
    magenta: '#da77f2', cyan: '#66d9e8', white: '#dee2e6',
    brightBlack: '#5c6480', brightRed: '#ff8787', brightGreen: '#8ce99a', brightYellow: '#ffe066',
    brightBlue: '#a5d8ff', brightMagenta: '#e599f7', brightCyan: '#99e9f2', brightWhite: '#ffffff',
  };

  const stage = {
    scene: null,
    timeline: { lines: [], duration: 0 },
    t0: null,
    term: null,

    async load(scene, timeline) {
      this.scene = scene;
      this.timeline = timeline || { lines: [], duration: 0 };
      const root = $('#scene');
      root.innerHTML = '';
      root.classList.toggle('windowed', ['terminal', 'browser', 'chat'].includes(scene.type));
      $('#chapter').innerHTML = scene.chapter || '';
      $('#chapter').classList.toggle('on', !!scene.chapter);
      const build = { title: buildTitle, chat: buildChat, terminal: buildTerminal, browser: buildBrowser }[scene.type];
      if (!build) throw new Error(`Unknown scene type ${scene.type}`);
      await document.fonts.ready;
      await build.call(this, root, scene);
      return true;
    },

    // Starts the scene's clock: captions follow the timeline
    start() {
      this.t0 = performance.now();
      this.timeline.lines.forEach((line, i) => {
        setTimeout(() => showCaption(line.text, i), line.start * 1000);
        setTimeout(() => hideCaption(i), line.end * 1000);
      });
      return true;
    },

    elapsed() { return (performance.now() - this.t0) / 1000; },

    // Plays the scene's own animation, and resolves when it's over (and the narration too)
    async run() {
      const s = this.scene;
      if (s.type === 'terminal') await this.replay(s.cast, s.speed || 1);
      if (s.type === 'chat') await this.playChat(s.messages);
      if (s.type === 'title') await sleep((s.hold || 3) * 1000);
      await this.until(this.timeline.duration);
      return true;
    },

    // Waits until the scene's clock reaches t seconds
    async until(t) {
      const remaining = t - this.elapsed();
      if (remaining > 0) await sleep(remaining * 1000);
    },

    // --- Terminal ---------------------------------------------------------
    async replay(cast, speed) {
      const start = performance.now();
      for (const [t, data] of cast.events) {
        const wait = start + (t / speed) * 1000 - performance.now();
        if (wait > 4) await sleep(wait);
        this.term.write(data);
      }
      await sleep((this.scene.hold ?? 1.5) * 1000);
    },

    // --- Chat ---------------------------------------------------------------
    async playChat(messages) {
      const list = $('.messages');
      const typing = $('.typing');
      const start = performance.now();
      for (const m of messages) {
        const due = start + (m.at || 0) * 1000;
        const typingAt = due - 900;
        if (typingAt > performance.now()) await sleep(typingAt - performance.now());
        typing.textContent = `${m.who} is typing…`;
        await sleep(Math.max(0, due - performance.now()));
        typing.textContent = '';
        const node = el('div', { class: 'msg' }, `
          <div class="avatar" style="background:${m.color}">${m.initials}</div>
          <div><div class="who">${m.who}<time>${m.time || ''}</time></div>
          <div class="text">${m.text}</div>
          ${m.attach ? `<div class="attach">${m.attach.map(attachment).join('')}</div>` : ''}</div>`);
        list.appendChild(node);
      }
      await sleep((this.scene.hold || 2) * 1000);
    },

    // --- Browser ------------------------------------------------------------
    // Coordinates: the driver measures elements in the app (the iframe's own pixels) and converts them here to
    // stage pixels, with the app's zoom and the camera (focus) applied, or not ("natural": what focus expects).
    setUrl(url) { $('.url .text').textContent = url; return true; },
    _zoom: null,

    toStage(x, y, natural = false) {
      const frame = $('iframe'), z = this.scene.zoom || 1;
      const origin = offset(frame);
      let px = origin.x + x * z, py = origin.y + y * z;
      if (!natural && this._zoom) {
        const { scale, dx, dy, cx, cy } = this._zoom;
        px = cx + (px - cx) * scale + dx;
        py = cy + (py - cy) * scale + dy;
      }
      return [px, py];
    },

    cursorTo(x, y, ms = 900) {
      const c = $('#cursor');
      c.style.transitionDuration = `${ms}ms, 300ms`;
      c.style.opacity = 1;
      c.style.transform = `translate(${x - 6}px, ${y - 4}px)`;
      return sleep(ms).then(() => true);
    },
    hideCursor() { $('#cursor').style.opacity = 0; return true; },
    ripple(x, y) {
      const r = el('div', { class: 'ripple' });
      r.style.left = `${x}px`; r.style.top = `${y}px`;
      document.body.appendChild(r);
      setTimeout(() => r.remove(), 700);
      return true;
    },

    // Zooms the camera on a point (stage pixels, without zoom), to the middle of the space above the captions.
    // The window keeps covering the screen down to the captions: the point gets as close as the window's edges allow.
    focus(x, y, scale = 1.6, ms = 1100) {
      const w = $('.window');
      const { x: left, y: top } = offset(w);
      const width = w.offsetWidth, height = w.offsetHeight;
      const cx = left + width / 2, cy = top + height / 2;
      const clamp = (d, c, size, lo, hi) => {
        const half = size * scale / 2;
        if (2 * half <= hi - lo) return (lo + hi) / 2 - c;
        return Math.min(lo - c + half, Math.max(hi - c - half, d));
      };
      const dx = clamp((960 - cx) - (x - cx) * scale, cx, width, 0, 1920);
      const dy = clamp((470 - cy) - (y - cy) * scale, cy, height, 0, 940);
      w.style.transitionDuration = `${ms}ms`;
      w.style.transform = `translate(${dx}px, ${dy}px) scale(${scale})`;
      document.body.classList.add('zoomed');
      this._zoom = { scale, dx, dy, cx, cy };
      return sleep(ms).then(() => true);
    },
    unfocus(ms = 900) {
      const w = $('.window');
      w.style.transitionDuration = `${ms}ms`;
      w.style.transform = '';
      document.body.classList.remove('zoomed');
      this._zoom = null;
      return sleep(ms).then(() => true);
    },
  };

  // --- Builders ---------------------------------------------------------------
  function buildTitle(root, s) {
    const card = el('div', { class: 'title' });
    if (s.kicker) card.appendChild(el('div', { class: 'kicker reveal' }, s.kicker));
    const h1 = el('h1', { class: 'reveal' }, s.title);
    h1.style.animationDelay = '.15s';
    card.appendChild(h1);
    if (s.subtitle) {
      const sub = el('div', { class: 'subtitle reveal' }, s.subtitle);
      sub.style.animationDelay = '.55s';
      card.appendChild(sub);
    }
    if (s.next) {
      const next = el('div', { class: 'next reveal' }, s.next);
      next.style.animationDelay = '1.1s';
      card.appendChild(next);
    }
    root.appendChild(card);
    if (s.sparkles) {
      for (let i = 0; i < 14; i++) {
        const star = el('div', { class: 'sparkle' }, '✦');
        star.style.left = `${8 + Math.random() * 84}%`;
        star.style.top = `${12 + Math.random() * 70}%`;
        star.style.fontSize = `${14 + Math.random() * 26}px`;
        star.style.animationDelay = `${Math.random() * 1.6}s`;
        root.appendChild(star);
      }
    }
  }

  function buildChat(root, s) {
    root.appendChild(el('div', { class: 'window chat enter' }, `
      <div class="bar"><div class="dot r"></div><div class="dot y"></div><div class="dot g"></div><div class="label">${s.app || 'Team chat'}</div></div>
      <div class="channel"># ${s.channel || 'algo-team'} <span>· ${s.topic || ''}</span></div>
      <div class="messages"></div>
      <div class="typing" style="padding: 0 30px 18px"></div>`));
  }

  async function buildTerminal(root, s) {
    const win = el('div', { class: 'window terminal enter' }, `
      <div class="bar"><div class="dot r"></div><div class="dot y"></div><div class="dot g"></div><div class="label">${s.title || 'Terminal'}</div></div>
      <div class="screen"></div>`);
    root.appendChild(win);
    const cols = s.cast.width, rows = s.cast.height;
    // Fit the terminal's grid in the window
    const fontSize = Math.floor(Math.min((1640 - 52) / (cols * 0.602), (830 - 52 - 44) / (rows * 1.2)));
    const term = new Terminal({
      cols, rows, fontSize, lineHeight: 1.12, fontFamily: "'JetBrains Mono', 'DejaVu Sans Mono', 'Noto Color Emoji', monospace",
      theme: THEME, allowProposedApi: true, cursorBlink: false, scrollback: 0, convertEol: false,
    });
    const unicode = new Unicode11Addon.Unicode11Addon();
    term.loadAddon(unicode);
    term.unicode.activeVersion = '11';
    term.open($('.screen', win));
    this.term = term;
    await sleep(50);
  }

  function buildBrowser(root, s) {
    const win = el('div', { class: 'window browser enter' }, `
      <div class="bar"><div class="dot r"></div><div class="dot y"></div><div class="dot g"></div>
        <div class="url"><span class="lock">●</span><span class="text">${s.display_url || s.url || ''}</span></div></div>
      <div class="viewport"><iframe src="${s.url || 'about:blank'}" name="app"></iframe></div>`);
    // zoom: the app renders in a smaller viewport, scaled up, so that it reads well in a video
    const z = s.zoom || 1, frame = $('iframe', win);
    frame.style.width = `${100 / z}%`;
    frame.style.height = `${100 / z}%`;
    frame.style.transform = `scale(${z})`;
    // the entrance animation would otherwise keep owning the window's transform (the camera)
    win.addEventListener('animationend', () => win.classList.remove('enter'), { once: true });
    root.appendChild(win);
  }

  // Position in the page without CSS transforms (the camera): where focus and the cursor start from
  function offset(node) {
    let x = 0, y = 0;
    for (; node; node = node.offsetParent) { x += node.offsetLeft; y += node.offsetTop; }
    return { x, y };
  }

  // An image file shows as a thumbnail with its file name, anything else as a file chip
  function attachment(a) {
    const name = a.split('/').pop();
    if (/\.(png|jpe?g|webp|gif)$/i.test(a)) return `<figure><img src="${a.startsWith('/') ? 'file://' + a : a}"><figcaption>${name}</figcaption></figure>`;
    return `<div class="file">${name}</div>`;
  }

  function showCaption(text, id) {
    const span = $('#caption span');
    span.innerHTML = text;
    span.dataset.id = id;
    span.classList.add('on');
  }
  function hideCaption(id) {
    const span = $('#caption span');
    if (span.dataset.id === String(id)) span.classList.remove('on');
  }

  window.stage = stage;
})();
