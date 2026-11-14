let csrfToken = "";

async function api(path, method = "GET", body) {
    if (method !== "GET" && !csrfToken) {
        const r = await fetch("/api/auth/csrf-token");
        csrfToken = (await r.json()).csrf_token;
    }
    const response = await fetch(path, {
        method,
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
        body: body ? JSON.stringify(body) : undefined
    });
    return { ok: response.ok, status: response.status, data: await response.json() };
}
const micBtn = document.getElementById("mic-btn");
const statusEl = document.getElementById("status");
const form = document.getElementById("command-form");
const input = document.getElementById("command-input");
const repeatBtn = document.getElementById("repeat-btn");
const slowBtn = document.getElementById("slow-btn");
const stopBtn = document.getElementById("stop-btn");

const TEXT = {
    en: {
        langGroup: "Choose language",
        welcome: "Welcome! Press the button to talk to Shobdo.",
        mic: "Speak",
        micLabel: "Start speaking",
        typeLabel: "Or type your request",
        placeholder: "Remind me to take medicine at 9 PM",
        send: "Send",
        remindersTitle: "My reminders",
        done: "Done",
        cancel: "Cancel",
        noReminders: "No reminders yet.",
        repeat: "Repeat",
        slower: "Slower",
        normalSpeed: "Normal speed",
        stop: "Stop",
        nothingToRepeat: "There is nothing to repeat yet.",
        greeting: "Hello, I am Shobdo. How can I help you?",
        youTyped: "You typed: ",
        typeFirst: "Please type something first.",
        cannotSpeak: "Sorry, this browser cannot speak."
    },
    bn: {
        langGroup: "ভাষা বেছে নিন",
        welcome: "স্বাগতম! শব্দের সঙ্গে কথা বলতে বোতামটি চাপুন।",
        mic: "বলুন",
        micLabel: "কথা বলা শুরু করুন",
        typeLabel: "অথবা আপনার অনুরোধ লিখুন",
        placeholder: "রাত ৯টায় ওষুধ খেতে মনে করিয়ে দিন",
        send: "পাঠান",
        remindersTitle: "আমার রিমাইন্ডার",
        done: "সম্পন্ন",
        cancel: "বাতিল",
        noReminders: "এখনও কোনো রিমাইন্ডার নেই।",
        repeat: "আবার বলুন",
        slower: "ধীরে বলুন",
        normalSpeed: "স্বাভাবিক গতি",
        stop: "থামুন",
        nothingToRepeat: "আবার বলার মতো কিছু নেই।",
        greeting: "আমি শব্দ। আপনাকে কীভাবে সাহায্য করতে পারি?",
        youTyped: "আপনি লিখেছেন: ",
        typeFirst: "অনুগ্রহ করে আগে কিছু লিখুন।",
        cannotSpeak: "দুঃখিত, এই ব্রাউজার কথা বলতে পারে না।"
    }
};
Object.assign(TEXT.en, {
    listening: "Listening... please speak.",
    heard: "I heard: ",
    noSpeech: "I did not hear anything. Please try again.",
    micBlocked: "The microphone is blocked. Please allow it in the browser, or type instead.",
    micError: "Voice input failed. Please try again, or type instead.",
    noRecognition: "Voice input is not supported in this browser. Please type instead."
});

Object.assign(TEXT.bn, {
    listening: "শুনছি... অনুগ্রহ করে বলুন।",
    heard: "আমি শুনেছি: ",
    noSpeech: "কিছু শুনতে পাইনি। আবার চেষ্টা করুন।",
    micBlocked: "মাইক্রোফোন বন্ধ আছে। ব্রাউজারে অনুমতি দিন, অথবা লিখে জানান।",
    micError: "কথা শোনা যায়নি। আবার চেষ্টা করুন, অথবা লিখে জানান।",
    noRecognition: "এই ব্রাউজারে কথা শোনা যায় না। অনুগ্রহ করে লিখে জানান।"
});

let currentLang = "en";
let slow = false;
let lastSpoken = "";

function t(key) {
    return TEXT[currentLang][key];
}

function getLanguage() {
    const chosen = document.querySelector('input[name="language"]:checked');
    return chosen ? chosen.value : "en";
}

function updateSlowButton() {
    slowBtn.textContent = slow ? t("normalSpeed") : t("slower");
    slowBtn.setAttribute("aria-pressed", slow ? "true" : "false");
}

function applyLanguage(lang) {
    currentLang = lang;
    document.documentElement.lang = lang;
    window.speechSynthesis.cancel();

    document.querySelectorAll("[data-i18n]").forEach((el) => {
        el.textContent = t(el.dataset.i18n);
    });
    document.querySelectorAll("[data-i18n-placeholder]").forEach((el) => {
        el.placeholder = t(el.dataset.i18nPlaceholder);
    });
    document.querySelectorAll("[data-i18n-aria]").forEach((el) => {
        el.setAttribute("aria-label", t(el.dataset.i18nAria));
    });
    document.querySelectorAll("[data-text-en]").forEach((el) => {
        el.textContent = lang === "bn" ? el.dataset.textBn : el.dataset.textEn;
    });

    updateSlowButton();
    lastSpoken = "";
    statusEl.textContent = "";
    localStorage.setItem("shobdo-lang", lang);
    loadReminders();
}

function speak(text) {
    if (!("speechSynthesis" in window)) {
        statusEl.textContent = t("cannotSpeak");
        return;
    }
    lastSpoken = text;
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = currentLang === "bn" ? "bn-BD" : "en-US";
    utterance.rate = slow ? 0.6 : 1;
    window.speechSynthesis.speak(utterance);
}

document.querySelectorAll('input[name="language"]').forEach((radio) => {
    radio.addEventListener("change", () => applyLanguage(radio.value));
});

const SpeechRecognitionAPI = window.SpeechRecognition || window.webkitSpeechRecognition;
let recognition = null;
let isListening = false;

function startListening() {
    if (!SpeechRecognitionAPI) {
        statusEl.textContent = t("noRecognition");
        return;
    }

    window.speechSynthesis.cancel();
    recognition = new SpeechRecognitionAPI();
    recognition.lang = currentLang === "bn" ? "bn-BD" : "en-US";
    recognition.interimResults = false;
    recognition.maxAlternatives = 1;

    recognition.onstart = () => {
        isListening = true;
        statusEl.textContent = t("listening");
    };

    recognition.onresult = (event) => {
        const heard = event.results[0][0].transcript;
        console.log("Heard:", heard, "confidence:", event.results[0][0].confidence);
        statusEl.textContent = t("heard") + heard;
        sendCommand(heard);
    };

    recognition.onerror = (event) => {
        console.log("Recognition error:", event.error);
        if (event.error === "not-allowed" || event.error === "service-not-allowed") {
            statusEl.textContent = t("micBlocked");
        } else if (event.error === "no-speech") {
            statusEl.textContent = t("noSpeech");
        } else {
            statusEl.textContent = t("micError");
        }
    };

    recognition.onend = () => {
        isListening = false;
    };

    recognition.start();
}

micBtn.addEventListener("click", () => {
    if (isListening && recognition) {
        recognition.stop();
    } else {
        startListening();
    }
});

repeatBtn.addEventListener("click", () => {
    if (lastSpoken === "") {
        statusEl.textContent = t("nothingToRepeat");
        return;
    }
    speak(lastSpoken);
});

slowBtn.addEventListener("click", () => {
    slow = !slow;
    updateSlowButton();
    if (lastSpoken !== "") {
        speak(lastSpoken);
    }
});

stopBtn.addEventListener("click", () => {
    window.speechSynthesis.cancel();
});

form.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = input.value.trim();

    if (text === "") {
        statusEl.textContent = t("typeFirst");
        input.focus();
        return;
    }

    statusEl.textContent = t("youTyped") + text;
    sendCommand(text);
    input.value = "";
});
const listEl = document.getElementById("reminder-list");

function formatTime(iso) {
    return new Date(iso).toLocaleString(currentLang === "bn" ? "bn-BD" : "en-US",
        { weekday: "long", hour: "numeric", minute: "2-digit" });
}
function actionButton(label, id, action) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn action-btn" + (action === "cancel" ? " action-cancel" : "");
    btn.textContent = label;
    btn.addEventListener("click", async () => {
        btn.disabled = true;
        await api("/api/reminders/" + id + "/" + action, "POST");
        loadReminders();
    });
    return btn;
}
function buildCard(r) {
    const card = document.createElement("div");
    card.className = "neu-card reminder-card mb-4";

    const task = document.createElement("div");
    task.className = "reminder-task";
    task.textContent = r.title;

    const time = document.createElement("div");
    time.className = "reminder-time";
    time.textContent = formatTime(r.remind_at);

    const row = document.createElement("div");
    row.className = "d-flex gap-2 mt-3";
    row.append(actionButton(t("done"), r.id, "complete"),
        actionButton(t("cancel"), r.id, "cancel"));

    card.append(task, time, row);
    return card;
}

async function loadReminders() {
    const res = await api("/api/reminders?status=pending");
    listEl.replaceChildren();
    if (!res.ok || res.data.reminders.length === 0) {
        const p = document.createElement("p");
        p.className = "text-center fs-5";
        p.textContent = t("noReminders");
        listEl.append(p);
        return;
    }
    res.data.reminders.forEach((r) => listEl.append(buildCard(r)));
}
async function checkDue() {
    // Chrome will not speak until the user has tapped the page once
    if (navigator.userActivation && !navigator.userActivation.hasBeenActive) return;

    const res = await api("/api/reminders/due");
    if (!res.ok || res.data.reminders.length === 0) return;

    const titles = res.data.reminders.map((r) => r.title).join(". ");
    const text = (currentLang === "bn" ? "রিমাইন্ডার: " : "Reminder: ") + titles;
    statusEl.textContent = text;
    speak(text);

    for (const r of res.data.reminders) {
        await api("/api/reminders/" + r.id + "/ack", "POST");
    }
}
let pendingId = null;

async function sendCommand(text) {
    const res = await api("/api/voice-command", "POST",
        { text: text, lang: currentLang, pending_id: pendingId });
    if (!res.ok) {
        pendingId = null;
        statusEl.textContent = t("micError");
        speak(t("micError"));
        return;
    }
    const d = res.data;
    const reply = currentLang === "bn" ? d.message_bn : d.message_en;
    pendingId = d.state === "confirm" ? d.pending_id : null;
    statusEl.textContent = reply;
    speak(reply);
    if (d.state === "done" || d.state === "cancelled") loadReminders();
}
// On page load: restore the last chosen language
if (localStorage.getItem("shobdo-lang") === "bn") {
    document.getElementById("lang-bn").checked = true;
}
applyLanguage(getLanguage());
setInterval(checkDue, 30000);
