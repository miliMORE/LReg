(function () {
  "use strict";
  function token() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") : "";
  }
  function ensureFormToken(form) {
    if (!form || (form.method || "get").toLowerCase() !== "post") return;
    if (form.querySelector('input[name="csrf_token"]')) return;
    var t = token();
    if (!t) return;
    var input = document.createElement("input");
    input.type = "hidden";
    input.name = "csrf_token";
    input.value = t;
    form.appendChild(input);
  }
  function injectAll() {
    document.querySelectorAll("form").forEach(ensureFormToken);
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", injectAll);
  } else {
    injectAll();
  }
  document.addEventListener(
    "submit",
    function (e) {
      if (e.target && e.target.tagName === "FORM") {
        ensureFormToken(e.target);
      }
    },
    true
  );
})();
