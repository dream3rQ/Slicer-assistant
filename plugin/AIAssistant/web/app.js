// Temporary chat UI. Talks to the plugin via fetch + polling of /api/events.
(function () {
  "use strict";
  var H = { "Content-Type": "application/json", "X-STE-AI": "1" };
  var $ = function (id) { return document.getElementById(id); };
  var chat = $("chat"), input = $("input"), chips = $("chips");
  var lastSeq = 0, sessionId = null, busy = false, pending = [], toolNodes = {}, progressNode = null, thinkingNode = null;

  function api(method, path, body) {
    return fetch(path, { method: method, headers: H, body: body ? JSON.stringify(body) : undefined })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (data) {
          if (!r.ok) throw new Error(data.error || ("HTTP " + r.status));
          return data;
        });
      });
  }

  // ---------- rendering
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }

  function inline(s) {
    return esc(s)
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
      .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<i>$2</i>");
  }

  function markdown(src) {
    var lines = String(src || "").replace(/\r/g, "").split("\n"), out = [], i = 0;
    while (i < lines.length) {
      var line = lines[i];
      if (/^\s*\|/.test(line) && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
        var rows = [];
        while (i < lines.length && /^\s*\|/.test(lines[i])) { rows.push(lines[i]); i++; }
        var cells = function (r) { return r.trim().replace(/^\||\|$/g, "").split("|").map(function (c) { return c.trim(); }); };
        var html = "<table><thead><tr>" + cells(rows[0]).map(function (c) { return "<th>" + inline(c) + "</th>"; }).join("") + "</tr></thead><tbody>";
        rows.slice(2).forEach(function (r) { html += "<tr>" + cells(r).map(function (c) { return "<td>" + inline(c) + "</td>"; }).join("") + "</tr>"; });
        out.push(html + "</tbody></table>");
        continue;
      }
      if (/^\s*[-*]\s+/.test(line) || /^\s*\d+[.)]\s+/.test(line)) {
        var ordered = /^\s*\d+[.)]\s+/.test(line), items = [];
        while (i < lines.length && (/^\s*[-*]\s+/.test(lines[i]) || /^\s*\d+[.)]\s+/.test(lines[i]))) {
          items.push("<li>" + inline(lines[i].replace(/^\s*([-*]|\d+[.)])\s+/, "")) + "</li>"); i++;
        }
        out.push((ordered ? "<ol>" : "<ul>") + items.join("") + (ordered ? "</ol>" : "</ul>"));
        continue;
      }
      var h = /^(#{1,4})\s+(.*)$/.exec(line);
      if (h) { out.push("<h4>" + inline(h[2]) + "</h4>"); i++; continue; }
      if (line.trim()) out.push("<p>" + inline(line) + "</p>");
      i++;
    }
    return out.join("");
  }

  function add(node) {
    var empty = $("empty"); if (empty) empty.remove();
    chat.appendChild(node);
    chat.scrollTop = chat.scrollHeight;
    return node;
  }
  function el(tag, cls, html) { var n = document.createElement(tag); if (cls) n.className = cls; if (html != null) n.innerHTML = html; return n; }
  function short(obj, max) {
    var s = typeof obj === "string" ? obj : JSON.stringify(obj);
    max = max || 140;
    return s && s.length > max ? s.slice(0, max) + "…" : (s || "");
  }
  function summarize(name, result) {
    if (!result) return "";
    if (result.error) return "ошибка: " + result.error;
    if (name === "slice" && result.print_time) return result.print_time + " · " + (result.total_weight_g != null ? result.total_weight_g : (result.material_weight_g || []).join("+")) + " г";
    if (name === "set_settings" && result.results) return result.results.map(function (r) { return r.error ? r.key + ": ошибка" : r.key + "=" + r.value + (r.unit ? " " + r.unit : "") + (r.warning ? " ⚠" : ""); }).join(", ");
    if (name === "set_material") return result.material_name || "";
    if (name === "set_print_mode") return result.label || result.print_mode || "";
    if (name === "save_results") return result.folder || "";
    if (result.size_mm) return result.size_mm.x + " × " + result.size_mm.y + " × " + result.size_mm.z + " мм";
    if (result.loaded) return result.loaded.map(function (o) { return o.name + " " + o.size_mm.x + "×" + o.size_mm.y + "×" + o.size_mm.z + " мм"; }).join("; ");
    if (result.count != null) return result.count + " найдено";
    if (result.materials) return result.materials.length + " материалов";
    if (result.profiles) return result.profiles.length + " профилей";
    return "ok";
  }

  function stopThinking() { if (thinkingNode) { thinkingNode.remove(); thinkingNode = null; } }

  function num(n) { return n == null ? "?" : String(n).replace(/\B(?=(\d{3})+(?!\d))/g, " "); }

  function usageNode(d) {
    var head = d.total_tokens != null
      ? "Токены за ответ: " + num(d.total_tokens) + " (вход " + num(d.prompt_tokens) +
        (d.cached_tokens ? ", из кэша " + num(d.cached_tokens) : "") + ", выход " + num(d.completion_tokens) +
        ") · шагов " + d.steps.length + " · за сессию " + num(d.session_tokens)
      : "Провайдер не сообщил токены · шагов " + d.steps.length + " · отправлено " + num(d.request_chars) + " символов";
    var rows = d.steps.map(function (s, i) {
      return "<tr><td>" + (i + 1) + "</td><td>" + num(s.prompt_tokens) + "</td><td>" + num(s.completion_tokens) +
        "</td><td>" + num(s.request_chars) + "</td><td>" + esc((s.tools || []).join(", ") || "ответ") + "</td></tr>";
    }).join("");
    var u = el("details", "usage");
    u.innerHTML = "<summary>" + esc(head) + (d.model ? " · " + esc(d.model) : "") + (d.compact_history === false ? " · без экономии" : "") + "</summary>" +
      "<table><thead><tr><th>Шаг</th><th>Вход</th><th>Выход</th><th>Символов в запросе</th><th>Вызванные инструменты</th></tr></thead><tbody>" +
      rows + "</tbody></table>";
    return u;
  }

  function render(ev) {
    var d = ev.data || {};
    if (ev.type !== "thinking") stopThinking();
    switch (ev.type) {
      case "user":
        var u = el("div", "msg user"); u.textContent = d.text || "";
        (d.attachments || []).forEach(function (a) { u.appendChild(el("span", "att", esc(a.name))); });
        add(u); break;
      case "thinking":
        stopThinking(); thinkingNode = add(el("div", "thinking", "Ассистент думает… шаг " + d.step + (d.tokens ? " · потрачено " + num(d.tokens) + " токенов" : ""))); break;
      case "note":
        add(el("div", "msg note", inline(d.text))); break;
      case "assistant":
        add(el("div", "msg assistant", markdown(d.text))); break;
      case "tool_call":
        var t = el("details", "tool");
        t.innerHTML = "<summary><span class='name'>" + esc(d.name) + "</span>(<span class='args'>" + esc(short(d.arguments || {}, 160)) + "</span>) <span class='res'>…</span></summary>" +
          "<pre>" + esc(JSON.stringify(d.arguments, null, 2)) + "</pre>";
        toolNodes[d.id] = add(t); break;
      case "tool_result":
        var n = toolNodes[d.id];
        if (n) {
          n.classList.add(d.ok ? "ok" : "fail");
          n.querySelector(".res").textContent = "→ " + summarize(d.name, d.result) + " (" + d.ms + " мс)";
          n.appendChild(el("pre", null, esc(JSON.stringify(d.result, null, 2))));
        }
        break;
      case "progress":
        if (!progressNode) {
          progressNode = add(el("div", "progress", "<span class='label'>Нарезка…</span><div class='bar'><span></span></div>"));
        }
        progressNode.querySelector(".bar span").style.width = Math.round((d.value || 0) * 100) + "%";
        progressNode.querySelector(".label").textContent = d.result ? ("Нарезка готова: " + d.result.print_time + ", " + (d.result.total_weight_g != null ? d.result.total_weight_g : (d.result.material_weight_g || []).join(" + ")) + " г") : ("Нарезка… " + Math.round((d.value || 0) * 100) + "%");
        if (d.result) progressNode = null;
        break;
      case "error":
        add(el("div", "msg error", esc(d.text))); break;
      case "status":
        add(el("div", "msg note", esc(d.text))); break;
      case "usage":
        add(usageNode(d)); break;
    }
  }

  // ---------- polling
  function poll() {
    api("GET", "/api/events?after=" + lastSeq).then(function (res) {
      if (sessionId && res.session !== sessionId) { chat.innerHTML = ""; lastSeq = 0; toolNodes = {}; }
      sessionId = res.session;
      res.events.forEach(function (ev) { if (ev.seq > lastSeq) { render(ev); lastSeq = ev.seq; } });
      setBusy(res.busy);
      $("statusDot").classList.remove("err");
    }).catch(function () { $("statusDot").classList.add("err"); })
      .then(function () { setTimeout(poll, busy ? 400 : 1500); });
  }

  function setBusy(b) {
    busy = b;
    $("statusDot").classList.toggle("busy", b);
    $("btnSend").hidden = b; $("btnStop").hidden = !b;
  }

  // ---------- attachments
  function addFiles(files) {
    Array.prototype.forEach.call(files, function (file) {
      var chip = el("span", "chip loading", esc(file.name));
      chips.appendChild(chip);
      var reader = new FileReader();
      reader.onload = function () {
        var b64 = String(reader.result).split(",")[1] || "";
        api("POST", "/api/upload", { name: file.name, data: b64 }).then(function (info) {
          chip.classList.remove("loading");
          var x = el("button", null, "×"); x.type = "button"; x.title = "Убрать";
          x.onclick = function () { pending = pending.filter(function (p) { return p !== info.id; }); chip.remove(); };
          chip.appendChild(x);
          pending.push(info.id);
        }).catch(function (e) { chip.textContent = file.name + ": " + e.message; });
      };
      reader.readAsDataURL(file);
    });
  }
  $("fileInput").addEventListener("change", function (e) { addFiles(e.target.files); e.target.value = ""; });
  var dragDepth = 0;
  window.addEventListener("dragenter", function (e) { e.preventDefault(); dragDepth++; $("drop").hidden = false; });
  window.addEventListener("dragleave", function () { if (--dragDepth <= 0) { dragDepth = 0; $("drop").hidden = true; } });
  window.addEventListener("dragover", function (e) { e.preventDefault(); });
  window.addEventListener("drop", function (e) { e.preventDefault(); dragDepth = 0; $("drop").hidden = true; addFiles(e.dataTransfer.files); });

  // ---------- sending
  function send() {
    var text = input.value.trim();
    if (!text && !pending.length) return;
    api("POST", "/api/message", { text: text, attachments: pending }).then(function () {
      input.value = ""; pending = []; chips.innerHTML = ""; setBusy(true);
    }).catch(function (e) { add(el("div", "msg error", esc(e.message))); });
  }
  $("composer").addEventListener("submit", function (e) { e.preventDefault(); send(); });
  input.addEventListener("keydown", function (e) { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } });
  $("btnStop").onclick = function () { api("POST", "/api/cancel"); };
  $("btnNew").onclick = function () { api("POST", "/api/reset").then(function () { chat.innerHTML = ""; lastSeq = 0; toolNodes = {}; }); };

  // ---------- voice input
  // "server": record with MediaRecorder, the plugin sends it to the gateway's
  // /audio/transcriptions (any browser, key stays in the plugin).
  // "browser": Web Speech API (Chrome/Edge only, free, live text, needs internet).
  var mic = $("btnMic"), micTimer = $("micTimer");
  var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  var canRecord = !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder);
  var voice = { engine: "auto", autosend: false };
  var serverFailed = false, rec = null, timerId = null, startedAt = 0, baseText = "", MAX_SEC = 120;

  function engineNow() {
    if (voice.engine === "browser") return SR ? "browser" : null;
    if (voice.engine === "server") return canRecord ? "server" : null;
    if (canRecord && !(serverFailed && SR)) return "server";
    return SR ? "browser" : null;
  }
  function micState(state) {
    mic.classList.toggle("rec", state === "rec"); mic.classList.toggle("wait", state === "wait");
    mic.setAttribute("aria-pressed", state === "rec" ? "true" : "false");
    clearInterval(timerId); micTimer.textContent = "";
    if (state === "rec") { startedAt = Date.now(); timerId = setInterval(tick, 250); tick(); }
  }
  function tick() {
    var sec = Math.floor((Date.now() - startedAt) / 1000);
    micTimer.textContent = Math.floor(sec / 60) + ":" + ("0" + sec % 60).slice(-2);
    if (sec >= MAX_SEC && rec) rec.stop(false);
  }
  function putText(text, done) {
    input.value = baseText + (baseText && text ? " " : "") + text;
    if (!done) return;
    input.focus();
    if (voice.autosend && text && !busy) send();
  }
  function micError(msg) { add(el("div", "msg error", esc(msg))); }

  function startServer() {
    micState("wait");
    navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } }).then(function (stream) {
      var type = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/webm", "audio/mp4"].filter(function (t) {
        return MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(t);
      })[0];
      var mr = type ? new MediaRecorder(stream, { mimeType: type }) : new MediaRecorder(stream);
      var chunks = [], cancelled = false;
      mr.ondataavailable = function (e) { if (e.data && e.data.size) chunks.push(e.data); };
      mr.onstop = function () {
        stream.getTracks().forEach(function (t) { t.stop(); });
        rec = null;
        if (cancelled || !chunks.length) { micState(""); return; }
        micState("wait");
        var blob = new Blob(chunks, { type: mr.mimeType || type || "audio/webm" });
        var reader = new FileReader();
        reader.onload = function () {
          api("POST", "/api/transcribe", { data: String(reader.result).split(",")[1] || "", mime: blob.type }).then(function (r) {
            micState("");
            if (r.text) putText(r.text, true);
            else micError("Речь не распознана. Попробуйте ещё раз, ближе к микрофону.");
          }).catch(function (e) {
            micState("");
            if (voice.engine === "auto" && SR) {
              serverFailed = true;
              micError(e.message + " Дальше распознаю речь средствами браузера: нажмите микрофон и повторите.");
            } else micError(e.message);
          });
        };
        reader.readAsDataURL(blob);
      };
      baseText = input.value.trim();
      mr.start(1000);
      rec = { stop: function (cancel) { cancelled = !!cancel; if (mr.state !== "inactive") mr.stop(); } };
      micState("rec");
    }).catch(function (e) {
      micState("");
      var name = e && e.name;
      micError(name === "NotAllowedError" || name === "SecurityError"
        ? "Нет доступа к микрофону. Разрешите его в адресной строке браузера (значок микрофона или замка) и нажмите ещё раз."
        : name === "NotFoundError" ? "Микрофон не найден. Подключите его и попробуйте снова."
        : "Не удалось включить микрофон: " + ((e && e.message) || e));
    });
  }

  function startBrowser() {
    var r = new SR(), finalText = "", cancelled = false;
    r.lang = "ru-RU"; r.interimResults = true; r.continuous = true;
    baseText = input.value.trim();
    r.onresult = function (e) {
      var interim = "";
      for (var i = e.resultIndex; i < e.results.length; i++) {
        if (e.results[i].isFinal) finalText += " " + e.results[i][0].transcript;
        else interim += " " + e.results[i][0].transcript;
      }
      putText((finalText + interim).replace(/\s+/g, " ").trim(), false);
    };
    r.onerror = function (e) {
      var msg = {
        "not-allowed": "Нет доступа к микрофону. Разрешите его в адресной строке браузера и нажмите ещё раз.",
        "service-not-allowed": "Браузер запретил распознавание речи. Выберите в настройках распознавание через шлюз LLM.",
        "no-speech": "Не услышал речь. Проверьте микрофон и попробуйте ещё раз.",
        "audio-capture": "Микрофон не найден. Подключите его и попробуйте снова.",
        "network": "Распознавание в браузере работает через интернет (серверы Google/Microsoft), а связи нет."
      }[e.error];
      if (e.error !== "aborted") micError(msg || ("Ошибка распознавания речи: " + e.error));
    };
    r.onend = function () {
      rec = null; micState("");
      var text = finalText.replace(/\s+/g, " ").trim();
      if (cancelled || !text) input.value = baseText;
      else putText(text, true);
    };
    r.start();
    rec = { stop: function (cancel) { cancelled = !!cancel; if (cancel) r.abort(); else r.stop(); } };
    micState("rec");
  }

  mic.onclick = function () {
    if (mic.classList.contains("wait")) return;
    if (rec) { rec.stop(false); return; }
    var engine = engineNow();
    if (!engine) {
      micError(voice.engine === "browser"
        ? "Этот браузер не умеет распознавать речь сам. Откройте чат в Chrome или Edge либо выберите в настройках распознавание через шлюз LLM."
        : "Этот браузер не умеет записывать звук. Откройте чат в Chrome, Edge или Firefox.");
      return;
    }
    if (engine === "server") startServer(); else startBrowser();
  };
  document.addEventListener("keydown", function (e) { if (e.key === "Escape" && rec) { e.preventDefault(); rec.stop(true); } });

  // ---------- settings
  var dlg = $("settings");
  function status(text, cls) { var s = $("cfgStatus"); s.textContent = text || ""; s.className = "hint " + (cls || ""); }
  function loadConfig() {
    return api("GET", "/api/config").then(function (c) {
      $("cfgUrl").value = c.llm_url || ""; $("cfgModel").value = c.llm_model || "";
      $("cfgKey").value = ""; $("cfgKey").placeholder = c.has_key ? ("сохранён: " + c.key_masked + " — оставьте пустым, чтобы не менять") : "введите ключ";
      $("cfgTemp").value = c.temperature; $("cfgSteps").value = c.max_steps; $("cfgResults").value = c.results_dir || "";
      $("cfgSsl").checked = !!c.ssl_verify; $("cfgDev").checked = !!c.dev_mode; $("cfgCompact").checked = c.compact_history !== false; $("devConsole").hidden = !c.dev_mode;
      $("cfgVoice").value = c.voice_engine || "auto"; $("cfgVoiceModel").value = c.voice_model || ""; $("cfgVoiceSend").checked = !!c.voice_autosend;
      voice.engine = c.voice_engine || "auto"; voice.autosend = !!c.voice_autosend; serverFailed = false;
      return c;
    });
  }
  function saveConfig() {
    return api("POST", "/api/config", {
      llm_url: $("cfgUrl").value.trim(), llm_key: $("cfgKey").value.trim(), llm_model: $("cfgModel").value.trim(),
      temperature: parseFloat($("cfgTemp").value || "0.2"), max_steps: parseInt($("cfgSteps").value || "30", 10),
      results_dir: $("cfgResults").value.trim(), ssl_verify: $("cfgSsl").checked, dev_mode: $("cfgDev").checked,
      compact_history: $("cfgCompact").checked,
      voice_engine: $("cfgVoice").value, voice_model: $("cfgVoiceModel").value.trim(), voice_autosend: $("cfgVoiceSend").checked
    }).then(function () { status("Сохранено", "ok"); return loadConfig(); }).catch(function (e) { status(e.message, "err"); });
  }
  $("btnSettings").onclick = function () { status(""); loadConfig().then(function () { dlg.showModal(); }); };
  $("btnClose").onclick = function () { dlg.close(); };
  $("btnSave").onclick = saveConfig;
  $("btnTest").onclick = function () {
    status("Проверяю…");
    saveConfig().then(function () { return api("POST", "/api/config/test"); }).then(function (r) {
      var list = $("modelList"); list.innerHTML = "";
      (r.models || []).forEach(function (m) { var o = document.createElement("option"); o.value = m; list.appendChild(o); });
      status("Подключение работает. Доступные модели: " + ((r.models || []).join(", ") || "список пуст"), "ok");
    }).catch(function (e) { status(e.message, "err"); });
  };
  $("btnDump").onclick = function () {
    status("Собираю настройки…");
    api("GET", "/api/debug/settings").then(function (data) {
      var blob = new Blob([JSON.stringify(data, null, 1)], { type: "application/json" });
      var a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = "steslicer_settings.json";
      document.body.appendChild(a); a.click(); a.remove();
      status("Файл steslicer_settings.json скачан", "ok");
    }).catch(function (e) { status(e.message, "err"); });
  };
  $("btnEval").onclick = function () {
    $("devOut").textContent = "…";
    api("POST", "/api/debug/eval", { code: $("devCode").value }).then(function (r) {
      $("devOut").textContent = (r.stdout || "") + (r.ok ? JSON.stringify(r.result, null, 2) : r.error);
    }).catch(function (e) { $("devOut").textContent = e.message; });
  };

  loadConfig().then(function (c) { if (!c.llm_url || !c.has_key) { status("Укажите URL и ключ LLM, чтобы начать."); dlg.showModal(); } });
  poll();
})();
