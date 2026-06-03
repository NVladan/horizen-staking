/* ============================================================
   Horizen · Admin — deploy & fund the contracts from the browser.
   Every transaction is signed in the operator's own wallet (ethers v6).
   ============================================================ */

const API = {
  config:    () => fetch("/api/config").then((r) => r.json()),
  stats:     () => fetch("/api/stats").then((r) => r.json()),
  artifacts: () => fetch("/api/artifacts").then((r) => r.json()),
  save:      (b) => fetch("/api/admin/deployment", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b),
  }).then((r) => r.json()),
  nonce:     () => fetch("/api/admin/nonce").then((r) => r.json()),
  login:     (signature) => fetch("/api/admin/login", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ signature }),
  }).then((r) => r.json()),
  logout:    () => fetch("/api/admin/logout", { method: "POST" }).then((r) => r.json()),
};

const state = {
  cfg: null, art: null, provider: null, signer: null, account: null,
  correctChain: false, tst: null, staking: null,
};

const byId = (id) => document.getElementById(id);

// ---------------------------------------------------------------- helpers
const short = (a) => (a ? `${a.slice(0, 6)}…${a.slice(-4)}` : "");

function prettyError(e) {
  if (e?.code === 4001 || e?.code === "ACTION_REJECTED") return "Rejected in wallet";
  return e?.shortMessage || e?.reason || e?.info?.error?.message || e?.message || "Unknown error";
}
function setLoading(btn, on) { if (btn) { btn.classList.toggle("is-loading", on); btn.disabled = on; } }
function humanize(s) {
  if (s % 86400 === 0) { const d = s / 86400; return d === 1 ? "daily" : `${d}-day`; }
  if (s % 3600 === 0) return `${s / 3600}-hour`;
  return `${Math.round(s / 60)}-minute`;
}
function setStatus(id, text, cls) { const el = byId(id); el.textContent = text; el.className = "step-status " + (cls || ""); }
function unlock(stepId) { byId(stepId).classList.remove("is-locked"); }

// Only allow http(s) URLs as links (blocks javascript:/data: from poisoned config).
function safeUrl(u) {
  try { const url = new URL(u, location.origin); if (url.protocol === "https:" || url.protocol === "http:") return url.href; } catch (_) {}
  return null;
}
function _linkNode(href, text) {
  const a = document.createElement("a"); a.className = "link";
  a.href = safeUrl(href) || "#"; a.target = "_blank"; a.rel = "noopener noreferrer"; a.textContent = text;
  return a;
}
// Built with DOM nodes/textContent — never innerHTML.
function showAddr(elId, label, addr, txHash) {
  const el = byId(elId); el.hidden = false;
  const ex = state.cfg.explorerUrl;
  el.replaceChildren(document.createTextNode(`${label}: `), _linkNode(`${ex}/address/${addr}`, addr));
  if (txHash) {
    el.appendChild(document.createTextNode(" · "));
    el.appendChild(_linkNode(`${ex}/tx/${txHash}`, "tx ↗"));
  }
}

// ---------------------------------------------------------------- toasts
let seq = 0;
function toast(type, title, msg, timeout = 6000) {
  const id = `t${++seq}`;
  const el = document.createElement("div");
  el.className = `toast ${type}`; el.id = id;
  const ico = document.createElement("span"); ico.className = "toast-ico";
  const body = document.createElement("div"); body.className = "toast-body";
  const t = document.createElement("div"); t.className = "toast-title"; t.textContent = title || "";
  const m = document.createElement("div"); m.className = "toast-msg"; m.textContent = msg || "";
  body.append(t, m); el.append(ico, body);
  byId("toasts").appendChild(el);
  if (timeout) setTimeout(() => { el.classList.add("out"); setTimeout(() => el.remove(), 350); }, timeout);
}

// ---------------------------------------------------------------- wallet
async function ensureChain() {
  const target = state.cfg.network.chainIdHex;
  const cur = await window.ethereum.request({ method: "eth_chainId" });
  if (cur.toLowerCase() === target.toLowerCase()) return;
  try {
    await window.ethereum.request({ method: "wallet_switchEthereumChain", params: [{ chainId: target }] });
  } catch (e) {
    if (e.code === 4902 || e?.data?.originalError?.code === 4902) {
      await window.ethereum.request({
        method: "wallet_addEthereumChain",
        params: [{
          chainId: target, chainName: state.cfg.network.chainName,
          rpcUrls: state.cfg.network.rpcUrls, blockExplorerUrls: state.cfg.network.blockExplorerUrls,
          nativeCurrency: state.cfg.network.nativeCurrency,
        }],
      });
    } else { throw e; }
  }
}

async function connect(eager = false) {
  if (!window.ethereum) { toast("error", "No wallet found", "Install MetaMask to continue."); return; }
  try {
    const accounts = eager
      ? await window.ethereum.request({ method: "eth_accounts" })
      : await window.ethereum.request({ method: "eth_requestAccounts" });
    if (!accounts.length) return;
    if (!eager) await ensureChain();
    state.provider = new ethers.BrowserProvider(window.ethereum);
    state.signer = await state.provider.getSigner();
    state.account = accounts[0];
    byId("net-pill").hidden = false;
    byId("connect-btn").classList.add("is-connected");
    byId("connect-label").textContent = short(state.account);
    await refreshNetwork();
  } catch (e) { toast("error", "Connection failed", prettyError(e)); }
}

async function refreshNetwork() {
  let ok = false;
  try { ok = parseInt(await window.ethereum.request({ method: "eth_chainId" }), 16) === state.cfg.network.chainId; } catch (_) {}
  state.correctChain = ok;
  byId("wrong-network-banner").hidden = ok;
  byId("wn-name").textContent = state.cfg.network.chainName;
  byId("net-pill").classList.toggle("warn", !ok);
  byId("net-label").textContent = ok ? `${state.cfg.network.chainName} · ${short(state.account)}` : "Wrong network";

  if (state.account && ok) {
    setStatus("st-connect", "Connected ✓", "ok");
    if (state.reuseToken) {
      byId("s-token").disabled = true;
      byId("s-token").textContent = "Using existing token";
    } else {
      unlock("step-token"); byId("s-token").disabled = false;
      byId("s-token").textContent = "Deploy tstZEN";
      if (state.tst) setStatus("st-token", "Deployed ✓", "ok");
    }
    // Pool step unlocks once a token is chosen (freshly deployed OR reused).
    if (state.tst) { unlock("step-pool"); byId("s-pool").disabled = false; }
    if (state.staking) { setStatus("st-pool", "Deployed ✓", "ok"); unlock("step-fund"); byId("s-fund").disabled = false; }
    const topup = byId("s-topup");
    if (topup && state.cfg.deployed) { topup.disabled = false; topup.textContent = "Top up reward pool"; }
  } else if (state.account) {
    setStatus("st-connect", "Wrong network", "err");
  }
}

function ready() {
  if (!state.signer) { toast("error", "Connect first", "Connect your wallet."); return false; }
  if (!state.correctChain) { toast("error", "Wrong network", `Switch to ${state.cfg.network.chainName}.`); return false; }
  if (!state.art) { toast("error", "No artifacts", "Contract artifacts failed to load."); return false; }
  return true;
}

// ---------------------------------------------------------------- steps
async function deployToken() {
  if (!ready()) return;
  const btn = byId("s-token"); setLoading(btn, true); setStatus("st-token", "Confirm in wallet…", "pending");
  try {
    const f = new ethers.ContractFactory(state.art.tstZEN.abi, state.art.tstZEN.bytecode, state.signer);
    const c = await f.deploy(state.account); // initialOwner
    setStatus("st-token", "Deploying…", "pending");
    await c.waitForDeployment();
    state.tst = await c.getAddress();
    persist();
    showAddr("out-token", "tstZEN", state.tst, c.deploymentTransaction()?.hash);
    setStatus("st-token", "Deployed ✓", "ok");
    unlock("step-pool"); byId("s-pool").disabled = false;
  } catch (e) { setStatus("st-token", prettyError(e), "err"); }
  finally { setLoading(btn, false); }
}

async function deployPool() {
  if (!ready() || !state.tst) return;
  const btn = byId("s-pool"); setLoading(btn, true); setStatus("st-pool", "Confirm in wallet…", "pending");
  try {
    const rewardWei = ethers.parseUnits(String(state.cfg.rewardPerYear), 18);
    const f = new ethers.ContractFactory(state.art.stakingPool.abi, state.art.stakingPool.bytecode, state.signer);
    // (stakeToken, rewardToken, rewardPerYear, epochDuration, owner)
    const c = await f.deploy(state.tst, state.tst, rewardWei, state.cfg.epochDurationSeconds, state.account);
    setStatus("st-pool", "Deploying…", "pending");
    await c.waitForDeployment();
    state.staking = await c.getAddress();
    persist();
    showAddr("out-pool", "StakingPool", state.staking, c.deploymentTransaction()?.hash);
    setStatus("st-pool", "Saving to app…", "pending");
    const res = await API.save({ tstZenAddress: state.tst, stakingAddress: state.staking });
    if (res.error) throw new Error(res.error);
    setStatus("st-pool", "Deployed & saved ✓", "ok");
    unlock("step-fund"); byId("s-fund").disabled = false;
  } catch (e) { setStatus("st-pool", prettyError(e), "err"); }
  finally { setLoading(btn, false); }
}

async function refreshManage() {
  if (!state.cfg.deployed) return;
  try {
    const s = await API.stats();
    if (s.error) return;
    const f = (x) => Number(x).toLocaleString("en-US", { maximumFractionDigits: 2 });
    byId("m-poolleft").textContent = f(s.rewardBudgetLeft.amount) + " tstZEN";
    byId("m-distributed").textContent = f(s.distributed.amount) + " tstZEN";
  } catch (_) {}
}

async function topUp() {
  if (!ready()) return;
  if (!state.cfg.deployed) { toast("error", "Not deployed", "Deploy the contracts first."); return; }
  const raw = byId("topup-input").value.trim();
  if (!raw || Number(raw) <= 0 || Number.isNaN(Number(raw))) {
    toast("error", "Invalid amount", "Enter an amount greater than zero."); return;
  }
  let amt;
  try { amt = ethers.parseUnits(raw, 18); } catch { toast("error", "Invalid amount", "Could not parse."); return; }

  const btn = byId("s-topup"); setLoading(btn, true);
  try {
    const token = new ethers.Contract(state.cfg.tstZenAddress, state.art.tstZEN.abi, state.signer);
    const pool = new ethers.Contract(state.cfg.stakingAddress, state.art.stakingPool.abi, state.signer);
    setStatus("st-topup", "1 / 3 · mint — confirm…", "pending");
    await (await token.mint(state.account, amt)).wait();
    setStatus("st-topup", "2 / 3 · approve — confirm…", "pending");
    await (await token.approve(state.cfg.stakingAddress, amt)).wait();
    setStatus("st-topup", "3 / 3 · fund — confirm…", "pending");
    await (await pool.fundRewards(amt)).wait();
    setStatus("st-topup", "Topped up ✓ — program extended", "ok");
    byId("topup-input").value = "";
    await refreshManage();
  } catch (e) { setStatus("st-topup", prettyError(e), "err"); }
  finally { setLoading(btn, false); }
}

async function fundPool() {
  if (!ready() || !state.tst || !state.staking) return;
  const btn = byId("s-fund"); setLoading(btn, true);
  try {
    const amt = ethers.parseUnits(String(state.cfg.rewardPerYear), 18);
    const token = new ethers.Contract(state.tst, state.art.tstZEN.abi, state.signer);
    const pool = new ethers.Contract(state.staking, state.art.stakingPool.abi, state.signer);
    setStatus("st-fund", "1 / 3 · mint — confirm…", "pending");
    await (await token.mint(state.account, amt)).wait();
    setStatus("st-fund", "2 / 3 · approve — confirm…", "pending");
    await (await token.approve(state.staking, amt)).wait();
    setStatus("st-fund", "3 / 3 · fund — confirm…", "pending");
    await (await pool.fundRewards(amt)).wait();
    setStatus("st-fund", "Funded ✓", "ok");
    byId("done-card").hidden = false;
    clearPersist();
    byId("done-card").scrollIntoView({ behavior: "smooth" });
  } catch (e) { setStatus("st-fund", prettyError(e), "err"); }
  finally { setLoading(btn, false); }
}

// ---------------------------------------------------------------- persistence
function persist() { try { localStorage.setItem("admin-deploy", JSON.stringify({ tst: state.tst, staking: state.staking })); } catch (_) {} }
function clearPersist() { try { localStorage.removeItem("admin-deploy"); } catch (_) {} }
function restore() { try { const d = JSON.parse(localStorage.getItem("admin-deploy") || "null"); if (d) { state.tst = d.tst; state.staking = d.staking; } } catch (_) {} }

// ---------------------------------------------------------------- theme
function initTheme() {
  byId("theme-btn").addEventListener("click", () => {
    const next = document.documentElement.getAttribute("data-theme") === "light" ? "dark" : "light";
    document.documentElement.setAttribute("data-theme", next);
    try { localStorage.setItem("theme", next); } catch (_) {}
  });
}

// ---------------------------------------------------------------- auth gate
async function signIn() {
  if (!window.ethereum) { toast("error", "No wallet found", "Install MetaMask to continue."); return; }
  const btn = byId("s-signin"); setLoading(btn, true);
  setStatus("st-signin", "Connecting…", "pending");
  try {
    const accounts = await window.ethereum.request({ method: "eth_requestAccounts" });
    if (!accounts.length) return;
    setStatus("st-signin", "Requesting challenge…", "pending");
    const ch = await API.nonce();
    if (ch.error) throw new Error(ch.error);
    const provider = new ethers.BrowserProvider(window.ethereum);
    const signer = await provider.getSigner();
    setStatus("st-signin", "Sign the message in your wallet…", "pending");
    const signature = await signer.signMessage(ch.message);
    setStatus("st-signin", "Verifying…", "pending");
    const res = await API.login(signature);
    if (res.ok) { setStatus("st-signin", "Access granted ✓", "ok"); location.reload(); }
    else { setStatus("st-signin", res.error || "Access denied", "err"); }
  } catch (e) { setStatus("st-signin", prettyError(e), "err"); }
  finally { setLoading(btn, false); }
}

function initGate() {
  byId("s-signin").addEventListener("click", signIn);
}

// Reuse the existing tstZEN token: skip step 2, deploy only a new StakingPool.
function onReuseToggle() {
  state.reuseToken = byId("reuse-token").checked;
  if (state.reuseToken) {
    state.tst = state.cfg.tstZenAddress;
    setStatus("st-token", `Reusing ${short(state.tst)} ✓`, "ok");
    showAddr("out-token", "tstZEN (existing)", state.tst);
    byId("step-token").classList.add("is-locked"); // visually de-emphasize
  } else {
    state.tst = null;
    setStatus("st-token", "", "");
    byId("out-token").hidden = true;
    byId("step-token").classList.remove("is-locked");
    byId("s-token").textContent = "Deploy tstZEN";
    byId("step-pool").classList.add("is-locked");
    byId("s-pool").disabled = true;
  }
  refreshNetwork();
}

// ---------------------------------------------------------------- init
async function init() {
  initTheme();
  try { state.cfg = await API.config(); }
  catch (e) { toast("error", "Backend error", "Could not load config."); return; }
  byId("foot-explorer").href = safeUrl(state.cfg.explorerUrl) || "#";

  if (document.body.dataset.authed !== "true") { initGate(); return; }

  const name = state.cfg.network.chainName;
  byId("ah-network").textContent = name;
  const rew = Number(state.cfg.rewardPerYear).toLocaleString("en-US");
  byId("ah-reward").textContent = rew;
  byId("ah-reward2").textContent = rew;
  byId("ah-epoch").textContent = humanize(state.cfg.epochDurationSeconds);
  if (state.cfg.deployed) {
    byId("already-banner").hidden = false;
    byId("manage-card").hidden = false;
    byId("wizard-head").hidden = false;
    byId("reuse-toggle").hidden = false;
    byId("reuse-addr").textContent = short(state.cfg.tstZenAddress);
    byId("reuse-token").addEventListener("change", onReuseToggle);
    refreshManage();
  }

  try { state.art = await API.artifacts(); }
  catch (e) { toast("error", "Artifacts error", "Could not load contract artifacts."); }

  restore();
  if (state.tst) showAddr("out-token", "tstZEN", state.tst);
  if (state.staking) showAddr("out-pool", "StakingPool", state.staking);

  byId("connect-btn").addEventListener("click", () => connect(false));
  byId("s-connect").addEventListener("click", () => connect(false));
  byId("switch-network-btn").addEventListener("click", async () => {
    try { await ensureChain(); } catch (e) { toast("error", "Switch failed", prettyError(e)); }
  });
  byId("s-token").addEventListener("click", deployToken);
  byId("s-pool").addEventListener("click", deployPool);
  byId("s-fund").addEventListener("click", fundPool);
  const topupBtn = byId("s-topup");
  if (topupBtn) topupBtn.addEventListener("click", topUp);
  const logout = byId("logout-link");
  if (logout) logout.addEventListener("click", async (e) => {
    e.preventDefault(); await API.logout(); location.reload();
  });

  if (window.ethereum) {
    window.ethereum.on?.("chainChanged", () => location.reload());
    window.ethereum.on?.("accountsChanged", () => location.reload());
    connect(true);
  }
}
document.addEventListener("DOMContentLoaded", init);
