/* ============================================================
   Horizen · ZEN Staking — frontend logic (ethers v6, vanilla JS)
   Reads come from the Flask JSON API; writes are signed in MetaMask.
   ============================================================ */

const API = {
  config:    () => fetch("/api/config").then((r) => r.json()),
  contracts: () => fetch("/api/contracts").then((r) => r.json()),
  stats:     () => fetch("/api/stats").then((r) => r.json()),
  user:      (a) => fetch(`/api/user/${a}`).then((r) => r.json()),
};

const RING_CIRCUMFERENCE = 2 * Math.PI * 52; // r=52 in the SVG

const state = {
  cfg: null,
  contracts: null,
  provider: null,
  signer: null,
  account: null,
  token: null,
  pool: null,
  stats: null,
  user: null,
  epochDuration: 86400,
  epochEndsAtMs: 0,
  statsTimer: null,
  tickTimer: null,
};

const byId = (id) => document.getElementById(id);

// ---------------------------------------------------------------- helpers
function fmt(decimalStr, dp = 2) {
  if (decimalStr === null || decimalStr === undefined) return "—";
  const n = Number(decimalStr);
  if (Number.isNaN(n)) return "—";
  return n.toLocaleString("en-US", { minimumFractionDigits: dp, maximumFractionDigits: dp });
}

function short(addr) {
  return addr ? `${addr.slice(0, 6)}…${addr.slice(-4)}` : "";
}

// Only allow http(s) URLs to be turned into links (blocks javascript:, data:, etc.)
function safeUrl(u) {
  try {
    const url = new URL(u, location.origin);
    if (url.protocol === "https:" || url.protocol === "http:") return url.href;
  } catch (_) {}
  return null;
}

function explorerTxUrl(hash) {
  return `${state.cfg.explorerUrl}/tx/${hash}`;
}

function prettyError(e) {
  if (e?.code === 4001 || e?.code === "ACTION_REJECTED") return "Rejected in wallet";
  return (
    e?.shortMessage || e?.reason || e?.info?.error?.message || e?.data?.message ||
    e?.message || "Unknown error"
  );
}

function setLoading(btn, on) {
  if (!btn) return;
  btn.classList.toggle("is-loading", on);
  btn.disabled = on;
}

function formatCountdown(secs) {
  secs = Math.max(0, Math.floor(secs));
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const s = secs % 60;
  const pad = (x) => String(x).padStart(2, "0");
  return `${pad(h)}:${pad(m)}:${pad(s)}`;
}

// "1800" -> "30 min"; "3600" -> "1 hr"; "86400" -> "1 day"
function humanizeDuration(secs) {
  if (secs % 86400 === 0) { const d = secs / 86400; return `${d} day${d > 1 ? "s" : ""}`; }
  if (secs % 3600 === 0) { const h = secs / 3600; return `${h} hr`; }
  return `${Math.round(secs / 60)} min`;
}
// adjective form for prose: "30-minute", "1-hour", "daily"
function cadenceAdj(secs) {
  if (secs % 86400 === 0) { const d = secs / 86400; return d === 1 ? "daily" : `${d}-day`; }
  if (secs % 3600 === 0) { const h = secs / 3600; return `${h}-hour`; }
  return `${Math.round(secs / 60)}-minute`;
}

// ---------------------------------------------------------------- toasts
// Built entirely with textContent/DOM nodes — never innerHTML — so revert
// strings, RPC errors, and server-supplied URLs can't inject markup/script.
let toastSeq = 0;
function buildToast(el, type, title, msg, link) {
  el.className = `toast ${type}`;
  el.replaceChildren();
  const ico = document.createElement("span"); ico.className = "toast-ico";
  const body = document.createElement("div"); body.className = "toast-body";
  const t = document.createElement("div"); t.className = "toast-title"; t.textContent = title || "";
  const m = document.createElement("div"); m.className = "toast-msg"; m.textContent = msg || "";
  if (link && link.href) {
    const href = safeUrl(link.href);
    if (href) {
      m.appendChild(document.createTextNode(" "));
      const a = document.createElement("a");
      a.href = href; a.target = "_blank"; a.rel = "noopener noreferrer";
      a.textContent = link.text || "open ↗";
      m.appendChild(a);
    }
  }
  body.append(t, m);
  el.append(ico, body);
}
function toast(type, title, msg, timeout = 6000, link = null) {
  const id = `t${++toastSeq}`;
  const el = document.createElement("div");
  el.id = id;
  buildToast(el, type, title, msg, link);
  byId("toasts").appendChild(el);
  if (timeout) setTimeout(() => dismissToast(id), timeout);
  return id;
}
function updateToast(id, type, title, msg, timeout = 0, link = null) {
  const el = byId(id);
  if (!el) return toast(type, title, msg, timeout, link);
  buildToast(el, type, title, msg, link);
  if (timeout) setTimeout(() => dismissToast(id), timeout);
  return id;
}
function dismissToast(id) {
  const el = byId(id);
  if (!el) return;
  el.classList.add("out");
  setTimeout(() => el.remove(), 350);
}

// ---------------------------------------------------------------- wallet
async function ensureChain() {
  const target = state.cfg.network.chainIdHex;
  const current = await window.ethereum.request({ method: "eth_chainId" });
  if (current.toLowerCase() === target.toLowerCase()) return;
  try {
    await window.ethereum.request({
      method: "wallet_switchEthereumChain",
      params: [{ chainId: target }],
    });
  } catch (e) {
    if (e.code === 4902 || e?.data?.originalError?.code === 4902) {
      await window.ethereum.request({
        method: "wallet_addEthereumChain",
        params: [{
          chainId: target,
          chainName: state.cfg.network.chainName,
          rpcUrls: state.cfg.network.rpcUrls,
          blockExplorerUrls: state.cfg.network.blockExplorerUrls,
          nativeCurrency: state.cfg.network.nativeCurrency,
        }],
      });
    } else {
      throw e;
    }
  }
}

// Explicitly add the Horizen network to MetaMask (works without connecting).
async function addNetwork() {
  if (!window.ethereum) {
    toast("error", "No wallet found", "Install MetaMask first.", 8000, { href: "https://metamask.io", text: "metamask.io" });
    return;
  }
  try {
    await window.ethereum.request({
      method: "wallet_addEthereumChain",
      params: [{
        chainId: state.cfg.network.chainIdHex,
        chainName: state.cfg.network.chainName,
        rpcUrls: state.cfg.network.rpcUrls,
        blockExplorerUrls: state.cfg.network.blockExplorerUrls,
        nativeCurrency: state.cfg.network.nativeCurrency,
      }],
    });
    toast("success", "Network added", `${state.cfg.network.chainName} is now in your wallet.`);
  } catch (e) {
    toast("error", "Could not add network", prettyError(e));
  }
}

// Import the tstZEN token into MetaMask so its balance is visible (EIP-747).
async function addToken() {
  if (!window.ethereum) {
    toast("error", "No wallet found", "Install MetaMask first.", 8000, { href: "https://metamask.io", text: "metamask.io" });
    return;
  }
  if (!state.cfg?.tstZenAddress) {
    toast("error", "Not available", "tstZEN isn't deployed on this network yet.");
    return;
  }
  try {
    const added = await window.ethereum.request({
      method: "wallet_watchAsset",
      params: {
        type: "ERC20",
        options: { address: state.cfg.tstZenAddress, symbol: "tstZEN", decimals: 18 },
      },
    });
    if (added) toast("success", "Token added", "tstZEN is now in your wallet.");
  } catch (e) {
    toast("error", "Could not add token", prettyError(e));
  }
}

async function loadContracts() {
  if (!state.contracts) state.contracts = await API.contracts();
  state.token = new ethers.Contract(
    state.contracts.tstZEN.address, state.contracts.tstZEN.abi, state.signer);
  state.pool = new ethers.Contract(
    state.contracts.stakingPool.address, state.contracts.stakingPool.abi, state.signer);
}

// ---------------------------------------------------------------- session
// Persist an EXPLICIT wallet connection so a refresh keeps the user connected,
// for up to 30 minutes from sign-in, then auto-logout. Stored in sessionStorage,
// so it survives a page refresh but is CLEARED when the tab/browser is closed.
// No marker => no auto-connect on load (a fresh visitor's address is never read
// until they click Connect).
const SESSION_KEY = "wallet-session";
const SESSION_TTL_MS = 30 * 60 * 1000;
let _logoutTimer = null;

function saveSession(account, at) {
  try { sessionStorage.setItem(SESSION_KEY, JSON.stringify({ account, at })); } catch (_) {}
}
function loadSession() {
  try {
    const raw = sessionStorage.getItem(SESSION_KEY);
    if (!raw) return null;
    const s = JSON.parse(raw);
    if (s && s.account && Date.now() - s.at < SESSION_TTL_MS) return s;
    sessionStorage.removeItem(SESSION_KEY); // expired -> clean up
  } catch (_) {}
  return null;
}
function clearSession() {
  try { sessionStorage.removeItem(SESSION_KEY); } catch (_) {}
  if (_logoutTimer) { clearTimeout(_logoutTimer); _logoutTimer = null; }
}
function scheduleAutoLogout(at) {
  if (_logoutTimer) clearTimeout(_logoutTimer);
  _logoutTimer = setTimeout(expireSession, Math.max(0, SESSION_TTL_MS - (Date.now() - at)));
}
function startSession(account) {           // explicit connect / account switch
  const at = Date.now();
  saveSession(account, at);
  scheduleAutoLogout(at);
}
function expireSession() {                 // 30-minute timeout
  clearSession();
  try { sessionStorage.setItem("wallet-session-expired", "1"); } catch (_) {}
  location.reload();
}

// Silent reconnect on load IF there's a valid (<30 min) session and the wallet
// still authorizes the same account. Never prompts the user.
async function restoreSession() {
  const s = loadSession();
  if (!s || !window.ethereum) return;
  try {
    const accounts = await window.ethereum.request({ method: "eth_accounts" });
    if (!accounts.length || accounts[0].toLowerCase() !== s.account.toLowerCase()) {
      clearSession();
      return;
    }
    state.provider = new ethers.BrowserProvider(window.ethereum);
    state.signer = await state.provider.getSigner();
    state.account = accounts[0];
    if (state.cfg?.deployed) await loadContracts();
    await onConnected();
    if (state.cfg?.deployed) await refreshUser();
    scheduleAutoLogout(s.at); // keep the ORIGINAL 30-minute clock across refreshes
  } catch (_) {
    clearSession();
  }
}

let _connecting = false;
async function connect() {
  if (_connecting) return;            // ignore double-clicks while a prompt is open
  if (!window.ethereum) {
    toast("error", "No wallet found", "Install MetaMask to continue.", 9000, { href: "https://metamask.io", text: "metamask.io" });
    return;
  }
  const btn = byId("connect-btn");
  _connecting = true;
  setLoading(btn, true);             // instant visual feedback before any await
  try {
    // Fire the wallet prompt as the very first thing so MetaMask opens immediately.
    // Force an explicit approval (unlock + account selection) rather than silently
    // reusing a stored permission.
    try {
      await window.ethereum.request({
        method: "wallet_requestPermissions",
        params: [{ eth_accounts: {} }],
      });
    } catch (e) {
      if (e?.code === 4001 || e?.code === "ACTION_REJECTED") throw e; // user declined
      // Wallet doesn't support the permissions method — fall back to a request.
      await window.ethereum.request({ method: "eth_requestAccounts" });
    }

    const accounts = await window.ethereum.request({ method: "eth_accounts" });
    if (!accounts.length) return;

    await ensureChain();

    state.provider = new ethers.BrowserProvider(window.ethereum);
    state.signer = await state.provider.getSigner();
    state.account = accounts[0];
    // Connecting a wallet does NOT require the contracts — only staking does.
    if (state.cfg?.deployed) await loadContracts();
    await onConnected();
    if (state.cfg?.deployed) await refreshUser();
    startSession(state.account); // persist for 30 min so a refresh stays connected
  } catch (e) {
    toast("error", "Connection failed", prettyError(e));
  } finally {
    _connecting = false;
    setLoading(btn, false);
  }
}

async function onConnected() {
  document.body.classList.add("connected");
  byId("net-pill").hidden = false;
  byId("connect-btn").classList.add("is-connected");
  byId("connect-label").textContent = short(state.account);
  byId("addr-tag").hidden = false;
  byId("addr-tag").textContent = short(state.account);
  await refreshNetwork();
}

// Verify MetaMask is on the chain the app expects; gate everything on it.
async function refreshNetwork() {
  let ok = false;
  try {
    const cur = await window.ethereum.request({ method: "eth_chainId" });
    ok = parseInt(cur, 16) === state.cfg.network.chainId;
  } catch (_) {}
  state.correctChain = ok;
  byId("wrong-network-banner").hidden = ok;
  const wn = byId("wn-name");
  if (wn) wn.textContent = state.cfg.network.chainName;
  byId("net-pill").classList.toggle("warn", !ok);
  byId("net-label").textContent = ok
    ? `${state.cfg.network.chainName} · ${short(state.account)}`
    : "Wrong network";

  const live = ok && state.cfg.deployed;
  enableActions(live);
  // Connected on the right network, but staking isn't deployed yet.
  if (ok && !state.cfg.deployed) {
    for (const id of ["stake-btn", "unstake-btn", "claim-btn"]) byId(id).textContent = "Staking not live yet";
    byId("faucet-btn").textContent = "Unavailable";
  }
}

function ensureActionReady() {
  if (!state.signer) {
    toast("error", "Connect wallet", "Connect your wallet first.");
    return false;
  }
  if (!state.cfg.deployed) {
    toast("error", "Not live yet", "Staking contracts aren't deployed on this network yet.");
    return false;
  }
  if (!state.correctChain) {
    toast("error", "Wrong network", `Switch to ${state.cfg.network.chainName} first.`);
    return false;
  }
  return true;
}

function enableActions(on) {
  byId("stake-btn").disabled = !on;
  byId("unstake-btn").disabled = !on;
  byId("claim-btn").disabled = !on;
  byId("faucet-btn").disabled = !on;
  if (on) {
    byId("stake-btn").textContent = "Stake";
    byId("unstake-btn").textContent = "Unstake";
    byId("claim-btn").textContent = "Claim rewards";
  }
}

// ---------------------------------------------------------------- tx flow
async function sendTx(label, fn) {
  const t = toast("pending", label, "Confirm in your wallet…", 0);
  try {
    const tx = await fn();
    updateToast(t, "pending", label, "Submitted — awaiting confirmation…");
    await tx.wait();
    updateToast(t, "success", "Confirmed", `${label} ·`, 8000, { href: explorerTxUrl(tx.hash), text: "view ↗" });
  } catch (e) {
    updateToast(t, "error", "Transaction failed", prettyError(e), 9000);
    throw e;
  }
}

function parseAmount(inputId, maxWei) {
  const raw = byId(inputId).value.trim();
  if (!raw || Number(raw) <= 0 || Number.isNaN(Number(raw))) {
    toast("error", "Invalid amount", "Enter an amount greater than zero.");
    return null;
  }
  let wei;
  try {
    wei = ethers.parseUnits(raw, 18);
  } catch {
    toast("error", "Invalid amount", "Could not parse that number.");
    return null;
  }
  if (maxWei !== undefined && wei > BigInt(maxWei)) {
    toast("error", "Amount too high", "That exceeds your available balance.");
    return null;
  }
  return wei;
}

async function doFaucet() {
  if (!ensureActionReady()) return;
  const btn = byId("faucet-btn");
  setLoading(btn, true);
  try {
    await sendTx("Faucet claim", () => state.token.faucet());
    await refreshAll();
  } catch (_) {} finally { setLoading(btn, false); }
}

async function doStake() {
  if (!ensureActionReady()) return;
  const amt = parseAmount("stake-input", state.user?.walletBalance.wei);
  if (amt === null) return;
  const btn = byId("stake-btn");
  setLoading(btn, true);
  try {
    const allowance = BigInt(state.user.allowance.wei);
    if (allowance < amt) {
      await sendTx("Approve tstZEN", () => state.token.approve(state.pool.target, amt));
    }
    await sendTx("Stake tstZEN", () => state.pool.stake(amt));
    byId("stake-input").value = "";
    await refreshAll();
  } catch (_) {} finally { setLoading(btn, false); }
}

async function doUnstake() {
  if (!ensureActionReady()) return;
  const amt = parseAmount("unstake-input", state.user?.stakedBalance.wei);
  if (amt === null) return;
  const btn = byId("unstake-btn");
  setLoading(btn, true);
  try {
    await sendTx("Unstake tstZEN", () => state.pool.unstake(amt));
    byId("unstake-input").value = "";
    await refreshAll();
  } catch (_) {} finally { setLoading(btn, false); }
}

async function doClaim() {
  if (!ensureActionReady()) return;
  const btn = byId("claim-btn");
  setLoading(btn, true);
  try {
    await sendTx("Claim rewards", () => state.pool.claim(0)); // 0 = claim all finalized
    await refreshAll();
  } catch (_) {} finally { setLoading(btn, false); }
}

// ---------------------------------------------------------------- render
function renderStats(s) {
  state.stats = s;
  byId("g-total").textContent = fmt(s.totalStaked.amount, 0);
  byId("g-apr").textContent = s.aprPercent === null ? "∞" : `${fmt(s.aprPercent, 1)}%`;
  byId("g-reserve").textContent = fmt(s.rewardBudgetLeft.amount, 0);
  byId("g-perepoch").textContent = fmt(s.rewardPerEpoch.amount, 2);

  byId("hero-pool").textContent = fmt(s.rewardPerYear.amount, 0);
  byId("p-year").textContent = fmt(s.rewardPerYear.amount, 0);
  byId("p-epoch").textContent = fmt(s.rewardPerEpoch.amount, 2);
  byId("p-distributed").textContent = fmt(s.distributed.amount, 2);
  byId("p-reserve").textContent = fmt(s.rewardBudgetLeft.amount, 0);
  byId("epoch-num").textContent = `#${s.currentEpoch}`;
  byId("epoch-length").textContent = humanizeDuration(s.epochDurationSeconds);
  const cad = byId("hero-cadence");
  if (cad) cad.textContent = cadenceAdj(s.epochDurationSeconds);

  const c = byId("p-contract");
  c.textContent = short(state.cfg.stakingAddress);
  c.href = safeUrl(`${state.cfg.explorerUrl}/address/${state.cfg.stakingAddress}`) || "#";

  // epoch ring sync
  state.epochDuration = s.epochDurationSeconds;
  state.epochEndsAtMs = Date.now() + s.secondsUntilNextEpoch * 1000;
  tickEpoch();
}

function renderUser(u) {
  if (u.error) return;
  state.user = u;
  byId("u-staked").textContent = fmt(u.stakedBalance.amount, 2);
  byId("u-share").textContent = `${fmt(u.sharePercent, 2)}%`;
  byId("u-pending").textContent = fmt(u.pendingRewards.amount, 4);
  byId("claim-amount").textContent = fmt(u.pendingRewards.amount, 4);
  byId("bal-wallet").textContent = fmt(u.walletBalance.amount, 2);
  byId("bal-staked").textContent = fmt(u.stakedBalance.amount, 2);

  const fb = byId("faucet-btn");
  if (u.faucetCooldownSeconds > 0) {
    fb.disabled = true;
    fb.textContent = "Cooling…";
  } else {
    fb.disabled = false;
    fb.textContent = "Faucet";
  }
}

function tickEpoch() {
  if (!state.epochEndsAtMs) return;
  const remaining = Math.max(0, (state.epochEndsAtMs - Date.now()) / 1000);
  byId("epoch-countdown").textContent = formatCountdown(remaining);

  const frac = Math.max(0, Math.min(1, remaining / state.epochDuration));
  byId("ring-fill").style.strokeDashoffset = RING_CIRCUMFERENCE * (1 - frac);

  if (remaining <= 0) {
    // Epoch rolled over — pull fresh numbers (debounced by the 2s cadence).
    refreshStats();
    if (state.account) refreshUser();
    state.epochEndsAtMs = 0;
  }
}

// ---------------------------------------------------------------- refresh
async function refreshStats() {
  try {
    const s = await API.stats();
    if (!s.error) renderStats(s);
  } catch (_) {}
}
async function refreshUser() {
  if (!state.account) return;
  try {
    renderUser(await API.user(state.account));
  } catch (_) {}
}
async function refreshAll() {
  await Promise.all([refreshStats(), refreshUser()]);
}

// ---------------------------------------------------------------- theme
function initTheme() {
  const btn = byId("theme-btn");
  if (!btn) return;
  btn.addEventListener("click", () => {
    const cur = document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
    const next = cur === "light" ? "dark" : "light";
    document.documentElement.setAttribute("data-theme", next);
    try { localStorage.setItem("theme", next); } catch (_) {}
  });
}

// ---------------------------------------------------------------- tabs
function initTabs() {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      const name = tab.dataset.tab;
      document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("is-active", t === tab));
      document.querySelectorAll(".tabpane").forEach((p) =>
        p.classList.toggle("is-active", p.dataset.pane === name));
    });
  });
}

function initMaxButtons() {
  document.querySelectorAll(".max").forEach((b) => {
    b.addEventListener("click", () => {
      if (!state.user) return;
      const which = b.dataset.max;
      const wei = which === "stake"
        ? state.user.walletBalance.wei
        : state.user.stakedBalance.wei;
      byId(`${which}-input`).value = ethers.formatUnits(wei, 18);
    });
  });
}

// ---------------------------------------------------------------- init
async function init() {
  // Notice shown right after a 30-minute auto-logout reload.
  try {
    if (sessionStorage.getItem("wallet-session-expired")) {
      sessionStorage.removeItem("wallet-session-expired");
      toast("info", "Session expired", "Signed out after 30 minutes — reconnect to continue.", 8000);
    }
  } catch (_) {}

  // 1) Wire all interactive controls FIRST, before any network I/O, so the page
  //    (especially Connect Wallet) is responsive the instant the DOM is ready.
  byId("connect-btn").addEventListener("click", () => connect());
  byId("switch-network-btn").addEventListener("click", async () => {
    try { await ensureChain(); } catch (e) { toast("error", "Switch failed", prettyError(e)); }
  });
  byId("add-network-btn").addEventListener("click", addNetwork);
  byId("add-token-btn").addEventListener("click", addToken);
  byId("faucet-btn").addEventListener("click", doFaucet);
  byId("stake-btn").addEventListener("click", doStake);
  byId("unstake-btn").addEventListener("click", doUnstake);
  byId("claim-btn").addEventListener("click", doClaim);
  initTabs();
  initMaxButtons();
  initTheme();

  if (window.ethereum) {
    // No auto-reconnect: we never read the address on load. Only react to wallet
    // events once the user has explicitly connected this session.
    window.ethereum.on?.("accountsChanged", (accts) => {
      if (!state.account) return;
      if (!accts.length) { clearSession(); location.reload(); }
      else { state.account = accts[0]; startSession(accts[0]); onConnected().then(refreshUser); }
    });
    window.ethereum.on?.("chainChanged", () => { if (state.account) location.reload(); });
  }

  // 2) Then load data in the background — the UI is already interactive.
  try {
    state.cfg = await API.config();
  } catch (e) {
    toast("error", "Backend unreachable", "Could not load app config.");
    return;
  }

  byId("foot-explorer").href = safeUrl(state.cfg.explorerUrl) || "#";

  if (state.cfg.deployed) {
    await refreshStats();
    state.statsTimer = setInterval(refreshStats, 15000);
    state.tickTimer = setInterval(tickEpoch, 1000);
  }
  setInterval(refreshUser, 15000);

  // 3) Restore a still-valid wallet session silently (no prompt). A fresh
  //    visitor with no session marker is left disconnected.
  await restoreSession();
}

document.addEventListener("DOMContentLoaded", init);
