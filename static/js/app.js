const micBtn = document.getElementById("mic-btn");
const statusEl = document.getElementById("status");

micBtn.addEventListener("click", () => {
    statusEl.textContent = "Button clicked! Voice is coming next.";
});