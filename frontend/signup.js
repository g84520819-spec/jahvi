// signup.js
// Handles: getting/caching the backend-issued device id, form validation,
// submitting signup, and showing success/error state.

const API_BASE_URL = window.JAHVI_API_BASE_URL || '';
const DEVICE_ID_STORAGE_KEY = "jahvi_device_id";

const form = document.getElementById("signup-form");
const submitBtn = document.getElementById("submit-btn");
const errorBox = document.getElementById("form-error");
const togglePasswordBtn = document.getElementById("toggle-password");
const passwordInput = document.getElementById("password");

let isSubmitting = false;

/**
 * Returns the cached device id if we have one, otherwise asks the backend
 * to generate a new cryptographically secure one and caches it.
 * The device id is never generated on the frontend — only stored here.
 */
async function getOrCreateDeviceId() {
  const cached = localStorage.getItem(DEVICE_ID_STORAGE_KEY);
  if (cached) return cached;

  const res = await fetch(`${API_BASE_URL}/auth/device-id`);
  if (!res.ok) {
    throw new Error("Could not initialize device id");
  }
  const data = await res.json();
  localStorage.setItem(DEVICE_ID_STORAGE_KEY, data.device_id);
  return data.device_id;
}

function showError(message) {
  errorBox.textContent = message;
  errorBox.classList.add("visible");
}

function clearError() {
  errorBox.textContent = "";
  errorBox.classList.remove("visible");
}

function setSubmitting(state) {
  isSubmitting = state;
  submitBtn.disabled = state;
  submitBtn.textContent = state ? "Creating account..." : "Create account";
}

function validateClientSide({ full_name, email, password, terms }) {
  if (full_name.length < 2) return "Enter your full name.";
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    return "Enter a valid email address.";
  }
  if (password.length < 8) return "Password must be at least 8 characters.";
  if (!/[A-Za-z]/.test(password) || !/[0-9]/.test(password)) {
    return "Password must contain at least one letter and one number.";
  }
  if (!terms) return "You must agree to the Terms and Privacy Policy.";
  return null;
}

togglePasswordBtn.addEventListener("click", () => {
  const isHidden = passwordInput.type === "password";
  passwordInput.type = isHidden ? "text" : "password";
  togglePasswordBtn.textContent = isHidden ? "🙈" : "👁";
  togglePasswordBtn.setAttribute("aria-label", isHidden ? "Hide password" : "Show password");
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearError();

  // Prevent duplicate submissions (double click, slow network + repeated Enter, etc.)
  if (isSubmitting) return;

  const payload = {
    full_name: document.getElementById("full_name").value.trim(),
    email: document.getElementById("email").value.trim(),
    password: passwordInput.value,
    terms: document.getElementById("terms").checked,
  };

  const clientError = validateClientSide(payload);
  if (clientError) {
    showError(clientError);
    return;
  }

  setSubmitting(true);

  try {
    const deviceId = await getOrCreateDeviceId();

    const res = await fetch(`${API_BASE_URL}/auth/signup`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include", // lets the browser store the HttpOnly refresh-token cookie
      body: JSON.stringify({
        full_name: payload.full_name,
        email: payload.email,
        password: payload.password,
        device_id: deviceId,
        // Note: no IP address is ever sent — the backend reads it from the connection itself.
      }),
    });

    const data = await res.json().catch(() => null);

    if (!res.ok) {
      const message = (data && data.detail) || "Something went wrong. Please try again.";
      showError(typeof message === "string" ? message : "Please check your details and try again.");
      setSubmitting(false);
      return;
    }

    localStorage.setItem("jahvi_user", JSON.stringify(data.user));
    window.location.href = "dashboard.html";
  } catch (err) {
    showError(`Could not reach the signup server at ${API_BASE_URL}. Make sure port 8000 is public and the backend is running.`);
    setSubmitting(false);
  }
});
