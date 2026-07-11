// Edit chat: prompt further edits to the current timeline, with undo.
// Reuses globals from app.js: `api`, `currentEditId`, `loadTimeline`.

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

chatForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const prompt = chatInput.value.trim();
  if (!prompt) return;
  if (!currentEditId) {
    alert("Open or generate an edit first, then ask for changes.");
    return;
  }
  // Drop the placeholder if present.
  const empty = chatMessages.querySelector(".chat-empty");
  if (empty) empty.remove();

  chatMessages.appendChild(chatBubble("user", prompt));
  chatInput.value = "";
  chatSend.disabled = true;
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
    await loadTimeline();   // reflect the new timeline in the editor
  } catch (err) {
    thinking.remove();
    const errMsg = chatBubble("assistant", `Couldn't apply that: ${err.message}`);
    errMsg.classList.add("chat-error");
    chatMessages.appendChild(errMsg);
  } finally {
    chatSend.disabled = false;
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
  if (currentEditId !== _lastChatEditId) {
    _lastChatEditId = currentEditId;
    loadChat();
  }
}, 500);
