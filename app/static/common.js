// 세 화면(QA 도구 / 쇼핑몰 / 판매자 어드민) 공통 헬퍼
const STATUS_LABEL = {
  PAID: "결제완료", PREPARING: "상품준비중", SHIPPING: "배송중", DELIVERED: "배송완료",
  CONFIRMED: "구매확정", CANCELED: "주문취소", RETURN_REQUESTED: "반품요청", RETURNED: "반품완료",
};
const $ = (id) => document.getElementById(id);
const won = (n) => n.toLocaleString("ko-KR");
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const numOrNull = (id) => ($(id).value === "" ? null : Number($(id).value));
const statusBadge = (s) => `<span class="status s-${s}" data-testid="status-badge">${STATUS_LABEL[s] || s}</span>`;

function toast(message, isError = false) {
  const el = document.createElement("div");
  el.className = "toast" + (isError ? " error" : "");
  el.innerHTML = message;
  el.dataset.testid = isError ? "toast-error" : "toast";
  $("toasts").appendChild(el);
  setTimeout(() => el.remove(), isError ? 6000 : 3000);
}

// quiet=true 면 에러 토스트를 띄우지 않는다 (일괄 처리처럼 호출한 쪽이 결과를 모아서 알려줄 때)
async function api(method, path, body, { quiet = false } = {}) {
  const res = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json", ...(window.apiHeaders ? window.apiHeaders() : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = data.error || { code: res.status, message: typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail) };
    if (!quiet) toast(`<code>${esc(err.code)}</code> ${esc(err.message)}`, true);
    throw new Error(err.code);
  }
  return data;
}

// 버튼을 누르는 동안 비활성화해서 중복 요청을 막는다
async function withButton(btn, fn) {
  btn.disabled = true;
  try { await fn(); } catch (e) { /* 에러는 api() 에서 토스트로 표시 */ } finally { btn.disabled = false; }
}

