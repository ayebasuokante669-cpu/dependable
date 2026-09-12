/* SCHOOLCORD interaction layer.
 *
 * No dependencies and no build step: this file is served as written. Every
 * behaviour here is an enhancement over markup that already works without it,
 * so a blocked or failed script costs polish and never content.
 *
 * Contents: theme switching, the setup-step card fan, and scroll reveals.
 */
(function () {
  'use strict';

  var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

  /* ====================================================================== *
   * Theme
   *
   * The stored value is one of "light", "dark" or absent. Absent means
   * "follow the OS", which is the default and stays live: a user who has
   * never chosen sees their system preference change under them, as they
   * should. The <html data-theme> attribute is applied by a tiny inline
   * script in <head> so the first paint is already correct; this half only
   * handles switching after load.
   * ====================================================================== */
  var STORAGE_KEY = 'schoolcord-theme';

  function storedTheme() {
    try {
      return window.localStorage.getItem(STORAGE_KEY);
    } catch (e) {
      // Private mode, or storage disabled by policy. Not an error worth
      // surfacing -- the theme simply stops persisting between pages.
      return null;
    }
  }

  function systemTheme() {
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }

  function activeTheme() {
    return storedTheme() || systemTheme();
  }

  function applyTheme(theme, animate) {
    var root = document.documentElement;

    // Colour-only transition, and only for the swap itself -- left on
    // permanently it would smear every hover state in the app.
    if (animate && !reduceMotion.matches) {
      root.classList.add('theme-anim');
      window.setTimeout(function () { root.classList.remove('theme-anim'); }, 220);
    }

    root.setAttribute('data-theme', theme);

    // Keep the mobile browser chrome in step with the page.
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) {
      meta.setAttribute('content', getComputedStyle(root)
        .getPropertyValue('--color-panel').trim() || '#0f2547');
    }

    syncToggles(theme);
  }

  function syncToggles(theme) {
    var options = document.querySelectorAll('[data-theme-set]');
    for (var i = 0; i < options.length; i++) {
      var pressed = options[i].getAttribute('data-theme-set') === theme;
      options[i].setAttribute('aria-pressed', pressed ? 'true' : 'false');
    }
  }

  function initTheme() {
    // The markup ships the switch hidden; it only makes sense once the code
    // that services it is running.
    var toggles = document.querySelectorAll('[data-theme-toggle]');
    for (var t = 0; t < toggles.length; t++) toggles[t].hidden = false;

    syncToggles(activeTheme());

    document.addEventListener('click', function (event) {
      var option = event.target.closest && event.target.closest('[data-theme-set]');
      if (!option) return;
      var choice = option.getAttribute('data-theme-set');

      // Replay the icon's entrance. Removing the class and forcing a reflow
      // before re-adding it is what lets the same animation run twice.
      option.classList.remove('is-switching');
      void option.offsetWidth;
      option.classList.add('is-switching');

      try {
        window.localStorage.setItem(STORAGE_KEY, choice);
      } catch (e) { /* see storedTheme */ }
      applyTheme(choice, true);
    });

    // Only meaningful while the user has made no explicit choice.
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function () {
      if (!storedTheme()) applyTheme(systemTheme(), true);
    });
  }

  /* ====================================================================== *
   * The card fan
   *
   * The four setup steps live in an ordinary CSS grid and are laid out by it
   * at all times. "Stacked" is not a different layout -- it is a transform
   * per card, measured from where the grid already put it, which is what
   * makes the movement a single interpolable property and keeps it on the
   * compositor.
   *
   * Consequences worth keeping: with JS off the grid renders as-is; and
   * re-measuring on resize means the effect survives a breakpoint change
   * instead of snapping.
   * ====================================================================== */
  var PEEK = 12;      // px each card behind the top one drops by
  var SHIFT = 7;      // px of horizontal spread across the deck, centred
  var SHRINK = 0.035; // scale lost per card of depth
  var TILT = 1.1;     // deg, alternating -- a hand-placed pile, not a ruler

  function initFan(root) {
    var grid = root.querySelector('[data-fan-grid]');
    var trigger = root.querySelector('[data-fan-trigger]');
    var viewport = root.querySelector('[data-fan-viewport]');
    var items = Array.prototype.slice.call(root.querySelectorAll('[data-fan-item]'));
    if (!grid || !trigger || !viewport || items.length < 2) return;

    var stacked = false;
    var offsets = [];

    function measure() {
      var previous = items.map(function (el) { return el.style.transform; });
      items.forEach(function (el) { el.style.transform = 'none'; });

      var base = items[0].getBoundingClientRect();
      var centre = grid.getBoundingClientRect();
      var centreX = centre.left + centre.width / 2;

      offsets = items.map(function (el) {
        var rect = el.getBoundingClientRect();
        return {
          // Horizontally, every card converges on the middle of the grid, so
          // the deck sits centred and opens outwards in both directions --
          // the first two cards travel left, the last two right.
          dx: centreX - (rect.left + rect.width / 2),
          // Vertically, only as far as the top row. The viewport crops to the
          // deck's height, and a deck centred in a tall (mobile) grid would
          // land below that crop.
          dy: base.top - rect.top
        };
      });

      items.forEach(function (el, i) { el.style.transform = previous[i]; });
      return base.height;
    }

    function stackTransform(i) {
      var o = offsets[i];
      // Spread the deck's own offset around its middle rather than running it
      // off to one side, so the pile stays centred on the grid's centre line.
      var fromMiddle = i - (items.length - 1) / 2;
      var tilt = i === 0 ? 0 : (i % 2 ? TILT : -TILT);
      return 'translate(' + (o.dx + fromMiddle * SHIFT) + 'px, ' +
                            (o.dy + i * PEEK) + 'px)' +
             ' rotate(' + tilt + 'deg)' +
             ' scale(' + (1 - i * SHRINK) + ')';
    }

    function render(height) {
      items.forEach(function (el, i) {
        // Front card first in the paint order, so the pile has a clear top.
        el.style.zIndex = String(items.length - i);
        el.style.transform = stacked ? stackTransform(i) : '';
      });

      // The grid still reserves its full height, which would leave a hole
      // under the pile. The viewport crops that down and animates the change.
      viewport.style.height = (stacked
        ? height + (items.length - 1) * PEEK
        : grid.getBoundingClientRect().height) + 'px';
    }

    function setStacked(next, animate) {
      stacked = next;
      root.classList.toggle('is-stacked', stacked);
      trigger.setAttribute('aria-expanded', stacked ? 'false' : 'true');
      trigger.hidden = !stacked;

      // A silent re-form has to kill the card transitions as well as the
      // viewport's, or the deck is still sliding back together when you
      // scroll up to it. The reflow between add and remove is what commits
      // the no-transition state -- without it both class changes collapse
      // into one style recalculation and nothing is suppressed.
      if (!animate) root.classList.add('fan-instant');
      render(measure());
      if (!animate) {
        void root.offsetHeight;
        root.classList.remove('fan-instant');
      }
    }

    function open() {
      if (!stacked) return;
      setStacked(false, true);
      // Send focus to the first card's heading so the keyboard lands where
      // the eye does, rather than back at the top of the page.
      var heading = items[0].querySelector('h3');
      if (heading) {
        heading.setAttribute('tabindex', '-1');
        heading.focus({ preventScroll: true });
      }
    }

    trigger.addEventListener('click', open);
    // The deck itself is the obvious thing to click. The button stays as the
    // keyboard and screen-reader route to the same action.
    viewport.addEventListener('click', open);

    var resizeTimer;
    window.addEventListener('resize', function () {
      window.clearTimeout(resizeTimer);
      resizeTimer = window.setTimeout(function () { render(measure()); }, 120);
    });

    // Reduced motion never sees the pile: it would be a state change with no
    // transition to explain it, which is worse than simply showing the four.
    if (reduceMotion.matches) {
      setStacked(false, false);
      trigger.hidden = true;
      return;
    }

    setStacked(true, false);

    // Scrolling past the cards re-forms the deck, so coming back to it shows
    // the pile again rather than the row you already opened -- the effect is
    // repeatable without reloading.
    //
    // The observed element is the deck itself, not the section around it.
    // Watching the section meant waiting for its heading, copy and padding to
    // clear the viewport too, which on a tall screen never quite happens.
    //
    // Every entry is checked rather than just entries[0]: a burst of scrolling
    // delivers several records in one callback, and only the last is current.
    if ('IntersectionObserver' in window) {
      // Held in the closure rather than left anonymous: the closure is kept
      // alive by the listeners above, so the observer cannot be collected out
      // from under the effect.
      var reformObserver = new IntersectionObserver(function (entries) {
        var latest = entries[entries.length - 1];
        if (!latest.isIntersecting && !stacked) setStacked(true, false);
      }, { threshold: 0 });
      reformObserver.observe(viewport);
    }
  }

  /* ====================================================================== *
   * Scroll reveal
   *
   * Armed from JS rather than in the stylesheet: the markup ships visible, so
   * the failure mode of this whole section is "no animation", never "no
   * content".
   * ====================================================================== */
  function initReveals() {
    var targets = Array.prototype.slice.call(document.querySelectorAll('.reveal'));
    if (!targets.length || reduceMotion.matches || !('IntersectionObserver' in window)) return;

    targets.forEach(function (el) { el.classList.add('reveal-armed'); });

    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        entry.target.classList.remove('reveal-armed');
        entry.target.classList.add('reveal-in');
        observer.unobserve(entry.target);
      });
    }, { rootMargin: '0px 0px -10% 0px', threshold: 0.05 });

    targets.forEach(function (el) { observer.observe(el); });
  }

  function init() {
    initTheme();
    initReveals();
    var fans = document.querySelectorAll('[data-fan]');
    for (var i = 0; i < fans.length; i++) initFan(fans[i]);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
