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
    setUrl(url) { $('.url .text').textContent = url; return true; },
    frameBox() { const r = $('iframe').getBoundingClientRect(); return { x: r.x, y: r.y, width: r.width, height: r.height }; },

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

    // Zooms the window so that a point (page coordinates, before zoom) comes to the center
    focus(x, y, scale = 1.6, ms = 1100) {
      const w = $('.window');
      w.style.transitionDuration = `${ms}ms`;
      const box = w.getBoundingClientRect();
      // undo the current transform to get the window's natural position
      const natural = { x: box.x, y: box.y, width: box.width, height: box.height };
      if (this._zoom) {
        natural.width /= this._zoom.scale; natural.height /= this._zoom.scale;
        natural.x = 960 - natural.width / 2; natural.y = 540 - natural.height / 2;
      }
      const ox = x - natural.x, oy = y - natural.y;
      const dx = (natural.width / 2 - ox) * scale, dy = (natural.height / 2 - oy) * scale;
      w.style.transformOrigin = '50% 50%';
      w.style.transform = `translate(${dx}px, ${dy}px) scale(${scale})`;
      this._zoom = { scale };
      return sleep(ms).then(() => true);
    },
    unfocus(ms = 900) {
      const w = $('.window');
      w.style.transitionDuration = `${ms}ms`;
      w.style.transform = '';
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
      cols, rows, fontSize, lineHeight: 1.12, fontFamily: "'JetBrains Mono', 'Noto Color Emoji', monospace",
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
    root.appendChild(el('div', { class: 'window browser enter' }, `
      <div class="bar"><div class="dot r"></div><div class="dot y"></div><div class="dot g"></div>
        <div class="url"><span class="lock">●</span><span class="text">${s.display_url || s.url || ''}</span></div></div>
      <iframe src="${s.url || 'about:blank'}" name="app"></iframe>`));
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
