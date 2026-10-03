/* A calendar you can get out of quickly.
 *
 * Every date on this platform was a bare `<input type="date">`. That is correct,
 * accessible and free -- and it is also whatever the browser feels like, which on
 * the two fields people use most is the wrong shape of control:
 *
 *   - a child's **date of birth** is seven or eight years back. Chrome's picker
 *     steps a month at a time, so that is ninety clicks, and its year spinner is
 *     a 14px chevron;
 *   - a **term's due date** or a payment-filter month is a few months either way,
 *     and the native picker makes you aim at an arrow to get there.
 *
 * So: day -> month -> year, two clicks to cross a decade. Press the "October 2026"
 * header and the grid becomes twelve months; press the year in *that* and it
 * becomes twelve years. That is the whole design, and it is the answer to "make
 * back-navigation fast".
 *
 * PROGRESSIVE, AND THE NATIVE INPUT IS STILL THE INPUT
 *
 * Nothing here replaces the field. The `<input type="date">` keeps its name, its
 * value and its validation; this draws a popover that writes into it and fires
 * `change`. With the script blocked, or on a browser where this would be worse
 * than the native one, the field is exactly what it was -- which is why the type
 * is never switched to `text`.
 *
 * Touch is the case where the native control genuinely wins: a phone's date wheel
 * is better than any popover, it is what people there expect, and it does not
 * need the viewport. So on a device with no fine pointer this does nothing at all.
 *
 * KEYBOARD
 *
 * Arrows move by a day, PageUp/PageDown by a month, Home/End to the ends of the
 * week, Enter picks, Escape closes and returns focus to the field. The grid is a
 * real `<table>` with `role="grid"`, so a screen reader reads it as a calendar
 * rather than as forty-two buttons.
 */
(function () {
  'use strict';

  var MONTHS = ['January', 'February', 'March', 'April', 'May', 'June',
                'July', 'August', 'September', 'October', 'November', 'December'];
  var SHORT_MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                      'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  //: Monday first. Nigerian school weeks start on Monday, and so does the term.
  var DAY_INITIALS = ['M', 'T', 'W', 'T', 'F', 'S', 'S'];
  var DAY_NAMES = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday',
                   'Saturday', 'Sunday'];

  var VIEW_DAYS = 'days';
  var VIEW_MONTHS = 'months';
  var VIEW_YEARS = 'years';
  //: How many years the year grid shows at once. Twelve, so it is the same
  //: three-by-four shape as the month grid and the eye does not have to relearn it.
  var YEAR_SPAN = 12;

  var open = null;   // the one open picker, if any

  /* ---------------------------------------------------------------------- *
   * Dates, as plain local values
   *
   * `new Date('2026-10-03')` parses as UTC and can come back as the 2nd in a
   * negative offset. Everything here is built from explicit parts for that
   * reason, and `iso()` formats by hand rather than via toISOString().
   * ---------------------------------------------------------------------- */
  function parseISO(value) {
    var match = /^(\d{4})-(\d{2})-(\d{2})$/.exec((value || '').trim());
    if (!match) return null;
    var date = new Date(+match[1], +match[2] - 1, +match[3]);
    return isNaN(date.getTime()) ? null : date;
  }

  function iso(date) {
    function pad(n) { return (n < 10 ? '0' : '') + n; }
    return date.getFullYear() + '-' + pad(date.getMonth() + 1) + '-' + pad(date.getDate());
  }

  function startOfDay(date) {
    return new Date(date.getFullYear(), date.getMonth(), date.getDate());
  }

  function addDays(date, n) {
    return new Date(date.getFullYear(), date.getMonth(), date.getDate() + n);
  }

  function addMonths(date, n) {
    // Clamped: 31 March minus one month is 28 February, not 3 March.
    var target = new Date(date.getFullYear(), date.getMonth() + n, 1);
    var last = new Date(target.getFullYear(), target.getMonth() + 1, 0).getDate();
    target.setDate(Math.min(date.getDate(), last));
    return target;
  }

  function sameDay(a, b) {
    return !!a && !!b && a.getFullYear() === b.getFullYear() &&
      a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  }

  /** Monday-first weekday index, 0..6. */
  function weekday(date) {
    return (date.getDay() + 6) % 7;
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function icon(d) {
    return '<svg class="size-4" fill="none" viewBox="0 0 24 24" stroke-width="2" ' +
      'stroke="currentColor" aria-hidden="true"><path stroke-linecap="round" ' +
      'stroke-linejoin="round" d="' + d + '" /></svg>';
  }

  var CHEVRON_LEFT = 'M15.75 19.5 8.25 12l7.5-7.5';
  var CHEVRON_RIGHT = 'm8.25 4.5 7.5 7.5-7.5 7.5';

  /* ====================================================================== *
   * One picker, bound to one input
   * ====================================================================== */
  function Picker(input) {
    this.input = input;
    this.min = parseISO(input.getAttribute('min'));
    this.max = parseISO(input.getAttribute('max'));
    this.view = VIEW_DAYS;
    this.cursor = null;     // the month/year being looked at
    this.panel = null;
    this.build();
  }

  Picker.prototype.build = function () {
    var self = this;

    var wrap = el('div', 'dp-wrap');
    this.input.parentNode.insertBefore(wrap, this.input);
    wrap.appendChild(this.input);
    this.input.classList.add('dp-input');

    var button = el('button', 'dp-trigger');
    button.type = 'button';
    button.setAttribute('aria-haspopup', 'dialog');
    button.setAttribute('aria-expanded', 'false');
    button.title = 'Open the calendar';
    button.innerHTML =
      '<span class="sr-only">Open the calendar</span>' +
      icon('M6.75 3v2.25M17.25 3v2.25M3 18.75V7.5a2.25 2.25 0 0 1 2.25-2.25h13.5' +
           'A2.25 2.25 0 0 1 21 7.5v11.25m-18 0A2.25 2.25 0 0 0 5.25 21h13.5' +
           'A2.25 2.25 0 0 0 21 18.75m-18 0v-7.5A2.25 2.25 0 0 1 5.25 9h13.5' +
           'A2.25 2.25 0 0 1 21 11.25v7.5');
    button.addEventListener('click', function () {
      if (self.panel) self.close();
      else self.open();
    });
    wrap.appendChild(button);
    this.trigger = button;
    this.wrap = wrap;
  };

  Picker.prototype.selected = function () {
    return parseISO(this.input.value);
  };

  Picker.prototype.outOfRange = function (date) {
    if (this.min && date < startOfDay(this.min)) return true;
    if (this.max && date > startOfDay(this.max)) return true;
    return false;
  };

  Picker.prototype.open = function () {
    if (open && open !== this) open.close();
    open = this;

    this.cursor = this.selected() || this.sensibleStart();
    this.view = VIEW_DAYS;

    this.panel = el('div', 'dp-panel');
    this.panel.setAttribute('role', 'dialog');
    this.panel.setAttribute('aria-modal', 'false');
    this.panel.setAttribute('aria-label', 'Choose a date');
    this.wrap.appendChild(this.panel);
    this.trigger.setAttribute('aria-expanded', 'true');

    this.render();
    this.position();

    var self = this;
    this.onDocClick = function (event) {
      if (!self.wrap.contains(event.target)) self.close();
    };
    this.onKey = function (event) {
      if (event.key === 'Escape') {
        event.stopPropagation();
        self.close();
        self.input.focus();
      }
    };
    this.onResize = function () { self.position(); };
    document.addEventListener('mousedown', this.onDocClick);
    this.panel.addEventListener('keydown', this.onKey);
    window.addEventListener('resize', this.onResize);
    window.addEventListener('scroll', this.onResize, true);
  };

  /** Where to start when the field is empty.
   *
   * Today, unless the field's own range says today is impossible -- a date of
   * birth with `max` last year should not open on a month every cell of which is
   * disabled.
   */
  Picker.prototype.sensibleStart = function () {
    var today = startOfDay(new Date());
    if (this.max && today > startOfDay(this.max)) return startOfDay(this.max);
    if (this.min && today < startOfDay(this.min)) return startOfDay(this.min);
    return today;
  };

  Picker.prototype.close = function () {
    if (!this.panel) return;
    document.removeEventListener('mousedown', this.onDocClick);
    window.removeEventListener('resize', this.onResize);
    window.removeEventListener('scroll', this.onResize, true);
    this.panel.remove();
    this.panel = null;
    this.trigger.setAttribute('aria-expanded', 'false');
    if (open === this) open = null;
  };

  /** Keep the panel on screen: above the field if there is no room below. */
  Picker.prototype.position = function () {
    if (!this.panel) return;
    var box = this.wrap.getBoundingClientRect();
    var height = this.panel.offsetHeight;
    var below = window.innerHeight - box.bottom;
    this.panel.classList.toggle('dp-panel-above', below < height + 12 && box.top > height);
    // And horizontally, for a field near the right edge.
    var right = window.innerWidth - box.left - this.panel.offsetWidth;
    this.panel.classList.toggle('dp-panel-right', right < 8);
  };

  Picker.prototype.render = function () {
    this.panel.innerHTML = '';
    if (this.view === VIEW_DAYS) this.renderDays();
    else if (this.view === VIEW_MONTHS) this.renderMonths();
    else this.renderYears();
  };

  /* ---------------------------------------------------------------------- *
   * The header: step, and the label that zooms out
   * ---------------------------------------------------------------------- */
  Picker.prototype.header = function (label, zoomView, onStep, stepLabels) {
    var self = this;
    var bar = el('div', 'dp-head');

    var prev = el('button', 'dp-step');
    prev.type = 'button';
    prev.title = stepLabels[0];
    prev.innerHTML = '<span class="sr-only">' + stepLabels[0] + '</span>' +
      icon(CHEVRON_LEFT);
    prev.addEventListener('click', function () { onStep(-1); });

    var next = el('button', 'dp-step');
    next.type = 'button';
    next.title = stepLabels[1];
    next.innerHTML = '<span class="sr-only">' + stepLabels[1] + '</span>' +
      icon(CHEVRON_RIGHT);
    next.addEventListener('click', function () { onStep(1); });

    var zoom = el('button', 'dp-zoom', label);
    zoom.type = 'button';
    if (zoomView) {
      // The fast way back. Two of these cross a decade.
      zoom.title = zoomView === VIEW_MONTHS
        ? 'Pick a month' : 'Pick a year';
      zoom.addEventListener('click', function () {
        self.view = zoomView;
        self.render();
        self.position();
        var first = self.panel.querySelector('.dp-cell:not(:disabled)');
        if (first) first.focus();
      });
    } else {
      zoom.disabled = true;
    }

    bar.appendChild(prev);
    bar.appendChild(zoom);
    bar.appendChild(next);
    return bar;
  };

  /* ---------------------------------------------------------------------- *
   * Days
   * ---------------------------------------------------------------------- */
  Picker.prototype.renderDays = function () {
    var self = this;
    var cursor = this.cursor;
    var selected = this.selected();
    var today = startOfDay(new Date());

    this.panel.appendChild(this.header(
      MONTHS[cursor.getMonth()] + ' ' + cursor.getFullYear(),
      VIEW_MONTHS,
      function (step) {
        self.cursor = addMonths(self.cursor, step);
        self.render();
      },
      ['Previous month', 'Next month']
    ));

    var table = el('table', 'dp-grid');
    table.setAttribute('role', 'grid');

    var head = el('thead');
    var headRow = el('tr');
    DAY_INITIALS.forEach(function (initial, index) {
      var cell = el('th', 'dp-dow');
      cell.scope = 'col';
      cell.setAttribute('abbr', DAY_NAMES[index]);
      cell.appendChild(el('span', null, initial));
      headRow.appendChild(cell);
    });
    head.appendChild(headRow);
    table.appendChild(head);

    var body = el('tbody');
    var first = new Date(cursor.getFullYear(), cursor.getMonth(), 1);
    var day = addDays(first, -weekday(first));

    for (var week = 0; week < 6; week++) {
      var row = el('tr');
      for (var d = 0; d < 7; d++) {
        row.appendChild(this.dayCell(day, cursor, selected, today));
        day = addDays(day, 1);
      }
      body.appendChild(row);
      // Six rows only when the month needs them, so the panel does not jump
      // height between a February and a May.
      if (day.getMonth() !== cursor.getMonth() && week >= 4) break;
    }
    table.appendChild(body);
    this.panel.appendChild(table);
    this.panel.appendChild(this.footer());
  };

  Picker.prototype.dayCell = function (day, cursor, selected, today) {
    var self = this;
    var date = new Date(day.getFullYear(), day.getMonth(), day.getDate());
    var cell = el('td', 'dp-day-cell');
    var button = el('button', 'dp-cell dp-day', String(date.getDate()));
    button.type = 'button';
    button.setAttribute('data-date', iso(date));

    if (date.getMonth() !== cursor.getMonth()) button.classList.add('dp-outside');
    if (sameDay(date, today)) button.classList.add('dp-today');
    if (sameDay(date, selected)) {
      button.classList.add('dp-selected');
      button.setAttribute('aria-selected', 'true');
    }
    if (this.outOfRange(date)) button.disabled = true;

    // The full date as the accessible name: "12" alone tells a screen-reader
    // user nothing about which month they are in.
    button.setAttribute('aria-label',
      DAY_NAMES[weekday(date)] + ' ' + date.getDate() + ' ' +
      MONTHS[date.getMonth()] + ' ' + date.getFullYear());

    button.addEventListener('click', function () { self.choose(date); });
    button.addEventListener('keydown', function (event) {
      self.onDayKey(event, date);
    });
    cell.appendChild(button);
    return cell;
  };

  Picker.prototype.onDayKey = function (event, date) {
    var step = {
      ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7
    }[event.key];
    var target = null;

    if (step !== undefined) target = addDays(date, step);
    else if (event.key === 'PageUp') target = addMonths(date, -1);
    else if (event.key === 'PageDown') target = addMonths(date, 1);
    else if (event.key === 'Home') target = addDays(date, -weekday(date));
    else if (event.key === 'End') target = addDays(date, 6 - weekday(date));
    else return;

    event.preventDefault();
    this.cursor = target;
    this.render();
    var next = this.panel.querySelector('[data-date="' + iso(target) + '"]');
    if (next && !next.disabled) next.focus();
  };

  /* ---------------------------------------------------------------------- *
   * Months, and years -- the fast way back
   * ---------------------------------------------------------------------- */
  Picker.prototype.renderMonths = function () {
    var self = this;
    var year = this.cursor.getFullYear();
    var selected = this.selected();

    this.panel.appendChild(this.header(
      String(year),
      VIEW_YEARS,
      function (step) {
        self.cursor = new Date(year + step, self.cursor.getMonth(), 1);
        self.render();
      },
      ['Previous year', 'Next year']
    ));

    var grid = el('div', 'dp-chunks');
    SHORT_MONTHS.forEach(function (name, index) {
      var button = el('button', 'dp-cell dp-chunk', name);
      button.type = 'button';
      button.setAttribute('aria-label', MONTHS[index] + ' ' + year);
      if (selected && selected.getFullYear() === year &&
          selected.getMonth() === index) {
        button.classList.add('dp-selected');
      }
      if (self.monthOutOfRange(year, index)) button.disabled = true;
      button.addEventListener('click', function () {
        self.cursor = new Date(year, index, 1);
        self.view = VIEW_DAYS;
        self.render();
        self.position();
        var focusable = self.panel.querySelector('.dp-day:not(.dp-outside):not(:disabled)');
        if (focusable) focusable.focus();
      });
      grid.appendChild(button);
    });
    this.panel.appendChild(grid);
    this.panel.appendChild(this.footer());
  };

  Picker.prototype.monthOutOfRange = function (year, month) {
    var last = new Date(year, month + 1, 0);
    var first = new Date(year, month, 1);
    if (this.min && last < startOfDay(this.min)) return true;
    if (this.max && first > startOfDay(this.max)) return true;
    return false;
  };

  Picker.prototype.renderYears = function () {
    var self = this;
    var year = this.cursor.getFullYear();
    // Anchor the block so paging is stable: the same twelve years every time
    // you come back to this decade, rather than a window centred on wherever
    // the cursor happens to be.
    var start = Math.floor(year / YEAR_SPAN) * YEAR_SPAN;
    var selected = this.selected();

    this.panel.appendChild(this.header(
      start + ' – ' + (start + YEAR_SPAN - 1),
      null,
      function (step) {
        self.cursor = new Date(year + step * YEAR_SPAN, self.cursor.getMonth(), 1);
        self.render();
      },
      ['Earlier years', 'Later years']
    ));

    var grid = el('div', 'dp-chunks');
    for (var offset = 0; offset < YEAR_SPAN; offset++) {
      (function (value) {
        var button = el('button', 'dp-cell dp-chunk', String(value));
        button.type = 'button';
        if (selected && selected.getFullYear() === value) {
          button.classList.add('dp-selected');
        }
        if (self.yearOutOfRange(value)) button.disabled = true;
        button.addEventListener('click', function () {
          self.cursor = new Date(value, self.cursor.getMonth(), 1);
          self.view = VIEW_MONTHS;
          self.render();
          self.position();
          var focusable = self.panel.querySelector('.dp-chunk:not(:disabled)');
          if (focusable) focusable.focus();
        });
        grid.appendChild(button);
      })(start + offset);
    }
    this.panel.appendChild(grid);
    this.panel.appendChild(this.footer());
  };

  Picker.prototype.yearOutOfRange = function (year) {
    if (this.min && year < this.min.getFullYear()) return true;
    if (this.max && year > this.max.getFullYear()) return true;
    return false;
  };

  /* ---------------------------------------------------------------------- *
   * Footer: today, and clear
   * ---------------------------------------------------------------------- */
  Picker.prototype.footer = function () {
    var self = this;
    var bar = el('div', 'dp-foot');

    var today = startOfDay(new Date());
    var jump = el('button', 'dp-foot-btn', 'Today');
    jump.type = 'button';
    jump.disabled = this.outOfRange(today);
    jump.addEventListener('click', function () { self.choose(today); });
    bar.appendChild(jump);

    if (!this.input.required) {
      var clear = el('button', 'dp-foot-btn', 'Clear');
      clear.type = 'button';
      clear.addEventListener('click', function () {
        self.input.value = '';
        self.fire();
        self.close();
        self.input.focus();
      });
      bar.appendChild(clear);
    }
    return bar;
  };

  Picker.prototype.choose = function (date) {
    if (this.outOfRange(date)) return;
    this.input.value = iso(date);
    this.fire();
    this.close();
    this.input.focus();
  };

  /** Let anything listening to the field know, as a real edit would. */
  Picker.prototype.fire = function () {
    this.input.dispatchEvent(new Event('input', { bubbles: true }));
    this.input.dispatchEvent(new Event('change', { bubbles: true }));
  };

  /* ====================================================================== *
   * Boot
   * ====================================================================== */
  function init() {
    // Touch first. A phone's own date wheel is better than any popover, it is
    // what people there expect, and it does not take the viewport. `any-pointer:
    // fine` is the question worth asking -- a tablet with a stylus or a
    // trackpad gets the calendar, a thumb does not.
    if (!window.matchMedia('(any-pointer: fine)').matches) return;

    var inputs = document.querySelectorAll(
      'input[type="date"]:not([data-no-datepicker])'
    );
    for (var i = 0; i < inputs.length; i++) {
      if (inputs[i].closest('.dp-wrap')) continue;
      new Picker(inputs[i]);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
