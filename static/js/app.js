const micBtn = document.getElementById("mic-btn");
const statusEl = document.getElementById("status");

micBtn.addEventListener("click", () => {
    statusEl.textContent = "Button clicked! Voice is coming next.";
});
const form = document.getElementById("command-form");
const input = document.getElementById("command-input");

form.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = input.value.trim();

    if (text === "") {
        statusEl.textContent = "Please type something first.";
        input.focus();
        return;
    }

    statusEl.textContent = "You typed: " + text;
    input.value = "";
});