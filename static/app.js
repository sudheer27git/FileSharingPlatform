const $ = (id) => document.getElementById(id);
let authMode = "signin";
let csrfToken = "";
let allFiles = [];
let currentUser = null;
const planNames = {starter:"Starter", pro:"Pro", team:"Team"};

function toast(message) {
  const el = $("toast"); el.textContent = message; el.style.display = "block";
  clearTimeout(toast.timer); toast.timer = setTimeout(() => el.style.display = "none", 3500);
}
async function api(url, options = {}) {
  const response = await fetch(url, {credentials:"same-origin", ...options});
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || "Request failed.");
  return data;
}
function formBody(values) {
  const body = new FormData();
  Object.entries(values).forEach(([key, value]) => body.append(key, value));
  return body;
}
function setAuthMode(mode) {
  authMode = mode;
  $("signinTab").classList.toggle("active", mode === "signin");
  $("signupTab").classList.toggle("active", mode === "signup");
  $("emailWrap").classList.toggle("hidden", mode !== "signup");
  $("email").required = mode === "signup";
  $("password").autocomplete = mode === "signin" ? "current-password" : "new-password";
  $("authSubmit").textContent = mode === "signin" ? "Sign in" : "Create account";
  $("authError").textContent = "";
}
$("signinTab").addEventListener("click", () => setAuthMode("signin"));
$("signupTab").addEventListener("click", () => setAuthMode("signup"));

function showApp(user) {
  currentUser = user;
  $("authScreen").classList.add("hidden");
  $("appScreen").classList.remove("hidden");
  $("profileName").textContent = user.username;
  $("topUsername").textContent = user.username;
  $("avatar").textContent = user.username.charAt(0).toUpperCase();
  updatePlan(user.plan);
  showPage("files");
  loadFiles();
}
function showAuth() {
  $("appScreen").classList.add("hidden");
  $("authScreen").classList.remove("hidden");
  csrfToken = "";
}
function updatePlan(plan) {
  const name = planNames[plan] || "Starter";
  $("profilePlan").textContent = `${name} plan`;
  $("currentPlan").textContent = name;
}
$("authForm").addEventListener("submit", async (event) => {
  event.preventDefault(); $("authError").textContent = "";
  const values = {username:$("username").value.trim(), password:$("password").value};
  if (authMode === "signup") values.email = $("email").value.trim();
  $("authSubmit").disabled = true;
  try {
    const data = await api(authMode === "signin" ? "/api/signin" : "/api/signup",
      {method:"POST", body:formBody(values)});
    csrfToken = data.csrf; $("authForm").reset(); showApp(data.user);
    toast(authMode === "signin" ? "Signed in successfully." : "Account created.");
  } catch (error) { $("authError").textContent = error.message; }
  finally { $("authSubmit").disabled = false; }
});
$("signoutBtn").addEventListener("click", async () => {
  try { await api("/api/signout", {method:"POST"}); allFiles=[]; showAuth(); toast("Signed out."); }
  catch (error) { toast(error.message); }
});
function showPage(page) {
  for (const name of ["files","pricing","history"]) $(`${name}Page`).classList.toggle("hidden", name !== page);
  document.querySelectorAll(".nav-link").forEach(btn => btn.classList.toggle("selected", btn.dataset.page === page));
  $("pageTitle").textContent = page === "files" ? "My files" : page === "pricing" ? "Plans & payments" : "Payment history";
  if (page === "pricing") checkPaymentConfig();
  if (page === "history") loadPaymentHistory();
}
document.querySelectorAll(".nav-link").forEach(btn => btn.addEventListener("click", () => showPage(btn.dataset.page)));

function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024**2) return `${(bytes/1024).toFixed(1)} KB`;
  if (bytes < 1024**3) return `${(bytes/1024**2).toFixed(2)} MB`;
  return `${(bytes/1024**3).toFixed(2)} GB`;
}
function safeText(tag, value, className) {
  const el = document.createElement(tag); el.textContent = value;
  if (className) el.className = className;
  return el;
}
function renderFiles() {
  const query = $("searchInput").value.trim().toLowerCase();
  const files = allFiles.filter(f => f.name.toLowerCase().includes(query));
  $("fileCount").textContent = allFiles.length;
  $("listCount").textContent = files.length;
  $("storageUsed").textContent = formatSize(allFiles.reduce((sum, f) => sum + f.size_bytes, 0));
  const table = $("fileTable"); table.replaceChildren();
  if (!files.length) {
    const row=document.createElement("tr"), cell=safeText("td", query ? "No matching files." : "No files yet. Upload your first file!", "empty");
    cell.colSpan=4; row.append(cell); table.append(row); return;
  }
  for (const file of files) {
    const row=document.createElement("tr"), nameCell=document.createElement("td");
    const ext=file.name.includes(".") ? file.name.split(".").pop().slice(0,4).toUpperCase() : "FILE";
    nameCell.append(safeText("span",ext,"file-badge"),safeText("span",file.name,"file-name"));
    const size=safeText("td",formatSize(file.size_bytes));
    const date=safeText("td",new Date(file.created_at).toLocaleString());
    const actionCell=document.createElement("td"), actions=document.createElement("div"); actions.className="actions";
    const download=document.createElement("a"); download.className="action"; download.href=`/api/files/${encodeURIComponent(file.id)}/download`; download.textContent="Download";
    const del=safeText("button","Delete","action delete"); del.type="button"; del.addEventListener("click",()=>deleteFile(file));
    actions.append(download,del); actionCell.append(actions); row.append(nameCell,size,date,actionCell); table.append(row);
  }
}
async function loadFiles() {
  try { allFiles=await api("/api/files"); renderFiles(); }
  catch(error) { toast(error.message); }
}
$("searchInput").addEventListener("input",renderFiles);
async function uploadFile(file) {
  if (!file) return;
  if (file.size === 0) return toast("Empty files are not allowed.");
  if (file.size > 25*1024*1024) return toast("Maximum file size is 25 MB.");
  const body=new FormData(); body.append("file",file);
  $("uploadStatus").textContent=`Uploading ${file.name}…`;
  try {
    await api("/api/files/upload",{method:"POST",body,headers:{"X-CSRF-Token":csrfToken}});
    $("uploadStatus").textContent=""; $("fileInput").value=""; toast("File uploaded."); await loadFiles();
  } catch(error) { $("uploadStatus").textContent=""; toast(error.message); }
}
$("fileInput").addEventListener("change",e=>uploadFile(e.target.files[0]));
const dropZone=$("dropZone");
dropZone.addEventListener("dragover",e=>{e.preventDefault();dropZone.classList.add("dragging");});
dropZone.addEventListener("dragleave",()=>dropZone.classList.remove("dragging"));
dropZone.addEventListener("drop",e=>{e.preventDefault();dropZone.classList.remove("dragging");uploadFile(e.dataTransfer.files[0]);});
async function deleteFile(file) {
  if (!confirm(`Permanently delete "${file.name}"?`)) return;
  try {
    await api(`/api/files/${encodeURIComponent(file.id)}`,{method:"DELETE",headers:{"X-CSRF-Token":csrfToken}});
    toast("File deleted."); await loadFiles();
  } catch(error) { toast(error.message); }
}
async function checkPaymentConfig() {
  const notice=$("paymentNotice");
  try {
    const config=await api("/api/payments/config");
    notice.classList.toggle("hidden",config.enabled);
    if (!config.enabled) notice.textContent="Checkout is not configured yet. Add Razorpay TEST Key ID and Key Secret to .env, then restart the backend.";
  } catch(error) { notice.classList.remove("hidden"); notice.textContent=error.message; }
}
document.querySelectorAll(".pay-btn").forEach(btn=>btn.addEventListener("click",()=>startCheckout(btn.dataset.plan)));
async function startCheckout(planId) {
  if (!csrfToken) return toast("Please sign in again.");
  if (typeof window.Razorpay === "undefined") return toast("Razorpay Checkout did not load. Check internet access and try again.");
  const btn=document.querySelector(`[data-plan="${planId}"]`); btn.disabled=true;
  try {
    const order=await api("/api/payments/create-order",{method:"POST",body:formBody({plan_id:planId}),headers:{"X-CSRF-Token":csrfToken}});
    const checkout=new Razorpay({
      key:order.key_id, amount:order.amount, currency:order.currency,
      name:"FileNest", description:`${order.plan_name} plan`,
      order_id:order.order_id,
      prefill:{name:order.user.name,email:order.user.email},
      theme:{color:"#315de8"},
      handler:async function(response) {
        try {
          await api("/api/payments/verify",{method:"POST",body:formBody({
            razorpay_order_id:response.razorpay_order_id,
            razorpay_payment_id:response.razorpay_payment_id,
            razorpay_signature:response.razorpay_signature
          }),headers:{"X-CSRF-Token":csrfToken}});
          const fresh=await api("/api/me"); updatePlan(fresh.plan); currentUser=fresh;
          toast("Payment verified and plan activated."); showPage("history"); loadPaymentHistory();
        } catch(error) { toast(`Payment verification: ${error.message}`); }
      },
      modal:{ondismiss:()=>toast("Checkout closed. If you completed payment, refresh payment history before retrying.")}
    });
    checkout.open();
  } catch(error) { toast(error.message); }
  finally { btn.disabled=false; }
}
async function loadPaymentHistory() {
  try {
    const payments=await api("/api/payments/history"), table=$("paymentTable"); table.replaceChildren();
    if (!payments.length) {
      const row=document.createElement("tr"), cell=safeText("td","No payments yet.","empty"); cell.colSpan=5; row.append(cell); table.append(row); return;
    }
    for (const p of payments) {
      const row=document.createElement("tr");
      row.append(safeText("td",p.order_id),safeText("td",planNames[p.plan]||p.plan),safeText("td",`${p.currency} ${(p.amount).toFixed(2)}`));
      const statusCell=document.createElement("td"), status=safeText("span",p.status,"status "+p.status); statusCell.append(status);
      row.append(statusCell,safeText("td",new Date(p.created_at).toLocaleString())); table.append(row);
    }
  } catch(error) { toast(error.message); }
}
async function initialize() {
  try {
    const data=await api("/api/me");
    csrfToken=data.csrf;
    showApp(data.user);
  } catch { showAuth(); }
}
initialize();
