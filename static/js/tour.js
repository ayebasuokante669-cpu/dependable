/* The guided tour: what this app is, in six or seven stops.
 *
 * Built on Driver.js (MIT, vendored at static/js/vendor/driver.js.iife.js), which
 * does the parts that are genuinely fiddly -- cutting a hole in an overlay around
 * an arbitrary element, positioning a popover that stays on screen, trapping focus,
 * scrolling a target into view, and coping with a resize mid-tour. Writing that
 * again would be a worse version of it.
 *
 * WHAT THE TOUR POINTS AT
 *
 * Elements are found by `data-tour="<name>"`, never by class or by position. A
 * class is a styling decision and moves; `data-tour` is a promise that a step in
 * this file depends on that element. A step whose target is missing is dropped
 * rather than shown against the wrong thing -- which is what makes this safe
 * across roles, because a bursar's sidebar genuinely does not contain the entries a
 * proprietor's does.
 *
 * ROLE AWARENESS
 *
 * Each step declares the roles it is for. The tour a bursar gets talks about
 * recording money and chasing balances; a proprietor's talks about campuses and
 * reports. One list with a `roles` field on each step rather than four lists,
 * because most of the stops are the same stop and duplicating them is how they
 * drift apart.
 *
 * WHEN IT RUNS
 *
 * Automatically, once, for an account that has not seen it -- tracked in
 * localStorage under a key that includes the account id, so two people sharing an
 * office computer each get their own first run. Deliberately not a column on the
 * user: this is a UI preference of the same kind as the collapsed sidebar and the
 * theme, both of which already live there, and a migration to remember that
 * somebody clicked "Done" would be a heavier promise than the feature needs.
 *
 * The consequence, and it is the right trade: clearing site data or signing in on
 * a second machine offers the tour again. That is a tour, not a loss.
 *
 * And on demand, always, from the "Take a tour" button in the sidebar footer --
 * so it is never a thing you got once and cannot get back.
 *
 * Progressive: with this file blocked the button is inert and nothing else on the
 * page changes. Under `prefers-reduced-motion` Driver.js is told not to animate.
 */
(function () {
  'use strict';

  var STORAGE_PREFIX = 'schoolcord-tour-seen:';

  function config() {
    try {
      var el = document.getElementById('tour-config');
      return el ? JSON.parse(el.textContent) : null;
    } catch (e) {
      return null;
    }
  }

  /* ---------------------------------------------------------------------- *
   * The stops
   *
   * `roles` is the permission role, as apps/core/roles.py spells it. `element`
   * is a `data-tour` name, or omitted for a step that floats in the middle of
   * the screen -- the opening and closing ones, which are about the app rather
   * than about a thing on it.
   * ---------------------------------------------------------------------- */
  var ALL = ['platform_owner', 'school_owner', 'principal', 'bursar'];
  var SCHOOL = ['school_owner', 'principal', 'bursar'];

  var STEPS = [
    {
      roles: ALL,
      title: 'Welcome to SCHOOLCORD',
      text:
        'A quick tour of where things are. Six stops, and you can leave at any ' +
        'point with Escape — the "Take a tour" button in the sidebar brings it ' +
        'back whenever you want it.'
    },
    {
      roles: ALL,
      element: 'sidebar',
      side: 'right',
      title: 'Everything lives here',
      text:
        'Your sidebar only ever shows what your role can actually open, so there ' +
        'is nothing here that will turn you away. The arrow at the top narrows it ' +
        'to icons when you want the room.'
    },
    {
      roles: ALL,
      element: 'nav-home',
      side: 'right',
      title: 'Your dashboard',
      text: {
        platform_owner:
          'Every school on the platform, as a card you open. Inside each one you ' +
          'set its plan and switch its features on and off.',
        school_owner:
          'Your school at a glance — how many campuses, how many children, and ' +
          'where the fees stand across all of them.',
        principal:
          'Your campus at a glance — who is enrolled, in what, and which classes ' +
          'nobody has priced yet.',
        bursar:
          'Your term at a glance: what it is worth, how much has come in, and what ' +
          'is still outstanding.'
      }
    },
    {
      roles: SCHOOL,
      element: 'nav-students',
      side: 'right',
      title: 'The roster',
      text:
        'Every child on one list. You can add them one at a time, or bring your ' +
        'existing register over from a spreadsheet — it tells you about every bad ' +
        'row before it writes anything.'
    },
    {
      roles: ['school_owner', 'principal'],
      element: 'nav-fees',
      side: 'right',
      title: 'Fees, set once',
      text:
        'What each class owes, per term. Set it here and every balance, reminder ' +
        'and receipt in the app comes from these figures — nothing is typed in ' +
        'twice, so nothing can disagree.'
    },
    {
      roles: ['bursar'],
      element: 'nav-payments',
      side: 'right',
      title: 'Taking money',
      text:
        'Record a payment against a child and their balance moves immediately. ' +
        'A receipt handed in but not yet checked sits in the pending queue and ' +
        'counts toward nothing until you confirm it.'
    },
    {
      roles: ALL,
      element: 'nav-reports',
      side: 'right',
      title: 'The numbers, in full',
      text:
        'Collections, outstanding balances and payment status — per campus, per ' +
        'class, per method. Everything is derived when you open it, so it is never ' +
        'stale, and the whole thing downloads as a PDF.'
    },
    {
      roles: ALL,
      element: 'theme-toggle',
      side: 'bottom',
      title: 'Light or dark',
      text: 'Whichever is easier on your eyes. It remembers.'
    },
    {
      roles: SCHOOL,
      element: 'setup-progress',
      side: 'bottom',
      title: 'Finish setting up',
      text:
        'Classes, then fees, then your students. Each step uses what the last one ' +
        'set up, which is why they run in that order — and you can stop and come ' +
        'back to any of them.'
    },
    {
      roles: ALL,
      title: 'That is the tour',
      text:
        'Nothing you do here can break anything a colleague has done — what your ' +
        'role cannot change, it cannot reach. Press "Take a tour" in the sidebar ' +
        'any time you want this again.'
    }
  ];

  /** Resolve a step's text, which may vary by role. */
  function textFor(step, role) {
    if (typeof step.text === 'string') return step.text;
    return step.text[role] || step.text[Object.keys(step.text)[0]] || '';
  }

  /** The steps this role gets, minus any whose target is not on the page. */
  function stepsFor(role) {
    var out = [];
    STEPS.forEach(function (step) {
      if (step.roles.indexOf(role) === -1) return;
      var selector = null;
      if (step.element) {
        selector = '[data-tour="' + step.element + '"]';
        // Only the first copy. `_nav.html` renders twice -- the desktop rail and
        // the mobile drawer -- and highlighting the hidden one would cut a hole
        // in the overlay around nothing.
        var target = document.querySelector(selector);
        if (!target || !isVisible(target)) return;
      }
      out.push({
        element: selector || undefined,
        popover: {
          title: step.title,
          description: textFor(step, role),
          side: step.side || 'bottom',
          align: 'start'
        }
      });
    });
    return out;
  }

  function isVisible(el) {
    // `offsetParent` is null for anything `display: none`, which is exactly the
    // mobile drawer on a desktop and the desktop rail on a phone.
    return !!(el.offsetParent || el.getClientRects().length);
  }

  /* ---------------------------------------------------------------------- *
   * Running it
   * ---------------------------------------------------------------------- */
  function start(role) {
    var factory = window.driver && window.driver.js && window.driver.js.driver;
    if (!factory) return false;

    var steps = stepsFor(role);
    if (!steps.length) return false;

    var still = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    var tour = factory({
      steps: steps,
      animate: !still,
      overlayColor: '#0f2547',
      overlayOpacity: 0.62,
      smoothScroll: !still,
      allowClose: true,
      showProgress: true,
      progressText: '{{current}} of {{total}}',
      nextBtnText: 'Next',
      prevBtnText: 'Back',
      doneBtnText: 'Done',
      popoverClass: 'tour-popover'
    });
    tour.drive();
    return true;
  }

  function seenKey(userId) {
    return STORAGE_PREFIX + (userId || 'anon');
  }

  function hasSeen(userId) {
    try {
      return window.localStorage.getItem(seenKey(userId)) === 'yes';
    } catch (e) {
      // Storage blocked: treat it as seen rather than reopening the tour on
      // every single page load, which would be far worse than never offering it.
      return true;
    }
  }

  function remember(userId) {
    try {
      window.localStorage.setItem(seenKey(userId), 'yes');
    } catch (e) { /* private mode: the tour simply offers itself again */ }
  }

  function init() {
    var settings = config();
    if (!settings || !settings.role) return;

    // The button is always there, whether or not the tour has been seen.
    var triggers = document.querySelectorAll('[data-tour-start]');
    for (var i = 0; i < triggers.length; i++) {
      triggers[i].addEventListener('click', function (event) {
        event.preventDefault();
        remember(settings.userId);
        if (!start(settings.role)) {
          if (window.toast) {
            window.toast('The tour could not load. Try refreshing the page.', 'info');
          }
        }
      });
    }

    if (settings.autoStart && !hasSeen(settings.userId)) {
      remember(settings.userId);
      // A beat after paint. The dashboard's own reveal animations are still
      // arriving, and cutting a hole around an element mid-slide measures it
      // where it was rather than where it lands.
      window.setTimeout(function () { start(settings.role); }, 700);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
