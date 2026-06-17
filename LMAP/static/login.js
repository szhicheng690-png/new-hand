const form = document.querySelector("#loginForm");
const statusText = document.querySelector("#loginStatus");

function nextUrl() {
  const params = new URLSearchParams(window.location.search);
  return params.get("next") || "/";
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const formData = new FormData(form);
  statusText.textContent = "登录中...";

  try {
    const response = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: String(formData.get("username") || "").trim(),
        password: String(formData.get("password") || ""),
      }),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || "登录失败");
    }
    window.location.href = nextUrl();
  } catch (error) {
    statusText.textContent = error.message;
  }
});
