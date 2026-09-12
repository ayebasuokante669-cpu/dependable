/* Password field controls: reveal, generate, and a live checklist.
 *
 * Every one of these is a convenience. The policy is enforced by
 * AUTH_PASSWORD_VALIDATORS on the server, and a password that gets past this
 * file is still rejected on submit -- the checklist cannot say "breached",
 * because answering that needs the network call the server makes.
 *
 * Fields are found by `input[type=password][autocomplete=new-password]`, which
 * is exactly the set of "choose a new password" inputs: signup, password reset,
 * password change, and the admin's add-user form. Sign-in fields are
 * `current-password` and are left alone. Nothing needs a class or a data
 * attribute, so no template has to know this file exists.
 */
(function () {
  'use strict';

  var config = {};
  try {
    var el = document.getElementById('password-policy');
    if (el) config = JSON.parse(el.textContent);
  } catch (e) {
    // Malformed or absent config: the checklist falls back to the documented
    // minimum and the generate button is left out. Neither is load-bearing.
  }

  var MIN_LENGTH = config.minLength || 8;
  var GENERATE_URL = config.generateUrl || '';

  function csrfToken() {
    var input = document.querySelector('input[name=csrfmiddlewaretoken]');
    if (input) return input.value;
    var match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]*)/);
    return match ? decodeURIComponent(match[1]) : '';
  }

  function svg(paths, extraClass) {
    var node = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    node.setAttribute('viewBox', '0 0 24 24');
    node.setAttribute('fill', 'none');
    node.setAttribute('stroke', 'currentColor');
    node.setAttribute('stroke-width', '1.7');
    node.setAttribute('aria-hidden', 'true');
    node.setAttribute('class', extraClass || 'size-5');
    paths.forEach(function (d) {
      var path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      path.setAttribute('stroke-linecap', 'round');
      path.setAttribute('stroke-linejoin', 'round');
      path.setAttribute('d', d);
      node.appendChild(path);
    });
    return node;
  }

  var EYE = ['M2.036 12.322a1.012 1.012 0 0 1 0-.639C3.423 7.51 7.36 4.5 12 4.5c4.638 0 8.573 3.007 9.963 7.178.07.207.07.431 0 .639C20.577 16.49 16.64 19.5 12 19.5c-4.638 0-8.573-3.007-9.964-7.178Z',
             'M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z'];
  var EYE_OFF = ['M3.98 8.223A10.477 10.477 0 0 0 1.934 12C3.226 16.338 7.244 19.5 12 19.5c.993 0 1.953-.138 2.863-.395M6.228 6.228A10.451 10.451 0 0 1 12 4.5c4.756 0 8.773 3.162 10.065 7.498a10.522 10.522 0 0 1-4.293 5.774M6.228 6.228 3 3m3.228 3.228 3.65 3.65m7.894 7.894L21 21m-3.228-3.228-3.65-3.65m0 0a3 3 0 1 0-4.243-4.243',
                 'M3 3l18 18'];

  /* ---------------------------------------------------------------------- *
   * Reveal
   * ---------------------------------------------------------------------- */
  function addReveal(input) {
    var wrap = document.createElement('div');
    wrap.className = 'password-wrap';
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);
    input.classList.add('password-input');

    var button = document.createElement('button');
    button.type = 'button';
    button.className = 'password-reveal';
    button.setAttribute('aria-pressed', 'false');
    button.setAttribute('aria-label', 'Show password');
    button.title = 'Show password';
    button.appendChild(svg(EYE));

    button.addEventListener('click', function () {
      var shown = input.type === 'text';
      input.type = shown ? 'password' : 'text';
      button.setAttribute('aria-pressed', shown ? 'false' : 'true');
      var label = shown ? 'Show password' : 'Hide password';
      button.setAttribute('aria-label', label);
      button.title = label;
      button.replaceChild(svg(shown ? EYE : EYE_OFF), button.firstChild);
      // Keep the caret where it was: switching `type` moves it to the end in
      // some browsers, which is jarring mid-edit.
      var at = input.value.length;
      input.focus();
      try { input.setSelectionRange(at, at); } catch (e) { /* not supported on all types */ }
    });

    wrap.appendChild(button);
    return wrap;
  }

  /* ---------------------------------------------------------------------- *
   * Generate
   *
   * The password comes from the server, which is the only place the policy is
   * defined. Generating one here would be a second, untested definition of
   * "strong enough" that would drift from the validators the moment either
   * changed.
   * ---------------------------------------------------------------------- */
  function addGenerate(wrap, input, partner) {
    if (!GENERATE_URL) return;

    var button = document.createElement('button');
    button.type = 'button';
    button.className = 'password-generate';
    button.appendChild(svg([
      'M9.813 15.904 9 18.75l-.813-2.846a4.5 4.5 0 0 0-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 0 0 3.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 0 0 3.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 0 0-3.09 3.09ZM18.259 8.715 18 9.75l-.259-1.035a3.375 3.375 0 0 0-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 0 0 2.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 0 0 2.456 2.456L21.75 6l-1.035.259a3.375 3.375 0 0 0-2.456 2.456Z'
    ], 'size-4'));
    button.appendChild(document.createTextNode('Generate'));

    var status = document.createElement('p');
    status.className = 'password-generate-status';
    status.setAttribute('role', 'status');

    button.addEventListener('click', function () {
      button.disabled = true;
      status.textContent = '';

      fetch(GENERATE_URL, {
        method: 'POST',
        headers: {
          'X-CSRFToken': csrfToken(),
          'X-Requested-With': 'XMLHttpRequest'
        },
        credentials: 'same-origin'
      })
        .then(function (response) {
          return response.json().then(function (body) {
            if (!response.ok) throw new Error(body.error || 'Could not generate a password.');
            return body;
          });
        })
        .then(function (body) {
          input.value = body.password;
          if (partner) partner.value = body.password;

          // Reveal it. A generated password the user cannot see is one they
          // cannot write down, and they have to store it somewhere to use it.
          if (input.type === 'password') {
            var reveal = wrap.querySelector('.password-reveal');
            if (reveal) reveal.click();
          }
          input.dispatchEvent(new Event('input', { bubbles: true }));
          if (partner) partner.dispatchEvent(new Event('input', { bubbles: true }));
          status.textContent = 'Generated — copy it somewhere safe before saving.';
          status.classList.remove('is-error');
        })
        .catch(function (error) {
          status.textContent = error.message + ' Type one instead.';
          status.classList.add('is-error');
        })
        .then(function () {
          button.disabled = false;
        });
    });

    wrap.parentNode.insertBefore(button, wrap.nextSibling);
    button.parentNode.insertBefore(status, button.nextSibling);
  }

  /* ---------------------------------------------------------------------- *
   * Checklist
   *
   * Explicitly a guide, not a gate -- it says so in the markup. The one rule
   * it cannot check is the important one: whether the password appears in a
   * breach corpus. That answer lives behind the server's API call.
   * ---------------------------------------------------------------------- */
  function addChecklist(anchor, input, partner) {
    var list = document.createElement('ul');
    list.className = 'password-checklist';
    list.setAttribute('aria-live', 'polite');

    var rules = [
      {
        label: 'At least ' + MIN_LENGTH + ' characters',
        test: function (v) { return v.length >= MIN_LENGTH; }
      },
      {
        label: 'Not only numbers',
        test: function (v) { return !/^\d+$/.test(v); }
      }
    ];
    if (partner) {
      rules.push({
        label: 'Both entries match',
        test: function (v) { return v.length > 0 && v === partner.value; }
      });
    }

    rules.forEach(function (rule) {
      var item = document.createElement('li');
      item.className = 'password-rule';
      item.appendChild(svg(['m4.5 12.75 6 6 9-13.5'], 'password-rule-icon size-3.5'));
      item.appendChild(document.createTextNode(rule.label));
      rule.node = item;
      list.appendChild(item);
    });

    var note = document.createElement('li');
    note.className = 'password-rule password-rule-note';
    note.textContent = 'Checked against known breached passwords when you save.';
    list.appendChild(note);

    function refresh() {
      var value = input.value;
      rules.forEach(function (rule) {
        var ok = value.length > 0 && rule.test(value);
        rule.node.classList.toggle('is-met', ok);
      });
    }

    input.addEventListener('input', refresh);
    if (partner) partner.addEventListener('input', refresh);
    refresh();

    anchor.parentNode.insertBefore(list, anchor.nextSibling);
    return list;
  }

  /* ---------------------------------------------------------------------- */
  function init() {
    var inputs = Array.prototype.slice.call(
      document.querySelectorAll('input[type="password"][autocomplete="new-password"]')
    );
    if (!inputs.length) return;

    // Where there are two -- a password and its confirmation -- the controls
    // belong on the first, and the second is filled and checked alongside it.
    var primary = inputs[0];
    var partner = inputs.length > 1 ? inputs[1] : null;

    inputs.forEach(function (input) {
      var wrap = addReveal(input);
      if (input === primary) {
        addGenerate(wrap, primary, partner);
        var status = wrap.parentNode.querySelector('.password-generate-status');
        addChecklist(status || wrap, primary, partner);
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
