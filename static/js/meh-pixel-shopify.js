// MailEngineHub — Shopify Custom Pixel v1
// Install: Shopify Admin > Settings > Customer events > Add custom pixel
// Runs in Shopify's sandboxed iframe — no direct DOM/localStorage access.
// Uses browser.localStorage (async) for session persistence.
// Posts all events to /api/track and /api/identify endpoints.

(async function () {
  var ENDPOINT = "https://mailenginehub.com/api/track";
  var ID_ENDPOINT = "https://mailenginehub.com/api/identify";

  // ── Session ID — persisted in top-frame localStorage via async bridge ──
  var sid = "";
  try {
    sid = await browser.localStorage.getItem("meh_sid");
  } catch (e) {}
  if (!sid) {
    sid =
      "sp_" +
      Math.random().toString(36).substr(2, 9) +
      Date.now().toString(36);
    try {
      await browser.localStorage.setItem("meh_sid", sid);
    } catch (e) {}
  }

  // ── Email — check localStorage for previously identified visitor ──
  var email = "";
  try {
    email = (await browser.localStorage.getItem("meh_email")) || "";
  } catch (e) {}

  // ── Helpers ──
  function track(type, data) {
    var payload = { event_type: type, event_data: data, session_id: sid };
    if (email) payload.email = email;
    fetch(ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      keepalive: true,
    }).catch(function () {});
  }

  function identify(newEmail) {
    if (!newEmail || newEmail === email) return;
    email = newEmail;
    try {
      browser.localStorage.setItem("meh_email", email);
    } catch (e) {}
    fetch(ID_ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sid, email: email }),
      keepalive: true,
    }).catch(function () {});
  }

  // Extract line items from checkout in a compact format
  function extractLineItems(checkout) {
    if (!checkout || !checkout.lineItems) return [];
    return checkout.lineItems.map(function (li) {
      var v = li.variant || li.merchandise || {};
      var p = v.product || {};
      return {
        product_id: String(p.id || v.id || ""),
        product_title: p.title || v.title || li.title || "",
        variant_id: String(v.id || ""),
        variant_title: v.title || "",
        sku: v.sku || "",
        quantity: li.quantity || 1,
        price: v.price ? String(v.price.amount || "0") : "0",
      };
    });
  }

  // Extract money value
  function money(m) {
    if (!m) return "0";
    return String(m.amount || "0");
  }

  // ────────────────────────────────────────────────────────
  // Standard Events
  // ────────────────────────────────────────────────────────

  // ── Page View ──
  analytics.subscribe("page_viewed", function (event) {
    var ctx = event.context || {};
    var doc = ctx.document || {};
    var loc = doc.location || {};
    track("viewed_page", {
      url: loc.pathname || "",
      page_title: doc.title || "",
      referrer: doc.referrer || "",
      source: "shopify_pixel",
    });
  });

  // ── Product Viewed ──
  analytics.subscribe("product_viewed", function (event) {
    var d = event.data || {};
    var pv = d.productVariant || {};
    var prod = pv.product || {};
    track("viewed_product", {
      product_id: String(prod.id || pv.id || ""),
      product_title: prod.title || pv.title || "",
      variant_id: String(pv.id || ""),
      price: pv.price ? String(pv.price.amount || "0") : "0",
      url: prod.url || "",
      source: "shopify_pixel",
    });
  });

  // ── Product Added to Cart ──
  analytics.subscribe("product_added_to_cart", function (event) {
    var d = event.data || {};
    var cl = d.cartLine || {};
    var merch = cl.merchandise || {};
    var prod = merch.product || {};
    track("added_to_cart", {
      product_id: String(prod.id || merch.id || ""),
      product_title: prod.title || merch.title || "",
      variant_id: String(merch.id || ""),
      variant_title: merch.title || "",
      sku: merch.sku || "",
      quantity: cl.quantity || 1,
      price: merch.price ? String(merch.price.amount || "0") : "0",
      url: prod.url || "",
      source: "shopify_pixel",
    });
  });

  // ── Product Removed from Cart (NEW — not in theme.liquid pixel) ──
  analytics.subscribe("product_removed_from_cart", function (event) {
    var d = event.data || {};
    var cl = d.cartLine || {};
    var merch = cl.merchandise || {};
    var prod = merch.product || {};
    track("removed_from_cart", {
      product_id: String(prod.id || merch.id || ""),
      product_title: prod.title || merch.title || "",
      variant_id: String(merch.id || ""),
      quantity: cl.quantity || 1,
      source: "shopify_pixel",
    });
  });

  // ── Cart Viewed ──
  analytics.subscribe("cart_viewed", function (event) {
    track("viewed_cart", { source: "shopify_pixel" });
  });

  // ── Collection Viewed ──
  analytics.subscribe("collection_viewed", function (event) {
    var d = event.data || {};
    var col = d.collection || {};
    track("viewed_collection", {
      collection_id: String(col.id || ""),
      collection_title: col.title || "",
      source: "shopify_pixel",
    });
  });

  // ── Search Submitted ──
  analytics.subscribe("search_submitted", function (event) {
    var d = event.data || {};
    var sq = d.searchResult || {};
    track("searched", {
      query: sq.query || "",
      source: "shopify_pixel",
    });
  });

  // ────────────────────────────────────────────────────────
  // Checkout Events (THE BIG WIN — invisible to theme.liquid)
  // ────────────────────────────────────────────────────────

  // ── Checkout Started ──
  analytics.subscribe("checkout_started", function (event) {
    var d = event.data || {};
    var co = d.checkout || {};
    // If checkout has email, stitch identity immediately
    if (co.email) identify(co.email);
    track("checkout_started", {
      checkout_token: co.token || "",
      total_price: money(co.totalPrice),
      subtotal_price: money(co.subtotalPrice),
      currency: co.currencyCode || "",
      line_items: extractLineItems(co),
      email: co.email || "",
      source: "shopify_pixel",
    });
  });

  // ── Checkout Contact Info Submitted (email captured!) ──
  analytics.subscribe("checkout_contact_info_submitted", function (event) {
    var d = event.data || {};
    var co = d.checkout || {};
    // This is the critical moment — customer typed their email at checkout
    if (co.email) identify(co.email);
    track("checkout_contact_info", {
      checkout_token: co.token || "",
      email: co.email || "",
      phone: co.phone || "",
      accepts_marketing: co.buyerAcceptsEmailMarketing || false,
      accepts_sms: co.buyerAcceptsSmsMarketing || false,
      source: "shopify_pixel",
    });
  });

  // ── Checkout Address Info Submitted ──
  analytics.subscribe("checkout_address_info_submitted", function (event) {
    var d = event.data || {};
    var co = d.checkout || {};
    var addr = co.shippingAddress || {};
    if (co.email) identify(co.email);
    track("checkout_address_info", {
      checkout_token: co.token || "",
      city: addr.city || "",
      province: addr.provinceCode || addr.province || "",
      country: addr.countryCode || addr.country || "",
      source: "shopify_pixel",
    });
  });

  // ── Checkout Shipping Info Submitted ──
  analytics.subscribe("checkout_shipping_info_submitted", function (event) {
    var d = event.data || {};
    var co = d.checkout || {};
    if (co.email) identify(co.email);
    var ship = co.shippingLine || {};
    track("checkout_shipping_info", {
      checkout_token: co.token || "",
      shipping_title: ship.title || "",
      shipping_price: ship.price ? String(ship.price.amount || "0") : "0",
      source: "shopify_pixel",
    });
  });

  // ── Payment Info Submitted (highest intent before purchase) ──
  analytics.subscribe("payment_info_submitted", function (event) {
    var d = event.data || {};
    var co = d.checkout || {};
    if (co.email) identify(co.email);
    track("payment_info_submitted", {
      checkout_token: co.token || "",
      total_price: money(co.totalPrice),
      currency: co.currencyCode || "",
      line_items: extractLineItems(co),
      source: "shopify_pixel",
    });
  });

  // ── Checkout Completed (purchase!) ──
  analytics.subscribe("checkout_completed", function (event) {
    var d = event.data || {};
    var co = d.checkout || {};
    if (co.email) identify(co.email);
    var order = co.order || {};
    track("checkout_completed", {
      checkout_token: co.token || "",
      order_id: String(order.id || ""),
      total_price: money(co.totalPrice),
      subtotal_price: money(co.subtotalPrice),
      total_tax: money(co.totalTax),
      total_discounts: money(co.discountsAmount),
      currency: co.currencyCode || "",
      email: co.email || "",
      line_items: extractLineItems(co),
      accepts_marketing: co.buyerAcceptsEmailMarketing || false,
      source: "shopify_pixel",
    });
  });
})();
