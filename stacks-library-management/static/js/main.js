document.addEventListener("DOMContentLoaded", function () {
  // ---- Mobile sidebar toggle ----
  var toggle = document.getElementById("navToggle");
  var sidebar = document.getElementById("sidebar");
  if (toggle && sidebar) {
    toggle.addEventListener("click", function () { sidebar.classList.toggle("open"); });
    document.addEventListener("click", function (e) {
      if (sidebar.classList.contains("open") && !sidebar.contains(e.target) && e.target !== toggle) {
        sidebar.classList.remove("open");
      }
    });
  }

  // ---- Flash messages rendered as auto-dismissing toasts ----
  var flashes = document.querySelectorAll(".flash-source [data-flash]");
  var stack = document.getElementById("toast-stack");
  if (stack) {
    flashes.forEach(function (el, i) {
      var category = el.getAttribute("data-flash");
      var text = el.textContent;
      setTimeout(function () { showToast(text, category); }, i * 80);
    });
  }

  // ---- Native <dialog> confirmation modals, replacing window.confirm ----
  document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      if (form.dataset.confirmed === "true") { return; }
      e.preventDefault();
      openConfirmModal(form.getAttribute("data-confirm"), function () {
        form.dataset.confirmed = "true";
        form.submit();
      });
    });
  });

  // ---- Genre chip picker ----
  document.querySelectorAll("[data-genre-picker]").forEach(initGenrePicker);

  // ---- Copy-to-clipboard buttons ----
  document.querySelectorAll("[data-copy]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var text = btn.getAttribute("data-copy");
      navigator.clipboard.writeText(text).then(function () {
        var original = btn.textContent;
        btn.textContent = "Copied!";
        setTimeout(function () { btn.textContent = original; }, 1600);
      });
    });
  });
});

function showToast(message, category) {
  var stack = document.getElementById("toast-stack");
  if (!stack) return;
  var el = document.createElement("div");
  el.className = "toast " + (category === "error" ? "error" : "success");
  el.textContent = message;
  stack.appendChild(el);
  setTimeout(function () {
    el.style.transition = "opacity .25s ease, transform .25s ease";
    el.style.opacity = "0";
    el.style.transform = "translateX(16px)";
    setTimeout(function () { el.remove(); }, 260);
  }, 4200);
}

var _confirmDialog = null;
function openConfirmModal(message, onConfirm) {
  if (!_confirmDialog) {
    _confirmDialog = document.createElement("dialog");
    _confirmDialog.className = "confirm-modal";
    _confirmDialog.innerHTML =
      '<div class="modal-body">' +
      '<h3 style="margin-bottom:8px;">Are you sure?</h3>' +
      '<p class="muted" id="confirmModalText" style="margin-bottom:0;"></p>' +
      '<div class="modal-actions">' +
      '<button type="button" class="btn btn-outline" id="confirmModalCancel">Cancel</button>' +
      '<button type="button" class="btn btn-primary" id="confirmModalOk" style="background:var(--red);box-shadow:none;">Confirm</button>' +
      "</div></div>";
    document.body.appendChild(_confirmDialog);
    _confirmDialog.querySelector("#confirmModalCancel").addEventListener("click", function () {
      _confirmDialog.close();
    });
  }
  _confirmDialog.querySelector("#confirmModalText").textContent = message;
  var okBtn = _confirmDialog.querySelector("#confirmModalOk");
  var freshOk = okBtn.cloneNode(true);
  okBtn.parentNode.replaceChild(freshOk, okBtn);
  freshOk.addEventListener("click", function () {
    _confirmDialog.close();
    onConfirm();
  });
  _confirmDialog.showModal();
}

function initGenrePicker(container) {
  var hiddenWrap = container.querySelector("[data-genre-hidden]");
  var selectedWrap = container.querySelector("[data-genre-selected]");
  var listWrap = container.querySelector("[data-genre-list]");
  var searchInput = container.querySelector("[data-genre-search]");
  var options = Array.prototype.slice.call(listWrap.querySelectorAll(".genre-option"));
  var picked = new Set(
    (container.getAttribute("data-genre-initial") || "")
      .split(",").map(function (s) { return s.trim(); }).filter(Boolean)
  );

  function render() {
    hiddenWrap.innerHTML = "";
    selectedWrap.innerHTML = "";
    if (picked.size === 0) {
      selectedWrap.innerHTML = '<span class="genre-empty-hint">No genres selected yet — pick from the list below.</span>';
    }
    picked.forEach(function (g) {
      var input = document.createElement("input");
      input.type = "hidden"; input.name = "genres"; input.value = g;
      hiddenWrap.appendChild(input);

      var chip = document.createElement("span");
      chip.className = "genre-chip";
      chip.innerHTML = g + ' <button type="button" aria-label="Remove ' + g + '">\u00d7</button>';
      chip.querySelector("button").addEventListener("click", function () {
        picked.delete(g); render();
      });
      selectedWrap.appendChild(chip);
    });
    options.forEach(function (opt) {
      opt.classList.toggle("picked", picked.has(opt.dataset.genre));
    });
  }

  options.forEach(function (opt) {
    opt.addEventListener("click", function () {
      var g = opt.dataset.genre;
      if (picked.has(g)) { picked.delete(g); } else { picked.add(g); }
      render();
    });
  });

  if (searchInput) {
    searchInput.addEventListener("input", function () {
      var q = searchInput.value.trim().toLowerCase();
      options.forEach(function (opt) {
        opt.style.display = opt.dataset.genre.toLowerCase().includes(q) ? "" : "none";
      });
    });
  }

  var clearBtn = container.querySelector("[data-genre-clear]");
  if (clearBtn) {
    clearBtn.addEventListener("click", function () { picked.clear(); render(); });
  }

  render();
}
