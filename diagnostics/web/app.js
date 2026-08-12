/* Copyright (c) 2026 Jahongir Habibov. All Rights Reserved.
   Proprietary and Confidential. Unauthorized use, copying, or distribution is
   strictly prohibited.

   Interface for the KASSIO diagnostics service.

   Two rules run through this file. First, no value coming from the server is
   ever put into innerHTML: container logs, device names and scan results are
   attacker-influenceable, so everything goes in through textContent. Second,
   the sudo session token lives in a JavaScript variable and never in
   localStorage or a cookie — a token in storage outlives the tab and a token in
   a cookie would be sent automatically, which is exactly what makes cross-site
   request forgery possible. */

(function () {
  "use strict";

  var state = {
    language: "de",
    strings: {},
    meta: null,
    results: [],
    summary: {},
    sessionToken: "",
    posToken: "",
    posUsername: "",
    activeTab: "overview",
    config: null,
    configTemplate: null,
    roles: [],
    draft: null,
    loading: false
  };

  var TABS = [
    { id: "overview", labelKey: "ui.tab.overview", groups: [] },
    { id: "system", labelKey: "ui.tab.system", groups: ["system"] },
    { id: "network", labelKey: "ui.tab.network", groups: ["network"] },
    { id: "devices", labelKey: "ui.tab.devices", groups: ["devices"] },
    { id: "docker", labelKey: "ui.tab.docker", groups: ["docker", "services", "pos"] },
    { id: "setup", labelKey: "ui.tab.setup", groups: [] }
  ];

  var STATUS_SYMBOL = {
    ok: "✓", warn: "!", fail: "✕", unknown: "?", unavailable: "–"
  };

  /* ---------------------------------------------------------------- utils */

  function el(tag, attributes, children) {
    var node = document.createElement(tag);
    if (attributes) {
      Object.keys(attributes).forEach(function (name) {
        var value = attributes[name];
        if (value === null || value === undefined || value === false) { return; }
        if (name === "class") { node.className = value; }
        else if (name === "text") { node.textContent = String(value); }
        else if (name.indexOf("on") === 0 && typeof value === "function") {
          node.addEventListener(name.slice(2), value);
        } else { node.setAttribute(name, String(value)); }
      });
    }
    (children || []).forEach(function (child) {
      if (child === null || child === undefined) { return; }
      node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
    });
    return node;
  }

  function clear(node) {
    while (node.firstChild) { node.removeChild(node.firstChild); }
  }

  function t(key, params) {
    if (!key) { return ""; }
    var template = state.strings[key];
    if (template === undefined) { return key; }
    if (!params) { return template; }
    return template.replace(/\{([a-zA-Z0-9_]+)\}/g, function (match, name) {
      return Object.prototype.hasOwnProperty.call(params, name)
        ? String(params[name]) : match;
    });
  }

  function toast(message, kind) {
    var node = document.getElementById("toast");
    node.textContent = message;
    node.className = "toast " + (kind || "");
    node.hidden = false;
    window.clearTimeout(node.dataset.timer);
    node.dataset.timer = window.setTimeout(function () { node.hidden = true; }, 6000);
  }

  function errorText(payload) {
    if (payload && payload.error && payload.error.key) {
      return t(payload.error.key, payload.error.params || {});
    }
    return t("ui.request_failed");
  }

  /* ------------------------------------------------------------------ api */

  function api(method, path, body) {
    var headers = { "Accept": "application/json" };
    if (method !== "GET") {
      headers["X-Kassio-Diag"] = "1";
      headers["Content-Type"] = "application/json";
    }
    if (state.sessionToken) { headers["X-Kassio-Diag-Session"] = state.sessionToken; }
    if (state.posToken) { headers["X-Kassio-Diag-Pos"] = state.posToken; }
    return fetch(path, {
      method: method,
      headers: headers,
      credentials: "omit",
      body: body === undefined ? undefined : JSON.stringify(body)
    }).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (payload) {
        return { status: response.status, payload: payload };
      });
    });
  }

  /* -------------------------------------------------------------- dialogs */

  var dialogCleanup = null;

  function closeDialog() {
    // Runs whatever the open dialog left behind — a log window's refresh timer
    // has to stop however the dialog is dismissed, not only via its close
    // button. Escape and a backdrop click would otherwise leave it polling the
    // server forever, into a detached node nobody can see.
    if (dialogCleanup) {
      var cleanup = dialogCleanup;
      dialogCleanup = null;
      try { cleanup(); } catch (error) { /* cleanup must never block closing */ }
    }
    var host = document.getElementById("dialog-host");
    clear(host);
    host.hidden = true;
  }

  function openDialog(title, bodyNodes, actionNodes, onClose) {
    // Closes any dialog already open, so its cleanup runs before this one
    // replaces it in the host.
    closeDialog();
    dialogCleanup = onClose || null;
    var host = document.getElementById("dialog-host");
    clear(host);
    var dialog = el("div", { class: "dialog", role: "dialog", "aria-modal": "true" }, [
      el("h2", { text: title }),
      el("div", { class: "dialog-body" }, bodyNodes),
      el("div", { class: "dialog-actions" }, actionNodes)
    ]);
    host.appendChild(dialog);
    host.hidden = false;
    host.onclick = function (event) { if (event.target === host) { closeDialog(); } };
    var focusable = dialog.querySelector("input, button, select, textarea");
    if (focusable) { focusable.focus(); }
    return dialog;
  }

  function askSudoPassword() {
    return new Promise(function (resolve) {
      var input = el("input", { type: "password", id: "sudo-input",
                                autocomplete: "current-password" });
      var errorNode = el("p", { class: "error" });
      errorNode.hidden = true;
      var submit = el("button", { class: "button button-primary",
                                  text: t("ui.btn.confirm") });

      function attempt() {
        var value = input.value;
        if (!value) { return; }
        submit.disabled = true;
        api("POST", "/api/session", { password: value }).then(function (response) {
          submit.disabled = false;
          input.value = "";
          if (response.status === 200 && response.payload.ok) {
            state.sessionToken = response.payload.data.token;
            renderSessionBadge(response.payload.data);
            closeDialog();
            resolve(true);
          } else {
            errorNode.textContent = errorText(response.payload);
            errorNode.hidden = false;
            input.focus();
          }
        }).catch(function () {
          submit.disabled = false;
          errorNode.textContent = t("ui.request_failed");
          errorNode.hidden = false;
        });
      }

      submit.addEventListener("click", attempt);
      input.addEventListener("keydown", function (event) {
        if (event.key === "Enter") { attempt(); }
      });

      openDialog(t("ui.sudo.title"), [
        el("p", { text: t("ui.sudo.explain") }),
        el("label", { for: "sudo-input", text: t("ui.sudo.title") }),
        input,
        errorNode
      ], [
        el("button", { class: "button", text: t("ui.btn.cancel"),
                       onclick: function () { closeDialog(); resolve(false); } }),
        submit
      ]);
    });
  }

  function askPosLogin() {
    return new Promise(function (resolve) {
      var user = el("input", { type: "text", id: "pos-user", autocomplete: "username" });
      var password = el("input", { type: "password", id: "pos-password",
                                   autocomplete: "current-password" });
      var errorNode = el("p", { class: "error" });
      errorNode.hidden = true;
      var submit = el("button", { class: "button button-primary",
                                  text: t("ui.btn.confirm") });

      function attempt() {
        if (!user.value || !password.value) { return; }
        submit.disabled = true;
        api("POST", "/api/pos/session",
            { username: user.value, password: password.value })
          .then(function (response) {
            submit.disabled = false;
            password.value = "";
            if (response.status === 200 && response.payload.ok) {
              state.posToken = response.payload.data.token;
              state.posUsername = response.payload.data.username;
              renderPosBadge();
              closeDialog();
              resolve(true);
            } else {
              errorNode.textContent = errorText(response.payload);
              errorNode.hidden = false;
            }
          }).catch(function () {
            submit.disabled = false;
            errorNode.textContent = t("ui.request_failed");
            errorNode.hidden = false;
          });
      }

      submit.addEventListener("click", attempt);
      password.addEventListener("keydown", function (event) {
        if (event.key === "Enter") { attempt(); }
      });

      openDialog(t("ui.pos.title"), [
        el("p", { text: t("ui.pos.explain") }),
        el("label", { for: "pos-user", text: t("ui.pos.username") }), user,
        el("label", { for: "pos-password", text: t("ui.pos.password") }), password,
        errorNode
      ], [
        el("button", { class: "button", text: t("ui.btn.cancel"),
                       onclick: function () { closeDialog(); resolve(false); } }),
        submit
      ]);
    });
  }

  function confirmAction(definition, params) {
    return new Promise(function (resolve) {
      var text = t(definition.confirm_key, params || {});
      openDialog(t(definition.label_key), [el("p", { text: text })], [
        el("button", { class: "button", text: t("ui.btn.cancel"),
                       onclick: function () { closeDialog(); resolve(false); } }),
        el("button", {
          class: definition.risk === "high" ? "button button-danger"
                                            : "button button-primary",
          text: t("ui.btn.confirm"),
          onclick: function () { closeDialog(); resolve(true); }
        })
      ]);
    });
  }

  /* -------------------------------------------------------------- actions */

  function actionDefinition(actionId) {
    if (!state.meta) { return null; }
    for (var index = 0; index < state.meta.actions.length; index += 1) {
      if (state.meta.actions[index].id === actionId) { return state.meta.actions[index]; }
    }
    return null;
  }

  function runAction(actionId, params, options) {
    var definition = actionDefinition(actionId);
    if (!definition) { return Promise.resolve(); }
    params = params || {};

    if (definition.client_only) { return handleClientAction(actionId, params); }

    return confirmAction(definition, params).then(function (confirmed) {
      if (!confirmed) { return null; }
      return ensureCredentials(definition).then(function (ready) {
        if (!ready) { return null; }
        return api("POST", "/api/actions/" + encodeURIComponent(actionId),
                   { params: params }).then(function (response) {
          var data = response.payload.data || {};
          if (response.payload.ok) {
            toast(t(data.message_key, data.params || {}), "ok");
            if (data.results && data.results.length) {
              // The server already re-ran the areas this repair touches;
              // re-running everything on top would cost a second full sweep,
              // including the network probes, for no new information.
              mergeResults(data.results);
              renderTabs();
              renderActive();
            } else {
              refresh();
            }
            if (actionId === "devices.scan" && !(options && options.quiet)) {
              showScanResults(data.data || {});
            }
          } else {
            toast(t(data.message_key || "", data.params || {}) || errorText(response.payload),
                  "fail");
            if (data.message_key === "pos.multiple_printer_settings") {
              chooseSetting(data.data || {}, params);
            }
          }
          return data;
        });
      });
    }).catch(function () { toast(t("ui.request_failed"), "fail"); });
  }

  function ensureCredentials(definition) {
    var chain = Promise.resolve(true);
    if (definition.needs_sudo && !state.sessionToken) {
      chain = chain.then(function () { return askSudoPassword(); });
    }
    if (definition.needs_pos_login) {
      chain = chain.then(function (ready) {
        if (!ready) { return false; }
        return state.posToken ? true : askPosLogin();
      });
    }
    return chain;
  }

  function handleClientAction(actionId, params) {
    if (actionId === "printer.open_web_ui") {
      if (params.url) { window.open(params.url, "_blank", "noopener"); }
      return Promise.resolve();
    }
    if (actionId === "printer.show_instructions") {
      openDialog(t("ui.instructions.title"),
                 [el("p", { text: t(params.instructions_key || "printer.instructions.generic") })],
                 [el("button", { class: "button button-primary", text: t("ui.btn.close"),
                                 onclick: closeDialog })]);
      return Promise.resolve();
    }
    if (actionId === "setup.open_wizard") {
      selectTab("setup");
      return Promise.resolve();
    }
    return Promise.resolve();
  }

  function chooseSetting(data, params) {
    var candidates = data.candidates || [];
    if (!candidates.length) { return; }
    var select = el("select", { id: "setting-select" },
      candidates.map(function (key) { return el("option", { value: key, text: key }); }));
    openDialog(t("pos.multiple_printer_settings", { count: candidates.length }),
      [el("label", { for: "setting-select", text: t("ui.setup.device_ip") }), select],
      [
        el("button", { class: "button", text: t("ui.btn.cancel"), onclick: closeDialog }),
        el("button", {
          class: "button button-primary", text: t("ui.btn.confirm"),
          onclick: function () {
            var chosen = select.value;
            closeDialog();
            api("POST", "/api/actions/printer.adopt_found_ip",
                { params: { ip: params.ip, setting_key: chosen } })
              .then(function (response) {
                var payload = response.payload.data || {};
                toast(t(payload.message_key, payload.params || {}),
                      response.payload.ok ? "ok" : "fail");
                refresh();
              });
          }
        })
      ]);
  }

  /* -------------------------------------------------------------- results */

  function mergeResults(incoming) {
    var byId = {};
    state.results.forEach(function (result) { byId[result.id] = result; });
    incoming.forEach(function (result) { byId[result.id] = result; });
    state.results = Object.keys(byId).map(function (key) { return byId[key]; });
  }

  function worstOf(results) {
    var ranking = { ok: 0, unavailable: 1, unknown: 2, warn: 3, fail: 4 };
    var worst = "ok";
    results.forEach(function (result) {
      if ((ranking[result.status] || 0) > (ranking[worst] || 0)) { worst = result.status; }
    });
    return worst;
  }

  function statusChip(status) {
    return el("span", { class: "chip " + status }, [
      el("span", { "aria-hidden": "true", text: STATUS_SYMBOL[status] || "?" }),
      el("span", { text: t("ui.status." + status) })
    ]);
  }

  function actionParamsFor(result, actionId) {
    var data = result.data || {};
    if (actionId === "container.restart") {
      return { container: data.container || (result.params || {}).container || "" };
    }
    if (actionId === "printer.adopt_found_ip") {
      return { ip: data.found_ip || "" };
    }
    if (actionId === "printer.open_web_ui") {
      return { url: data.web_ui || "" };
    }
    if (actionId === "printer.show_instructions") {
      return { instructions_key: data.instructions_key || "printer.instructions.generic" };
    }
    return {};
  }

  function resultCard(result) {
    var body = [
      el("div", { class: "card-head" }, [
        el("h3", { class: "card-title", text: t(result.title_key) }),
        statusChip(result.status)
      ]),
      el("p", { class: "card-message", text: t(result.message_key, result.params || {}) })
    ];

    if (result.actual || result.expected) {
      body.push(el("div", { class: "card-values" }, [
        result.actual ? el("div", { text: t("ui.actual") + ": " + result.actual }) : null,
        result.expected ? el("div", { text: t("ui.expected") + ": " + result.expected }) : null
      ]));
    }

    var actions = (result.actions || []).map(function (actionId) {
      var definition = actionDefinition(actionId);
      if (!definition) { return null; }
      var params = actionParamsFor(result, actionId);
      if (actionId === "printer.open_web_ui" && !params.url) { return null; }
      if (actionId === "printer.adopt_found_ip" && !params.ip) { return null; }
      if (actionId === "container.restart" && !params.container) { return null; }
      return el("button", {
        class: "button button-small", text: t(definition.label_key),
        onclick: function () { runAction(actionId, params); }
      });
    }).filter(Boolean);

    if (result.data && result.data.container) {
      actions.push(el("button", {
        class: "button button-small", text: t("ui.btn.logs"),
        onclick: function () { openLogWindow(result.data.container); }
      }));
    }
    if (actions.length) { body.push(el("div", { class: "card-actions" }, actions)); }

    if (result.details) {
      body.push(el("details", { class: "details" }, [
        el("summary", { text: t("ui.btn.details") }),
        el("pre", { text: result.details })
      ]));
    }
    return el("article", { class: "card " + result.status }, body);
  }

  /* ------------------------------------------------------------ log window */

  function openLogWindow(container) {
    var lineSelect = el("select", {},
      (state.meta && state.meta.log_line_options ? state.meta.log_line_options : [50, 200, 1000])
        .map(function (count) {
          return el("option", { value: String(count), text: String(count),
                                selected: count === 200 ? "selected" : null });
        }));
    var autoRefresh = el("input", { type: "checkbox", id: "log-auto" });
    var box = el("pre", { class: "logbox", text: t("ui.loading") });
    var timer = null;

    function load() {
      api("GET", "/api/containers/" + encodeURIComponent(container)
          + "/logs?lines=" + encodeURIComponent(lineSelect.value))
        .then(function (response) {
          var data = response.payload.data || {};
          if (!response.payload.ok || data.available === false) {
            box.textContent = data.error_key ? t(data.error_key) : t("ui.logs.empty");
            return;
          }
          var lines = data.lines || [];
          // textContent, never innerHTML: log content is not ours.
          box.textContent = lines.length ? lines.join("\n") : t("ui.logs.empty");
          box.scrollTop = box.scrollHeight;
        }).catch(function () { box.textContent = t("ui.request_failed"); });
    }

    function stop() {
      if (timer) { window.clearInterval(timer); timer = null; }
    }

    lineSelect.addEventListener("change", load);
    autoRefresh.addEventListener("change", function () {
      stop();
      if (autoRefresh.checked) { timer = window.setInterval(load, 5000); }
    });

    openDialog(t("ui.logs.title", { container: container }), [
      el("div", { class: "dialog-actions", style: "justify-content:flex-start" }, [
        el("label", { text: t("ui.logs.lines") }), lineSelect,
        el("label", { for: "log-auto", text: t("ui.logs.autorefresh") }), autoRefresh
      ]),
      box
    ], [
      el("button", {
        class: "button", text: t("ui.btn.copy"),
        onclick: function () { copyText(box.textContent); }
      }),
      el("button", {
        class: "button", text: t("ui.btn.save_file"),
        onclick: function () {
          downloadText(container + "-log.txt", box.textContent);
        }
      }),
      el("button", {
        class: "button button-primary", text: t("ui.btn.close"),
        onclick: closeDialog
      })
    ], stop);
    load();
  }

  function copyText(text) {
    function fallback() { toast(t("ui.btn.copy_failed"), "fail"); }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text)
        .then(function () { toast(t("ui.btn.copied"), "ok"); })
        .catch(fallback);
    } else { fallback(); }
  }

  function downloadText(filename, text) {
    var blob = new Blob([text], { type: "text/plain;charset=utf-8" });
    var url = URL.createObjectURL(blob);
    var link = el("a", { href: url, download: filename });
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    window.setTimeout(function () { URL.revokeObjectURL(url); }, 2000);
  }

  /* --------------------------------------------------------------- panels */

  function resultsOf(groups) {
    return state.results.filter(function (result) {
      return groups.indexOf(result.group) !== -1;
    });
  }

  function renderGroupPanel(panel, groups) {
    clear(panel);
    var results = resultsOf(groups);
    if (!results.length) {
      panel.appendChild(el("p", { class: "intro",
                                  text: state.loading ? t("ui.loading") : t("ui.no_results") }));
      return;
    }
    groups.forEach(function (group) {
      var groupResults = results.filter(function (r) { return r.group === group; });
      if (!groupResults.length) { return; }
      panel.appendChild(el("h2", { text: t("ui.group." + group) }));
      panel.appendChild(el("div", { class: "cards" }, groupResults.map(resultCard)));
    });
    panel.appendChild(el("div", { class: "card-actions" }, [
      el("button", { class: "button", text: t("ui.btn.recheck"),
                     onclick: function () { refresh(groups); } })
    ]));
  }

  function renderOverview() {
    var panel = document.getElementById("panel-overview");
    clear(panel);
    panel.appendChild(el("p", { class: "intro", text: t("ui.overview.intro") }));

    function highlight(titleKey, result) {
      var status = result ? result.status : "unknown";
      return el("div", { class: "highlight" }, [
        el("h3", { text: t(titleKey) }),
        el("div", { class: "value" }, [statusChip(status)]),
        el("p", { class: "card-message",
                  text: result ? t(result.message_key, result.params || {}) : t("ui.loading") })
      ]);
    }

    function find(id) {
      for (var index = 0; index < state.results.length; index += 1) {
        if (state.results[index].id === id) { return state.results[index]; }
      }
      return null;
    }

    var deviceResults = resultsOf(["devices"]).filter(function (r) {
      return r.id.indexOf("devices.device:") === 0;
    });
    var printerSummary = deviceResults.length
      ? deviceResults.filter(function (r) { return r.status === worstOf(deviceResults); })[0]
      : find("devices.configured");

    var serviceResults = resultsOf(["docker", "pos"]);
    var serviceSummary = serviceResults.length
      ? serviceResults.filter(function (r) { return r.status === worstOf(serviceResults); })[0]
      : null;

    panel.appendChild(el("div", { class: "highlights" }, [
      highlight("ui.overview.clock", find("system.time")),
      highlight("ui.overview.printer", printerSummary),
      highlight("ui.overview.services", serviceSummary)
    ]));

    var groups = (state.meta && state.meta.groups) || [];
    panel.appendChild(el("div", { class: "cards" }, groups.map(function (group) {
      var groupResults = resultsOf([group]);
      var status = groupResults.length ? worstOf(groupResults) : "unknown";
      var counts = groupResults.reduce(function (accumulator, result) {
        accumulator[result.status] = (accumulator[result.status] || 0) + 1;
        return accumulator;
      }, {});
      var summaryLine = Object.keys(counts).map(function (key) {
        return t("ui.status." + key) + ": " + counts[key];
      }).join("  ·  ");
      return el("article", { class: "card " + status }, [
        el("div", { class: "card-head" }, [
          el("h3", { class: "card-title", text: t("ui.group." + group) }),
          statusChip(status)
        ]),
        el("p", { class: "card-message", text: summaryLine || t("ui.loading") })
      ]);
    })));
  }

  function renderDockerPanel() {
    var panel = document.getElementById("panel-docker");
    renderGroupPanel(panel, ["docker", "services", "pos"]);
    api("GET", "/api/containers").then(function (response) {
      var data = response.payload.data || {};
      var containers = data.containers || [];
      if (!containers.length) { return; }
      var table = el("table", { class: "table" }, [
        el("thead", {}, [el("tr", {}, [
          el("th", { text: "Container" }), el("th", { text: "Status" }),
          el("th", { text: "Image" }), el("th", { text: "" })
        ])]),
        el("tbody", {}, containers.map(function (container) {
          return el("tr", {}, [
            el("td", { text: container.name }),
            el("td", { text: container.status || container.state || "" }),
            el("td", { text: container.image || "" }),
            el("td", {}, [el("button", {
              class: "button button-small", text: t("ui.btn.logs"),
              onclick: function () { openLogWindow(container.name); }
            })])
          ]);
        }))
      ]);
      panel.appendChild(el("div", { class: "section" }, [
        el("h3", { text: t("ui.group.docker") }),
        el("div", { class: "table-wrap" }, [table])
      ]));
    }).catch(function () { /* the cards above already carry the diagnosis */ });
  }

  function showScanResults(data) {
    var found = (data && data.found) || [];
    var matches = (data && data.matches) || [];
    var body = [];
    if (!found.length) {
      body.push(el("p", { text: t("ui.scan.none") }));
    } else {
      body.push(el("div", { class: "table-wrap" }, [
        el("table", { class: "table" }, [
          el("thead", {}, [el("tr", {}, [
            el("th", { text: t("ui.setup.device_ip") }),
            el("th", { text: t("ui.setup.device_mac") }),
            el("th", { text: t("ui.setup.device_vendor") }),
            el("th", { text: "Ports" })
          ])]),
          el("tbody", {}, found.map(function (entry) {
            return el("tr", {}, [
              el("td", { text: entry.ip }),
              el("td", { text: entry.mac || "" }),
              el("td", { text: entry.vendor || "" }),
              el("td", { text: (entry.open_ports || []).join(", ") })
            ]);
          }))
        ])
      ]));
    }
    matches.filter(function (match) { return match.moved; }).forEach(function (match) {
      body.push(el("p", {
        text: match.device_name + " — " + t("ui.scan.moved", {
          expected_ip: match.expected_ip, found_ip: match.found_ip
        })
      }));
    });
    openDialog(t("ui.scan.title"), body,
      [el("button", { class: "button button-primary", text: t("ui.btn.close"),
                      onclick: closeDialog })]);
  }

  /* ---------------------------------------------------------------- setup */

  function renderSetupPanel() {
    var panel = document.getElementById("panel-setup");
    clear(panel);
    panel.appendChild(el("h2", { text: t("ui.setup.title") }));
    panel.appendChild(el("p", { class: "intro", text: t("ui.setup.intro") }));

    if (!state.sessionToken) {
      panel.appendChild(el("div", { class: "section" }, [
        el("p", { text: t("ui.setup.locked") }),
        el("button", {
          class: "button button-primary", text: t("ui.sudo.title"),
          onclick: function () {
            askSudoPassword().then(function (ok) { if (ok) { loadConfig(); } });
          }
        })
      ]));
      return;
    }
    if (!state.draft) {
      panel.appendChild(el("p", { text: t("ui.loading") }));
      loadConfig();
      return;
    }

    var draft = state.draft;
    var site = draft.site || (draft.site = {});
    var network = draft.network || (draft.network = {});

    function field(labelKey, value, onInput, helpKey, type) {
      var input = el("input", { type: type || "text", value: value || "" });
      input.addEventListener("input", function () { onInput(input.value); });
      return el("div", {}, [
        el("label", { text: t(labelKey) }),
        input,
        helpKey ? el("p", { class: "hint", text: t(helpKey) }) : null
      ]);
    }

    panel.appendChild(el("div", { class: "section" }, [
      el("h3", { text: t("ui.setup.site") }),
      el("div", { class: "form-grid" }, [
        field("ui.setup.site_name", site.name, function (v) { site.name = v; }),
        field("ui.setup.technician", site.technician, function (v) { site.technician = v; })
      ])
    ]));

    var identity = draft.identity || (draft.identity = {});
    var current = (state.configMeta && state.configMeta.current_machine_id) || "";
    if (current) {
      var recorded = el("span", {
        text: identity.machine_id_hash ? identity.machine_id_hash : "—"
      });
      panel.appendChild(el("div", { class: "section" }, [
        el("h3", { text: t("check.system.machine_id.title") }),
        el("p", { class: "hint", text: t("check.system.machine_id.not_recorded") }),
        el("p", {}, [recorded]),
        el("div", { class: "card-actions" }, [
          el("button", {
            class: "button", text: t("ui.setup.record_machine_id"),
            onclick: function () {
              identity.machine_id_hash = current;
              recorded.textContent = current;
            }
          })
        ])
      ]));
    }

    panel.appendChild(el("div", { class: "section" }, [
      el("h3", { text: t("ui.setup.network") }),
      el("div", { class: "form-grid" }, [
        field("ui.setup.interface", network.interface, function (v) { network.interface = v; }),
        field("ui.setup.subnet", network.subnet, function (v) { network.subnet = v; },
              "ui.setup.help.subnet"),
        field("ui.setup.gateway", network.gateway, function (v) { network.gateway = v; })
      ])
    ]));

    var devicesSection = el("div", { class: "section" }, [
      el("h3", { text: t("ui.setup.devices") })
    ]);
    (draft.devices || []).forEach(function (device, index) {
      devicesSection.appendChild(deviceBlock(device, index));
    });
    devicesSection.appendChild(el("div", { class: "card-actions" }, [
      el("button", {
        class: "button", text: t("ui.btn.add_device"),
        onclick: function () {
          draft.devices = draft.devices || [];
          draft.devices.push({ id: "device-" + (draft.devices.length + 1), name: "",
                               role: "receipt_printer", ip: "", mac: "", port: 9100 });
          renderSetupPanel();
        }
      }),
      el("button", {
        class: "button", text: t("ui.btn.scan_suggest"),
        onclick: function () { suggestFromScan(); }
      })
    ]));
    panel.appendChild(devicesSection);

    var findings = el("div", { class: "section" });
    panel.appendChild(findings);

    panel.appendChild(el("div", { class: "card-actions" }, [
      el("button", {
        class: "button button-primary", text: t("ui.btn.save"),
        onclick: function () { saveConfig(findings); }
      })
    ]));

    var backups = (state.configMeta && state.configMeta.backups) || [];
    if (backups.length) {
      panel.appendChild(el("div", { class: "section" }, [
        el("h3", { text: t("ui.setup.backups") }),
        el("ul", {}, backups.map(function (backup) {
          return el("li", { text: backup.name });
        }))
      ]));
    }
  }

  function deviceBlock(device, index) {
    function field(labelKey, key, helpKey, type) {
      var input = el("input", { type: type || "text",
                                value: device[key] === undefined ? "" : device[key] });
      input.addEventListener("input", function () {
        device[key] = key === "port" ? parseInt(input.value, 10) || 0 : input.value;
      });
      return el("div", {}, [
        el("label", { text: t(labelKey) }),
        input,
        helpKey ? el("p", { class: "hint", text: t(helpKey) }) : null
      ]);
    }
    var roleSelect = el("select", {}, (state.roles || []).map(function (role) {
      return el("option", { value: role, text: role,
                            selected: role === device.role ? "selected" : null });
    }));
    roleSelect.addEventListener("change", function () { device.role = roleSelect.value; });

    return el("div", { class: "device-block" }, [
      el("h4", { text: device.name || t("ui.setup.devices") + " " + (index + 1) }),
      el("div", { class: "form-grid" }, [
        field("ui.setup.device_name", "name"),
        field("ui.setup.device_id", "id"),
        el("div", {}, [el("label", { text: t("ui.setup.device_role") }), roleSelect]),
        field("ui.setup.device_ip", "ip", "ui.setup.help.ip"),
        field("ui.setup.device_mac", "mac", "ui.setup.help.mac"),
        field("ui.setup.device_port", "port", "ui.setup.help.port", "number"),
        field("ui.setup.device_model", "model"),
        field("ui.setup.device_notes", "notes")
      ]),
      el("div", { class: "card-actions" }, [
        el("button", {
          class: "button button-small button-danger", text: t("ui.btn.remove"),
          onclick: function () {
            state.draft.devices.splice(index, 1);
            renderSetupPanel();
          }
        })
      ])
    ]);
  }

  function suggestFromScan() {
    // quiet: the setup wizard shows its own picker, so the generic result
    // dialog would only be opened to be replaced a moment later.
    runAction("devices.scan", {}, { quiet: true }).then(function (data) {
      if (!data || !data.data) { return; }
      var found = data.data.found || [];
      if (!found.length) { return; }
      var select = el("select", {}, found.map(function (entry) {
        return el("option", { value: JSON.stringify(entry),
                              text: entry.ip + "  " + (entry.mac || "") + "  "
                                    + (entry.vendor || "") });
      }));
      openDialog(t("ui.scan.title"),
        [el("label", { text: t("ui.btn.use") }), select],
        [
          el("button", { class: "button", text: t("ui.btn.cancel"), onclick: closeDialog }),
          el("button", {
            class: "button button-primary", text: t("ui.btn.use"),
            onclick: function () {
              var entry = JSON.parse(select.value);
              state.draft.devices = state.draft.devices || [];
              state.draft.devices.push({
                id: "device-" + (state.draft.devices.length + 1),
                name: entry.vendor || "", role: "receipt_printer",
                ip: entry.ip, mac: entry.mac || "",
                port: (entry.open_ports || []).indexOf(9100) !== -1 ? 9100
                      : (entry.open_ports || [9100])[0]
              });
              closeDialog();
              renderSetupPanel();
            }
          })
        ]);
    });
  }

  function loadConfig() {
    api("GET", "/api/config").then(function (response) {
      var data = response.payload.data || {};
      state.configMeta = data;
      state.roles = data.roles || [];
      state.config = data.config;
      state.draft = JSON.parse(JSON.stringify(data.config || data.template || {}));
      if (state.activeTab === "setup") { renderSetupPanel(); }
    }).catch(function () { toast(t("ui.request_failed"), "fail"); });
  }

  function saveConfig(findingsNode) {
    clear(findingsNode);
    api("PUT", "/api/config", { config: state.draft }).then(function (response) {
      var findings = response.payload.findings
        || (response.payload.data && response.payload.data.findings) || [];
      if (findings.length) {
        findingsNode.appendChild(el("h3", { text: t("ui.status.warn") }));
        findingsNode.appendChild(el("ul", {}, findings.map(function (finding) {
          return el("li", {
            class: finding.severity === "error" ? "error" : "",
            text: t(finding.key, finding.params || {})
          });
        })));
      }
      if (response.payload.ok) {
        toast(t("ui.setup.saved"), "ok");
        loadConfig();
        refresh();
      } else {
        toast(errorText(response.payload), "fail");
      }
    }).catch(function () { toast(t("ui.request_failed"), "fail"); });
  }

  /* ----------------------------------------------------------------- tabs */

  function renderTabs() {
    var bar = document.getElementById("tabs");
    clear(bar);
    TABS.forEach(function (tab) {
      var status = tab.groups.length ? worstOf(resultsOf(tab.groups)) : null;
      var children = [];
      if (status) {
        children.push(el("span", { class: "dot " + status, "aria-hidden": "true" }));
      }
      children.push(el("span", { text: t(tab.labelKey) }));
      bar.appendChild(el("button", {
        class: "tab", role: "tab", type: "button",
        "aria-selected": state.activeTab === tab.id ? "true" : "false",
        onclick: function () { selectTab(tab.id); }
      }, children));
    });
  }

  function selectTab(tabId) {
    state.activeTab = tabId;
    TABS.forEach(function (tab) {
      document.getElementById("panel-" + tab.id).hidden = tab.id !== tabId;
    });
    renderTabs();
    renderActive();
  }

  function renderActive() {
    if (state.activeTab === "overview") { renderOverview(); }
    else if (state.activeTab === "docker") { renderDockerPanel(); }
    else if (state.activeTab === "setup") { renderSetupPanel(); }
    else {
      var tab = TABS.filter(function (entry) { return entry.id === state.activeTab; })[0];
      renderGroupPanel(document.getElementById("panel-" + tab.id), tab.groups);
    }
  }

  /* -------------------------------------------------------------- session */

  function renderPosBadge() {
    var badge = document.getElementById("pos-badge");
    var logout = document.getElementById("pos-logout");
    if (state.posToken) {
      badge.textContent = t("ui.pos.signed_in", { username: state.posUsername });
      badge.classList.remove("badge-hidden");
      logout.classList.remove("button-hidden");
    } else {
      badge.classList.add("badge-hidden");
      logout.classList.add("button-hidden");
    }
  }

  function renderSessionBadge(status) {
    var badge = document.getElementById("session-badge");
    var lock = document.getElementById("lock-button");
    if (status && status.active) {
      badge.textContent = t("ui.sudo.active", { seconds: status.expires_in });
      badge.classList.remove("badge-hidden");
      lock.classList.remove("button-hidden");
    } else {
      badge.classList.add("badge-hidden");
      lock.classList.add("button-hidden");
      state.sessionToken = "";
    }
  }

  function pollSession() {
    if (!state.sessionToken) { return; }
    api("GET", "/api/session").then(function (response) {
      renderSessionBadge(response.payload.data || {});
    }).catch(function () { /* transient; the next poll will tell */ });
  }

  /* --------------------------------------------------------------- boot */

  function refresh(groups) {
    state.loading = true;
    var query = groups && groups.length
      ? "?force=1&groups=" + encodeURIComponent(groups.join(","))
      : "?force=1";
    return api("GET", "/api/checks" + query).then(function (response) {
      state.loading = false;
      if (!response.payload.ok) { toast(errorText(response.payload), "fail"); return; }
      if (groups && groups.length) { mergeResults(response.payload.data.results); }
      else { state.results = response.payload.data.results; }
      renderTabs();
      renderActive();
    }).catch(function () {
      state.loading = false;
      toast(t("ui.request_failed"), "fail");
    });
  }

  function applyStaticText() {
    document.querySelectorAll("[data-i18n]").forEach(function (node) {
      node.textContent = t(node.getAttribute("data-i18n"));
    });
    document.documentElement.lang = state.language;
  }

  function setLanguage(language) {
    return api("GET", "/api/i18n/" + encodeURIComponent(language))
      .then(function (response) {
        var data = response.payload.data || {};
        state.language = data.language || "de";
        state.strings = data.strings || {};
        try { window.localStorage.setItem("kassio-diag-language", state.language); }
        catch (error) { /* private mode: the choice simply does not persist */ }
        document.getElementById("language-select").value = state.language;
        applyStaticText();
        renderPosBadge();
        renderTabs();
        renderActive();
      });
  }

  function start() {
    document.getElementById("language-select")
      .addEventListener("change", function (event) { setLanguage(event.target.value); });
    document.getElementById("checkall-button")
      .addEventListener("click", function () { refresh(); });
    document.getElementById("lock-button").addEventListener("click", function () {
      api("DELETE", "/api/session").then(function () {
        state.sessionToken = "";
        renderSessionBadge(null);
        if (state.activeTab === "setup") { renderSetupPanel(); }
      });
    });
    document.getElementById("pos-logout").addEventListener("click", function () {
      api("DELETE", "/api/pos/session").then(function () {
        state.posToken = "";
        state.posUsername = "";
        renderPosBadge();
      });
    });
    document.getElementById("report-button").addEventListener("click", function () {
      window.location.href = "/api/report?lang=" + encodeURIComponent(state.language);
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") { closeDialog(); }
    });

    api("GET", "/api/meta").then(function (response) {
      state.meta = response.payload.data || { actions: [], groups: [] };
      var stored = null;
      try { stored = window.localStorage.getItem("kassio-diag-language"); }
      catch (error) { stored = null; }
      return setLanguage(stored || state.meta.default_language || "de");
    }).then(function () {
      selectTab("overview");
      return refresh();
    }).then(function () {
      if (state.meta && state.meta.config_present === false) {
        toast(t("config.missing"), "warn");
      }
    }).catch(function () { toast(t("ui.request_failed"), "fail"); });

    window.setInterval(pollSession, 15000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else { start(); }
}());
