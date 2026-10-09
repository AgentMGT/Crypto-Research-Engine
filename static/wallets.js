// Watch-only wallets: balances for public addresses on EVM chains, Solana and Bittensor.
// The page only ever reads addresses: it never asks a wallet to sign or send anything.
// Addresses live in this browser (localStorage "wallets-v1").
(function () {
  var $ = function (id) { return document.getElementById(id); };
  if (!$("wl-list")) return;
  var KEY = "wallets-v1";
  var CG = "https://api.coingecko.com/api/v3";

  var EVM = [
    { id: "ethereum", name: "Ethereum", rpc: "https://ethereum-rpc.publicnode.com", scan: "https://etherscan.io/address/", tokens: [
      ["USDC", "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", 6, "usd-coin"], ["USDT", "0xdAC17F958D2ee523a2206206994597C13D831ec7", 6, "tether"],
      ["WETH", "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", 18, "ethereum"], ["WBTC", "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599", 8, "bitcoin"],
      ["wTAO", "0x77E06c9eCCf2E797fd462A92B6D7642EF85b0A44", 9, "bittensor"]] },
    { id: "base", name: "Base", rpc: "https://base-rpc.publicnode.com", scan: "https://basescan.org/address/", tokens: [
      ["USDC", "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", 6, "usd-coin"], ["WETH", "0x4200000000000000000000000000000000000006", 18, "ethereum"]] },
    { id: "arbitrum", name: "Arbitrum", rpc: "https://arbitrum-one-rpc.publicnode.com", scan: "https://arbiscan.io/address/", tokens: [
      ["USDC", "0xaf88d065e77c8cC2239327C5EDb3A432268e5831", 6, "usd-coin"], ["USDT", "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9", 6, "tether"],
      ["WETH", "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1", 18, "ethereum"], ["ARB", "0x912CE59144191C1204E64559FE8253a0e49E6548", 18, "arbitrum"]] },
    { id: "optimism", name: "Optimism", rpc: "https://optimism-rpc.publicnode.com", scan: "https://optimistic.etherscan.io/address/", tokens: [
      ["USDC", "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85", 6, "usd-coin"], ["OP", "0x4200000000000000000000000000000000000042", 18, "optimism"]] }
  ];
  var SOL_RPC = "https://solana-rpc.publicnode.com";
  var SOL_MINTS = {
    EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v: "USDC", Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB: "USDT",
    JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN: "JUP", DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263: "BONK",
    jtojtomepa8beP8AuQc6eXt5FriJwfFMwQx2v2f9mCL: "JTO", EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm: "WIF",
    mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So: "mSOL", J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn: "JitoSOL",
    HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3: "PYTH"
  };
  var TOKEN_PROGRAMS = ["TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA", "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"];
  var TAO_WS = "wss://entrypoint-finney.opentensor.ai:443";

  // ------------------------------------------------------------ helpers
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function n(v, dp) {
    if (v == null || isNaN(v)) return "–";
    return Number(v).toLocaleString("en-US", { minimumFractionDigits: dp == null ? 2 : dp, maximumFractionDigits: dp == null ? 2 : dp });
  }
  function amt(v) { return v >= 1000 ? n(v, 2) : v >= 1 ? n(v, 4) : n(v, 6); }
  function usd(v) { return v == null || isNaN(v) ? "–" : "$" + n(v, 2); }
  function short(a) { return a.length > 14 ? a.slice(0, 6) + "…" + a.slice(-4) : a; }
  function read() { try { return JSON.parse(localStorage.getItem(KEY) || "[]"); } catch (e) { return []; } }
  function write(v) { try { localStorage.setItem(KEY, JSON.stringify(v)); } catch (e) {} }
  function post(url, body) {
    return fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); });
  }
  function getJSON(url) { return fetch(url).then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); }); }
  function units(hex, dec) {
    if (!hex || hex === "0x") return 0;
    var b = BigInt(hex), d = BigInt(10) ** BigInt(dec);
    return Number(b / d) + Number(b % d) / Math.pow(10, dec);
  }

  // ------------------------------------------------------------ base58, blake2b, SS58
  var B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";
  function b58decode(s) {
    var bytes = [0];
    for (var i = 0; i < s.length; i++) {
      var c = B58.indexOf(s[i]);
      if (c < 0) return null;
      for (var j = 0; j < bytes.length; j++) { c += bytes[j] * 58; bytes[j] = c & 255; c >>= 8; }
      while (c) { bytes.push(c & 255); c >>= 8; }
    }
    for (var k = 0; k < s.length && s[k] === "1"; k++) bytes.push(0);
    return new Uint8Array(bytes.reverse());
  }
  // BLAKE2b (RFC 7693) with BigInt words; inputs here are tiny, so speed doesn't matter.
  var IV = ["6a09e667f3bcc908", "bb67ae8584caa73b", "3c6ef372fe94f82b", "a54ff53a5f1d36f1", "510e527fade682d1", "9b05688c2b3e6c1f", "1f83d9abfb41bd6b", "5be0cd19137e2179"].map(function (h) { return BigInt("0x" + h); });
  var SIGMA = [[0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15],[14,10,4,8,9,15,13,6,1,12,0,2,11,7,5,3],[11,8,12,0,5,2,15,13,10,14,3,6,7,1,9,4],[7,9,3,1,13,12,11,14,2,6,5,10,4,0,15,8],[9,0,5,7,2,4,10,15,14,1,11,12,6,8,3,13],[2,12,6,10,0,11,8,3,4,13,7,5,15,14,1,9],[12,5,1,15,14,13,4,10,0,7,6,3,9,2,8,11],[13,11,7,14,12,1,3,9,5,0,15,4,8,6,2,10],[6,15,14,9,11,3,0,8,12,2,13,7,1,4,10,5],[10,2,8,4,7,6,1,5,15,11,9,14,3,12,13,0]];
  var M64 = (BigInt(1) << BigInt(64)) - BigInt(1);
  function rotr(x, r) { r = BigInt(r); return ((x >> r) | (x << (BigInt(64) - r))) & M64; }
  function blake2b(input, outlen) {
    var h = IV.slice();
    h[0] ^= BigInt(0x01010000 ^ outlen);
    var blocks = Math.max(1, Math.ceil(input.length / 128)), t = 0;
    for (var bi = 0; bi < blocks; bi++) {
      var last = bi === blocks - 1, chunk = new Uint8Array(128);
      chunk.set(input.subarray(bi * 128, bi * 128 + 128));
      t += last ? input.length - bi * 128 : 128;
      var m = [];
      for (var i = 0; i < 16; i++) {
        var w = BigInt(0);
        for (var j = 7; j >= 0; j--) w = (w << BigInt(8)) | BigInt(chunk[i * 8 + j]);
        m.push(w);
      }
      var v = h.concat(IV);
      v[12] ^= BigInt(t);
      if (last) v[14] ^= M64;
      for (var r = 0; r < 12; r++) {
        var s = SIGMA[r % 10];
        var G = function (a, b, c, d, x, y) {
          v[a] = (v[a] + v[b] + x) & M64; v[d] = rotr(v[d] ^ v[a], 32);
          v[c] = (v[c] + v[d]) & M64; v[b] = rotr(v[b] ^ v[c], 24);
          v[a] = (v[a] + v[b] + y) & M64; v[d] = rotr(v[d] ^ v[a], 16);
          v[c] = (v[c] + v[d]) & M64; v[b] = rotr(v[b] ^ v[c], 63);
        };
        G(0, 4, 8, 12, m[s[0]], m[s[1]]); G(1, 5, 9, 13, m[s[2]], m[s[3]]); G(2, 6, 10, 14, m[s[4]], m[s[5]]); G(3, 7, 11, 15, m[s[6]], m[s[7]]);
        G(0, 5, 10, 15, m[s[8]], m[s[9]]); G(1, 6, 11, 12, m[s[10]], m[s[11]]); G(2, 7, 8, 13, m[s[12]], m[s[13]]); G(3, 4, 9, 14, m[s[14]], m[s[15]]);
      }
      for (var k = 0; k < 8; k++) h[k] ^= v[k] ^ v[k + 8];
    }
    var out = new Uint8Array(outlen);
    for (var o = 0; o < outlen; o++) out[o] = Number((h[o >> 3] >> BigInt(8 * (o & 7))) & BigInt(255));
    return out;
  }
  function hex(bytes) { return Array.prototype.map.call(bytes, function (b) { return ("0" + b.toString(16)).slice(-2); }).join(""); }
  // SS58 address -> 32-byte public key, checking the checksum. Bittensor uses the generic prefix 42.
  function ss58(addr) {
    var d = b58decode(addr);
    if (!d || d.length !== 35 || d[0] >= 64) return null;
    var pre = new TextEncoder().encode("SS58PRE"), buf = new Uint8Array(pre.length + 33);
    buf.set(pre); buf.set(d.subarray(0, 33), pre.length);
    var sum = blake2b(buf, 64);
    if (sum[0] !== d[33] || sum[1] !== d[34]) return null;
    return d.subarray(1, 33);
  }

  function detect(a) {
    a = a.trim();
    if (/^0x[0-9a-fA-F]{40}$/.test(a)) return "evm";
    if (/^[1-9A-HJ-NP-Za-km-z]{46,48}$/.test(a) && ss58(a)) return "tao";
    if (/^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(a)) { var d = b58decode(a); if (d && d.length === 32) return "sol"; }
    return null;
  }
  var KIND = { evm: "Ethereum-style", sol: "Solana", tao: "Bittensor" };
  var CHAINS = { evm: "Ethereum, Base, Arbitrum, Optimism", sol: "Solana", tao: "Bittensor" };

  // ------------------------------------------------------------ balance readers
  function evmBalances(addr) {
    var data = "0x70a08231000000000000000000000000" + addr.slice(2).toLowerCase();
    return Promise.all(EVM.map(function (ch) {
      var calls = [{ jsonrpc: "2.0", id: 0, method: "eth_getBalance", params: [addr, "latest"] }].concat(ch.tokens.map(function (t, i) {
        return { jsonrpc: "2.0", id: i + 1, method: "eth_call", params: [{ to: t[1], data: data }, "latest"] };
      }));
      return post(ch.rpc, calls).then(function (res) {
        var by = {};
        (Array.isArray(res) ? res : [res]).forEach(function (r) { by[r.id] = r.result; });
        var out = [{ chain: ch.name, symbol: "ETH", amount: units(by[0], 18), cg: "ethereum", link: ch.scan + addr }];
        ch.tokens.forEach(function (t, i) { out.push({ chain: ch.name, symbol: t[0], amount: units(by[i + 1], t[2]), cg: t[3], link: ch.scan + addr }); });
        return out;
      }).catch(function (e) { return [{ chain: ch.name, error: e.message }]; });
    })).then(function (lists) { return [].concat.apply([], lists); });
  }

  function solBalances(addr) {
    var link = "https://solscan.io/account/" + addr;
    var calls = [{ jsonrpc: "2.0", id: 0, method: "getBalance", params: [addr] }].concat(TOKEN_PROGRAMS.map(function (pid, i) {
      return { jsonrpc: "2.0", id: i + 1, method: "getTokenAccountsByOwner", params: [addr, { programId: pid }, { encoding: "jsonParsed" }] };
    }));
    return post(SOL_RPC, calls).then(function (res) {
      var by = {};
      (Array.isArray(res) ? res : [res]).forEach(function (r) { by[r.id] = r.result; });
      var out = [{ chain: "Solana", symbol: "SOL", amount: ((by[0] || {}).value || 0) / 1e9, cg: "solana", link: link }];
      var mints = {};
      [1, 2].forEach(function (k) {
        (((by[k] || {}).value) || []).forEach(function (acc) {
          var info = ((acc.account || {}).data || {}).parsed;
          info = info && info.info;
          if (!info || !info.tokenAmount || !(info.tokenAmount.uiAmount > 0)) return;
          mints[info.mint] = (mints[info.mint] || 0) + info.tokenAmount.uiAmount;
        });
      });
      Object.keys(mints).forEach(function (m) { out.push({ chain: "Solana", symbol: SOL_MINTS[m] || short(m), amount: mints[m], mint: m, link: link }); });
      return out;
    }).catch(function (e) { return [{ chain: "Solana", error: e.message }]; });
  }

  // Free TAO from the chain's System.Account storage, over a WebSocket JSON-RPC call.
  function taoBalances(addr) {
    var link = "https://taostats.io/account/" + addr;
    var pk = ss58(addr);
    var key = "0x26aa394eea5630e07c48ae0c9558cef7b99d880ec681799c0cf30e8886371da9" + hex(blake2b(pk, 16)) + hex(pk);
    return new Promise(function (resolve) {
      var done = false, ws;
      var fail = function (m) { if (!done) { done = true; try { ws.close(); } catch (e) {} resolve([{ chain: "Bittensor", error: m, link: link }]); } };
      var timer = setTimeout(function () { fail("the Bittensor node didn't answer"); }, 15000);
      try { ws = new WebSocket(TAO_WS); } catch (e) { return fail(e.message); }
      ws.onopen = function () { ws.send(JSON.stringify({ jsonrpc: "2.0", id: 1, method: "state_getStorage", params: [key] })); };
      ws.onerror = function () { fail("couldn't reach the Bittensor node"); };
      ws.onmessage = function (ev) {
        var r = JSON.parse(ev.data);
        if (r.id !== 1) return;
        clearTimeout(timer); done = true; ws.close();
        // AccountInfo: nonce, consumers, providers, sufficients (u32 each), then free, reserved, frozen (u128 LE).
        var free = 0, reserved = 0;
        if (r.result) {
          var b = r.result.slice(2), le = function (off) { var x = BigInt(0); for (var i = 15; i >= 0; i--) x = (x << BigInt(8)) | BigInt(parseInt(b.substr((off + i) * 2, 2), 16)); return Number(x) / 1e9; };
          free = le(16); reserved = le(32);
        }
        var out = [{ chain: "Bittensor", symbol: "TAO", amount: free, cg: "bittensor", link: link, note: "free balance" }];
        if (reserved > 0) out.push({ chain: "Bittensor", symbol: "TAO", amount: reserved, cg: "bittensor", link: link, note: "reserved" });
        out.push({ chain: "Bittensor", info: "Staked TAO and subnet alpha aren't read here yet.", link: link });
        resolve(out);
      };
    });
  }

  function prices(rows) {
    var ids = {}, mints = [];
    rows.forEach(function (r) { if (r.cg) ids[r.cg] = 1; if (r.mint) mints.push(r.mint); });
    var a = Object.keys(ids).length ? getJSON(CG + "/simple/price?ids=" + Object.keys(ids).join(",") + "&vs_currencies=usd").catch(function () { return {}; }) : Promise.resolve({});
    var b = mints.length ? getJSON(CG + "/simple/token_price/solana?contract_addresses=" + mints.slice(0, 30).join(",") + "&vs_currencies=usd").catch(function () { return {}; }) : Promise.resolve({});
    return Promise.all([a, b]).then(function (r) {
      rows.forEach(function (x) {
        var p = x.cg ? (r[0][x.cg] || {}).usd : x.mint ? (r[1][x.mint] || r[1][x.mint.toLowerCase()] || {}).usd : null;
        if (p != null && x.amount != null) { x.price = p; x.value = p * x.amount; }
      });
      return rows;
    });
  }

  // ------------------------------------------------------------ render
  var results = {};
  function render() {
    var list = read();
    $("wl-empty").hidden = !!list.length;
    var total = 0, any = false;
    $("wl-list").innerHTML = list.map(function (w, i) {
      var res = results[w.address], rows = res && res.rows, body;
      if (!res) body = '<p class="sub">Loading balances…</p>';
      else {
        var held = rows.filter(function (r) { return !r.error && !r.info && r.amount > 0; }).sort(function (a, b) { return (b.value || 0) - (a.value || 0); });
        var sub = held.reduce(function (a, r) { return a + (r.value || 0); }, 0);
        total += sub; any = true;
        body = (held.length ? '<div class="tablewrap"><table><thead><tr><th class="l">Asset</th><th class="l">Chain</th><th>Amount</th><th>Price</th><th>Value</th></tr></thead><tbody>' +
          held.map(function (r) {
            return '<tr><td class="l"><b>' + esc(r.symbol) + "</b>" + (r.note ? ' <span class="faint">' + esc(r.note) + "</span>" : "") + '</td><td class="l">' + esc(r.chain) + "</td><td>" + amt(r.amount) + "</td><td>" + usd(r.price) + "</td><td>" + usd(r.value) + "</td></tr>";
          }).join("") + "</tbody></table></div>" : '<p class="sub">No balances found for the assets this page checks.</p>') +
          rows.filter(function (r) { return r.error || r.info; }).map(function (r) {
            return '<p class="wl-err">' + esc(r.chain) + ": " + esc(r.error ? "couldn't load (" + r.error + ")" : r.info) + (r.link ? ' <a href="' + esc(r.link) + '" target="_blank" rel="noopener">' + (r.info ? "See staking on taostats" : "Check on the explorer") + "</a>" : "") + "</p>";
          }).join("");
        body = '<div class="wl-sub">' + usd(sub) + "</div>" + body;
      }
      var link = w.kind === "evm" ? EVM[0].scan + w.address : w.kind === "sol" ? "https://solscan.io/account/" + w.address : "https://taostats.io/account/" + w.address;
      return '<div class="pp-panel wl-card"><div class="wl-head"><div><b>' + esc(w.label || KIND[w.kind] + " wallet") + '</b> <span class="tag">' + esc(CHAINS[w.kind]) + '</span><div class="wl-addr"><a href="' + esc(link) + '" target="_blank" rel="noopener" title="' + esc(w.address) + '">' + esc(short(w.address)) + "</a>" +
        (w.source ? ' <span class="faint">from ' + esc(w.source) + "</span>" : "") + '</div></div><button type="button" class="rs-btn" data-rm="' + i + '">Remove</button></div>' + body + "</div>";
    }).join("");
    $("wl-total").innerHTML = any ? "Total across watched wallets: <b>" + usd(total) + "</b> · prices from CoinGecko · " + new Date().toLocaleTimeString() : "";
  }

  function refresh() {
    var list = read();
    results = {};
    render();
    list.forEach(function (w) {
      var f = w.kind === "evm" ? evmBalances : w.kind === "sol" ? solBalances : taoBalances;
      f(w.address).then(prices).then(function (rows) { results[w.address] = { rows: rows }; render(); });
    });
  }

  function add(address, label, source) {
    address = address.trim();
    var kind = detect(address);
    if (!kind) { msg("That doesn't look like an Ethereum-style (0x…), Solana or Bittensor (5…) address.", true); return false; }
    var list = read();
    if (list.some(function (w) { return w.address.toLowerCase() === address.toLowerCase(); })) { msg("Already watching that address."); return false; }
    list.push({ address: address, kind: kind, label: label || "", source: source || "" });
    write(list);
    msg("Added " + (kind === "evm" ? "an " : "a ") + KIND[kind] + " address.");
    var f = kind === "evm" ? evmBalances : kind === "sol" ? solBalances : taoBalances;
    render();
    f(address).then(prices).then(function (rows) { results[address] = { rows: rows }; render(); });
    return true;
  }
  function msg(t, bad) { $("wl-msg").innerHTML = '<span class="' + (bad ? "down" : "") + '">' + esc(t) + "</span>"; }

  // ------------------------------------------------------------ events
  $("wl-form").addEventListener("submit", function (e) {
    e.preventDefault();
    if (add($("wl-addr").value, $("wl-label").value.trim())) { $("wl-addr").value = ""; $("wl-label").value = ""; }
  });
  $("wl-addr").addEventListener("input", function () {
    var k = detect(this.value);
    $("wl-kind").textContent = this.value.trim() ? (k ? "Looks like " + (k === "evm" ? "an " : "a ") + KIND[k] + " address (" + CHAINS[k] + ")." : "Not a recognised address yet.") : "";
  });
  $("wl-list").addEventListener("click", function (e) {
    var b = e.target.closest("button[data-rm]");
    if (!b) return;
    var list = read(); list.splice(+b.dataset.rm, 1); write(list); render();
  });
  $("wl-refresh").addEventListener("click", refresh);

  // Browser wallets: request the public address only. No signatures, no transactions.
  $("wl-connect-evm").addEventListener("click", function () {
    if (!window.ethereum) { msg("No Ethereum wallet found in this browser (MetaMask, Rabby, Coinbase Wallet and others add one).", true); return; }
    window.ethereum.request({ method: "eth_requestAccounts" }).then(function (accs) {
      var added = (accs || []).filter(function (a) { return add(a, "", "browser wallet"); }).length;
      if (!added && accs && accs.length) msg("Those addresses are already on the list.");
    }).catch(function (e) { msg("The wallet didn't share an address (" + (e.message || e) + ").", true); });
  });
  $("wl-connect-sol").addEventListener("click", function () {
    var p = (window.phantom && window.phantom.solana) || window.solflare || window.solana;
    if (!p || !p.connect) { msg("No Solana wallet found in this browser (Phantom, Solflare and others add one).", true); return; }
    p.connect().then(function (r) {
      var pk = (r && r.publicKey) || p.publicKey;
      add(pk.toString(), "", "browser wallet");
    }).catch(function (e) { msg("The wallet didn't share an address (" + (e.message || e) + ").", true); });
  });
  $("wl-connect-tao").addEventListener("click", function () {
    var inj = window.injectedWeb3 || {}, names = ["talisman", "subwallet-js", "polkadot-js"].filter(function (k) { return inj[k]; });
    if (!names.length) { msg("No Bittensor-capable wallet found in this browser (Talisman, SubWallet or the Polkadot.js extension).", true); return; }
    inj[names[0]].enable("Crypto research engine").then(function (ext) { return ext.accounts.get(); }).then(function (accs) {
      var added = (accs || []).filter(function (a) { return add(a.address, a.name || "", names[0]); }).length;
      if (!added) msg(accs && accs.length ? "Those addresses are already on the list." : "The wallet shared no accounts.");
    }).catch(function (e) { msg("The wallet didn't share an address (" + (e.message || e) + ").", true); });
  });

  // For tests: the hashing and address helpers.
  window.__wallets = { blake2b: blake2b, hex: hex, ss58: ss58, detect: detect };
  refresh();
})();
