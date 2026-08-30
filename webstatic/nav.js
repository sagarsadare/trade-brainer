/* Shared page-switcher for TradeBrain.
 *
 * One source of truth for both dashboards. Include with:
 *   <div id="tbNav"></div>
 *   <script src="/static/nav.js?v=2" data-active="pcr"></script>
 *
 * Renders a hamburger button labelled with the CURRENT page, so it reads as a
 * page switcher rather than a decorative icon. Always visible at every width --
 * the previous inline links wrapped onto a second line on narrow viewports and
 * were easy to miss entirely.
 */
(function () {
  "use strict";

  var PAGES = [
    { key: "pcr", href: "/", label: "PCR",
      sub: "15-min Put-Call Ratio, live + backfilled" },
    { key: "calendar", href: "/strategy", label: "Math Calendar Spread",
      sub: "Premium-driven calendar spread, chain to order" },
    { key: "hilega", href: "/hilega", label: "Hilega Milega Strategy",
      sub: "RSI(9) + EMA(3) + WMA(21), multi-timeframe gated" }
  ];

  var script = document.currentScript;
  var active = (script && script.dataset.active) || "";

  var CSS = [
    "#tbNav{position:relative;display:inline-flex;margin-left:8px;z-index:50}",
    "#tbNav .tb-btn{display:inline-flex;align-items:center;gap:9px;cursor:pointer;",
    "  background:var(--panel-2,#1a2330);color:var(--ink,#e6edf7);",
    "  border:1px solid var(--line,#243044);border-radius:8px;",
    "  padding:7px 12px;font:inherit;font-size:13px;line-height:1;white-space:nowrap}",
    "#tbNav .tb-btn:hover{border-color:#3a4c68}",
    "#tbNav .tb-btn[aria-expanded='true']{border-color:#2563eb;background:#1d4ed826}",
    "#tbNav .tb-bars{display:inline-flex;flex-direction:column;gap:3px;flex:0 0 auto}",
    "#tbNav .tb-bars i{display:block;width:15px;height:2px;border-radius:2px;",
    "  background:var(--muted,#8b9bb4)}",
    "#tbNav .tb-btn:hover .tb-bars i,#tbNav .tb-btn[aria-expanded='true'] .tb-bars i",
    "  {background:var(--ink,#e6edf7)}",
    "#tbNav .tb-cur{font-weight:600}",
    "#tbNav .tb-caret{color:var(--dim,#5b6b82);font-size:10px}",
    "#tbNav .tb-menu{position:absolute;top:calc(100% + 7px);left:0;min-width:290px;",
    "  background:var(--panel,#131a24);border:1px solid var(--line,#243044);",
    "  border-radius:10px;padding:6px;box-shadow:0 14px 40px #00000073;display:none}",
    "#tbNav .tb-menu.open{display:block}",
    "#tbNav .tb-menu a{display:block;padding:9px 11px;border-radius:7px;",
    "  text-decoration:none;color:var(--muted,#8b9bb4);border:1px solid transparent}",
    "#tbNav .tb-menu a:hover,#tbNav .tb-menu a:focus{background:var(--panel-2,#1a2330);",
    "  color:var(--ink,#e6edf7);outline:none;border-color:var(--line,#243044)}",
    "#tbNav .tb-menu a.on{background:#1d4ed826;border-color:#2563eb;color:var(--ink,#e6edf7)}",
    "#tbNav .tb-lbl{display:flex;align-items:center;gap:8px;font-size:13.5px;font-weight:600}",
    "#tbNav .tb-sub{font-size:11.5px;color:var(--dim,#5b6b82);margin-top:2px;font-weight:400}",
    "#tbNav .tb-dot{width:6px;height:6px;border-radius:50%;flex:0 0 auto;background:#2563eb}",
    "#tbNav .tb-menu a:not(.on) .tb-dot{background:transparent}",
    "@media (max-width:560px){#tbNav .tb-menu{min-width:min(290px,calc(100vw - 40px))}}"
  ].join("");

  function mount() {
    var root = document.getElementById("tbNav");
    if (!root) return;

    var style = document.createElement("style");
    style.textContent = CSS;
    document.head.appendChild(style);

    var current = PAGES.filter(function (p) { return p.key === active; })[0] || PAGES[0];

    var btn = document.createElement("button");
    btn.className = "tb-btn";
    btn.type = "button";
    btn.setAttribute("aria-haspopup", "true");
    btn.setAttribute("aria-expanded", "false");
    btn.setAttribute("aria-label", "Switch page. Current page: " + current.label);
    btn.innerHTML = '<span class="tb-bars" aria-hidden="true"><i></i><i></i><i></i></span>'
      + '<span class="tb-cur"></span><span class="tb-caret" aria-hidden="true">&#9660;</span>';
    btn.querySelector(".tb-cur").textContent = current.label;

    var menu = document.createElement("div");
    menu.className = "tb-menu";
    menu.setAttribute("role", "menu");
    PAGES.forEach(function (p) {
      var a = document.createElement("a");
      a.href = p.href;
      a.setAttribute("role", "menuitem");
      if (p.key === current.key) {
        a.className = "on";
        a.setAttribute("aria-current", "page");
      }
      var lbl = document.createElement("span");
      lbl.className = "tb-lbl";
      var dot = document.createElement("span");
      dot.className = "tb-dot";
      lbl.appendChild(dot);
      lbl.appendChild(document.createTextNode(p.label));
      var sub = document.createElement("span");
      sub.className = "tb-sub";
      sub.textContent = p.sub;
      a.appendChild(lbl);
      a.appendChild(sub);
      menu.appendChild(a);
    });

    root.appendChild(btn);
    root.appendChild(menu);

    function place() {
      // Default to left-aligned under the button, but flip to right-aligned
      // when that would run past the viewport -- the button sits well into
      // the header, so a fixed left anchor pushes the panel off screen and
      // gives the whole page a horizontal scrollbar.
      menu.style.left = "0";
      menu.style.right = "auto";
      var r = menu.getBoundingClientRect();
      if (r.right > window.innerWidth - 8) {
        menu.style.left = "auto";
        menu.style.right = "0";
        if (menu.getBoundingClientRect().left < 8) {
          // Too wide to sit under the button either way: pin it to the
          // viewport and let the panel shrink.
          menu.style.right = "auto";
          menu.style.left = (8 - root.getBoundingClientRect().left) + "px";
          menu.style.maxWidth = (window.innerWidth - 16) + "px";
        }
      }
      // Final clamp. Whatever the branch above chose, the panel must not stick
      // out of the viewport -- even a few pixels gives the whole page a
      // horizontal scrollbar.
      var f = menu.getBoundingClientRect();
      var over = f.right - (window.innerWidth - 8);
      if (over > 0) {
        var rootLeft = root.getBoundingClientRect().left;
        menu.style.right = "auto";
        menu.style.left = Math.max(8 - rootLeft, f.left - rootLeft - over) + "px";
        menu.style.maxWidth = (window.innerWidth - 16) + "px";
      }
    }

    function setOpen(open) {
      menu.classList.toggle("open", open);
      btn.setAttribute("aria-expanded", open ? "true" : "false");
      if (open) {
        place();
        var first = menu.querySelector("a");
        if (first) first.focus();
      }
    }

    window.addEventListener("resize", function () {
      if (menu.classList.contains("open")) place();
    });

    btn.addEventListener("click", function (e) {
      e.stopPropagation();
      setOpen(!menu.classList.contains("open"));
    });
    document.addEventListener("click", function (e) {
      if (!root.contains(e.target)) setOpen(false);
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && menu.classList.contains("open")) {
        setOpen(false);
        btn.focus();
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mount);
  } else {
    mount();
  }
})();
