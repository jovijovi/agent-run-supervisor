/* Agent Run Supervisor — bounded documentation behaviour.
 *
 * Two reading affordances and nothing else. Both exist because a wide table or
 * a long code line otherwise forces the whole page to scroll sideways, and a
 * mouse-free reader cannot reach the overflow at all.
 *
 * There is no analytics, no network call, no console, and no live data here.
 */
(function () {
  "use strict";

  var SCROLLABLE = "ars-scroll";

  /* Give an element its own scroll box, reachable from the keyboard.
   * `tabindex="0"` on a scroll container is the documented way to let arrow
   * keys reach overflowing content; the label names what is being scrolled. */
  function makeScrollable(element, label) {
    if (!element || element.dataset.arsScroll === "1") {
      return;
    }
    element.dataset.arsScroll = "1";
    element.classList.add(SCROLLABLE);
    element.setAttribute("tabindex", "0");
    element.setAttribute("role", "region");
    element.setAttribute("aria-label", label);
  }

  function enhance() {
    var tables = document.querySelectorAll(".md-typeset table:not([class])");
    Array.prototype.forEach.call(tables, function (table) {
      var parent = table.parentNode;
      if (!parent) {
        return;
      }
      if (parent.classList && parent.classList.contains(SCROLLABLE)) {
        makeScrollable(parent, "Table, scrollable");
        return;
      }
      var box = document.createElement("div");
      parent.insertBefore(box, table);
      box.appendChild(table);
      makeScrollable(box, "Table, scrollable");
    });

    var blocks = document.querySelectorAll(".md-typeset pre");
    Array.prototype.forEach.call(blocks, function (block) {
      makeScrollable(block, "Code block, scrollable");
    });
  }

  /* Material's instant navigation swaps the article without a page load, so the
   * enhancement has to re-run per document. `document$` is Material's own
   * per-document observable; the listener fallback covers a plain page load. */
  if (typeof window.document$ !== "undefined" && window.document$.subscribe) {
    window.document$.subscribe(enhance);
  } else if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", enhance);
  } else {
    enhance();
  }
})();
