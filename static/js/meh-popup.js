/**
 * MailEngineHub Popup Subscription Widget v4
 * Self-contained — no dependencies. Loads on any Shopify storefront.
 * Bottom-left slide-up popup — non-blocking, mobile responsive.
 * LDAS brand colors: black bg, blue (#2563EB) accent, white text.
 */
(function() {
  'use strict';

  var API = 'https://mailenginehub.com/api/subscribe';
  var LOGO_URL = '//ldas.ca/cdn/shop/files/Untitled_design_Logo.png?v=1758142321&width=200';
  var COOKIE_NAME = 'meh_popup_dismissed';
  var COOKIE_DAYS = 30;
  var SHOW_DELAY  = 3000; // ms before popup appears

  // ── Helpers ──────────────────────────────────────────────
  function getCookie(name) {
    var v = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)');
    return v ? v.pop() : '';
  }
  function setCookie(name, val, days) {
    var d = new Date();
    d.setTime(d.getTime() + days * 86400000);
    document.cookie = name + '=' + val + ';expires=' + d.toUTCString() + ';path=/;SameSite=Lax';
  }
  function getSessionId() {
    var sid = localStorage.getItem('meh_sid');
    if (!sid) {
      sid = Math.random().toString(36).substr(2, 9);
      localStorage.setItem('meh_sid', sid);
    }
    return sid;
  }

  // ── Don't show if already dismissed / subscribed ────────
  if (getCookie(COOKIE_NAME)) return;
  if (localStorage.getItem('meh_email')) return;

  // ── Inject CSS ──────────────────────────────────────────
  var css = document.createElement('style');
  css.textContent = [

    /* ── Backdrop (only for centered mode) ── */
    '#meh-backdrop {',
    '  position:fixed; inset:0; z-index:999998;',
    '  background:rgba(0,0,0,0.5); backdrop-filter:blur(2px);',
    '  opacity:0; transition:opacity 0.3s ease;',
    '  pointer-events:none;',
    '}',
    '#meh-backdrop.meh-show { opacity:1; pointer-events:auto; }',

    /* ── Popup — starts centered, moves to bottom-left after first close ── */
    '#meh-popup {',
    '  position:fixed; z-index:999999;',
    '  top:50%; left:50%; transform:translate(-50%, -50%) scale(0.9);',
    '  width:420px; max-width:calc(100vw - 32px);',
    '  background:#0a0a0a; border-radius:16px;',
    '  padding:36px 32px 32px; text-align:center;',
    '  box-shadow:0 25px 60px rgba(0,0,0,0.5);',
    '  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;',
    '  opacity:0; transition:transform 0.4s ease, opacity 0.3s ease, top 0.4s ease, left 0.4s ease, width 0.4s ease;',
    '  pointer-events:none;',
    '}',
    '#meh-popup.meh-show {',
    '  transform:translate(-50%, -50%) scale(1); opacity:1;',
    '  pointer-events:auto;',
    '}',
    /* Bottom-left mode (after first close, reopened from teaser) */
    '#meh-popup.meh-corner {',
    '  top:auto; left:20px; bottom:20px;',
    '  transform:translateY(120%); opacity:0;',
    '  width:380px; padding:28px 24px 24px;',
    '}',
    '#meh-popup.meh-corner.meh-show {',
    '  transform:translateY(0); opacity:1;',
    '}',

    /* Close button */
    '#meh-popup .meh-close {',
    '  position:absolute; top:10px; right:12px;',
    '  width:28px; height:28px; border-radius:50%;',
    '  background:rgba(255,255,255,0.08); border:none;',
    '  color:rgba(255,255,255,0.5); font-size:16px;',
    '  cursor:pointer; display:flex; align-items:center; justify-content:center;',
    '  transition:background 0.2s, color 0.2s; line-height:1;',
    '}',
    '#meh-popup .meh-close:hover { background:rgba(255,255,255,0.15); color:#fff; }',

    /* Logo */
    '#meh-popup .meh-logo {',
    '  width:120px; height:auto; margin:0 auto 14px; display:block;',
    '}',

    /* Heading */
    '#meh-popup h2 {',
    '  color:#fff; font-size:20px; font-weight:800; margin:0 0 6px;',
    '  line-height:1.2; letter-spacing:-0.3px;',
    '}',
    '#meh-popup .meh-subtitle {',
    '  color:rgba(255,255,255,0.55); font-size:13px; margin:0 0 18px;',
    '  font-style:italic;',
    '}',

    /* Email input */
    '#meh-popup input[type="email"] {',
    '  width:100%; box-sizing:border-box;',
    '  padding:12px 16px; border-radius:8px;',
    '  border:1.5px solid rgba(255,255,255,0.15); background:#fff;',
    '  color:#111; font-size:14px; outline:none;',
    '  transition:border-color 0.2s;',
    '}',
    '#meh-popup input[type="email"]:focus {',
    '  border-color:#2563EB;',
    '}',
    '#meh-popup input[type="email"]::placeholder {',
    '  color:#999;',
    '}',

    /* Submit button */
    '#meh-popup .meh-submit {',
    '  width:100%; padding:12px 18px; border-radius:8px; border:none;',
    '  background:#2563EB; color:#fff;',
    '  font-size:15px; font-weight:700; cursor:pointer;',
    '  margin-top:10px;',
    '  transition:background 0.2s, transform 0.1s;',
    '}',
    '#meh-popup .meh-submit:hover { background:#1d4ed8; }',
    '#meh-popup .meh-submit:active { transform:scale(0.98); }',
    '#meh-popup .meh-submit:disabled { opacity:0.5; cursor:wait; transform:none; }',

    /* Error */
    '#meh-popup .meh-error {',
    '  color:#ef4444; font-size:12px; margin-top:6px; min-height:16px;',
    '}',

    /* Success state */
    '#meh-popup .meh-success { display:none; }',
    '#meh-popup .meh-success .meh-check-icon {',
    '  font-size:36px; margin-bottom:10px;',
    '}',
    '#meh-popup .meh-success h2 { color:#10b981; }',
    '#meh-popup .meh-code-box {',
    '  display:inline-flex; align-items:center; gap:10px;',
    '  background:rgba(37,99,235,0.08); border:2px dashed rgba(37,99,235,0.4);',
    '  padding:12px 20px; border-radius:10px; margin:14px 0 6px;',
    '}',
    '#meh-popup .meh-code-text {',
    '  color:#2563EB; font-size:18px; font-weight:800; letter-spacing:2px;',
    '}',
    '#meh-popup .meh-copy-btn {',
    '  padding:6px 14px; border-radius:6px; border:1px solid rgba(37,99,235,0.4);',
    '  background:rgba(37,99,235,0.1); color:#2563EB; font-size:12px;',
    '  font-weight:600; cursor:pointer; transition:background 0.2s;',
    '}',
    '#meh-popup .meh-copy-btn:hover { background:rgba(37,99,235,0.25); }',
    '#meh-popup .meh-success-note {',
    '  color:rgba(255,255,255,0.4); font-size:12px; margin-top:8px;',
    '}',

    /* ── Bottom teaser bar ── */
    '#meh-teaser {',
    '  position:fixed; bottom:20px; left:20px; z-index:999997;',
    '  background:#0a0a0a; border:1px solid rgba(37,99,235,0.3);',
    '  border-radius:12px; padding:12px 48px 12px 16px; cursor:pointer;',
    '  display:flex; align-items:center; gap:8px;',
    '  transform:translateY(200%); transition:transform 0.4s cubic-bezier(.22,.68,0,.98);',
    '  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;',
    '  box-shadow:0 8px 24px rgba(0,0,0,0.3);',
    '}',
    '#meh-teaser.meh-show { transform:translateY(0); }',
    '#meh-teaser .meh-teaser-icon { font-size:16px; }',
    '#meh-teaser .meh-teaser-text {',
    '  color:#fff; font-size:13px; font-weight:700; letter-spacing:1px;',
    '  text-transform:uppercase;',
    '}',
    '#meh-teaser .meh-teaser-cta {',
    '  background:#2563EB; color:#fff; border:none; border-radius:6px;',
    '  padding:6px 12px; font-size:11px; font-weight:700; cursor:pointer;',
    '  text-transform:uppercase; letter-spacing:0.5px; margin-left:4px;',
    '  transition:background 0.2s;',
    '}',
    '#meh-teaser .meh-teaser-cta:hover { background:#1d4ed8; }',
    '#meh-teaser .meh-teaser-close {',
    '  position:absolute; right:10px; top:50%; transform:translateY(-50%);',
    '  background:rgba(255,255,255,0.08); border:none; border-radius:50%;',
    '  color:rgba(255,255,255,0.5); font-size:14px; width:24px; height:24px;',
    '  cursor:pointer; display:flex; align-items:center; justify-content:center;',
    '  line-height:1; transition:background 0.2s, color 0.2s;',
    '}',
    '#meh-teaser .meh-teaser-close:hover { background:rgba(255,255,255,0.15); color:#fff; }',

    /* ── Mobile responsive ── */
    '@media (max-width:480px) {',
    '  #meh-popup {',
    '    width:calc(100vw - 24px); max-width:100%;',
    '    padding:28px 20px 24px;',
    '  }',
    '  #meh-popup.meh-corner {',
    '    bottom:0; left:0; right:0;',
    '    width:100%;',
    '    border-radius:16px 16px 0 0;',
    '  }',
    '  #meh-popup h2 { font-size:18px; }',
    '  #meh-popup .meh-logo { width:100px; }',
    '  #meh-popup .meh-code-box { flex-direction:column; gap:6px; padding:10px 14px; }',
    '  #meh-popup .meh-code-text { font-size:16px; letter-spacing:1px; }',
    '  #meh-teaser {',
    '    bottom:0; left:0; right:0;',
    '    border-radius:12px 12px 0 0;',
    '    justify-content:center;',
    '  }',
    '}',

  ].join('\n');
  document.head.appendChild(css);


  // ── Build DOM ───────────────────────────────────────────

  // Backdrop (only shown in centered mode)
  var backdrop = document.createElement('div');
  backdrop.id = 'meh-backdrop';
  document.body.appendChild(backdrop);

  // Popup
  var popup = document.createElement('div');
  popup.id = 'meh-popup';
  popup.innerHTML =
    '<button class="meh-close" aria-label="Close">&times;</button>' +

    /* Logo */
    '<img class="meh-logo" src="' + LOGO_URL + '" alt="LDAS" />' +

    /* Form state */
    '<div class="meh-form-state">' +
      '<h2>Get 5% Off Your First Order</h2>' +
      '<p class="meh-subtitle">\uD83D\uDD25 Plus early access to exclusive drops & deals!</p>' +
      '<input type="email" id="meh-email" placeholder="Enter your email" autocomplete="email" />' +
      '<button class="meh-submit" id="meh-submit">Submit</button>' +
      '<div class="meh-error" id="meh-error"></div>' +
    '</div>' +

    /* Success state */
    '<div class="meh-success" id="meh-success">' +
      '<div class="meh-check-icon">\uD83C\uDF89</div>' +
      '<h2>You\'re In!</h2>' +
      '<p class="meh-subtitle">Here\'s your exclusive discount code:</p>' +
      '<div class="meh-code-box">' +
        '<span class="meh-code-text" id="meh-code"></span>' +
        '<button class="meh-copy-btn" id="meh-copy">Copy</button>' +
      '</div>' +
      '<p class="meh-success-note">Use at checkout. Valid for 30 days.</p>' +
    '</div>';
  document.body.appendChild(popup);

  // Bottom teaser (hidden initially, shows after popup is closed)
  var teaser = document.createElement('div');
  teaser.id = 'meh-teaser';
  teaser.innerHTML =
    '<span class="meh-teaser-icon">\uD83D\uDD25</span>' +
    '<span class="meh-teaser-text">5% OFF</span>' +
    '<span class="meh-teaser-cta">Claim Now</span>' +
    '<button class="meh-teaser-close" aria-label="Close">&times;</button>';
  document.body.appendChild(teaser);


  // ── Element refs ────────────────────────────────────────
  var emailInput   = document.getElementById('meh-email');
  var submitBtn    = document.getElementById('meh-submit');
  var errorEl      = document.getElementById('meh-error');
  var successEl    = document.getElementById('meh-success');
  var formState    = popup.querySelector('.meh-form-state');
  var codeEl       = document.getElementById('meh-code');
  var copyBtn      = document.getElementById('meh-copy');
  var closeBtn     = popup.querySelector('.meh-close');
  var teaserClose  = teaser.querySelector('.meh-teaser-close');


  // ── State ───────────────────────────────────────────────
  var hasSubscribed = false;
  var hasBeenClosed = false; // after first close, popup goes to corner mode

  function showPopup() {
    if (hasBeenClosed) {
      // Reopen in corner mode (from teaser)
      popup.classList.add('meh-corner');
      backdrop.classList.remove('meh-show');
    } else {
      // First time — centered with backdrop
      backdrop.classList.add('meh-show');
    }
    popup.classList.add('meh-show');
    teaser.classList.remove('meh-show');
    if (!hasSubscribed) {
      setTimeout(function() { emailInput.focus(); }, 400);
    }
  }

  function closePopup() {
    popup.classList.remove('meh-show');
    backdrop.classList.remove('meh-show');
    if (hasSubscribed) {
      dismissAll();
    } else {
      hasBeenClosed = true;
      // Switch to corner mode for next open
      popup.classList.add('meh-corner');
      setTimeout(function() { teaser.classList.add('meh-show'); }, 400);
    }
  }

  function dismissAll() {
    setCookie(COOKIE_NAME, '1', COOKIE_DAYS);
    popup.classList.remove('meh-show');
    backdrop.classList.remove('meh-show');
    teaser.classList.remove('meh-show');
  }


  // ── Events ──────────────────────────────────────────────

  // Close popup → minimize to teaser
  closeBtn.addEventListener('click', closePopup);

  // Click backdrop → close popup (centered mode)
  backdrop.addEventListener('click', closePopup);

  // Click teaser → reopen popup
  teaser.addEventListener('click', function(e) {
    if (e.target === teaserClose || teaserClose.contains(e.target)) {
      dismissAll();
    } else {
      showPopup();
    }
  });

  // Enter key submits
  emailInput.addEventListener('keydown', function(e) {
    if (e.key === 'Enter') {
      e.preventDefault();
      submitBtn.click();
    }
  });

  // Submit
  submitBtn.addEventListener('click', function() {
    var email = (emailInput.value || '').trim().toLowerCase();
    errorEl.textContent = '';

    // Basic validation
    if (!email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      errorEl.textContent = 'Please enter a valid email address.';
      emailInput.focus();
      return;
    }

    submitBtn.disabled = true;
    submitBtn.textContent = 'Sending...';

    var payload = {
      email: email,
      session_id: getSessionId()
    };

    fetch(API, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (data.ok) {
        hasSubscribed = true;

        localStorage.setItem('meh_email', email);
        window.meh_email = email;

        formState.style.display = 'none';
        successEl.style.display = 'block';
        codeEl.textContent = data.discount_code || 'CHECK YOUR EMAIL';

        setCookie(COOKIE_NAME, '1', COOKIE_DAYS);

        // Auto-dismiss after 8 seconds
        setTimeout(function() { dismissAll(); }, 8000);
      } else {
        errorEl.textContent = data.error || 'Something went wrong. Please try again.';
        submitBtn.disabled = false;
        submitBtn.textContent = 'Submit';
      }
    })
    .catch(function() {
      errorEl.textContent = 'Network error. Please try again.';
      submitBtn.disabled = false;
      submitBtn.textContent = 'Submit';
    });
  });

  // Copy button
  copyBtn.addEventListener('click', function() {
    var code = codeEl.textContent;
    if (navigator.clipboard) {
      navigator.clipboard.writeText(code).then(function() {
        copyBtn.textContent = 'Copied!';
        setTimeout(function() { copyBtn.textContent = 'Copy'; }, 2000);
      });
    } else {
      var ta = document.createElement('textarea');
      ta.value = code;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
      copyBtn.textContent = 'Copied!';
      setTimeout(function() { copyBtn.textContent = 'Copy'; }, 2000);
    }
  });

  // ── Show popup after delay ────────────────────────────
  setTimeout(showPopup, SHOW_DELAY);

})();
