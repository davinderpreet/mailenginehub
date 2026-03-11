/**
 * MailEngineHub Popup Subscription Widget v3
 * Self-contained — no dependencies. Loads on any Shopify storefront.
 * Centered modal overlay → minimizes to bottom teaser bar on close.
 * LDAS brand colors: black bg, blue (#2563EB) accent, white text.
 */
(function() {
  'use strict';

  var API = 'https://mailenginehub.com/api/subscribe';
  var LOGO_URL = '//ldas.ca/cdn/shop/files/Untitled_design_Logo.png?v=1758142321&width=200';
  var COOKIE_NAME = 'meh_popup_dismissed';
  var COOKIE_DAYS = 30;
  var SHOW_DELAY  = 3000; // ms before modal appears

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

  // ── Inject CSS ──────────────────────────────────────────
  var css = document.createElement('style');
  css.textContent = [

    /* ── Backdrop overlay ── */
    '#meh-backdrop {',
    '  position:fixed; inset:0; z-index:999998;',
    '  background:rgba(0,0,0,0.6); backdrop-filter:blur(2px);',
    '  opacity:0; transition:opacity 0.3s ease;',
    '  pointer-events:none;',
    '}',
    '#meh-backdrop.meh-show { opacity:1; pointer-events:auto; }',

    /* ── Centered modal ── */
    '#meh-modal {',
    '  position:fixed; z-index:999999;',
    '  top:50%; left:50%; transform:translate(-50%, -50%) scale(0.9);',
    '  width:440px; max-width:calc(100vw - 32px);',
    '  background:#0a0a0a; border-radius:16px;',
    '  padding:40px 36px 36px; text-align:center;',
    '  box-shadow:0 25px 60px rgba(0,0,0,0.5);',
    '  opacity:0; transition:opacity 0.3s ease, transform 0.3s ease;',
    '  pointer-events:none;',
    '  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;',
    '}',
    '#meh-modal.meh-show {',
    '  opacity:1; transform:translate(-50%, -50%) scale(1);',
    '  pointer-events:auto;',
    '}',

    /* Close button */
    '#meh-modal .meh-close {',
    '  position:absolute; top:12px; right:14px;',
    '  width:32px; height:32px; border-radius:50%;',
    '  background:rgba(255,255,255,0.08); border:none;',
    '  color:rgba(255,255,255,0.5); font-size:18px;',
    '  cursor:pointer; display:flex; align-items:center; justify-content:center;',
    '  transition:background 0.2s, color 0.2s; line-height:1;',
    '}',
    '#meh-modal .meh-close:hover { background:rgba(255,255,255,0.15); color:#fff; }',

    /* Logo */
    '#meh-modal .meh-logo {',
    '  width:140px; height:auto; margin:0 auto 20px; display:block;',
    '}',

    /* Heading */
    '#meh-modal h2 {',
    '  color:#fff; font-size:24px; font-weight:800; margin:0 0 8px;',
    '  line-height:1.2; letter-spacing:-0.3px;',
    '}',
    '#meh-modal .meh-subtitle {',
    '  color:rgba(255,255,255,0.6); font-size:14px; margin:0 0 24px;',
    '  font-style:italic;',
    '}',

    /* Email input */
    '#meh-modal input[type="email"] {',
    '  width:100%; box-sizing:border-box;',
    '  padding:14px 18px; border-radius:8px;',
    '  border:1.5px solid rgba(255,255,255,0.2); background:#fff;',
    '  color:#111; font-size:15px; outline:none;',
    '  transition:border-color 0.2s;',
    '}',
    '#meh-modal input[type="email"]:focus {',
    '  border-color:#2563EB;',
    '}',
    '#meh-modal input[type="email"]::placeholder {',
    '  color:#999;',
    '}',

    /* Submit button */
    '#meh-modal .meh-submit {',
    '  width:100%; padding:14px 20px; border-radius:8px; border:none;',
    '  background:#2563EB; color:#fff;',
    '  font-size:16px; font-weight:700; cursor:pointer;',
    '  margin-top:12px;',
    '  transition:background 0.2s, transform 0.1s;',
    '}',
    '#meh-modal .meh-submit:hover { background:#1d4ed8; }',
    '#meh-modal .meh-submit:active { transform:scale(0.98); }',
    '#meh-modal .meh-submit:disabled { opacity:0.5; cursor:wait; transform:none; }',

    /* Error */
    '#meh-modal .meh-error {',
    '  color:#ef4444; font-size:13px; margin-top:8px; min-height:18px;',
    '}',

    /* Success state */
    '#meh-modal .meh-success { display:none; }',
    '#meh-modal .meh-success .meh-check-icon {',
    '  font-size:40px; margin-bottom:12px;',
    '}',
    '#meh-modal .meh-success h2 { color:#10b981; }',
    '#meh-modal .meh-code-box {',
    '  display:inline-flex; align-items:center; gap:12px;',
    '  background:rgba(37,99,235,0.08); border:2px dashed rgba(37,99,235,0.4);',
    '  padding:14px 24px; border-radius:10px; margin:16px 0 8px;',
    '}',
    '#meh-modal .meh-code-text {',
    '  color:#2563EB; font-size:22px; font-weight:800; letter-spacing:2px;',
    '}',
    '#meh-modal .meh-copy-btn {',
    '  padding:8px 16px; border-radius:8px; border:1px solid rgba(37,99,235,0.4);',
    '  background:rgba(37,99,235,0.1); color:#2563EB; font-size:13px;',
    '  font-weight:600; cursor:pointer; transition:background 0.2s;',
    '}',
    '#meh-modal .meh-copy-btn:hover { background:rgba(37,99,235,0.25); }',
    '#meh-modal .meh-success-note {',
    '  color:rgba(255,255,255,0.45); font-size:13px; margin-top:10px;',
    '}',

    /* ── Bottom teaser bar (shows after modal is closed) ── */
    '#meh-teaser {',
    '  position:fixed; bottom:0; left:0; right:0; z-index:999997;',
    '  background:#0a0a0a; border-top:2px solid #2563EB;',
    '  padding:16px 60px 16px 20px; cursor:pointer;',
    '  display:flex; align-items:center; justify-content:center; gap:10px;',
    '  transform:translateY(100%); transition:transform 0.4s cubic-bezier(.22,.68,0,.98);',
    '  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;',
    '  box-shadow:0 -4px 20px rgba(0,0,0,0.4);',
    '}',
    '#meh-teaser.meh-show { transform:translateY(0); }',
    '#meh-teaser .meh-teaser-icon {',
    '  font-size:18px;',
    '}',
    '#meh-teaser .meh-teaser-text {',
    '  color:#fff; font-size:15px; font-weight:700; letter-spacing:1.5px;',
    '  text-transform:uppercase;',
    '}',
    '#meh-teaser .meh-teaser-cta {',
    '  background:#2563EB; color:#fff; border:none; border-radius:6px;',
    '  padding:8px 16px; font-size:12px; font-weight:700; cursor:pointer;',
    '  text-transform:uppercase; letter-spacing:0.5px; margin-left:8px;',
    '  transition:background 0.2s;',
    '}',
    '#meh-teaser .meh-teaser-cta:hover { background:#1d4ed8; }',
    '#meh-teaser .meh-teaser-close {',
    '  position:absolute; right:16px; top:50%; transform:translateY(-50%);',
    '  background:rgba(255,255,255,0.08); border:none; border-radius:50%;',
    '  color:rgba(255,255,255,0.5); font-size:16px; width:28px; height:28px;',
    '  cursor:pointer; display:flex; align-items:center; justify-content:center;',
    '  line-height:1; transition:background 0.2s, color 0.2s;',
    '}',
    '#meh-teaser .meh-teaser-close:hover { background:rgba(255,255,255,0.15); color:#fff; }',

    /* ── Mobile responsive ── */
    '@media (max-width:480px) {',
    '  #meh-modal { padding:32px 20px 28px; }',
    '  #meh-modal h2 { font-size:20px; }',
    '  #meh-modal .meh-logo { width:120px; }',
    '  #meh-modal .meh-code-box { flex-direction:column; gap:8px; padding:12px 16px; }',
    '  #meh-modal .meh-code-text { font-size:18px; letter-spacing:1px; }',
    '}',

  ].join('\n');
  document.head.appendChild(css);


  // ── Build DOM ───────────────────────────────────────────

  // Backdrop
  var backdrop = document.createElement('div');
  backdrop.id = 'meh-backdrop';
  document.body.appendChild(backdrop);

  // Modal
  var modal = document.createElement('div');
  modal.id = 'meh-modal';
  modal.innerHTML =
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
  document.body.appendChild(modal);

  // Bottom teaser bar (hidden initially, shows after modal is closed)
  var teaser = document.createElement('div');
  teaser.id = 'meh-teaser';
  teaser.innerHTML =
    '<span class="meh-teaser-icon">\uD83D\uDD25</span>' +
    '<span class="meh-teaser-text">GET 5% OFF!</span>' +
    '<span class="meh-teaser-cta">Claim Now</span>' +
    '<button class="meh-teaser-close" aria-label="Close">&times;</button>';
  document.body.appendChild(teaser);


  // ── Element refs ────────────────────────────────────────
  var emailInput   = document.getElementById('meh-email');
  var submitBtn    = document.getElementById('meh-submit');
  var errorEl      = document.getElementById('meh-error');
  var successEl    = document.getElementById('meh-success');
  var formState    = modal.querySelector('.meh-form-state');
  var codeEl       = document.getElementById('meh-code');
  var copyBtn      = document.getElementById('meh-copy');
  var closeBtn     = modal.querySelector('.meh-close');
  var teaserClose  = teaser.querySelector('.meh-teaser-close');


  // ── State ───────────────────────────────────────────────
  function showModal() {
    backdrop.classList.add('meh-show');
    modal.classList.add('meh-show');
    teaser.classList.remove('meh-show');
    setTimeout(function() { emailInput.focus(); }, 350);
  }

  function closeModal() {
    backdrop.classList.remove('meh-show');
    modal.classList.remove('meh-show');
    // Show teaser bar at bottom
    setTimeout(function() { teaser.classList.add('meh-show'); }, 300);
  }

  function dismissAll() {
    setCookie(COOKIE_NAME, '1', COOKIE_DAYS);
    backdrop.classList.remove('meh-show');
    modal.classList.remove('meh-show');
    teaser.classList.remove('meh-show');
  }


  // ── Events ──────────────────────────────────────────────

  // Close modal → minimize to teaser
  closeBtn.addEventListener('click', closeModal);

  // Click backdrop → close modal
  backdrop.addEventListener('click', closeModal);

  // Click teaser → reopen modal
  teaser.addEventListener('click', function(e) {
    if (e.target === teaserClose || teaserClose.contains(e.target)) {
      dismissAll();
    } else {
      showModal();
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
        // Store email for pixel tracking stitching
        localStorage.setItem('meh_email', email);
        window.meh_email = email;

        // Show success
        formState.style.display = 'none';
        successEl.style.display = 'block';
        codeEl.textContent = data.discount_code || 'CHECK YOUR EMAIL';

        // Set cookie so popup doesn't reappear
        setCookie(COOKIE_NAME, '1', COOKIE_DAYS);
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

  // ── Show modal after delay ──────────────────────────────
  setTimeout(showModal, SHOW_DELAY);

})();
