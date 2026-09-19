(function () {
  "use strict";

  function escapeHtml(value) {
    return value.replace(/[&<>'"]/g, function (character) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[character];
    });
  }

  function decorateDiff(pre) {
    var lines = pre.textContent.replace(/\n$/, "").split("\n");
    pre.innerHTML = lines.map(function (line) {
      var kind = "context";
      if (line.indexOf("+++") === 0 || line.indexOf("---") === 0) kind = "file";
      else if (line.indexOf("@@") === 0) kind = "hunk";
      else if (line.indexOf("+") === 0) kind = "added";
      else if (line.indexOf("-") === 0) kind = "removed";
      return '<span class="diff-line diff-line-' + kind + '">' + escapeHtml(line || " ") + "</span>";
    }).join("");
  }

  document.querySelectorAll("[data-diff]").forEach(decorateDiff);

  var action = document.querySelector("[data-diff-action]");
  if (action) {
    action.addEventListener("click", function () {
      var shouldExpand = action.dataset.diffAction === "expand";
      document.querySelectorAll(".change-file").forEach(function (file) { file.open = shouldExpand; });
      action.dataset.diffAction = shouldExpand ? "collapse" : "expand";
      action.innerHTML = shouldExpand
        ? '<i class="bx bx-collapse-alt me-1"></i>Collapse all'
        : '<i class="bx bx-expand-alt me-1"></i>Expand all';
    });
  }
}());
