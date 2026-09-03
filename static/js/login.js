// VPS-PANEL login/register
const loginForm = document.getElementById("login-form");
const registerForm = document.getElementById("register-form");
const regToggle = document.getElementById("register-toggle");
const errBox = document.getElementById("login-error");

function showError(msg) {
    errBox.textContent = msg;
    errBox.style.display = "block";
    setTimeout(() => (errBox.style.display = "none"), 6000);
}

loginForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = document.getElementById("login-btn");
    btn.disabled = true;
    try {
        const res = await fetch("/login", {
            method: "POST",
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body: new URLSearchParams({
                username: document.getElementById("login-username").value.trim(),
                password: document.getElementById("login-password").value,
            }),
        });
        if (res.ok) {
            window.location.href = "/";
            return;
        }
        const data = await res.json().catch(() => ({}));
        showError(data.detail || `Error ${res.status}`);
    } catch (err) {
        showError("Network error");
    } finally {
        btn.disabled = false;
    }
});

regToggle.addEventListener("click", () => {
    const hidden = registerForm.style.display === "none";
    registerForm.style.display = hidden ? "block" : "none";
    regToggle.textContent = hidden ? "BACK TO LOGIN" : "REGISTER NEW ACCOUNT";
});

registerForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const res = await fetch("/register", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
            username: document.getElementById("reg-username").value.trim(),
            password: document.getElementById("reg-password").value,
        }),
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
        showError(data.approval_required
            ? "Account created. Wait for admin approval, then login."
            : "Account created! You can login now.");
        registerForm.style.display = "none";
        regToggle.textContent = "REGISTER NEW ACCOUNT";
    } else {
        showError(data.detail || `Error ${res.status}`);
    }
});