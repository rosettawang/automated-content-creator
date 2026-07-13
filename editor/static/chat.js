// Edit chat: prompt further edits to the current timeline, with undo.
// Reuses globals from app.js: `api`, `currentEditId`, `loadTimeline`.

// ---- Verify media: re-check files on disk, relink moved ones by content hash ----
(function () {
  const btn = document.getElementById("verify-media-btn");
  const status = document.getElementById("verify-media-status");
  if (!btn || !status) return;

  btn.addEventListener("click", async () => {
    btn.disabled = true;
    status.textContent = "Checking files on disk…";
    try {
      const started = await fetch("/api/media/verify", { method: "POST" }).then((r) => r.json());
      if (!started.job_id) throw new Error(started.error || "could not start");
      for (;;) {
        const job = await fetch(`/api/import-jobs/${started.job_id}`).then((r) => r.json());
        if (job.total) {
          const phase = job.phase === "relinking" ? "relinking moved files" : "checking";
          status.textContent = `${phase}: ${job.done}/${job.total}`;
        }
        if (job.finished) {
          const r = (job.results && job.results[0]) || {};
          const miss = (r.missing || []).length;
          const bits = [`${r.present || 0} present`];
          if (r.relocated) bits.push(`${r.relocated} relinked`);
          bits.push(`${miss} missing`);
          status.textContent = bits.join(" · ") +
            (miss ? ` — ${(r.missing).map((m) => m.file_stem).join(", ")}` : "");
          status.classList.toggle("has-missing", miss > 0);
          // Reflect any relinks/missing state in the open edit + source bin.
          if (typeof loadTimeline === "function" && currentEditId) loadTimeline();
          if (typeof loadClips === "function") {
            const s = document.getElementById("search");
            loadClips(s ? s.value : "");
          }
          break;
        }
        await new Promise((res) => setTimeout(res, 700));
      }
    } catch (err) {
      status.textContent = `Error: ${err.message}`;
    } finally {
      btn.disabled = false;
    }
  });
})();

// ---- vertical sliders: drag the bars to resize Program / Edit Chat / Timeline ----
// Each bar adjusts the height of the panel BELOW it (chat, timeline); the program
// monitor takes whatever space remains. Heights persist per machine.
(function () {
  // Legacy cleanup: the chat used to be a side column sized by --chat-w.
  localStorage.removeItem("editor.chatWidth");

  function makeVResizer(resizerId, panelId, cssVar, key, min, max) {
    const resizer = document.getElementById(resizerId);
    const panel = document.getElementById(panelId);
    if (!resizer || !panel) return;

    const clamp = (h) => Math.max(min, Math.min(max, h));
    const setHeight = (h) => document.documentElement.style.setProperty(cssVar, `${clamp(h)}px`);

    const saved = parseFloat(localStorage.getItem(key));
    if (!isNaN(saved)) setHeight(saved);

    let dragging = false, startY = 0, startH = 0;
    resizer.addEventListener("mousedown", (e) => {
      e.preventDefault();
      dragging = true;
      startY = e.clientY;
      startH = panel.getBoundingClientRect().height;
      resizer.classList.add("dragging");
      document.body.classList.add("row-resizing");
    });
    window.addEventListener("mousemove", (e) => {
      if (!dragging) return;
      // The bar sits ABOVE its panel: dragging up (clientY decreases) grows the panel.
      setHeight(startH + (startY - e.clientY));
    });
    window.addEventListener("mouseup", () => {
      if (!dragging) return;
      dragging = false;
      resizer.classList.remove("dragging");
      document.body.classList.remove("row-resizing");
      const h = panel.getBoundingClientRect().height;
      localStorage.setItem(key, String(Math.round(h)));
    });
    // Double-click resets to the default height.
    resizer.addEventListener("dblclick", () => {
      document.documentElement.style.removeProperty(cssVar);
      localStorage.removeItem(key);
    });
  }

  makeVResizer("chat-resizer", "chat-panel", "--chat-h", "editor.chatHeight", 110, 600);
  makeVResizer("timeline-resizer", "timeline-region", "--timeline-h", "editor.timelineHeight", 90, 520);
})();

const chatMessages = document.getElementById("chat-messages");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const chatSend = document.getElementById("chat-send");
const chatUndo = document.getElementById("chat-undo");

function chatBubble(role, text) {
  const div = document.createElement("div");
  div.className = `chat-msg chat-${role}`;
  div.textContent = text;
  return div;
}

function clearChatView() {
  chatMessages.innerHTML =
    '<div class="chat-empty">Ask for changes to this edit — e.g. “tighten it to 20 seconds”, ' +
    "“open on the butterfly shot”, “drop the machinery clips”. Each request can be undone.</div>";
}

function scrollChatDown() {
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

// Load the transcript + undo availability for the current edit.
async function loadChat() {
  if (!currentEditId) { clearChatView(); chatUndo.disabled = true; return; }
  try {
    const data = await api(`/api/edits/${currentEditId}/chat`);
    chatMessages.innerHTML = "";
    if (!data.messages.length) {
      clearChatView();
    } else {
      data.messages.forEach((m) => chatMessages.appendChild(chatBubble(m.role, m.content)));
    }
    chatUndo.disabled = !data.can_undo;
    scrollChatDown();
  } catch {
    clearChatView();
    chatUndo.disabled = true;
  }
}

// True while a send is in flight. Guards against double-submit (Enter + click) and
// tells the chat poller to leave the DOM alone so it can't wipe the exchange.
let chatSending = false;

chatForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  if (chatSending) return;                         // already sending
  const prompt = chatInput.value.trim();           // capture up front, DOM-independent
  if (!prompt) return;
  if (!currentEditId) {
    alert("Open or generate an edit first, then ask for changes.");
    return;                                        // input untouched — text preserved
  }

  chatSending = true;
  chatSend.disabled = true;
  chatInput.disabled = true;                       // pending; DON'T clear yet

  // Drop the placeholder if present.
  const empty = chatMessages.querySelector(".chat-empty");
  if (empty) empty.remove();
  chatMessages.appendChild(chatBubble("user", prompt));
  const thinking = chatBubble("assistant", "Editing…");
  thinking.classList.add("chat-thinking");
  chatMessages.appendChild(thinking);
  scrollChatDown();

  try {
    const r = await api(`/api/edits/${currentEditId}/chat`, {
      method: "POST",
      body: JSON.stringify({ prompt }),
    });
    thinking.remove();
    chatMessages.appendChild(chatBubble("assistant", r.reply));
    chatUndo.disabled = !r.can_undo;
    chatInput.value = "";                          // clear ONLY after acknowledged
    await loadTimeline();   // reflect the new timeline in the editor
  } catch (err) {
    thinking.remove();
    const errMsg = chatBubble("assistant", `Couldn't apply that: ${err.message}`);
    errMsg.classList.add("chat-error");
    chatMessages.appendChild(errMsg);
    // Leave the prompt in the box so the user can retry without retyping.
  } finally {
    chatSending = false;
    chatSend.disabled = false;
    chatInput.disabled = false;
    chatInput.focus();
    scrollChatDown();
  }
});

// Enter to send, Shift+Enter for a newline.
chatInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    chatForm.requestSubmit();
  }
});

chatUndo.addEventListener("click", async () => {
  if (!currentEditId) return;
  chatUndo.disabled = true;
  try {
    const r = await api(`/api/edits/${currentEditId}/undo`, { method: "POST" });
    await loadTimeline();
    await loadChat();   // transcript + undo state are trimmed server-side
  } catch (err) {
    chatUndo.disabled = false;
    alert(`Undo failed: ${err.message}`);
  }
});

// Refresh the chat whenever the active edit changes. app.js owns the edit selector,
// so poll currentEditId for changes rather than intercepting its handlers.
let _lastChatEditId = undefined;
setInterval(() => {
  if (chatSending) return;   // never rebuild the transcript mid-send
  if (currentEditId !== _lastChatEditId) {
    _lastChatEditId = currentEditId;
    loadChat();
  }
}, 500);
