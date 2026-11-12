(function () {
  // In-app replacements for window.alert/prompt. A packaged webview silently blocks the
  // native dialogs, so the token prompt could never appear and the download failure was
  // invisible (owner rule, 2026-11-13). Escape closes both.
  function overlay(build) {
    return new Promise((resolve) => {
      const host = document.createElement("div");
      host.setAttribute("role", "dialog");
      host.setAttribute("aria-modal", "true");
      host.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,.45);display:flex;align-items:center;justify-content:center;z-index:9999";
      const box = document.createElement("div");
      box.style.cssText = "background:#fff;color:#111;border-radius:10px;padding:20px;min-width:320px;max-width:480px;box-shadow:0 10px 40px rgba(0,0,0,.3);font:14px system-ui";
      host.appendChild(box);
      document.body.appendChild(host);
      const close = (value) => { host.remove(); document.removeEventListener("keydown", onKey); resolve(value); };
      const onKey = (event) => { if (event.key === "Escape") close(null); };
      document.addEventListener("keydown", onKey);
      build(box, close);
    });
  }
  window.khAlert = (message) => overlay((box, close) => {
    const text = document.createElement("p");
    text.textContent = String(message);
    box.appendChild(text);
    const ok = document.createElement("button");
    ok.textContent = "OK";
    ok.style.cssText = "margin-top:12px";
    ok.onclick = () => close(true);
    box.appendChild(ok);
    ok.focus();
  });
  window.khPrompt = (message) => overlay((box, close) => {
    const label = document.createElement("label");
    label.textContent = String(message);
    box.appendChild(label);
    const input = document.createElement("input");
    input.style.cssText = "display:block;width:100%;margin-top:8px;padding:6px";
    box.appendChild(input);
    const row = document.createElement("div");
    row.style.cssText = "display:flex;gap:8px;justify-content:flex-end;margin-top:12px";
    const cancel = document.createElement("button");
    cancel.textContent = "Cancel";
    cancel.onclick = () => close(null);
    const ok = document.createElement("button");
    ok.textContent = "Continue";
    ok.onclick = () => close(input.value);
    row.append(cancel, ok);
    box.appendChild(row);
    input.focus();
  });
  const nativeFetch = window.fetch.bind(window);
  const key = "kh_studio_token";
  window.fetch = async function (input, init) {
    const url = new URL(typeof input === "string" ? input : input.url, location.href);
    if (url.origin !== location.origin) return nativeFetch(input, init);
    const requestInit = Object.assign({}, init || {});
    const headers = new Headers(requestInit.headers || (input instanceof Request ? input.headers : undefined));
    const saved = sessionStorage.getItem(key);
    if (saved) headers.set("X-Studio-Token", saved);
    requestInit.headers = headers;
    let response = await nativeFetch(input, requestInit);
    if (response.status !== 401) return response;
    const entered = await window.khPrompt("This Studio is protected. Enter the fleet token shown in StudioHub's Remote tab.");
    if (!entered) return response;
    sessionStorage.setItem(key, entered.trim());
    headers.set("X-Studio-Token", entered.trim());
    response = await nativeFetch(input, requestInit);
    if (response.status === 401) sessionStorage.removeItem(key);
    return response;
  };
})();

