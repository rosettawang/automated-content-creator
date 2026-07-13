// Tiny toast utility shared by every panel (editor / library / campaigns) and the
// unified /studio document. Inline-styled on purpose so it can't collide with any
// panel's CSS. Exposes window.showToast(message, { type, duration }).
(function () {
  if (window.showToast) return; // define once, even if loaded on several panels

  const COLORS = {
    error: { bg: "#3a1f1f", border: "#7a4a4a", fg: "#f0c0c0" },
    warn:  { bg: "#332b1a", border: "#7a6a3a", fg: "#f0dfa8" },
    info:  { bg: "#232323", border: "#4a4a4a", fg: "#e6e6e6" },
    success: { bg: "#1f2f22", border: "#4a6a4a", fg: "#bfe6c6" },
  };

  function stack() {
    let el = document.getElementById("toast-stack");
    if (!el) {
      el = document.createElement("div");
      el.id = "toast-stack";
      Object.assign(el.style, {
        position: "fixed", right: "1rem", bottom: "1rem", zIndex: "10000",
        display: "flex", flexDirection: "column", gap: "0.5rem",
        maxWidth: "min(380px, 90vw)", pointerEvents: "none",
      });
      document.body.appendChild(el);
    }
    return el;
  }

  window.showToast = function (message, opts = {}) {
    const c = COLORS[opts.type] || COLORS.info;
    const t = document.createElement("div");
    t.textContent = message;
    Object.assign(t.style, {
      background: c.bg, border: `1px solid ${c.border}`, color: c.fg,
      borderRadius: "8px", padding: "0.6rem 0.8rem", fontSize: "0.82rem",
      lineHeight: "1.35", boxShadow: "0 6px 20px rgba(0,0,0,0.45)",
      cursor: "pointer", pointerEvents: "auto", opacity: "0",
      transform: "translateY(6px)", transition: "opacity .15s, transform .15s",
      fontFamily: "-apple-system, sans-serif",
    });
    stack().appendChild(t);
    requestAnimationFrame(() => { t.style.opacity = "1"; t.style.transform = "none"; });

    let done = false;
    const dismiss = () => {
      if (done) return; done = true;
      t.style.opacity = "0"; t.style.transform = "translateY(6px)";
      setTimeout(() => t.remove(), 180);
    };
    t.addEventListener("click", dismiss);
    // Errors linger longer (8s) since they're actionable; others 4s. 0 = sticky.
    const ms = opts.duration != null ? opts.duration : (opts.type === "error" ? 8000 : 4000);
    if (ms) setTimeout(dismiss, ms);
    return dismiss;
  };
})();
