
// Injects a chat panel on YouTube watch pages and talks to the backend
// through background.js.

(() => {
  if (window.__ytragLoaded) return;
  window.__ytragLoaded = true;

  const state = {
    videoId: null,
    indexed: false,
    busy: false,
    history: [], // [{role, content}] for the current video
  };

  // ---------- helpers ----------
  const getVideoId = () => new URLSearchParams(location.search).get("v");

  const api = (path, body) =>
    new Promise((resolve) => {
      chrome.runtime.sendMessage({ type: "api", path, body }, (res) => {
        if (chrome.runtime.lastError) {
          resolve({ ok: false, data: { detail: chrome.runtime.lastError.message } });
        } else {
          resolve(res);
        }
      });
    });

  const el = (tag, attrs = {}, text = "") => {
    const node = document.createElement(tag);
    Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, v));
    if (text) node.textContent = text;
    return node;
  };

  // ---------- UI ----------
  const fab = el("button", { id: "ytrag-fab", title: "Chat with this video" }, "💬");
  const panel = el("div", { id: "ytrag-panel" });

  const head = el("div", { id: "ytrag-head" });
  head.appendChild(el("span", {}, "Chat with video"));

  const clearBtn = el("button", { title: "Clear chat" }, "Clear");
  head.appendChild(clearBtn);

  const status = el("div", { id: "ytrag-status" }, "");
  const msgs = el("div", { id: "ytrag-msgs" });

  const form = el("form", { id: "ytrag-form" });
  const input = el("input", {
    id: "ytrag-input",
    placeholder: "Ask about this video...",
    autocomplete: "off"
  });

  const send = el("button", {
    id: "ytrag-send",
    type: "submit"
  }, "Send");

  form.append(input, send);
  panel.append(head, status, msgs, form);
  document.body.append(fab, panel);

  // ---------- message rendering ----------
  function addMessage(text, kind, sources) {
    const box = el("div", { class: `ytrag-msg ytrag-${kind}` });

    // Render Markdown only for bot responses
    if (kind === "bot" && window.marked && window.DOMPurify) {
      const content = el("div", { class: "ytrag-markdown" });

      const html = window.marked.parse(text, {
        gfm: true,
        breaks: true
      });

      content.innerHTML = window.DOMPurify.sanitize(html);
      box.appendChild(content);
    } else {
      box.appendChild(document.createTextNode(text));
    }

    // Video source timestamps
    if (sources && sources.length) {
      const row = el("div", { class: "ytrag-sources" });

      sources.forEach((s) => {
        const chip = el(
          "button",
          {
            class: "ytrag-chip",
            type: "button",
            title: s.text
          },
          `▶ ${s.time}`
        );

        chip.addEventListener("click", () => {
          const video = document.querySelector("video");

          if (video) {
            video.currentTime = s.start;
            video.play();
          }
        });

        row.appendChild(chip);
      });

      box.appendChild(row);
    }

    msgs.appendChild(box);
    msgs.scrollTop = msgs.scrollHeight;

    return box;
  }

  function setBusy(busy) {
    state.busy = busy;
    input.disabled = busy;
    send.disabled = busy;
  }

  // ---------- indexing ----------
  async function ensureIndexed() {
    if (state.indexed || !state.videoId) return state.indexed;

    setBusy(true);
    status.textContent = "Indexing transcript... (first time can take a bit)";

    const res = await api("/index", {
      video_id: state.videoId
    });

    if (res.ok) {
      state.indexed = true;
      status.textContent = `Ready - ${res.data.chunks} chunks indexed`;
    } else {
      status.textContent = "Not ready";
      addMessage(res.data.detail || "Indexing failed.", "err");
    }

    setBusy(false);
    return state.indexed;
  }

  // ---------- chat ----------
  async function ask(question) {
    if (!state.videoId) {
      return addMessage("Open a YouTube video first.", "err");
    }

    addMessage(question, "user");

    if (!(await ensureIndexed())) return;

    setBusy(true);

    const thinking = addMessage("Thinking...", "bot");

    const res = await api("/chat", {
      video_id: state.videoId,
      question,
      history: state.history,
    });

    thinking.remove();

    if (res.ok) {
      addMessage(res.data.answer, "bot", res.data.sources);

      state.history.push({
        role: "user",
        content: question
      });

      state.history.push({
        role: "assistant",
        content: res.data.answer
      });

    } else {
      addMessage(res.data.detail || "Something went wrong.", "err");
    }

    setBusy(false);
    input.focus();
  }

  // ---------- events ----------
  fab.addEventListener("click", () => {
    panel.classList.toggle("open");

    if (panel.classList.contains("open")) {
      input.focus();
      ensureIndexed();
    }
  });

  form.addEventListener("submit", (e) => {
    e.preventDefault();

    const q = input.value.trim();

    if (!q || state.busy) return;

    input.value = "";
    ask(q);
  });

  clearBtn.addEventListener("click", () => {
    state.history = [];
    msgs.textContent = "";
  });

  // ---------- YouTube navigation ----------
  function onVideoChange() {
    const id = getVideoId();
    const onWatch = location.pathname === "/watch" && id;

    fab.style.display = onWatch ? "block" : "none";

    if (!onWatch) {
      panel.classList.remove("open");
      return;
    }

    if (id !== state.videoId) {
      state.videoId = id;
      state.indexed = false;
      state.history = [];

      msgs.textContent = "";
      status.textContent = "";

      if (panel.classList.contains("open")) {
        ensureIndexed();
      }
    }
  }

  window.addEventListener("yt-navigate-finish", onVideoChange);

  onVideoChange();

})();
