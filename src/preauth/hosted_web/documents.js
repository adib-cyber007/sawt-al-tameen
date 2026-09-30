const el = id => document.getElementById(id);
let currentCase;
let accessKey = "";
let busy = false;
const types = ["REFERRAL_LETTER", "CLINICAL_NOTES", "IMAGING_REPORT", "LAB_RESULTS", "PRIOR_TREATMENT_RECORD", "OPERATIVE_PLAN"];
const label = value => value.replaceAll("_", " ").toLowerCase();
for (const type of types) {
  const option = document.createElement("option");
  option.value = type; option.textContent = label(type); el("document-type").append(option);
}
function headers(extra = {}) {
  return { "X-Gateway-Secret": accessKey, "X-Actor-Type": "PROVIDER_PORTAL", "X-Actor-Id": "document-desk", ...extra };
}
async function api(path, options = {}) {
  const reply = await fetch(path, { ...options, headers: headers(options.headers), cache: "no-store" });
  if (!reply.ok) {
    const data = await reply.json().catch(() => ({}));
    throw new Error(reply.status === 401 ? "The access key was not accepted." : data.error?.message || "The request failed. Please try again.");
  }
  return reply;
}
async function refresh() {
  if (!currentCase) return;
  const id = currentCase.id;
  const [caseReply, needsReply] = await Promise.all([api(`/api/v1/cases/${id}`), api(`/api/v1/cases/${id}/required-information`)]);
  const [updated, needs] = await Promise.all([caseReply.json(), needsReply.json()]);
  currentCase = updated;
  el("case-heading").textContent = `${updated.case_reference} — ${label(updated.status)}`;
  el("missing").replaceChildren();
  for (const need of needs.missing_information) {
    const item = document.createElement("li"); item.textContent = need.message || need.description || need.code;
    el("missing").append(item);
  }
  if (!needs.missing_information.length) {
    const item = document.createElement("li"); item.textContent = "All currently requested information is registered."; el("missing").append(item);
  }
  const missingType = needs.missing_information.map(n => n.code?.replace("document.", "")).find(t => types.includes(t));
  if (missingType) el("document-type").value = missingType;
  const editable = ["RECEIVED", "INFORMATION_COLLECTION", "PENDING_INFORMATION", "RECOMMENDATION_READY"].includes(updated.status);
  el("upload").disabled = !editable;
  el("document-type").disabled = el("document-file").disabled = !editable;
  el("received").replaceChildren();
  for (const doc of updated.documents) {
    const item = document.createElement("li");
    const text = document.createElement("span"); text.textContent = `${label(doc.document_type)}: ${doc.title} `;
    const button = document.createElement("button"); button.type = "button"; button.className = "secondary"; button.textContent = "Download";
    button.addEventListener("click", () => action(async () => {
      const response = await api(`/api/v1/cases/${id}/documents/${doc.id}/content`);
      const url = URL.createObjectURL(await response.blob()); const link = document.createElement("a");
      link.href = url; link.download = doc.id + ({"application/pdf":".pdf","image/png":".png","image/jpeg":".jpg"}[doc.media_type] || ""); link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    }));
    item.append(text, button); el("received").append(item);
  }
  if (!updated.documents.length) { const item = document.createElement("li"); item.textContent = "No documents received yet."; el("received").append(item); }
  el("next-step").textContent = editable ? `After submitting the requested documents, return to your conversation and say: “The documents for ${updated.case_reference} are uploaded. Please recheck the case.”` : "This case is already with the review team or closed. Contact the desk if you need to add information.";
  el("case-workspace").hidden = false;
}
async function action(work) {
  if (busy) return;
  busy = true; el("lookup").disabled = el("refresh").disabled = true;
  try { await work(); } catch (error) { el("document-status").textContent = error.message; }
  finally { busy = false; el("lookup").disabled = el("refresh").disabled = false; }
}
el("lookup-form").addEventListener("submit", event => {
  event.preventDefault();
  action(async () => {
    currentCase = undefined; el("case-workspace").hidden = true;
    accessKey = el("access-key").value;
    const reference = el("case-reference").value.trim().toUpperCase();
    if (!/^PA-[0-9A-Z]{8}$/.test(reference)) throw new Error("Enter the case reference in PA-XXXXXXXX format.");
    el("document-status").textContent = "Looking up your case…";
    currentCase = await (await api(`/api/v1/cases/by-reference/${reference}`)).json();
    await refresh(); el("document-status").textContent = "Case found. Requirements are shown below.";
  });
});
el("refresh").addEventListener("click", () => action(async () => { await refresh(); el("document-status").textContent = "Requirements updated."; }));
el("upload-form").addEventListener("submit", event => {
  event.preventDefault();
  action(async () => {
    if (!currentCase) return;
    const file = el("document-file").files[0];
    if (!file || !file.size || file.size > 10 * 1024 * 1024) throw new Error("Choose a non-empty file of at most 10 MB.");
    if (!["application/pdf", "image/png", "image/jpeg"].includes(file.type)) throw new Error("Choose a PDF, PNG or JPEG.");
    el("upload").disabled = true; el("document-status").textContent = "Uploading your document…";
    try {
      const params = new URLSearchParams({ document_type: el("document-type").value, title: file.name.slice(0, 200) });
      await api(`/api/v1/cases/${currentCase.id}/documents/upload?${params}`, { method: "POST", body: file, headers: { "Content-Type": file.type } });
      el("document-file").value = ""; await refresh();
      el("document-status").textContent = "Document received and registered. Updated requirements are shown below.";
    } finally {
      if (currentCase && ["RECEIVED", "INFORMATION_COLLECTION", "PENDING_INFORMATION", "RECOMMENDATION_READY"].includes(currentCase.status)) el("upload").disabled = false;
    }
  });
});
